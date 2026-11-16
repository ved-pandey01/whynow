"""
report.py — puts everything together:

  1. research_company()  → fetch Companies House + Tavily data, give every source an ID (S1, S2…)
  2. build_report()      → ask the AI to write the report, then CHECK its work in code:
       - drop any claim whose source IDs we didn't actually fetch
       - only allow news picks that are real news sources
       - apply hard ICP rules (closed company, size limits) in code, not by the AI
       - make sure the email is under the word limit
  The result is a plain dictionary the web page (Step 6) can show.

Try it (about 9 Tavily credits + 1–2 Gemini requests):
    .venv/bin/python -m report "Cognism"
"""

import sys
import time
from datetime import date

import settings
from ai.prompts import build_report_prompt, build_shorten_prompt
from ai.provider import AIError, generate_structured
from models import AIReport, Claim
from sources import news_tavily as tavily
from sources import uk_companies_house as ch

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


def research_company(company_number, typed):
    """Fetch everything we know about one company. Never crashes on a Tavily problem."""
    sources = SourceList()
    warnings = []

    profile = ch.get_company_profile(company_number)
    sic = ", ".join(f"{c} ({settings.SOFTWARE_SIC_CODES.get(c, 'see Companies House')})"
                    for c in profile["sic_codes"]) or None
    sources.add("companies_house", f"Companies House: {profile['name']}", profile["source"], _lines([
        ("Legal name", profile["name"]), ("Company number", profile["company_number"]),
        ("Status", profile["status"]), ("Closed on", profile["closed_on"]),
        ("Founded", profile["founded"]), ("Registered address", profile["address"]),
        ("Industry codes (SIC)", sic), ("Last accounts type", profile["last_accounts_type"]),
    ]))

    research = {"profile": profile, "card": None, "website": None,
                "sources": sources, "warnings": warnings, "typed": typed}
    if profile["not_worth_contacting"]:
        return research  # no point spending credits on a closed company

    directors = ch.get_recent_directors(company_number)
    if directors:
        sources.add("signal", "Companies House: recently appointed directors", directors[0]["source"],
                    "\n".join(f"{d['name']} ({d['role']}, {d['occupation'] or 'occupation not listed'}), "
                              f"appointed {d['appointed_on']}" for d in directors))

    brand = brand_from(typed)
    website = typed if looks_like_website(typed) else None
    try:
        card = tavily.get_company_card(brand, legal_name=profile["name"], website=website)
        if card:
            research["card"] = card
            website = website or card["website"]
            sources.add("company_card", f"Company profile: {brand}", card["source"], _lines([
                ("Website", card["website"]), ("Headquarters", card["headquarters"]),
                ("Industry", card["industry"]),
                ("Business areas", ", ".join(card["business_areas"] or []) or None),
                ("Business model", card["business_model"]), ("SaaS", card["saas"]),
                ("Founded", card["founded"]), ("Employees (headcount)", card["headcount"]),
                ("Headcount change", card["headcount_yearly_change"]),
                ("Funding rounds", card["funding_rounds"]),
                ("Total raised", ", ".join(card["total_raised"] or []) or None),
                ("Description", card["description"]),
            ]))

        topic = tavily.topic_hint(card)
        for item in tavily.get_recent_news(brand, website, topic):
            sources.add("news", item["title"], item["source"], item["snippet"], item["date"])
        for signal_type, items in tavily.get_signals(brand, website, topic).items():
            for item in items:
                sources.add("signal", f"[{signal_type}] {item['title']}", item["source"],
                            item["snippet"], item["date"])

        web = tavily.get_website_text(website)
        if web:
            research["website"] = web["source"]
            sources.add("website", f"{brand} website", web["source"], web["text"])
    except tavily.NewsError as e:
        warnings.append(f"News and signals unavailable: {e}")

    return research


