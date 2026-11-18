"""
report.py — puts everything together:

  1. research_company()  → fetch Companies House + Tavily data, give every source an ID (S1, S2…)
  2. build_report()      → ask the AI to write the report, then CHECK its work in code:
       - drop any claim whose source IDs we didn't actually fetch
       - only allow news picks that are real news sources
       - apply hard ICP rules (closed company, size limits) in code, not by the AI
  3. write_email()        → only when the user asks: the outreach email, using only facts
                            already in the report, checked for length, spelling and trigger
  The result is a plain dictionary the web page (Step 6) can show.

Try it (about 9 Tavily credits + 1–2 Gemini requests):
    .venv/bin/python -m report "Cognism"
"""

import re
import sys
import time
from datetime import date, timedelta

import settings
from ai.prompts import build_email_prompt, build_report_prompt, build_shorten_prompt
from ai.provider import AIError, generate_structured, last_model_used
from models import AIReport, Claim, EmailDraft
from sources import news_tavily as tavily
from sources import uk_companies_house as ch

# The live progress steps shown on the page, in order. Each one matches real work below.
STEPS = [
    "Finding the company on Companies House",   # profile and directors
    "Reading recent news",                      # company profile card, website, news
    "Checking reasons to reach out",            # the signal searches
    "Scoring the fit",                          # the AI request
    "Writing the report",                       # code checks and building the report
]
START, DONE = "start", "done"


def _no_progress(step, state):
    """Default: nobody is watching the progress (command line, tests)."""


ALLOWED_SIGNAL_TYPES = {
    "hiring_sales", "funding", "new_sales_leader", "expansion", "layoffs",
    "headcount_growth", "headcount_decline", "new_director",
}


# ---------------------------------------------------------------------------
# Numbered sources
# ---------------------------------------------------------------------------
class SourceList:
    """Every piece of fetched data gets an ID (S1, S2…) so claims can point to it."""

    def __init__(self):
        self.items = []      # list of source dicts, in order
        self._by_url = {}    # url → id, so the same page isn't listed twice

    def add(self, kind, title, url, content, date=None):
        if url in self._by_url:
            return self._by_url[url]
        source_id = f"S{len(self.items) + 1}"
        self.items.append({"id": source_id, "kind": kind, "title": title,
                           "url": url, "date": date, "content": content})
        self._by_url[url] = source_id
        return source_id

    def get(self, source_id):
        return next((s for s in self.items if s["id"] == source_id), None)

    def ids(self, kind=None):
        return {s["id"] for s in self.items if kind is None or s["kind"] == kind}

    def as_prompt_text(self):
        """All sources written out for the AI, grouped by type."""
        sections = {
            "companies_house": "OFFICIAL COMPANY DATA (Companies House)",
            "company_card": "COMPANY PROFILE (headcount, funding)",
            "website": "COMPANY WEBSITE",
            "news": "NEWS CANDIDATES (some may be about something else)",
            "signal": "SIGNAL CANDIDATES (check each one really shows the signal)",
        }
        lines = []
        for kind, heading in sections.items():
            group = [s for s in self.items if s["kind"] == kind]
            if not group:
                continue
            lines.append(f"\n## {heading}")
            for s in group:
                dated = f" ({s['date']})" if s.get("date") else ""
                lines.append(f"[{s['id']}] {s['title']}{dated}\n{s['content']}")
        return "\n".join(lines)


def _lines(pairs):
    """Turn [("Status", "active"), ("Founded", None)] into text, skipping missing values."""
    return "\n".join(f"{label}: {value}" for label, value in pairs if value not in (None, [], ""))


# ---------------------------------------------------------------------------
# 1. Research
# ---------------------------------------------------------------------------
def brand_from(typed):
    """What to search news for: 'https://www.paddle.com' → 'Paddle', 'Sage Group plc' → 'Sage Group'."""
    words = [w for w in ch.clean_query(typed).split() if w not in ("ltd", "limited", "plc", "llp")]
    return " ".join(words).title()


def looks_like_website(typed):
    t = (typed or "").strip().lower()
    return t.startswith(("http://", "https://", "www.")) or ("." in t and " " not in t)


