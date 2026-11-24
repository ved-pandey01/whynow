"""
whynow/report.py — puts everything together:

  1. research_company()  → fetch Companies House + Tavily data, give every source an ID (S1, S2…)
  2. build_report()      → ask the AI to write the report, then CHECK its work in code:
       - drop any claim whose source IDs we didn't actually fetch
       - only allow news picks that are real news sources
       - apply hard ICP rules (closed company, size limits) in code, not by the AI
  3. write_email()        → only when the user asks: the outreach email, using only facts
                            already in the report, checked for length, spelling and trigger
  The result is a plain dictionary the web page (Step 6) can show.

Try it (about 9 Tavily credits + 1–2 Gemini requests):
    .venv/bin/python -m whynow.report "Cognism"
"""

import re
import sys
import time
from datetime import date, timedelta

from whynow import settings
from whynow.ai.prompts import build_email_prompt, build_report_prompt
from whynow.ai.provider import AIError, generate_structured, last_model_used
from whynow.models import AIReport, Claim, OutreachDraft
from whynow.sources import news_tavily as tavily
from whynow.sources import uk_companies_house as ch

# The live progress steps shown on the page, in order. Each one matches real work below.
STEPS = [
    "Finding the company on Companies House",   # profile and directors
    "Reading recent news",                      # company profile card, website, news
    "Checking reasons to reach out",            # the signal searches
    "Scoring the fit",                          # the AI request
    "Writing the report",                       # code checks and building the report
]
START, DONE = "start", "done"


NEWS_UNAVAILABLE = "News and reasons to reach out are unavailable right now"
TAVILY_STEPS = ("Company profile", "News", "Signals", "Jobs")   # 'Website' handles its own errors (it's optional)


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
    """
    What to search news for: 'https://www.paddle.com' → 'Paddle', 'Sage Group plc' → 'Sage Group',
    'BT Local Business – Home Counties North' → 'BT Local Business' (the user's own capitals are kept;
    all-lower or ALL-CAPS text is tidied to 'Title Case').
    """
    text = typed if looks_like_website(typed) else ch.main_name(typed)
    text = ch.clean_query(text) if looks_like_website(typed) else text
    words = [w for w in text.split() if w.lower() not in ("ltd", "limited", "plc", "llp")]
    joined = " ".join(words)
    return joined.title() if joined.islower() or joined.isupper() else joined


def looks_like_website(typed):
    t = (typed or "").strip().lower()
    return t.startswith(("http://", "https://", "www.")) or ("." in t and " " not in t)


def too_old(iso_date, today=None):
    """True if a dated event is older than EVENT_WINDOW_MONTHS. Undated items can't be judged, so they're kept."""
    if not iso_date:
        return False
    cutoff = (today or date.today()) - timedelta(days=30 * settings.EVENT_WINDOW_MONTHS)
    return str(iso_date)[:10] < cutoff.isoformat()


