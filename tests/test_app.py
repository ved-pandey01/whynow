"""
Tests for the web page (app.py), using Streamlit's built-in test runner.
The research and AI steps are faked, so no API calls or credits are used.
"""

import json

import pytest
from streamlit.testing.v1 import AppTest

import report
import settings
from sources import uk_companies_house as ch
from ai.provider import AIError

MATCHES = [
    {"name": "ACME LTD", "company_number": "123", "status": "active", "founded": "2015-01-01",
     "address": "Leeds", "exact_match": True, "close_match": True, "source": "https://ch/acme",
     "size": "Large: parent of a group", "industry": "Business and domestic software development", "city": "Leeds"},
    {"name": "ACME HOLDINGS LTD", "company_number": "456", "status": "active", "founded": "2019-01-01",
     "address": "York", "exact_match": False, "close_match": True, "source": "https://ch/acme2",
     "size": "Small", "industry": None, "city": "York"},
]

SOURCES = [
    {"id": "S1", "kind": "companies_house", "title": "Companies House: ACME LTD", "url": "https://ch/acme",
     "date": None, "content": ""},
    {"id": "S2", "kind": "news", "title": "[funding] Acme raises $5m", "url": "https://news/1",
     "date": "2026-11-01", "content": ""},
]

PROFILE = {
    "brand": {"value": "Acme", "source": None}, "legal_name": {"value": "ACME LTD", "source": "S1"},
    "status": {"value": "active", "source": "S1"}, "website": None, "domain": None,
    "headquarters": None, "industry": {"value": "Software", "source": "S1"},
    "founded": {"value": "2015", "source": "S1"}, "headcount": {"value": 300, "source": "S1"},
    "headcount_change": {"value": "+30 yearly (11%)", "source": "S1"}, "total_raised": None,
    "revenue": None, "linkedin_followers": None, "business_model": None, "saas": None,
}

REPORT = {
    "company": {"name": "ACME LTD", "number": "123", "status": "active", "source": "https://ch/acme"},
    "not_worth_contacting": False, "offer": "x", "profile": PROFILE,
    "what_they_do": {"text": "Acme makes sales software.", "sources": ["S1"]},
    "snapshot": [{"text": "Founded in 2015.", "sources": ["S1"]}],
    "news": [{"source": "S2", "why_it_matters": "New money means new sales targets."}],
    "signals": [{"type": "funding", "text": "Raised $5m.", "sources": ["S2"]}],
    "fit_score": 8, "fit_reasons": [{"text": "B2B software in the UK.", "sources": ["S1"]}],
    "suggested_role": "Head of Sales", "role_reason": "Owns the sales team.",
    "not_found": ["Revenue not found"], "sources": SOURCES, "all_sources": SOURCES,
    "checks": [], "warnings": [], "timings": {"AI report": 10.0}, "seconds": 10.0,
}

EMAIL = {"subject": "Your $5m round", "body": "Hi [First name], congrats on the round. [Your name]",
         "word_count": 9, "trigger_sources": ["S2"], "warnings": [], "checks": [], "timings": {}}


@pytest.fixture
def app(monkeypatch):
    """The page with fake Companies House search, a fake report and a fake email."""
    import streamlit as st
    st.cache_data.clear()      # don't reuse results from another test
    st.cache_resource.clear()  # including the 6-hour report cache
    monkeypatch.setattr(ch, "search_companies", lambda typed: MATCHES)
    monkeypatch.setattr(report, "research_company", lambda number, typed, **k: {"number": number})
    monkeypatch.setattr(report, "build_report", lambda research, offer=None, **k: REPORT)
    monkeypatch.setattr(report, "write_email", lambda r, **k: EMAIL)
    return AppTest.from_file("../app.py", default_timeout=30)


def button(at, label):
    return next(b for b in at.button if b.label == label)


def html(at):
    """All the custom HTML on the page, as one string."""
    return " ".join(h.proto.body for h in at.get("html"))


def search(at, typed="Acme"):
    at.run()
    at.text_input[0].input(typed)
    button(at, "Research →").click().run()
    return at


def research_and_open(at):
    search(at)
    at.button(key="pick_123").click().run()
    return at


# --- Landing ----------------------------------------------------------------
def test_landing_shows_brand_slogan_and_how_it_works(app):
    app.run()
    page = html(app)
    for text in [settings.APP_LABEL, "Why<span>Now</span>", "Know the fit.", "Know the moment.",
                 "Find the company", "Research the signals", "Score the fit"]:
        assert text in page
    assert app.text_area[0].value.startswith("An AI tool that helps SDR teams")
    assert any(c.value == settings.OFFER_HINT for c in app.caption)


def test_footer_has_author_links(app):
    app.run()
    page = html(app)
    assert settings.AUTHOR_NAME in page
    assert settings.AUTHOR_LINKEDIN in page and settings.AUTHOR_GITHUB in page
    assert "YOUR-LINK" not in page


def test_sample_report_loads_with_no_api_calls(app, monkeypatch, tmp_path):
    def no_calls(*a, **k):
        raise AssertionError("the sample must not call any API")
    monkeypatch.setattr(ch, "search_companies", no_calls)
    monkeypatch.setattr(report, "build_report", no_calls)
    monkeypatch.setattr(report, "write_email", no_calls)
    sample = dict(REPORT, saved_on="17 Nov 2026", saved_email=EMAIL)
    sample_file = tmp_path / "sample.json"
    sample_file.write_text(json.dumps(sample))
    monkeypatch.setattr(settings, "SAMPLE_REPORT_FILE", str(sample_file))

    app.run()
    button(app, "See a sample report").click().run()
    assert not app.exception
    assert "Saved example from 17 Nov 2026" in app.info[0].value
    assert "← Back to home" in html(app)
    button(app, "Create email for this company").click().run()
    assert app.code[0].value.startswith("Hi [First name]")