def too_old(iso_date, today=None):
    """True if a dated event is older than EVENT_WINDOW_MONTHS. Undated items can't be judged, so they're kept."""
    if not iso_date:
        return False
    cutoff = (today or date.today()) - timedelta(days=30 * settings.EVENT_WINDOW_MONTHS)
    return str(iso_date)[:10] < cutoff.isoformat()


def research_company(company_number, typed, on_step=_no_progress):
    """
    Fetch everything we know about one company. Never crashes on a Tavily problem.
    Stops starting new searches after RESEARCH_TIME_BUDGET_SECONDS, and records how long each part took.
    on_step(step_number, "start"/"done") is called as each of the STEPS begins and ends (for live progress).
    """
    started = time.monotonic()
    on_step(0, START)
    sources = SourceList()
    warnings = []

    profile = ch.get_company_profile(company_number)
    # Official SIC meanings from Companies House, so the AI never has to guess what a code means
    sic = "; ".join(profile["sic_descriptions"]) or None
    sources.add("companies_house", f"Companies House: {profile['name']}", profile["source"], _lines([
        ("Legal name", profile["name"]), ("Company number", profile["company_number"]),
        ("Status", profile["status"]), ("Closed on", profile["closed_on"]),
        ("Founded", profile["founded"]), ("Registered address", profile["address"]),
        ("Industry codes (SIC)", sic), ("Last accounts type", profile["last_accounts_type"]),
    ]))

    research = {"profile": profile, "card": None, "website": None,
                "sources": sources, "warnings": warnings, "typed": typed, "timings": {}}
    if profile["not_worth_contacting"]:
        on_step(0, DONE)
        return research  # no point spending credits on a closed company

    directors = ch.get_recent_directors(company_number)
    if directors:
        sources.add("signal", "Companies House: recently appointed directors", directors[0]["source"],
                    "\n".join(f"{d['name']} ({d['role']}, {d['occupation'] or 'occupation not listed'}), "
                              f"appointed {d['appointed_on']}" for d in directors),
                    max((d["appointed_on"] for d in directors if d.get("appointed_on")), default=None))

    research["timings"]["Companies House"] = round(time.monotonic() - started, 1)
    on_step(0, DONE)
    brand = brand_from(typed)
    website = typed if looks_like_website(typed) else None

    def out_of_time():
        return time.monotonic() - started > settings.RESEARCH_TIME_BUDGET_SECONDS

    def run_step(label, fn):
        """Run one Tavily step, timing it. A slow step is skipped; a key/credit problem stops all."""
        if out_of_time():
            warnings.append(f"{label} skipped to keep the report under a minute.")
            return None
        step_start = time.monotonic()
        try:
            return fn()
        except tavily.NewsError as e:
            if not e.temporary:
                raise
            warnings.append(f"{label} skipped: {e}")
            return None
        finally:
            research["timings"][label] = round(time.monotonic() - step_start, 1)

    try:
        on_step(1, START)
        # Order matters: the card gives the website and topic word that make later searches accurate.
        # The website comes before news and signals because the AI needs it to spot lookalike results.
        card = run_step("Company profile", lambda: tavily.get_company_card(
            brand, legal_name=profile["name"], website=website, registered_address=profile["address"]))
        if card:
            research["card"] = card
            website = website or card["website"]
            _add_card_source(sources, brand, card)
        topic = tavily.topic_hint(card)

        web = run_step("Website", lambda: tavily.get_website_text(website))
        if web:
            research["website"] = web["source"]
            sources.add("website", f"{brand} website", web["source"], web["text"])

        old = 0   # dated results older than 6 months are left out, so they can't count as reasons to reach out
        for item in run_step("News", lambda: tavily.get_recent_news(brand, website, topic)) or []:
            if too_old(item["date"]):
                old += 1
                continue
            sources.add("news", item["title"], item["source"], item["snippet"], item["date"])
        on_step(1, DONE)

        on_step(2, START)
        signals = run_step("Signals", lambda: tavily.get_signals(
            brand, website, topic, out_of_time=out_of_time)) or {}
        skipped = [t for t in tavily.SIGNAL_SEARCHES if t not in signals]
        if signals and skipped:
            warnings.append(f"Signal searches skipped to keep the report under a minute: {', '.join(skipped)}")
        for signal_type, items in signals.items():
            for item in items:
                if too_old(item["date"]):
                    old += 1
                    continue
                sources.add("signal", f"[{signal_type}] {item['title']}", item["source"],
                            item["snippet"], item["date"])
        research["left_out_old"] = old
        on_step(2, DONE)
    except tavily.NewsError as e:
        warnings.append(f"News and signals unavailable: {e}")
        on_step(1, DONE)
        on_step(2, DONE)

    return research