def _is_old_round(signal_text, card, today=None):
    """
    True if a funding signal is really the company's last round, and that round is old.
    Real problem (Paddle): a news page dated Nov 2026 reported a $25m round that the company profile
    dates 17 Aug 2025 (debt financing). The page date looked recent; the round wasn't.
    """
    last = (card or {}).get("last_round")
    if not last or not too_old(last["date"], today):
        return False
    m = re.match(r"([\d.]+)", last["amount"])
    if not m:
        return False
    amount = f"{float(m.group(1)):g}"                       # '25.00M USD' → '25'
    return bool(re.search(rf"(?<![\d.]){re.escape(amount)}(?![\d])", signal_text))


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
        ("Last accounts made up to", profile.get("last_accounts_made_up_to")),
        ("Next accounts due", profile.get("next_accounts_due")),
        ("Accounts overdue", "yes" if profile.get("accounts_overdue") else None),
        ("Last confirmation statement", profile.get("last_confirmation_statement")),
        ("Confirmation statement overdue", "yes" if profile.get("confirmation_statement_overdue") else None),
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

    try:   # for the "More about" section; a problem here never stops the report
        research["directors"] = ch.get_current_directors(company_number)
        research["owners"] = ch.get_owners(company_number)
        filings = ch.get_filing_history(company_number)
        if filings["filings"]:
            research["filings"] = filings
            sources.add("companies_house", "Companies House: filing history", filings["source"],
                        _lines([(f["date"], f["what"]) for f in filings["filings"]]),
                        filings["filings"][0]["date"])
    except ch.CompaniesHouseError as e:
        warnings.append(f"Directors and owners unavailable: {e}")
    research["timings"]["Companies House"] = round(time.monotonic() - started, 1)
    on_step(0, DONE)
    brand = brand_from(typed)
    website = typed if looks_like_website(typed) else None

    def out_of_time():
        return time.monotonic() - started > settings.RESEARCH_TIME_BUDGET_SECONDS

    failed_steps = []   # Tavily steps that failed for a temporary reason (slow or not responding)

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
            failed_steps.append((label, str(e).rstrip(".")))   # summed up in one message at the end
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
            _check_former_business(research, brand, profile, card, sources)
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
        # The one extra search per report: job adverts for "Hiring now" (kept out of the AI's sources)
        for item in run_step("Jobs", lambda: tavily.get_job_listings(brand, website)) or []:
            if not too_old(item["date"]):
                sources.add("job", f"[jobs] {item['title']}", item["source"], item["snippet"], item["date"])
        on_step(2, DONE)
    except tavily.NewsError as e:
        warnings.append(f"{NEWS_UNAVAILABLE} ({str(e).rstrip('.')}). The score uses Companies House data only.")
        on_step(1, DONE)
        on_step(2, DONE)
    else:
        if failed_steps:   # one clear message, not one per step
            reason = failed_steps[0][1]
            if len(failed_steps) == len(TAVILY_STEPS):
                warnings.append(f"{NEWS_UNAVAILABLE} ({reason}). The score uses Companies House data only.")
            else:
                parts = ", ".join(label.lower() for label, _ in failed_steps)
                warnings.append(f"{NEWS_UNAVAILABLE} for: {parts} ({reason}). The report uses the rest of the research.")

    return research


def ownership_note(research):
    """The note at the top of the report when the brand now belongs to a different company, else None."""
    former = research.get("former_business")
    if not former:
        return None
    owner = research["profile"]["name"]
    return {"owner": owner, "former": former["name"], "former_source": former["source"],
            "text": f"This brand is now owned by {owner}. Some details may describe the former business."}


def _check_former_business(research, brand, profile, card, sources):
    """
    If the brand used to be run by a company that has closed (Cazoo → now MOTORS.CO.UK LIMITED), remember it:
    the report then shows a note at the top and prefers Companies House for the founded date, because the
    company profile may describe the former business. The AI is told too, through a Companies House source.
    """
    try:
        former = ch.former_business(brand, profile["name"], card.get("founded"))
    except ch.CompaniesHouseError:
        former = None
    if not former:
        return
    research["former_business"] = {"name": former["name"], "number": former["company_number"],
                                   "status": former["status"], "source": former["source"]}
    sources.add("companies_house", f"Companies House: {former['name']} (the brand's former company)",
                former["source"], _lines([
                    ("Note", f"The {brand} brand now belongs to {profile['name']}. {former['name']} ran it before "
                             f"and is now {former['status']}. Company profile facts (founded, funding, revenue, "
                             f"headcount) may describe that former business: use Companies House for the founded date."),
                    ("Former company", former["name"]), ("Status", former["status"]), ("Founded", former.get("founded")),
                ]))


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
        ("Last funding round", " ".join(card["last_round"].values()) if card.get("last_round") else None),
        ("Products", ", ".join(card.get("products") or []) or None),
        ("Investors", ", ".join(card.get("investors") or []) or None),
        ("Open roles listed", card.get("open_roles")),
        ("Similar companies", ", ".join(card.get("similar_companies") or []) or None),
        ("LinkedIn followers", card.get("linkedin_followers")),
        ("Description", card["description"]),
        ("Confirmed as the same company because", card.get("match_reason")),
    ]))


