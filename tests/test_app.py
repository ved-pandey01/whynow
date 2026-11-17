"""
Tests for the web page (app.py), using Streamlit's built-in test runner.
The research and AI steps are faked, so no API calls or credits are used.
"""

import pytest
from streamlit.testing.v1 import AppTest

import report
from ai.provider import AIError
from sources import uk_companies_house as ch

MATCHES = [
    {"name": "ACME LTD", "company_number": "123", "status": "active", "founded": "2015-01-01",
     "address": "Leeds", "exact_match": True, "close_match": True, "source": "https://ch/acme"},
    {"name": "ACME HOLDINGS LTD", "company_number": "456", "status": "active", "founded": "2019-01-01",
     "address": "York", "exact_match": False, "close_match": True, "source": "https://ch/acme2"},
]

SOURCES = [
    {"id": "S1", "kind": "companies_house", "title": "Companies House: ACME LTD", "url": "https://ch/acme",
     "date": None, "content": ""},
    {"id": "S2", "kind": "news", "title": "Acme raises $5m", "url": "https://news/1",
     "date": "2026-11-01", "content": ""},
]

REPORT = {
    "company": {"name": "ACME LTD", "number": "123", "status": "active", "source": "https://ch/acme"},
    "not_worth_contacting": False, "offer": "x",
    "what_they_do": {"text": "Acme makes sales software.", "sources": ["S1"]},
    "snapshot": [{"text": "Founded in 2015.", "sources": ["S1"]}],
    "news": [{"source": "S2", "why_it_matters": "New money means new sales targets."}],
    "signals": [{"type": "funding", "text": "Raised $5m.", "sources": ["S2"]}],
    "fit_score": 8, "fit_reasons": [{"text": "B2B software in the UK.", "sources": ["S1"]}],
    "suggested_role": "Head of Sales", "role_reason": "Owns the sales team.",
    "email": {"subject": "Your $5m round", "body": "Hi [First name], congrats on the round. [Your name]",
              "word_count": 9, "trigger_sources": ["S2"], "warnings": []},
    "not_found": ["Headcount not found"], "sources": SOURCES, "all_sources": SOURCES,
    "checks": [], "warnings": [], "timings": {"AI report": 10.0}, "seconds": 10.0,
}


@pytest.fixture
def app(monkeypatch):
    """The page with fake Companies House search and a fake report."""
    import streamlit as st
    st.cache_data.clear()  # don't reuse results from another test
    monkeypatch.setattr(ch, "search_companies", lambda typed: MATCHES)
    monkeypatch.setattr(report, "research_company", lambda number, typed: {"number": number})
    monkeypatch.setattr(report, "build_report", lambda research, offer=None: REPORT)
    return AppTest.from_file("../app.py", default_timeout=30)


def search(at, typed="Acme"):
    at.run()
    at.text_input[0].input(typed)
    at.button[0].click().run()  # "Find company"
    return at


def all_text(at):
    return " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.code)


def test_offer_box_is_prefilled(app):
    app.run()
    assert app.text_area[0].value.startswith("An AI tool that helps SDR teams")


def test_search_shows_company_picker(app):
    search(app)
    assert len(app.radio[0].options) == 2
    assert "ACME LTD" in app.radio[0].options[0]


def test_full_report_shows_all_8_cards(app):
    search(app)
    app.button[1].click().run()  # "Research this company"
    assert not app.exception
    text = all_text(app)
    for heading in ["1 · Snapshot", "2 · Recent news", "3 · \"Why now\" signals", "4 · ICP fit score",
                    "5 · Who to contact", "6 · Outreach email", "7 · What we couldn't find", "8 · Sources"]:
        assert heading in text
    assert app.metric[0].value == "8/10"
    assert "[S1](https://ch/acme)" in text            # citations are clickable links
    assert "Raised \\$5m" in text                     # '$' is escaped, not read as maths
    assert app.code[0].value.startswith("Hi [First name]")


def test_no_matches_gives_friendly_message(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed: [])
    search(app, "Zzqx Widgets Ltd")
    assert "No companies found" in app.info[0].value
    assert not app.radio


def test_loose_matches_get_a_warning(app, monkeypatch):
    loose = [dict(MATCHES[1], close_match=False)]
    monkeypatch.setattr(ch, "search_companies", lambda typed: loose)
    search(app, "Zzqx Widgets Ltd")
    assert "No close match" in app.warning[0].value


def test_companies_house_error_is_shown_kindly(app, monkeypatch):
    def broken(typed):
        raise ch.CompaniesHouseError("Companies House rejected the API key.")
    monkeypatch.setattr(ch, "search_companies", broken)
    search(app)
    assert "rejected the API key" in app.error[0].value
    assert not app.exception


def test_ai_error_is_shown_kindly(app, monkeypatch):
    def broken(research, offer=None):
        raise AIError("Gemini is having problems right now.")
    monkeypatch.setattr(report, "build_report", broken)
    search(app)
    app.button[1].click().run()
    assert "Gemini is having problems" in app.error[0].value
    assert not app.exception