def _add_card_source(sources, brand, card):
    sources.add("company_card", f"Company profile: {brand}", card["source"], _lines([
        ("Website", card["website"]), ("Headquarters", card["headquarters"]),
        ("Industry", card["industry"]),
        ("Business areas", ", ".join(card["business_areas"] or []) or None),
        ("Business model", card["business_model"]), ("SaaS", card["saas"]),
        ("Founded", card["founded"]), ("Employees (headcount)", card["headcount"]),
        ("Headcount change", card["headcount_yearly_change"]),
        ("Funding rounds", card["funding_rounds"]),
        ("Total raised", ", ".join(card["total_raised"] or []) or None),
        ("Annual revenue", card.get("annual_revenue")),
        ("LinkedIn followers", card.get("linkedin_followers")),
        ("Description", card["description"]),
        ("Confirmed as the same company because", card.get("match_reason")),
    ]))


def company_profile(research):
    """
    The facts for the banner, profile card and stat tiles. Each is {"value", "source"}
    (or None if not found), taken only from Companies House or the confirmed company profile.
    """
    sources, p, card = research["sources"], research["profile"], research["card"] or {}
    ch_id = sources._by_url.get(p["source"])
    card_id = sources._by_url.get(card.get("source"))

    def fact(value, source_id):
        return {"value": value, "source": source_id} if value not in (None, "", []) else None

    founded_year = (p.get("founded") or "")[:4] or None
    website = card.get("website") or research.get("website")
    return {
        "brand": fact(brand_from(research.get("typed") or p["name"]), None),
        "legal_name": fact(p["name"], ch_id),
        "status": fact(p["status"], ch_id),
        "website": fact(website, card_id if card.get("website") else sources._by_url.get(website)),
        "domain": tavily.domain_of(website) if website else None,
        "headquarters": fact(card.get("headquarters"), card_id),
        "industry": (fact(card.get("industry"), card_id)
                     or fact(ch.sic_description((p.get("sic_codes") or [None])[0]), ch_id)),
        "founded": fact(card.get("founded"), card_id) or fact(founded_year, ch_id),
        "headcount": fact(card.get("headcount"), card_id),
        "headcount_change": fact(card.get("headcount_yearly_change"), card_id),
        "total_raised": fact(", ".join(card.get("total_raised") or []), card_id),
        "revenue": fact(card.get("annual_revenue"), card_id),
        "linkedin_followers": fact(card.get("linkedin_followers"), card_id),
        "business_model": fact(card.get("business_model"), card_id),
        "saas": fact(card.get("saas"), card_id),
    }