_HIRING_WORDS = re.compile(r"\b(hir\w*|recruit\w*|job\w*|vacanc\w*|open roles?|sales (?:roles?|executives?))\b",
                           re.IGNORECASE)


def _add_sales_hiring_signal(signals, more_about, brand, checks, most=3):
    """
    A job ad has no date, so the AI often skips it under the 'recent events only' rule, but an open sales role
    is exactly the 'hiring for sales' reason to reach out. (Live test: Juro's 'Strategic Account Executive'
    and Ometria's 'Enterprise Account Executive' ads were found but not used.) If the AI gave no hiring
    reason, add one from the sales job ads in 'Hiring now', linked to those ads. Returns True if added.
    """
    if any(s["type"] == "hiring_sales" for s in signals):
        return False
    roles = [r for r in (more_about.get("hiring") or {}).get("roles", []) if r["sales"]][:most]
    if not roles:
        return False
    names = ", ".join(r["title"] for r in roles)
    who = brand or "The company"
    signals.append({"type": "hiring_sales", "text": f"{who} has job ads for sales roles: {names}.",
                    "sources": [r["source"] for r in roles]})
    checks.append(f"Added a 'Hiring for sales' reason from job ads the AI skipped: {names}")
    return True


# Companies House industry codes for holding companies and head offices
HOLDING_SIC_CODES = {"64201", "64202", "64203", "64204", "64205", "64209", "70100"}
HOLDING_NAME_WORDS = {"holdings", "holding", "group", "holdco", "topco", "bidco", "midco"}
_FOUNDED_IN_TEXT = re.compile(r"\b(?:founded|established|launched|started)\s+(?:in\s+)?((?:18|19|20)\d\d)\b",
                              re.IGNORECASE)


def is_holding_company(profile):
    """A holding company or group parent: its industry code says so, it files group accounts, or its name does."""
    return bool(set(profile.get("sic_codes") or []) & HOLDING_SIC_CODES
                or (profile.get("last_accounts_type") or "") == "group"
                or set(ch._words(profile.get("name"))) & HOLDING_NAME_WORDS)


def _brand_founding_year(research):
    """
    (year, source id) for when the brand itself was founded, from the sources only: the company profile's
    'Founded', or else a 'founded in 2012' phrase in the profile, website or news text. (None, None) if not found.
    """
    sources, card = research["sources"], research.get("card") or {}
    year = str(card.get("founded") or "")[:4]
    if year.isdigit():
        return year, sources._by_url.get(card.get("source"))
    for s in sources.items:
        if s["kind"] in ("company_card", "website", "news"):
            found = _FOUNDED_IN_TEXT.search(s.get("content") or "")
            if found:
                return found.group(1), s["id"]
    return None, None


def registered_later(research):
    """
    When the registered company is newer than the brand (often a holding company set up later, e.g.
    ONFIDO HOLDINGS LIMITED in 2021, or RIGHTMOVE PLC in 2007 for a brand from 2000), 'Founded' should show
    the brand's year from the sources, with a note: 'Holding company registered 2021'.
    Returns {"brand_year", "source", "registered", "note"} or None when the dates agree.
    For a holding company with no brand year in the sources, Founded is left 'not found' (never the
    registration year passed off as the founding year), and the note still says when it was registered.
    """
    profile = research["profile"]
    registered = (profile.get("founded") or "")[:4]
    if not registered.isdigit():
        return None
    holding = is_holding_company(profile)
    brand_year, source = _brand_founding_year(research)
    if brand_year and int(brand_year) >= int(registered):
        return None                                   # registered when the brand began (or earlier): nothing to note
    if not brand_year and not holding:
        return None                                   # an ordinary company: its registration year is its founding year
    kind = "Holding company" if holding else "Company"
    return {"brand_year": brand_year, "source": source, "registered": registered,
            "note": f"{kind} registered {registered}"}