# --- Picker -------------------------------------------------------------------
def test_picker_shows_cards_with_details(app):
    search(app)
    page = html(app)
    assert "ACME LTD" in page and "ACME HOLDINGS LTD" in page
    assert "Business and domestic software development · Large: parent of a group · Founded 2015 · Leeds" in page
    assert len([b for b in app.button if b.label == "Research this company"]) == 2


def test_no_matches_gives_friendly_message(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed: [])
    search(app, "Zzqx Widgets Ltd")
    assert "No companies found" in app.info[0].value


def test_loose_matches_get_a_warning(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed: [dict(MATCHES[1], close_match=False)])
    search(app, "Zzqx Widgets Ltd")
    assert "No close match" in app.warning[0].value


def test_companies_house_error_is_shown_kindly(app, monkeypatch):
    def broken(typed):
        raise ch.CompaniesHouseError("Companies House rejected the API key.")
    monkeypatch.setattr(ch, "search_companies", broken)
    search(app)
    assert "rejected the API key" in app.error[0].value
    assert not app.exception


# --- Report -------------------------------------------------------------------
def test_full_report_shows_every_section(app):
    research_and_open(app)
    assert not app.exception
    page = html(app)
    for heading in ["ICP fit", "Why reach out now", "Recent news", "What we couldn&#x27;t find", "Sources"]:
        assert heading in page
    assert 'class="wn-badge strong"' in page                  # 8/10 → green
    assert "← Back to home" in page
    assert '<span class="icon" aria-hidden="true">payments</span>Funding' in page   # signal icon
    assert ">Acme raises $5m<" in page                        # internal "[funding]" tag removed
    assert "300" in page and "▲ +30 yearly (11%)" in page     # stat tile with trend
    link = next(b for b in app.get("link_button"))
    assert link.proto.label == "Find Head of Sales at Acme on LinkedIn"
    assert link.proto.url.endswith("keywords=Head+of+Sales+Acme")


def test_email_is_written_only_when_asked(app, monkeypatch):
    calls = []
    monkeypatch.setattr(report, "write_email", lambda r, **k: calls.append(k.get("persona")) or EMAIL)
    research_and_open(app)
    assert not app.code and calls == []          # no email yet, no AI call
    button(app, "Create email for this company").click().run()
    assert app.code[0].value.startswith("Hi [First name]")
    assert calls == ["Head of Sales"]        # the default persona


def test_low_fit_shows_note_beside_email_button(app, monkeypatch):
    monkeypatch.setattr(report, "build_report", lambda research, offer=None, **k: dict(REPORT, fit_score=2))
    research_and_open(app)
    assert any(c.value == report.LOW_FIT_NOTE for c in app.caption)
    assert 'class="wn-badge weak"' in html(app)
    assert not app.code


def test_good_fit_has_no_low_fit_note(app):
    research_and_open(app)
    assert not any(c.value == report.LOW_FIT_NOTE for c in app.caption)


def test_ai_error_is_shown_kindly(app, monkeypatch):
    def broken(research, offer=None, **k):
        raise AIError("Gemini is having problems right now.")
    monkeypatch.setattr(report, "build_report", broken)
    research_and_open(app)
    assert "Gemini is having problems" in app.error[0].value
    assert not app.exception


def test_same_company_again_comes_from_the_6_hour_cache(app, monkeypatch):
    calls = []
    monkeypatch.setattr(report, "research_company", lambda number, typed, **k: calls.append(number) or {})
    research_and_open(app)
    app.html  # first report made
    search(app)
    app.button(key="pick_123").click().run()
    assert calls == ["123"]          # researched once; the second time came from the cache
    assert not app.exception


# --- Try chips -----------------------------------------------------------------
def test_try_chips_are_shown_under_the_search(app):
    app.run()
    labels = [b.label for b in app.button]
    assert all(name in labels for name in ("Cognism", "Darktrace", "Paddle"))
    assert "Try:" in html(app)


def test_try_chip_goes_straight_to_the_report(app, monkeypatch):
    searched = []
    monkeypatch.setattr(ch, "search_companies", lambda typed: searched.append(typed) or MATCHES)
    app.run()
    button(app, "Cognism").click().run()
    assert searched == ["cognism.com"]                  # searched by website, for accuracy
    assert not app.exception
    assert 'class="wn-badge strong"' in html(app)      # the report is showing
    assert not [b for b in app.button if b.label == "Research this company"]   # no picker step


def test_try_chip_with_no_close_match_says_so(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed: [dict(MATCHES[1], close_match=False)])
    app.run()
    button(app, "Paddle").click().run()
    assert "No close match found" in app.info[0].value
    assert not app.exception


def test_report_has_pdf_and_csv_downloads(app):
    research_and_open(app)
    labels = [d.proto.label for d in app.get("download_button")]
    assert labels == ["Download PDF", "Download CSV"]


def test_persona_selector_writes_one_email_per_persona(app, monkeypatch):
    calls = []
    monkeypatch.setattr(report, "write_email", lambda r, **k: calls.append(k["persona"]) or dict(EMAIL, persona=k["persona"]))
    research_and_open(app)
    selector = app.get("button_group")[0]
    assert list(selector.proto.options[i].content for i in range(3)) == ["Head of Sales", "Head of RevOps", "Founder / CEO"]
    button(app, "Create email for this company").click().run()
    app.get("button_group")[0].set_value("Head of RevOps").run()
    assert not app.code                                   # nothing for RevOps until asked
    button(app, "Create email for this company").click().run()
    app.get("button_group")[0].set_value("Head of Sales").run()
    assert app.code                                       # the Sales email is still there, free
    assert calls == ["Head of Sales", "Head of RevOps"]
