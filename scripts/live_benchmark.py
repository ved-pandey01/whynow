"""
Step 8: live test on real UK companies. LIVE: uses Tavily credits and Gemini requests (about 10 credits
and 1 request per company). Run only when Ved asks (CLAUDE.md rule 9).

    .venv/bin/python -m scripts.live_benchmark            # the full list below
    .venv/bin/python -m scripts.live_benchmark "tide.co"  # just one

For each company it follows the same steps as the app: Companies House search (Active only), the
registered-name check from public sources when Companies House alone can't tell, then research and
the report. It records which company was matched, the fit score, the reasons to reach out, the code
checks (e.g. claims removed for having no source) and the time taken. Each finished report is also
saved to the app's 24-hour report cache, so it can be opened in the app for free.
"""

import hashlib
import json
import sys
import time
from pathlib import Path

from whynow import cache
from whynow import report
from whynow import settings
from sources import news_tavily as tavily
from sources import uk_companies_house as ch

COMPANIES = [   # (what a user would type, sector, rough size)
    ("Sage Group", "Accounting software", "Large (plc)"),
    ("rightmove.co.uk", "Property portal", "Large (plc)"),
    ("autotrader.co.uk", "Automotive marketplace", "Large (plc)"),
    ("gocardless.com", "Payments", "Mid-size"),
    ("Thought Machine", "Core banking software", "Mid-size"),
    ("tide.co", "Business banking", "Mid-size"),
    ("onfido.com", "Identity verification", "Mid-size"),
    ("Featurespace", "Fraud prevention", "Mid-size"),
    ("peak.ai", "AI / decision software", "Small to mid"),
    ("huboo.com", "E-commerce fulfilment", "Mid-size"),
    ("mention-me.com", "Referral marketing", "Small"),
    ("juro.com", "Legal tech", "Small"),
    ("hyperexponential.com", "Insurance software", "Small to mid"),
    ("ravelin.com", "Fraud detection", "Small"),
    ("Ometria", "Retail marketing", "Small"),
    # Tricky ones
    ("Brandwatch", "Brand name (registered as something else)", "Tricky"),
    ("Thougth Machine", "Misspelling of Thought Machine", "Tricky"),
    ("Cazoo", "Dissolved / closed company", "Tricky"),
]
OUT = Path(settings.CACHE_FOLDER) / "benchmark"


def report_id(number, typed, offer):
    """The same id the app uses, so the app can open these reports from its cache."""
    return hashlib.sha1(f"{number}|{typed}|{offer}".encode()).hexdigest()[:12]


def find_registered(typed):
    """The app's public-sources lookup (see app.find_registered), without Streamlit."""
    website = typed if report.looks_like_website(typed) else None
    try:
        found = tavily.find_registration(report.brand_from(typed), website)
    except tavily.NewsError as e:
        return None, str(e)
    match = ch.lookup_registration(found)
    if match:
        match = dict(match, typed=found.get("website") or typed)
    return match, None


def pick(typed):
    """What the app would put first in the list, with Active only on (the default)."""
    matches = [m for m in ch.search_companies(typed, active_only=True) if (m.get("status") or "active") == "active"]
    how = "Companies House"
    chosen = next((m for m in matches if m["close_match"]), None)
    if ch.registration_check_needed(typed, matches):
        found, error = find_registered(typed if report.looks_like_website(typed) else ch.main_name(typed))
        if found:
            chosen, how = found, "public source → Companies House"
        elif error:
            how = f"public-sources lookup failed: {error}"
    if not chosen:
        closed = [m for m in ch.search_companies(typed) if (m.get("status") or "active") != "active"]
        return None, ("only closed companies match (hidden by Active only)" if closed else "no match shown"), closed
    return chosen, how, []


def run_one(typed, sector, size):
    row = {"typed": typed, "sector": sector, "size": size}
    t = time.time()
    chosen, how, closed = pick(typed)
    row["search_seconds"] = round(time.time() - t, 1)
    row["matched_by"] = how
    if not chosen and closed:
        chosen = dict(closed[0])          # the user unticks 'Active only' and picks it
        row["matched_by"] += "; then picked with Active only off"
    if not chosen:
        row.update(matched=None, number=None)
        return row
    row.update(matched=chosen["name"], number=chosen["company_number"])
    research_typed = chosen.get("typed") or typed
    t = time.time()
    research = report.research_company(chosen["company_number"], research_typed)
    r = report.build_report(research, offer=settings.DEFAULT_OFFER)
    row["report_seconds"] = round(time.time() - t, 1)
    rid = report_id(chosen["company_number"], research_typed, settings.DEFAULT_OFFER)
    r["report_id"] = rid
    cache.put("report", rid, r)
    (OUT / f"{rid}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False))
    p = r["profile"]
    row.update(
        report_id=rid, fit_score=r["fit_score"], reasons=len(r.get("signals") or []),
        not_worth_contacting=r.get("not_worth_contacting"),
        website=(p.get("website") or {}).get("value"), employees=(p.get("headcount") or {}).get("value"),
        unsourced_removed=sum("no valid source" in c for c in r.get("checks", [])),
        checks=r.get("checks", []), warnings=r.get("warnings", []),
        ai_model=(r.get("ai_model") or {}).get("name"), sources=len(r.get("all_sources") or []),
    )
    return row


def main(names=None):
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [c for c in COMPANIES if not names or c[0] in names]
    rows = []
    for typed, sector, size in todo:
        print(f"→ {typed}", flush=True)
        try:
            row = run_one(typed, sector, size)
        except Exception as e:            # one failure never stops the rest
            row = {"typed": typed, "sector": sector, "size": size, "error": f"{type(e).__name__}: {e}"}
        rows.append(row)
        print("  ", {k: row.get(k) for k in ("matched", "number", "fit_score", "reasons", "report_seconds", "error")},
              flush=True)
        (OUT / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return rows


if __name__ == "__main__":
    main(sys.argv[1:] or None)