# ---------------------------------------------------------------------------
# 2. Build and check the report
# ---------------------------------------------------------------------------
def build_report(research, offer=None, ai=generate_structured, today=None):
    """Ask the AI for the report, then check every part of it in code."""
    started = time.time()
    offer = (offer or "").strip() or settings.DEFAULT_OFFER
    sources = research["sources"]
    profile = research["profile"]

    if profile["not_worth_contacting"]:
        return _closed_company_report(research)

    prompt = build_report_prompt(profile["name"], offer, sources.as_prompt_text(),
                                 (today or date.today()).isoformat())
    draft = ai(prompt, AIReport)

    checks = []      # notes on what code changed, shown for transparency
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

    # News: only real news sources, max 3, no repeats
    news, news_ids = [], sources.ids("news")
    for pick in draft.news:
        if pick.source_id in news_ids and pick.source_id not in [n["source"] for n in news]:
            news.append({"source": pick.source_id, "why_it_matters": pick.why_it_matters})
        elif pick.source_id not in news_ids:
            checks.append(f"Removed a news pick that wasn't a fetched news item ({pick.source_id})")
    news = news[: settings.NEWS_ITEMS]

    # Signals: known type and real sources only
    signals = []
    for s in draft.signals:
        good = [i for i in s.source_ids if i in valid]
        if s.signal_type in ALLOWED_SIGNAL_TYPES and good:
            signals.append({"type": s.signal_type, "text": s.text, "sources": good})
        else:
            checks.append(f"Removed an unsupported signal: \"{s.text[:60]}\"")

    # Score: keep it 1–10, then apply hard ICP rules in code
    score = max(1, min(10, draft.fit_score))
    score, fit_reasons = _apply_size_rule(score, fit_reasons, research, checks)

    email = _check_email(draft, valid, ai, checks)
    if score <= LOW_FIT_SCORE:
        email["warnings"].insert(0, "Low fit: we don't recommend contacting this company.")

    used = set(n["source"] for n in news)
    for group in ([what_they_do] if what_they_do else []) + snapshot + fit_reasons + signals:
        used.update(group["sources"])
    used.update(email["trigger_sources"])

    not_found = list(draft.not_found)
    if not research["card"]:
        not_found.append("Company profile (headcount, funding) not found or couldn't be confirmed")
    if not research["website"]:
        not_found.append("Company website couldn't be read")

    return {
        "company": {"name": profile["name"], "number": profile["company_number"],
                    "status": profile["status"], "source": profile["source"]},
        "not_worth_contacting": False,
        "offer": offer,
        "what_they_do": what_they_do,
        "snapshot": snapshot,
        "news": news,
        "signals": signals,
        "fit_score": score,
        "fit_reasons": fit_reasons,
        "suggested_role": draft.suggested_role,
        "role_reason": draft.role_reason,
        "email": email,
        "not_found": not_found,
        "sources": [s for s in sources.items if s["id"] in used],
        "all_sources": sources.items,
        "checks": checks,
        "warnings": research["warnings"],
        "seconds": round(time.time() - started, 1),
    }


PLC_MAX_SCORE = 4  # a PLC with no headcount data can't score above this
LOW_FIT_SCORE = 3  # at or below this, the email gets a "don't recommend contacting" warning


