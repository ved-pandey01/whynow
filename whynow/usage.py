"""
whynow/usage.py — a log of every paid API call (Gemini and Tavily), and today's totals for the owner.

One line per call in .usage/calls-YYYY-MM-DD.jsonl. Cache hits are logged too, at zero cost,
so the owner can see how much the cache saves. No API keys or search text are ever logged.
"""

import hashlib
import json
import time
from datetime import date

from whynow import config
from whynow import settings


def _folder():
    return config.data_folder(settings.USAGE_FOLDER)


def _today_file(name):
    return _folder() / f"{name}-{date.today().isoformat()}.jsonl"


def log_call(service, kind, cost=0, cached=False, model=None, pages=None):
    """
    service: 'tavily' or 'gemini'; kind: 'search', 'extract' or 'generate';
    cost: estimated Tavily credits (0 when cached); model: which Gemini model.
    """
    line = {"time": time.strftime("%H:%M:%S"), "service": service, "kind": kind, "cached": cached,
            "cost": 0 if cached else cost, "model": model, "pages": pages}
    try:
        _folder().mkdir(parents=True, exist_ok=True)
        with _today_file("calls").open("a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass   # logging never stops the app


def today_calls():
    try:
        return [json.loads(line) for line in _today_file("calls").read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def today_totals():
    """{'tavily_searches', 'tavily_page_reads', 'tavily_credits', 'cached', 'gemini': {model: n}}"""
    calls = today_calls()
    paid = [c for c in calls if not c["cached"]]
    gemini = {}
    for c in paid:
        if c["service"] == "gemini":
            gemini[c["model"]] = gemini.get(c["model"], 0) + 1
    return {
        "tavily_searches": sum(1 for c in paid if c["service"] == "tavily" and c["kind"] == "search"),
        "tavily_page_reads": sum(c.get("pages") or 0 for c in paid if c["service"] == "tavily" and c["kind"] == "extract"),
        "tavily_credits": sum(c["cost"] for c in paid if c["service"] == "tavily"),
        "cached": sum(1 for c in calls if c["cached"]),
        "gemini": gemini,
    }


# ---------------------------------------------------------------------------
# Daily live-search limits (per visitor and in total)
# ---------------------------------------------------------------------------
def _searches_file():
    return _folder() / f"searches-{date.today().isoformat()}.json"


def searches_today():
    """{'total': n, 'visitors': {visitor_id: n}} for today."""
    try:
        return json.loads(_searches_file().read_text())
    except (OSError, ValueError):
        return {"total": 0, "visitors": {}}


def record_live_search(visitor):
    counts = searches_today()
    counts["total"] += 1
    counts["visitors"][visitor] = counts["visitors"].get(visitor, 0) + 1
    try:
        _folder().mkdir(parents=True, exist_ok=True)
        _searches_file().write_text(json.dumps(counts))
    except OSError:
        pass


def undo_live_search(visitor):
    """Give a search back when the research failed before a report was made (the visitor got nothing)."""
    counts = searches_today()
    if counts["visitors"].get(visitor, 0) <= 0:
        return
    counts["total"] = max(0, counts["total"] - 1)
    counts["visitors"][visitor] -= 1
    try:
        _searches_file().write_text(json.dumps(counts))
    except OSError:
        pass


def searches_left(visitor):
    """How many free live searches this visitor has left today (their own limit or everyone's, whichever is lower)."""
    counts = searches_today()
    mine = settings.VISITOR_LIVE_SEARCHES_PER_DAY - counts["visitors"].get(visitor, 0)
    everyone = settings.TOTAL_LIVE_SEARCHES_PER_DAY - counts["total"]
    return max(0, min(mine, everyone))


def live_search_allowed(visitor, owner=False):
    """(True, None) if this visitor may run a fresh search now, else (False, 'visitor' or 'total')."""
    if owner:
        return True, None
    counts = searches_today()
    if counts["visitors"].get(visitor, 0) >= settings.VISITOR_LIVE_SEARCHES_PER_DAY:
        return False, "visitor"
    if counts["total"] >= settings.TOTAL_LIVE_SEARCHES_PER_DAY:
        return False, "total"
    return True, None



def visitor_from(headers, ip_address):
    """
    A short hash of the visitor's IP address (the first X-Forwarded-For entry if behind a proxy)
    and browser. The same person refreshing gets the same ID. Only the hash is stored, never the IP.
    """
    forwarded = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for") or ""
    ip = forwarded.split(",")[0].strip() or ip_address or "local"
    browser = headers.get("User-Agent") or headers.get("user-agent") or ""
    return hashlib.sha256(f"{ip}|{browser}".encode()).hexdigest()[:16]
