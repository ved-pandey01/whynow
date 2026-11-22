"""
Search check: 20 real searches against Companies House, showing where each one fails.

    .venv/bin/python -m scripts.search_check

FREE: it uses only Companies House (free). Tavily and Gemini are switched off and replaced with
stand-ins, so no credits are used. For each search it records:
  lookup   - did Companies House return the expected company for the text WhyNow sends?
  matching - did WhyNow keep it and put it in the list the user sees?
  filters  - is it hidden by 'Active companies only' (the default)?
  picker   - where it appears in the list (1 = top)
  report   - can a report be built for it from Companies House data alone (no news found)?
"""

import json
import sys

import report
import settings
from models import AIReport, Claim
from sources import news_tavily as tavily
from sources import uk_companies_house as ch

SEARCHES = [  # (typed, kind, acceptable company numbers; empty = should find nothing)
    ("Tesco", "big brand", {"00445790"}),
    ("Greggs", "big brand", {"00502851"}),
    ("Rolls-Royce", "big brand, punctuation", {"07524813", "01003142"}),
    ("BT", "big brand, very short", {"04190816"}),
    ("Paddle", "brand differs from legal name", {"08172165"}),
    ("greggs.co.uk", "website", {"00502851"}),
    ("Adel Global Ltd", "small Ltd, sole director", {"17100522"}),
    ("Adelgeo Ltd", "small Ltd, sole director", {"13829163"}),
    ("Adel Gardens Management Company Ltd", "small Ltd, generic words", {"14150540"}),
    ("Adelfi Studio Limited", "small Ltd, sole director", {"16233031"}),
    ("ADELH LIMITED", "small Ltd", {"11620530"}),
    ("Adel Hassan Foods Limited", "small Ltd, sole director", {"09140685"}),
    ("Adel Grace Ltd", "dissolved", {"15211311"}),
    ("Adelfoods Limited", "dissolved", {"15306173"}),
    ("Carillion plc", "in liquidation", {"03782379"}),
    ("Gregs", "misspelling", {"00502851"}),
    ("Tescco", "misspelling", {"00445790"}),
    ("Adelgeo Limted", "misspelt suffix", {"13829163"}),
    ("17100522", "company number", {"17100522"}),
    ("Adel Force Limited", "not on the register", set()),
]


def switch_off_paid_services():
    """No Tavily or Gemini: every search finds nothing, as for a small company with no news."""
    tavily._search = lambda query, **options: []
    tavily._read_pages = lambda urls: []


def fake_ai(prompt, shape):
    """A stand-in for Gemini that cites the Companies House source (S1), like a careful real answer."""
    return AIReport(overview=[Claim(text="A UK registered company.", source_ids=["S1"])], overview_more=[], snapshot=[],
                    news=[], signals=[], fit_score=3, fit_reasons=[Claim(text="Little public information.", source_ids=["S1"])],
                    suggested_role="Founder / CEO", role_reason="Small company.", not_found=["No recent news"])


def raw_lookup_numbers(typed):
    """The company numbers Companies House returns for the exact text(s) WhyNow searches."""
    numbers = set()
    for term in ch.search_terms(typed):
        data = ch._call_api("/search/companies", {"q": term, "items_per_page": 100}) or {}
        numbers |= {i.get("company_number") for i in data.get("items", [])}
    return numbers


def check(typed, kind, want):
    """Follows the picker: Active companies by default, with 'Include dissolved companies' as the toggle."""
    row = {"typed": typed, "kind": kind}
    try:
        default = ch.find_companies(typed)                          # what the picker shows by default
        with_closed = ch.find_companies(typed, include_dissolved=True)
    except ch.CompaniesHouseError as e:
        row.update(result="error", where="lookup", note=str(e))
        return row
    shown = [m["company_number"] for m in default["matches"]]
    if not want:
        close = [m for m in default["matches"] if m["close_match"]]
        row.update(result="ok" if not close else "shows a wrong match", where="", picker=len(shown),
                   note="no close match; " + ("nearest names shown with a warning" if shown else "empty-state tips"))
        return row
    hit = next((n for n in shown if n in want), None)
    closed_hit = next((m["company_number"] for m in default["hidden_closed"] if m["company_number"] in want), None)
    with_closed_numbers = [m["company_number"] for m in with_closed["matches"]]
    raw = raw_lookup_numbers(typed)
    if hit:
        row.update(result="ok", where="", picker=shown.index(hit) + 1,
                   note=("spelling suggestion: " + default["spelling"]) if default["spelling"] else "")
    elif closed_hit and closed_hit in with_closed_numbers:
        row.update(result="ok", where="", picker=with_closed_numbers.index(closed_hit) + 1,
                   note="closed: named in a message by default, listed with 'Include dissolved'")
    elif not (raw & want):
        row.update(result="FAIL", where="lookup", note="Companies House search didn't return it")
    elif closed_hit:
        row.update(result="FAIL", where="filters", note="hidden by 'Active companies only'")
    else:
        row.update(result="FAIL", where="matching", note="returned by Companies House, dropped by WhyNow")
    number = hit or closed_hit
    if number:
        try:
            research = report.research_company(number, typed)
            r = report.build_report(research, ai=fake_ai)
            row["report"] = "ok" if r else "no report"
        except Exception as e:
            row["report"] = f"FAIL: {type(e).__name__}: {str(e)[:80]}"
            if row["result"] == "ok":
                row.update(result="FAIL", where="report")
    return row


def main(out=None):
    switch_off_paid_services()
    rows = [check(*s) for s in SEARCHES]
    width = max(len(r["typed"]) for r in rows)
    for r in rows:
        print(f'{r["typed"]:{width}}  {r["kind"]:32} {r["result"]:16} {r.get("where",""):9} '
              f'picker #{r.get("picker","-")!s:3} report: {r.get("report","-"):12} {r.get("note","")}')
    ok = sum(r["result"] == "ok" for r in rows)
    print(f"\n{ok} of {len(rows)} searches work")
    if out:
        with open(out, "w") as f:
            json.dump(rows, f, indent=1)
    return rows


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