# ---------------------------------------------------------------------------
# 2. Build and check the report
# ---------------------------------------------------------------------------
def build_report(research, offer=None, ai=generate_structured, today=None, on_step=_no_progress):
    """Ask the AI for the report, then check every part of it in code. on_step: see research_company()."""
    started = time.time()
    offer = (offer or "").strip() or settings.DEFAULT_OFFER
    sources = research["sources"]
    profile = research["profile"]

    if profile["not_worth_contacting"]:
        for step in range(1, len(STEPS)):   # nothing more to do: tick the remaining steps
            on_step(step, DONE)
        return _closed_company_report(research)

    prompt = build_report_prompt(profile["name"], offer, sources.as_prompt_text(),
                                 (today or date.today()).isoformat())
    ai_start = time.monotonic()
    on_step(3, START)
    draft = ai(prompt, AIReport)
    on_step(3, DONE)
    on_step(4, START)
    ai_model = _model_used(ai)
    timings = dict(research.get("timings", {}))
    timings["AI report"] = round(time.monotonic() - ai_start, 1)

    checks = []      # notes on what code changed, shown for transparency
    if research.get("left_out_old"):
        checks.append(f"Left out {research['left_out_old']} search result(s) older than "
                      f"{settings.EVENT_WINDOW_MONTHS} months, so they don't count as reasons to reach out or in the score")
    valid = sources.ids()

    def check_claim(claim):
        """Keep only real source IDs. Returns None if the claim has no real source."""
        good = [i for i in claim.source_ids if i in valid]
        if not good:
            checks.append(f"Removed a claim with no valid source: \"{claim.text[:60]}\"")
            return None
        return {"text": claim.text, "sources": good}

    what_they_do = check_claim(draft.what_they_do)
    snapshot = [c for c in (check_claim(x) for x in draft.snapshot) if c]
    fit_reasons = [c for c in (check_claim(x) for x in draft.fit_reasons) if c]

    # Signals: known type and real sources only
    signals = []
    for s in draft.signals:
        good = [i for i in s.source_ids if i in valid]
        dates = [sources.get(i)["date"] for i in good if sources.get(i).get("date")]
        if s.signal_type in ALLOWED_SIGNAL_TYPES and good and not (dates and too_old(max(dates), today)):
            signals.append({"type": s.signal_type, "text": s.text, "sources": good})
        elif dates and too_old(max(dates), today):
            checks.append(f"Removed a reason to reach out older than {settings.EVENT_WINDOW_MONTHS} months: \"{s.text[:60]}\"")
        else:
            checks.append(f"Removed an unsupported signal: \"{s.text[:60]}\"")

    news = _pick_news(draft.news, signals, sources, today or date.today(), checks)

    # Score: keep it 1–10, then apply hard ICP rules in code
    score = max(1, min(10, draft.fit_score))
    score, fit_reasons = _apply_size_rule(score, fit_reasons, research, checks)


    profile_facts = company_profile(research)
    used = set(n["source"] for n in news)
    for group in ([what_they_do] if what_they_do else []) + snapshot + fit_reasons + signals:
        used.update(group["sources"])
    used.update(f["source"] for f in profile_facts.values() if isinstance(f, dict) and f.get("source"))

    not_found = list(draft.not_found)
    if not research["card"]:
        not_found.append("Company profile (headcount, funding) not found or couldn't be confirmed")
    if not research["website"]:
        not_found.append("Company website couldn't be read")

    on_step(4, DONE)
    return renumber_sources({
        "company": {"name": profile["name"], "number": profile["company_number"],
                    "status": profile["status"], "source": profile["source"]},
        "not_worth_contacting": False,
        "offer": offer,
        "profile": profile_facts,
        "what_they_do": what_they_do,
        "snapshot": snapshot,
        "news": news,
        "signals": signals,
        "fit_score": score,
        "fit_reasons": fit_reasons,
        "suggested_role": draft.suggested_role,
        "role_reason": draft.role_reason,
        "not_found": not_found,
        "sources": [s for s in sources.items if s["id"] in used],
        "all_sources": sources.items,
        "checks": checks,
        "warnings": research["warnings"],
        "timings": timings,
        "ai_model": ai_model,
        "created_on": (today or date.today()).strftime("%-d %b %Y"),
        "seconds": round(time.time() - started, 1),
    })


def renumber_sources(r):
    """
    Number the sources the report shows as S1, S2, S3… in order, with no gaps, and update every tag
    to match. Sources the report doesn't use come after them (they're never listed).
    Works on finished reports, including saved ones and their saved email.
    """
    shown = [s["id"] for s in r["sources"]]
    rest = [s["id"] for s in r["all_sources"] if s["id"] not in shown]
    new_id = {old: f"S{n}" for n, old in enumerate(shown + rest, start=1)}

    def ids(old_ids):
        return [new_id.get(i, i) for i in old_ids]

    def claim(c):
        return dict(c, sources=ids(c["sources"])) if c else c

    def email(e):
        return dict(e, trigger_sources=ids(e.get("trigger_sources", []))) if e else e

    def retag(text):
        return re.sub(r"\bS\d+\b", lambda m: new_id.get(m.group(0), m.group(0)), text)

    out = dict(r)
    out["what_they_do"] = claim(r.get("what_they_do"))
    for key in ("snapshot", "fit_reasons", "signals"):
        out[key] = [claim(c) for c in r.get(key, [])]
    out["news"] = [dict(n, source=new_id.get(n["source"], n["source"])) for n in r.get("news", [])]
    out["profile"] = {k: (dict(f, source=new_id.get(f["source"], f["source"])) if isinstance(f, dict) and f.get("source") else f)
                      for k, f in (r.get("profile") or {}).items()}
    out["all_sources"] = sorted((dict(s, id=new_id[s["id"]]) for s in r["all_sources"]),
                                key=lambda s: int(s["id"][1:]))
    out["sources"] = out["all_sources"][:len(shown)]
    out["checks"] = [retag(c) for c in r.get("checks", [])]
    if r.get("saved_email"):
        out["saved_email"] = email(r["saved_email"])
    return out