def _apply_size_rule(score, reasons, research, checks):
    """
    Too small or too big for the ICP → score can't be above 3. Decided by code.
    If we have no headcount but Companies House says it's a PLC (public limited company),
    cap at 4: PLCs are usually far bigger than 50–500-staff scale-ups. A rule of thumb, so we say so.
    """
    card = research["card"]
    headcount = card and card.get("headcount")
    if not headcount:
        if research["profile"].get("company_type") == "plc" and score > PLC_MAX_SCORE:
            ch_id = research["sources"].ids("companies_house").copy().pop()
            checks.append(f"Score capped at {PLC_MAX_SCORE}: a PLC with no headcount data "
                          "is usually larger than the ICP")
            score = PLC_MAX_SCORE
            reasons = [{"text": "Registered as a public limited company (PLC), which is usually "
                                "much larger than the 50–500-employee target. Headcount not found.",
                        "sources": [ch_id]}] + reasons
        return score, reasons
    if headcount < settings.TOO_SMALL_BELOW or headcount > settings.TOO_BIG_ABOVE:
        card_id = research["sources"]._by_url[card["source"]]
        if score > 3:
            checks.append(f"Score capped at 3: {headcount} employees is outside the ICP size range")
            score = 3
        reasons = [{"text": f"About {headcount:,} employees, outside the ideal "
                            f"{settings.ICP_MIN_EMPLOYEES}–{settings.ICP_MAX_EMPLOYEES} range.",
                    "sources": [card_id]}] + reasons
    return score, reasons


def word_count(text):
    return len((text or "").split())


def _check_email(draft, valid, ai, checks):
    """Make sure the email is short enough and opens with a trigger we can verify."""
    body = draft.email_body.strip()
    words = word_count(body)
    if words > settings.EMAIL_MAX_WORDS:
        try:
            shorter = ai(build_shorten_prompt(body, words), Claim).text.strip()
            if word_count(shorter) <= settings.EMAIL_MAX_WORDS:
                checks.append(f"Email shortened from {words} to {word_count(shorter)} words")
                body, words = shorter, word_count(shorter)
        except AIError:
            pass
    warnings = []
    if words > settings.EMAIL_MAX_WORDS:
        warnings.append(f"Email is {words} words, over the {settings.EMAIL_MAX_WORDS}-word limit. Trim before sending.")
    triggers = [i for i in draft.email_trigger_source_ids if i in valid]
    if not triggers:
        warnings.append("The email's opening trigger couldn't be matched to a source. Check it before sending.")
    return {"subject": draft.email_subject.strip(), "body": body, "word_count": words,
            "trigger_sources": triggers, "warnings": warnings}


def _closed_company_report(research):
    """Closed, insolvent or dormant: flagged by code with no AI, so it can't be got wrong."""
    p = research["profile"]
    source_id = research["sources"].ids("companies_house").pop()
    if p["last_accounts_type"] == settings.DORMANT_ACCOUNTS_TYPE and p["status"] == "active":
        reason = "Companies House shows the company's last accounts as dormant (not trading)."
    else:
        closed = f" (closed on {p['closed_on']})" if p["closed_on"] else ""
        reason = f"Companies House lists the company as {p['status']}{closed}."
    return {
        "company": {"name": p["name"], "number": p["company_number"],
                    "status": p["status"], "source": p["source"]},
        "not_worth_contacting": True,
        "what_they_do": None,
        "snapshot": [{"text": f"Founded {p['founded']}." if p["founded"] else "Founded date not found.",
                      "sources": [source_id]}],
        "news": [], "signals": [],
        "fit_score": 1,
        "fit_reasons": [{"text": reason + " Not worth contacting.", "sources": [source_id]}],
        "suggested_role": None, "role_reason": None, "email": None,
        "not_found": [], "sources": research["sources"].items, "all_sources": research["sources"].items,
        "checks": ["Flagged by code from Companies House status. No AI used."],
        "warnings": research["warnings"], "seconds": 0.0,
    }


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
    if r["email"]:
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


if __name__ == "__main__":
    typed = sys.argv[1] if len(sys.argv) > 1 else "Cognism"
    started = time.time()
    try:
        matches = ch.search_companies(typed)
        if not matches or not matches[0]["close_match"]:
            print(f'No close match found for "{typed}". Check the spelling or try the website.')
            sys.exit(0)
        research = research_company(matches[0]["company_number"], typed)
        result = build_report(research)
        _print_report(result, {s["id"]: s for s in result["all_sources"]})
        print(f"\nTotal time: {time.time() - started:.1f} seconds")
    except (ch.CompaniesHouseError, AIError) as e:
        print(f"Sorry: {e}")
