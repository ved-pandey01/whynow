"""
Tests for brands that now belong to a different company (live test: Cazoo → MOTORS.CO.UK LIMITED).
All with fake Companies House answers.
"""

from datetime import date

import exports
import report
from sources import uk_companies_house as ch
from tests.test_report import FakeAI, make_draft, make_research

CAZOO_GROUP = {"name": "CAZOO GROUP LTD", "company_number": "11111111", "status": "closed-on",
               "founded": "2021-09-01", "close_match": True, "source": "https://ch/cazoo"}


def fake_search(monkeypatch, results):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: results)


# --- Spotting a former business ------------------------------------------------------
def test_closed_brand_company_from_around_the_brands_founding_counts(monkeypatch):
    fake_search(monkeypatch, [CAZOO_GROUP])
    assert ch.former_business("Cazoo", "MOTORS.CO.UK LIMITED", "2018")["name"] == "CAZOO GROUP LTD"


def test_brand_always_registered_under_another_name_is_not_flagged(monkeypatch):
    # Gousto's only closed lookalike is an unrelated takeaway set up 12 years after Gousto
    fake_search(monkeypatch, [dict(CAZOO_GROUP, name="LA GOUSTO ELEGANTE LTD", founded="2024-08-12")])
    assert ch.former_business("Gousto", "SCA INVESTMENTS LIMITED", "2012") is None
    fake_search(monkeypatch, [])                                   # Brandwatch: no Brandwatch companies at all
    assert ch.former_business("Brandwatch", "RUNTIME COLLECTIVE LIMITED", "2005") is None


def test_not_flagged_when_the_owner_carries_the_brand_name_or_the_year_is_unknown(monkeypatch):
    fake_search(monkeypatch, [CAZOO_GROUP])
    assert ch.former_business("Cazoo", "CAZOO HOLDINGS LIMITED", "2018") is None   # same brand, not a new owner
    assert ch.former_business("Cazoo", "MOTORS.CO.UK LIMITED", None) is None       # no founding year to compare
    fake_search(monkeypatch, [dict(CAZOO_GROUP, status="active")])
    assert ch.former_business("Cazoo", "MOTORS.CO.UK LIMITED", "2018") is None     # the brand's company is still open


# --- The report ------------------------------------------------------------------------
def cazoo_research(monkeypatch):
    fake_search(monkeypatch, [CAZOO_GROUP])
    research = make_research()
    research["profile"].update(name="MOTORS.CO.UK LIMITED", founded="2006-10-25")
    research["typed"] = "cazoo.co.uk"
    research["card"]["founded"] = "2018"
    report._check_former_business(research, "Cazoo", research["profile"], research["card"], research["sources"])
    return research


def test_report_has_the_note_and_uses_companies_house_for_founded(monkeypatch):
    research = cazoo_research(monkeypatch)
    r = report.build_report(research, ai=FakeAI(make_draft()), today=date(2026, 11, 16))
    assert r["ownership_note"]["text"] == ("This brand is now owned by MOTORS.CO.UK LIMITED. "
                                           "Some details may describe the former business.")
    assert r["ownership_note"]["former"] == "CAZOO GROUP LTD"
    founded = r["profile"]["founded"]
    assert founded["value"] == "2006"                            # Companies House, not the card's 2018
    assert founded["source"] == "S1"                             # the Companies House source
    assert r["profile"]["status"]["value"] == "active"           # status always comes from Companies House


def test_the_ai_is_told_the_card_may_describe_the_former_business(monkeypatch):
    research = cazoo_research(monkeypatch)
    note = next(s for s in research["sources"].items if "former company" in s["title"])
    assert "may describe that former business" in note["content"]
    assert "use Companies House for the founded date" in note["content"]


def test_no_note_for_an_ordinary_company():
    r = report.build_report(make_research(), ai=FakeAI(make_draft()), today=date(2026, 11, 16))
    assert r["ownership_note"] is None


def test_page_and_pdf_show_the_note(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    research = cazoo_research(monkeypatch)
    r = report.build_report(research, ai=FakeAI(make_draft()), today=date(2026, 11, 16))
    assert exports.report_to_pdf(r).startswith(b"%PDF")
    st.cache_data.clear()
    monkeypatch.setattr(report, "research_company", lambda number, typed, **k: research)
    monkeypatch.setattr(report, "build_report", lambda research, offer=None, **k: r)
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [
        {"name": "MOTORS.CO.UK LIMITED", "company_number": "05975777", "status": "active", "founded": "2006-10-25",
         "exact_match": False, "close_match": True, "source": "https://ch/motors", "size": "Large", "city": "London"}])
    at = AppTest.from_file("../app.py", default_timeout=30)
    at.run()
    at.text_input[0].input("Motors")
    next(b for b in at.button if b.label == "Research →").click().run()
    at.button(key="pick_05975777").click().run()
    assert not at.exception
    assert any("This brand is now owned by MOTORS.CO.UK LIMITED. Some details may describe the former business."
               in w.value for w in at.warning)