def _model_used(ai):
    """Which AI model answered (main or backup). Only known for the real AI, not test fakes."""
    return last_model_used() if ai is generate_structured else getattr(ai, "model_used", None)


LOW_FIT_SCORE = 3  # at or below this, the report says "probably not worth contacting"


def _recent_story_ids(sources, today):
    """IDs of sources that count as recent news: news results, plus dated signal stories from the last 6 months."""
    cutoff = (today - timedelta(days=30 * settings.NEWS_WINDOW_MONTHS)).isoformat()
    signal_stories = {s["id"] for s in sources.items
                      if s["kind"] == "signal" and s.get("date") and s["date"] >= cutoff}
    return sources.ids("news") | signal_stories


def _pick_news(picks, signals, sources, today, checks):
    """
    The news card, at most 3 stories, shown newest first.
    Recent stories used as "why now" signals go in first, so the two cards never disagree
    (real problem: Howden's news said "none found" while a signal showed a 20 Oct story).
    The AI's other picks fill any places left. Only real recent stories are allowed.
    """
    allowed = _recent_story_ids(sources, today)
    ai_reasons = {}
    for pick in picks:
        if pick.source_id in allowed:
            ai_reasons.setdefault(pick.source_id, pick.why_it_matters)
        else:
            checks.append(f"Removed a news pick that wasn't a recent news story ({pick.source_id})")

    signal_stories = {}
    for signal in signals:
        for source_id in signal["sources"]:
            if source_id in allowed:
                signal_stories.setdefault(source_id, signal["text"])

    def newest_first(ids):
        return sorted(ids, key=lambda i: sources.get(i).get("date") or "", reverse=True)

    chosen = list(dict.fromkeys(newest_first(signal_stories) + newest_first(ai_reasons)))[: settings.NEWS_ITEMS]
    for source_id in chosen:
        if source_id not in ai_reasons:
            checks.append(f"Added {source_id} to news because it's a recent story used as a signal")
    return [{"source": i, "why_it_matters": ai_reasons.get(i) or signal_stories[i]} for i in newest_first(chosen)]


def size_band(headcount):
    """'best', 'good' or 'weak' for a number of employees (see the bands in settings.py)."""
    if settings.BEST_FIT_MIN_EMPLOYEES <= headcount <= settings.BEST_FIT_MAX_EMPLOYEES:
        return "best"
    if settings.GOOD_FIT_MIN_EMPLOYEES <= headcount <= settings.GOOD_FIT_MAX_EMPLOYEES:
        return "good"
    return "weak"