def _note_registration_year(research):
    """Add a plain note to the Companies House source, so the AI doesn't call the registration year the founding year."""
    later = registered_later(research)
    profile = research["profile"]
    item = research["sources"].get(research["sources"]._by_url.get(profile["source"]) or "")
    if not later or not item or "Registration note:" in item["content"]:
        return
    brand = f"the brand dates from {later['brand_year']}" if later["brand_year"] else "the brand's founding year isn't in this data"
    item["content"] += (f"\nRegistration note: {later['note']}; {brand}. This registration date is not the brand's "
                        "founding date.")


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
    changed_owner = bool(research.get("former_business"))   # the card may describe the former business
    later = None if changed_owner else registered_later(research)   # e.g. a holding company set up after the brand
    typed_site = research.get("website") if looks_like_website(research.get("typed") or "") else None
    website = typed_site or card.get("website") or research.get("website")   # the site the user typed wins
    return {
        "brand": fact(brand_from(research.get("typed") or p["name"]), None),
        "legal_name": fact(p["name"], ch_id),
        "status": fact(p["status"], ch_id),
        "website": fact(website, sources._by_url.get(website) if website != card.get("website") else card_id),
        "domain": tavily.domain_of(website) if website else None,
        "headquarters": fact(card.get("headquarters"), card_id),
        "industry": (fact(card.get("industry"), card_id)
                     or fact(ch.sic_description((p.get("sic_codes") or [None])[0]), ch_id)),
        "founded": (fact(later["brand_year"], later["source"]) if later
                    else (fact(founded_year, ch_id) or fact(card.get("founded"), card_id)) if changed_owner
                    else (fact(card.get("founded"), card_id) or fact(founded_year, ch_id))),
        "founded_note": later["note"] if later else None,
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

    _note_registration_year(research)
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
        return {"text": clean_ai_text(claim.text), "sources": good}

    # Overview: every sentence needs a real source; 3–4 sentences, and 5–7 for "Read more"
    overview = [c for c in (check_claim(x) for x in draft.overview) if c][:settings.OVERVIEW_SENTENCES]
    overview_more = [c for c in (check_claim(x) for x in draft.overview_more) if c]
    # "Read more" only adds sentences: drop any that repeat (or closely reword) one already shown
    shown = list(overview)
    extra = []
    for c in overview_more:
        if any(_same_sentence(c["text"], s["text"]) for s in shown):
            checks.append(f"Removed a 'Read more' sentence that repeated the overview: \"{c['text'][:60]}\"")
            continue
        extra.append(c)
        shown.append(c)
    overview_more = extra[:settings.OVERVIEW_MORE_SENTENCES - len(overview)]
    what_they_do = overview[0] if overview else None   # the one-line summary, used by the email and older code
    snapshot = [c for c in (check_claim(x) for x in draft.snapshot) if c]
    fit_reasons = [c for c in (check_claim(x) for x in draft.fit_reasons) if c]

    # Signals: known type and real sources only
    signals = []
    for s in draft.signals:
        good = [i for i in s.source_ids if i in valid]
        dates = [sources.get(i)["date"] for i in good if sources.get(i).get("date")]
        if s.signal_type == "funding" and _is_old_round(s.text, research["card"], today):
            checks.append(f"Removed a funding signal: the company profile dates that round "
                          f"{research['card']['last_round']['date']}, older than {settings.EVENT_WINDOW_MONTHS} months")
            continue
        if s.signal_type in ALLOWED_SIGNAL_TYPES and good and not (dates and too_old(max(dates), today)):
            signals.append({"type": s.signal_type, "text": clean_ai_text(s.text), "sources": good})
        elif dates and too_old(max(dates), today):
            checks.append(f"Removed a reason to reach out older than {settings.EVENT_WINDOW_MONTHS} months: \"{s.text[:60]}\"")
        else:
            checks.append(f"Removed an unsupported signal: \"{s.text[:60]}\"")

    news = _pick_news(draft.news, signals, sources, today or date.today(), checks)

    # Score: keep it 1–10, then apply hard ICP rules in code
    score = max(1, min(10, draft.fit_score))
    score, fit_reasons = _apply_size_rule(score, fit_reasons, research, checks)


    profile_facts = company_profile(research)
    more_about = _more_about(research, sources)
    hiring_added = _add_sales_hiring_signal(signals, more_about, (profile_facts.get("brand") or {}).get("value"), checks)
    used = set(n["source"] for n in news)
    used.update(_more_about_ids(more_about))
    for group in overview + overview_more + snapshot + fit_reasons + signals:
        used.update(group["sources"])
    used.update(f["source"] for f in profile_facts.values() if isinstance(f, dict) and f.get("source"))

    not_found = [clean_ai_text(x) for x in draft.not_found]
    if hiring_added:   # don't say "no sales hiring found" next to the job ads we just used
        not_found = [x for x in not_found if not _HIRING_WORDS.search(x)]
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
        "overview": overview,
        "overview_more": overview_more,
        "about_link": _about_link(sources, research),
        "more_about": more_about,
        "snapshot": snapshot,
        "news": news,
        "signals": signals,
        "fit_score": score,
        "fit_reasons": fit_reasons,
        "suggested_role": draft.suggested_role,
        "role_reason": clean_ai_text(draft.role_reason),
        "not_found": not_found,
        "ownership_note": ownership_note(research),
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
    for key in ("overview", "overview_more", "snapshot", "fit_reasons", "signals"):
        out[key] = [claim(c) for c in r.get(key, [])]
    out["news"] = [dict(n, source=new_id.get(n["source"], n["source"])) for n in r.get("news", [])]
    out["profile"] = {k: (dict(f, source=new_id.get(f["source"], f["source"])) if isinstance(f, dict) and f.get("source") else f)
                      for k, f in (r.get("profile") or {}).items()}
    out["all_sources"] = sorted((dict(s, id=new_id[s["id"]]) for s in r["all_sources"]),
                                key=lambda s: int(s["id"][1:]))
    out["sources"] = out["all_sources"][:len(shown)]
    out["checks"] = [retag(c) for c in r.get("checks", [])]
    if r.get("more_about"):
        m = r["more_about"]
        one = lambda i: new_id.get(i, i) if i else i   # noqa: E731
        out["more_about"] = {
            "directors": dict(m["directors"], source=one(m["directors"]["source"])),
            "owners": dict(m["owners"], source=one(m["owners"]["source"])),
            "competitors": dict(m["competitors"], source=one(m["competitors"]["source"])),
            "hiring": dict(m["hiring"], open_roles_source=one(m["hiring"]["open_roles_source"]),
                           roles=[dict(x, source=one(x["source"])) for x in m["hiring"]["roles"]]),
        }
        if m.get("filings"):
            out["more_about"]["filings"] = dict(m["filings"], source=one(m["filings"]["source"]),
                                                profile_source=one(m["filings"]["profile_source"]))
    if r.get("saved_email"):
        out["saved_email"] = email(r["saved_email"])
    return out


def _more_about(research, sources):
    """
    The four 'More about' cards, built by code from sources only (no AI):
    Key people and Owners from Companies House; Competitors and Hiring now from the company profile
    and job adverts. Anything not found is left empty, and the page says 'Not found in public sources'.
    """
    card = research.get("card") or {}
    card_id = sources._by_url.get(card.get("source"))
    brand = brand_from(research.get("typed") or research["profile"]["name"])

    directors = research.get("directors") or {}
    people, people_id = directors.get("people") or [], None
    if people:
        people_id = sources.add("companies_house", "Companies House: directors", directors["source"],
                                _lines([(p["name"], f"{p['role']}, appointed {p['appointed_on']}") for p in people]))
        sources.get(people_id)["title"] = "Companies House: directors"   # also the new-director signal's page

    owners_data = research.get("owners") or {}
    owners, owners_id = owners_data.get("owners") or [], None
    if owners:
        owners_id = sources.add("companies_house", "Companies House: people with significant control", owners_data["source"],
                                _lines([(o["name"], "; ".join(o["control"])) for o in owners]))

    similar = (card.get("similar_companies") or [])[:settings.COMPETITORS_SHOWN]
    roles, seen = [], set()
    for s in sources.items:
        if s["kind"] == "job" or (s["kind"] == "signal" and s["title"].startswith("[hiring_sales]")):
            role = tavily.role_from_title(s["title"], brand)
            if role and role.lower() not in seen:
                seen.add(role.lower())
                roles.append({"title": role, "sales": tavily.is_sales_role(role), "source": s["id"]})
    roles.sort(key=lambda r: not r["sales"])   # sales roles first

    filings = research.get("filings") or {}
    filings_id = sources._by_url.get(filings.get("source"))
    profile = research["profile"]
    return {
        "filings": {"filings": filings.get("filings") or [], "total": filings.get("total", 0), "source": filings_id,
                    "last_accounts": profile.get("last_accounts_made_up_to"),
                    "next_accounts_due": profile.get("next_accounts_due"),
                    "accounts_overdue": profile.get("accounts_overdue", False),
                    "profile_source": sources._by_url.get(profile["source"])},
        "directors": {"people": people, "total": directors.get("total", 0), "source": people_id},
        "owners": {"owners": owners, "source": owners_id},
        "competitors": {"names": similar, "source": card_id if similar else None},
        "hiring": {"open_roles": card.get("open_roles"), "open_roles_source": card_id if card.get("open_roles") else None,
                   "roles": roles[:settings.HIRING_EXAMPLES_SHOWN]},
    }


def _more_about_ids(m):
    """Every source ID the 'More about' cards cite."""
    ids = [m["directors"]["source"], m["owners"]["source"], m["competitors"]["source"],
           m["hiring"]["open_roles_source"]] + [r["source"] for r in m["hiring"]["roles"]]
    if m.get("filings"):
        ids += [m["filings"]["source"], m["filings"]["profile_source"]]
    return {i for i in ids if i}


# A whole number of 4+ digits that isn't part of a date, decimal, code, ID or longer number
_BIG_NUMBER = re.compile(r"(?<![\w.,/:#+-])(?<!\d,)([1-9]\d{3,8})(?![\w/-]|[.,]\d)")   # 4–9 digits: longer is a phone number or code


_INLINE_TAGS = re.compile(r"\s*[\[(]\s*S\d+(?:\s*[,;&/]\s*S\d+)*\s*[\])]")


def strip_source_tags(text):
    """
    Remove source tags the AI typed inside its text, e.g. 'billing [S2, S3].' → 'billing.'
    Real problem: those typed tags kept the numbers from before renumbering, so they could be wrong.
    The page shows the right tags from source_ids instead.
    """
    return _INLINE_TAGS.sub("", text or "").strip()


def spaced_dashes(text):
    """British style: a spaced en dash between words ('[Your company] – I saw'), never a tight em dash."""
    return re.sub(r"\s*—\s*|(?<=\S) – (?=\S)|\s+–\s*|\s*–\s+", " – ", text) if text else text


def _tidy(text):
    """Typed source tags removed, numbers given commas, dashes spaced (the outreach checks fix and list spelling
    themselves)."""
    return spaced_dashes(with_commas(strip_source_tags(text)))


def clean_ai_text(text):
    """Every piece of AI-written text: typed source tags removed, numbers given commas, British spelling."""
    return to_british(with_commas(strip_source_tags(text)))[0]


_CODE_BEFORE = re.compile(r"\b(?:sic|codes?|numbers?|no\.?|ref(?:erence)?|id|account|postcode|tel(?:ephone)?|phone)\b"
                          r"[\s:#.]*(?:is\s+)?(?:\d+\s*(?:and|,)\s*)*$", re.IGNORECASE)


def with_commas(text):
    """'2604 employees' → '2,604 employees'. Years (1900–2099), dates, decimals, IDs and phone numbers are left alone."""
    def add(m):
        n = int(m.group(1))
        if len(m.group(1)) == 4 and 1900 <= n <= 2099:
            return m.group(1)          # most likely a year
        before = m.string[max(0, m.start() - 25):m.start()]
        previous = re.search(r"(\d+)\s*,\s*$", before)
        listed_code = not previous or len(previous.group(1)) == len(m.group(1))   # 'codes 62012, 70100' (same length)
        if listed_code and _CODE_BEFORE.search(before):
            return m.group(1)          # a code or reference, e.g. 'SIC code 56290', 'company number 8027386'
        return f"{n:,}"
    return _BIG_NUMBER.sub(add, text or "")


def _same_sentence(a, b, overlap=0.8):
    """True if two sentences say the same thing: most of the shorter one's words appear in the other."""
    words = lambda t: set(re.findall(r"[a-z0-9]+", t.lower())) - {"the", "a", "an", "and", "of", "to", "in", "for", "its", "is"}  # noqa: E731
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= overlap


def _about_link(sources, research):
    """The company's About page if a fetched source is one on its own website; otherwise its website. Never guessed."""
    website = research.get("website") or (research.get("card") or {}).get("website")
    domain = tavily.domain_of(website) if website else None
    if domain:
        for s in sources.items:
            if tavily.domain_of(s["url"]) == domain and re.search(r"/about(-us)?/?($|[?#])", s["url"], re.IGNORECASE):
                return {"url": s["url"], "label": "About page"}
    if research.get("website"):
        return {"url": research["website"], "label": "Visit website"}
    if website:
        return {"url": website if website.startswith("http") else f"https://{website}", "label": "Visit website"}
    return None


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
            ai_reasons.setdefault(pick.source_id, clean_ai_text(pick.why_it_matters))
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
    """'best', 'good' or 'weak' for a number of employees (see the bands in whynow/settings.py)."""
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
    ("specializ", "specialis", _IZE), ("modeliz", "modelis", _IZE), ("monetiz", "monetis", _IZE),
    ("automatiz", "automatis", _IZE), ("emphasiz", "emphasis", _IZE),
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


def trim_email(body, max_words):
    """
    Remove whole sentences from the middle until the email fits, keeping the first sentence
    (the trigger) and the closing (the question and sign-off). Paragraph breaks are kept.
    """
    parts = re.split(r"(?<=[.!?])(\s+)", body)          # sentences, with the spaces/newlines after each
    sentences = [[parts[i], parts[i + 1] if i + 1 < len(parts) else ""] for i in range(0, len(parts), 2)]
    if len(sentences) <= 3:
        return body
    keep_end = 2 if "?" in sentences[-2][0] or "[Your name]" in sentences[-1][0] else 1
    middle = list(range(1, len(sentences) - keep_end))
    while middle and word_count("".join(a + b for a, b in sentences)) > max_words:
        i = middle.pop()                                   # drop middle sentences from the end backwards
        sentences[i] = ["", ""]
    return "".join(a + b for a, b in sentences).strip()


def _check_email(draft, valid, ai, checks, timings=None):
    """Make sure the email is short enough and opens with a trigger we can verify."""
    body = _tidy(draft.body.strip())
    words = word_count(body)
    if words > settings.EMAIL_MAX_WORDS:
        # No second AI request (it costs money): code removes whole middle sentences instead
        trimmed = trim_email(body, settings.EMAIL_MAX_WORDS)
        if word_count(trimmed) < words:
            checks.append(f"Email trimmed by code from {words} to {word_count(trimmed)} words "
                          "(kept the opening trigger, the question and the sign-off)")
            body, words = trimmed, word_count(trimmed)
    # British spelling, enforced by code as well as asked for in the prompt
    body, fixed_body = to_british(body)
    subject, fixed_subject = to_british(_tidy(draft.subject.strip()))
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


def _check_linkedin_note(note, checks):
    """British spelling, and no more than 300 characters (cut at the last full sentence that fits)."""
    note, fixed = to_british(_tidy((note or "").strip()))
    if fixed:
        checks.append("British spelling (LinkedIn note): " + ", ".join(fixed))
    limit = settings.LINKEDIN_NOTE_MAX_CHARS
    if len(note) <= limit:
        return note
    cut = max(note.rfind(end, 0, limit) for end in (". ", "? ", "! "))
    short = note[:cut + 1] if cut > 0 else note[:limit - 1].rsplit(" ", 1)[0] + "…"
    checks.append(f"LinkedIn note cut from {len(note)} to {len(short)} characters (LinkedIn allows {limit})")
    return short


def _check_call_opener(opener, checks, company=None):
    """British spelling, the caller is never 'from' the company being called, and exactly 2 lines at most."""
    opener, fixed = to_british(_tidy((opener or "").strip()))
    if fixed:
        checks.append("British spelling (call opener): " + ", ".join(fixed))
    # Real problem: "[Your name] here from Paddle" — the caller doesn't work at the company they're calling
    if company:
        wrong = re.compile(rf"(\[Your name\]\s+here,?\s+(?:from|at|with)\s+){re.escape(company)}\b", re.IGNORECASE)
        if wrong.search(opener):
            opener = wrong.sub(r"\1[Your company]", opener)
            checks.append(f"Call opener said the caller was from {company}; changed to [Your company]")
    lines = [line.strip() for line in opener.splitlines() if line.strip()]
    if len(lines) == 1:   # two sentences on one line: put the question on its own line
        parts = re.split(r"(?<=[.!])\s+(?=[A-Z])", lines[0], maxsplit=1)
        lines = [p.strip() for p in parts if p.strip()]
    if len(lines) > settings.CALL_OPENER_MAX_LINES:
        checks.append(f"Call opener cut from {len(lines)} to {settings.CALL_OPENER_MAX_LINES} lines")
        lines = lines[:settings.CALL_OPENER_MAX_LINES]
    return "\n".join(lines)


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
    for c in r.get("overview") or ([r["what_they_do"]] if r.get("what_they_do") else []):
        lines.append(cited(c["text"], c["sources"]))
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
    or Founder / CEO). Code then checks it: under 120 words (trimmed by code if not, no second AI request), British spelling,
    and an opening trigger that cites a source the report actually uses.
    """
    persona = persona if persona in settings.EMAIL_PERSONAS else settings.DEFAULT_PERSONA
    if r["not_worth_contacting"]:
        return None
    checks, timings = [], {}
    started = time.monotonic()
    name = r.get("profile", {}).get("brand", {}).get("value") or r["company"]["name"]
    draft = ai(build_email_prompt(name, r["offer"], email_facts(r, persona), (today or date.today()).isoformat(),
                                  persona), OutreachDraft)
    timings["Email"] = round(time.monotonic() - started, 1)
    ai_model = _model_used(ai)
    valid = {s["id"] for s in r["sources"]}   # only sources the report cites
    email = _check_email(draft, valid, ai, checks, timings)
    email["linkedin_note"] = _check_linkedin_note(draft.linkedin_note, checks)
    email["call_opener"] = _check_call_opener(draft.call_opener, checks, name)
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
        "what_they_do": None, "overview": [], "overview_more": [], "about_link": None,
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
