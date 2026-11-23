"""
Save one real report (and its outreach) as the "See a sample report" example on the landing page.

Fresh research (about 11 Tavily credits + 2 Gemini requests):
    .venv/bin/python -m scripts.save_sample
The research is kept in .cache/ (git-ignored), so running it again only redoes the AI part.

Rebuild from the saved sample, with NO Tavily calls at all (2 Gemini requests):
    .venv/bin/python -m scripts.save_sample --from-saved
The saved sample already holds every source's full text; Companies House is re-read (free).

Refresh only the email, LinkedIn note and call opener (1 Gemini request, no Tavily calls):
    .venv/bin/python -m scripts.save_sample --outreach-only

No retries here: the app's AI code already tries each Gemini model once, then the backups.
Only the report is saved: no API keys are ever written to the file.
"""

import json
import io
import pickle
import re
from datetime import date
from pathlib import Path

from whynow import report
from whynow import settings
from whynow.sources import news_tavily as tavily
from whynow.sources import uk_companies_house as ch

COMPANY = "paddle.com"
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / settings.SAMPLE_REPORT_FILE
RESEARCH_CACHE = ROOT / ".cache" / "sample_research.pkl"   # kept, so a rerun costs no Tavily credits


class _OldNamesUnpickler(pickle.Unpickler):
    """Research saved before the code moved into whynow/ refers to 'report'; read it as 'whynow.report'."""
    def find_class(self, module, name):
        if module in ("report", "settings", "models"):
            module = f"whynow.{module}"
        return super().find_class(module, name)


def load_or_run_research():
    if RESEARCH_CACHE.exists():
        print("Using the saved research (no Tavily credits used).")
        return _OldNamesUnpickler(io.BytesIO(RESEARCH_CACHE.read_bytes())).load()
    match = ch.search_companies(COMPANY)[0]
    print(f"Researching {match['name']}…")
    research = report.research_company(match["company_number"], COMPANY)   # the credits are spent here
    RESEARCH_CACHE.parent.mkdir(exist_ok=True)
    RESEARCH_CACHE.write_bytes(pickle.dumps(research))
    return research


# Labels used in the company-profile source text (see report._add_card_source) → card fields
_CARD_FIELDS = {
    "Website": "website", "Headquarters": "headquarters", "Industry": "industry", "Business model": "business_model",
    "SaaS": "saas", "Founded": "founded", "Headcount change": "headcount_yearly_change", "Annual revenue": "annual_revenue",
    "Description": "description", "Confirmed as the same company because": "match_reason",
}
_CARD_NUMBERS = {"Employees (headcount)": "headcount", "Funding rounds": "funding_rounds",
                 "Open roles listed": "open_roles", "LinkedIn followers": "linkedin_followers"}
_CARD_LISTS = {"Business areas": "business_areas", "Total raised": "total_raised", "Products": "products",
               "Investors": "investors", "Similar companies": "similar_companies"}


def card_from_source(source):
    """The company-profile facts, read back from the profile source's own text (nothing new)."""
    card = {name: None for name in list(_CARD_FIELDS.values()) + list(_CARD_NUMBERS.values()) + list(_CARD_LISTS.values())}
    card["last_round"] = None
    for line in source["content"].splitlines():
        label, _, value = line.partition(": ")
        if label in _CARD_FIELDS:
            card[_CARD_FIELDS[label]] = value
        elif label in _CARD_NUMBERS and value.replace(",", "").isdigit():
            card[_CARD_NUMBERS[label]] = int(value.replace(",", ""))
        elif label in _CARD_LISTS:
            card[_CARD_LISTS[label]] = [x.strip() for x in value.split(",") if x.strip()]
        elif label == "Last funding round":
            m = re.match(r"(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(.+)$", value)
            card["last_round"] = {"type": m.group(1), "date": m.group(2), "amount": m.group(3)} if m else None
    card["source"] = source["url"]
    return card


def research_from_saved(saved):
    """
    Rebuild the research from a saved report: every source with its full text, in the same order,
    plus Companies House (free). Makes no Tavily calls.
    """
    sources = report.SourceList()
    for s in sorted(saved["all_sources"], key=lambda s: int(s["id"][1:])):
        sources.add(s["kind"], s["title"], s["url"], s["content"], s.get("date"))
    number = saved["company"]["number"]
    card_source = next((s for s in saved["all_sources"] if s["kind"] == "company_card"), None)
    website_source = next((s for s in saved["all_sources"] if s["kind"] == "website"), None)
    return {
        "profile": ch.get_company_profile(number),
        "card": card_from_source(card_source) if card_source else None,
        "website": website_source["url"] if website_source else None,
        "sources": sources, "warnings": [], "typed": COMPANY, "timings": {}, "left_out_old": 0,
        "directors": ch.get_current_directors(number),
        "owners": ch.get_owners(number),
    }


def save(r):
    SAMPLE.write_text(json.dumps(r, indent=2, ensure_ascii=False))
    print(f"Saved to {SAMPLE} (score {r['fit_score']}/10, report by {r['ai_model']['name']}, "
          f"outreach by {r['saved_email']['ai_model']['name']}, email {r['saved_email']['word_count']} words)")


def build_and_save(research):
    r = report.build_report(research)                 # 1 Gemini request
    r["saved_email"] = report.write_email(r)          # 1 Gemini request (email, LinkedIn note, call opener)
    r["saved_on"] = date.today().strftime("%-d %b %Y")
    save(r)


def main():
    build_and_save(load_or_run_research())


def from_saved():
    """Remake the sample's AI parts from the saved sample, with Tavily switched off so no credits can be spent."""
    def no_tavily():
        raise RuntimeError("Tavily is switched off for --from-saved.")
    tavily._client = no_tavily
    build_and_save(research_from_saved(json.loads(SAMPLE.read_text())))


def refresh_outreach():
    """Rewrite only the sample's email, LinkedIn note and call opener (1 Gemini request, no Tavily calls)."""
    r = json.loads(SAMPLE.read_text())
    r["saved_email"] = report.write_email(r)
    save(r)


if __name__ == "__main__":
    import sys
    if "--outreach-only" in sys.argv:
        refresh_outreach()
    elif "--from-saved" in sys.argv:
        from_saved()
    else:
        main()