def _apply_size_rule(score, reasons, research, checks):
    """
    Size bands decided by code, not the AI:
      best fit (200–2,000)              → no change
      good fit (50–199 or 2,001–10,000) → score at most 8, with the reason shown
      weak fit (under 50 or over 10,000) → score at most 3, with the reason shown
    If headcount wasn't found, there's no rule: the AI judges size from the sources.
    """
    card = research["card"]
    headcount = card and card.get("headcount")
    if not headcount:
        return score, reasons

    band = size_band(headcount)
    if band == "best":
        return score, reasons

    best_range = f"{settings.BEST_FIT_MIN_EMPLOYEES:,}–{settings.BEST_FIT_MAX_EMPLOYEES:,}"
    if band == "good":
        cap = settings.GOOD_FIT_MAX_SCORE
        side = "smaller" if headcount < settings.BEST_FIT_MIN_EMPLOYEES else "larger"
        reason = (f"About {headcount:,} employees: a good fit, but {side} than the best-fit "
                  f"{best_range} range, so the score is slightly lower.")
    else:
        cap = settings.WEAK_FIT_MAX_SCORE
        side = (f"under {settings.GOOD_FIT_MIN_EMPLOYEES:,}" if headcount < settings.GOOD_FIT_MIN_EMPLOYEES
                else f"over {settings.GOOD_FIT_MAX_EMPLOYEES:,}")
        reason = f"About {headcount:,} employees ({side}): a weak fit on size."

    if score > cap:
        checks.append(f"Score capped at {cap}: {headcount:,} employees is a {band} fit on size")
        score = cap
    # Code gives the size reason, so drop the AI's own size reason: each reason appears once
    kept = [r for r in reasons if not _is_size_reason(r["text"], headcount)]
    if len(kept) < len(reasons):
        checks.append("Removed a repeated company-size reason (the size reason above comes from code)")
    card_id = research["sources"]._by_url[card["source"]]
    return score, [{"text": reason, "sources": [card_id]}] + kept


SIZE_WORDS = re.compile(r"\b(employees?|staff|headcount|workforce|size band|company size)\b", re.IGNORECASE)


def _is_size_reason(text, headcount):
    """Is this fit reason about company size? (mentions the headcount, or words like 'employees' or 'size band')"""
    numbers = {f"{headcount:,}", str(headcount)}
    return bool(SIZE_WORDS.search(text)) or any(n in text for n in numbers)


def word_count(text):
    return len((text or "").split())


# Common American → British spellings in sales emails.
# Each entry: (American word start, British word start, allowed endings).
# Endings are listed so 'color' fixes 'colors' but never touches 'Colorado'.
_IZE = r"(?:e|es|ed|ing|ation|ations|er|ers)"
US_TO_UK = [
    ("personaliz", "personalis", _IZE), ("organiz", "organis", _IZE), ("optimiz", "optimis", _IZE),
    ("prioritiz", "prioritis", _IZE), ("realiz", "realis", _IZE), ("recogniz", "recognis", _IZE),
    ("maximiz", "maximis", _IZE), ("minimiz", "minimis", _IZE), ("customiz", "customis", _IZE),
    ("standardiz", "standardis", _IZE), ("centraliz", "centralis", _IZE), ("utiliz", "utilis", _IZE),
    ("analyz", "analys", r"(?:e|es|ed|ing|er|ers)"),
    ("favorite", "favourite", r"(?:s)?"), ("behavior", "behaviour", r"(?:s|al)?"),
    ("color", "colour", r"(?:s|ed|ful)?"), ("center", "centre", r"(?:s|d)?"),
    ("catalog", "catalogue", r"(?:s)?"),
]


def to_british(text):
    """Swap common American spellings for British ones, keeping capitals. Returns (text, changed_words)."""
    changed = []
    for us, uk, endings in US_TO_UK:
        def swap(match, us=us, uk=uk):
            word = match.group(0)
            new = uk + word[len(us):]
            new = new[0].upper() + new[1:] if word[0].isupper() else new
            changed.append(f"{word} → {new}")
            return new
        text = re.sub(rf"\b{us}{endings}\b", swap, text or "", flags=re.IGNORECASE)
    return text, changed


def _check_email(draft, valid, ai, checks, timings=None):
    """Make sure the email is short enough and opens with a trigger we can verify."""
    body = draft.body.strip()
    words = word_count(body)
    if words > settings.EMAIL_MAX_WORDS:
        shorten_start = time.monotonic()
        try:
            shorter = ai(build_shorten_prompt(body, words), Claim).text.strip()
            if word_count(shorter) <= settings.EMAIL_MAX_WORDS:
                checks.append(f"Email shortened from {words} to {word_count(shorter)} words")
                body, words = shorter, word_count(shorter)
        except AIError as e:
            checks.append(f"Couldn't shorten the email: {e}")
        if timings is not None:
            timings["Email shortening"] = round(time.monotonic() - shorten_start, 1)
    # British spelling, enforced by code as well as asked for in the prompt
    body, fixed_body = to_british(body)
    subject, fixed_subject = to_british(draft.subject.strip())
    if fixed_body or fixed_subject:
        checks.append("British spelling: " + ", ".join(fixed_subject + fixed_body))

    warnings = []
    if words > settings.EMAIL_MAX_WORDS:
        warnings.append(f"Email is {words} words, over the {settings.EMAIL_MAX_WORDS}-word limit. Trim before sending.")
    triggers = [i for i in draft.trigger_source_ids if i in valid]
    if not triggers:
        warnings.append("The email's opening trigger couldn't be matched to a source. Check it before sending.")
    return {"subject": subject, "body": body, "word_count": words,
            "trigger_sources": triggers, "warnings": warnings}


# ---------------------------------------------------------------------------
# 3. The email (only when the user clicks "Create email")
# ---------------------------------------------------------------------------
LOW_FIT_NOTE = "Low fit: probably not worth contacting."


def email_facts(r, persona=None):
    """The report's own facts, with their sources, written out for the email prompt."""
    by_id = {s["id"]: s for s in r["all_sources"]}

    def cited(text, ids):
        return f"- {text} " + " ".join(f"[{i}]" for i in ids)

    lines = ["WHAT THEY DO"]
    if r["what_they_do"]:
        lines.append(cited(r["what_they_do"]["text"], r["what_they_do"]["sources"]))
    lines.append("\nWHY NOW SIGNALS")
    lines += [cited(s["text"], s["sources"]) for s in r["signals"]] or ["- none found"]
    lines.append("\nRECENT NEWS")
    for n in r["news"]:
        s = by_id[n["source"]]
        lines.append(cited(f"{s['title']} ({s.get('date') or 'no date'}): {n['why_it_matters']}", [n["source"]]))
    if not r["news"]:
        lines.append("- none found")
    lines.append("\nFIT")
    lines += [cited(c["text"], c["sources"]) for c in r["fit_reasons"]]
    persona = persona or settings.DEFAULT_PERSONA
    lines.append(f"\nWHO WE'RE WRITING TO: {persona}")
    lines.append(f"WHAT THEY CARE ABOUT: {settings.EMAIL_PERSONAS.get(persona, '')}")
    return "\n".join(lines)


def write_email(r, ai=generate_structured, today=None, persona=None):
    """
    Write the outreach email for a finished report, for one persona (Head of Sales, Head of RevOps
    or Founder / CEO). Code then checks it: under 120 words (shortened once if not), British spelling,
    and an opening trigger that cites a source the report actually uses.
    """
    persona = persona if persona in settings.EMAIL_PERSONAS else settings.DEFAULT_PERSONA
    if r["not_worth_contacting"]:
        return None
    checks, timings = [], {}
    started = time.monotonic()
    name = r.get("profile", {}).get("brand", {}).get("value") or r["company"]["name"]
    draft = ai(build_email_prompt(name, r["offer"], email_facts(r, persona), (today or date.today()).isoformat(),
                                  persona), EmailDraft)
    timings["Email"] = round(time.monotonic() - started, 1)
    ai_model = _model_used(ai)
    valid = {s["id"] for s in r["sources"]}   # only sources the report cites
    email = _check_email(draft, valid, ai, checks, timings)
    if r["fit_score"] <= LOW_FIT_SCORE:
        email["warnings"].insert(0, LOW_FIT_NOTE)
    email["checks"], email["timings"], email["ai_model"], email["persona"] = checks, timings, ai_model, persona
    return email


def _closed_company_report(research):
    """Closed, insolvent or dormant: flagged by code with no AI, so it can't be got wrong."""
    p = research["profile"]
    source_id = research["sources"].ids("companies_house").pop()
    if p["last_accounts_type"] == settings.DORMANT_ACCOUNTS_TYPE and p["status"] == "active":
        reason = "Companies House shows the company's last accounts as dormant (not trading)."
    else:
        closed = f" (closed on {p['closed_on']})" if p["closed_on"] else ""
        reason = f"Companies House lists the company as {p['status']}{closed}."
    return renumber_sources({
        "company": {"name": p["name"], "number": p["company_number"],
                    "status": p["status"], "source": p["source"]},
        "not_worth_contacting": True,
        "profile": company_profile(research),
        "what_they_do": None,
        "snapshot": [{"text": f"Founded {p['founded']}." if p["founded"] else "Founded date not found.",
                      "sources": [source_id]}],
        "news": [], "signals": [],
        "fit_score": 1,
        "fit_reasons": [{"text": reason + " Not worth contacting.", "sources": [source_id]}],
        "suggested_role": None, "role_reason": None,
        "not_found": [], "sources": research["sources"].items, "all_sources": research["sources"].items,
        "checks": ["Flagged by code from Companies House status. No AI used."],
        "warnings": research["warnings"], "timings": dict(research.get("timings", {})), "seconds": 0.0,
        "ai_model": None,   # no AI used
        "created_on": date.today().strftime("%-d %b %Y"),
    })


# ---------------------------------------------------------------------------
# Run this file directly to try it out
# ---------------------------------------------------------------------------
def _print_report(r, sources_by_id):
    def cite(ids):
        return " " + "".join(f"[{i}]" for i in ids)

    print(f"\n{'=' * 70}\n{r['company']['name']} ({r['company']['number']}) | {r['company']['status']}")
    if r["not_worth_contacting"]:
        print("⚠️  NOT WORTH CONTACTING")
    print(f"{'=' * 70}")
    print("\nWHAT THEY DO:", (r["what_they_do"]["text"] + cite(r["what_they_do"]["sources"]))
          if r["what_they_do"] else "not found")
    print("\nSNAPSHOT:")
    for c in r["snapshot"]:
        print(f"  - {c['text']}{cite(c['sources'])}")
    print("\nRECENT NEWS:")
    for n in r["news"] or [{"source": None}]:
        if not n["source"]:
            print("  none found")
            continue
        s = sources_by_id[n["source"]]
        print(f"  - [{s['date'] or 'no date'}] {s['title'][:80]} [{n['source']}]\n    → {n['why_it_matters']}")
    print("\n'WHY NOW' SIGNALS:")
    for s in r["signals"] or [None]:
        print(f"  - {s['type']}: {s['text']}{cite(s['sources'])}" if s else "  none found")
    print(f"\nFIT SCORE: {r['fit_score']}/10")
    for c in r["fit_reasons"]:
        print(f"  - {c['text']}{cite(c['sources'])}")
    if r["suggested_role"]:
        print(f"\nCONTACT: {r['suggested_role']}: {r['role_reason']}")
    if r.get("email"):
        e = r["email"]
        print(f"\nEMAIL ({e['word_count']} words, trigger{cite(e['trigger_sources']) or ' none'}):")
        print(f"  Subject: {e['subject']}\n")
        print("  " + e["body"].replace("\n", "\n  "))
        for w in e["warnings"]:
            print(f"  ⚠️  {w}")
    print("\nWHAT WE COULDN'T FIND:")
    for x in r["not_found"] or ["(nothing listed)"]:
        print(f"  - {x}")
    print("\nSOURCES USED:")
    for s in r["sources"]:
        print(f"  [{s['id']}] {s['title'][:70]}\n       {s['url']}")
    if r["checks"]:
        print("\nCODE CHECKS:")
        for c in r["checks"]:
            print(f"  ✓ {c}")
    for w in r["warnings"]:
        print(f"⚠️  {w}")
    if r.get("timings"):
        print("\nTIME TAKEN: " + " · ".join(f"{k} {v}s" for k, v in r["timings"].items()))


if __name__ == "__main__":
    typed = sys.argv[1] if len(sys.argv) > 1 else "Cognism"
    started = time.time()
    try:
        matches = ch.search_companies(typed)
        search_seconds = round(time.time() - started, 1)
        if not matches or not matches[0]["close_match"]:
            print(f'No close match found for "{typed}". Check the spelling or try the website.')
            sys.exit(0)
        research = research_company(matches[0]["company_number"], typed)
        research["timings"] = {"Company search": search_seconds, **research["timings"]}
        result = build_report(research)
        result["email"] = write_email(result)
        _print_report(result, {s["id"]: s for s in result["all_sources"]})
        print(f"\nTotal time: {time.time() - started:.1f} seconds")
    except (ch.CompaniesHouseError, AIError) as e:
        print(f"Sorry: {e}")
