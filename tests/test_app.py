"""
Tests for the web page (app.py), using Streamlit's built-in test runner.
The research and AI steps are faked, so no API calls or credits are used.
"""

import json

import pytest
from streamlit.testing.v1 import AppTest

from whynow import report
from whynow import settings
from whynow.sources import news_tavily as tavily
from whynow.sources import uk_companies_house as ch
from whynow.ai.provider import AIError

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
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: MATCHES)
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
    assert ("No. 123 · Incorporated 1 Jan 2015 · Leeds · Business and domestic software development · "
            "Large: parent of a group") in page
    assert len([b for b in app.button if b.label == "Research this company"]) == 2


def test_no_matches_gives_friendly_message(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [])
    search(app, "Zzqx Widgets Ltd")
    assert any("No company found" in w.value and "search by the 8-digit company number" in w.value for w in app.warning)


def test_loose_matches_get_a_warning(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [dict(MATCHES[1], close_match=False)])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: None)
    search(app, "Zzqx Widgets Ltd")
    assert any("No close match" in w.value for w in app.warning)


def test_companies_house_error_is_shown_kindly(app, monkeypatch):
    def broken(typed, **kw):
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


def test_try_chip_uses_its_fixed_company_number(app, monkeypatch):
    """Real problem: the Darktrace chip opened DARKTRACE MIDCO I LIMITED instead of DARKTRACE HOLDINGS LIMITED."""
    researched, searched = [], []
    monkeypatch.setattr(report, "research_company", lambda number, typed, **k: researched.append((number, typed)) or {})
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: searched.append(typed) or MATCHES)
    app.run()
    button(app, "Darktrace").click().run()
    assert researched == [("08562035", "darktrace.com")]
    assert searched == []                               # no search, so ranking can't pick the wrong company
    assert not app.exception
    assert 'class="wn-badge strong"' in html(app)
    assert not [b for b in app.button if b.label == "Research this company"]   # no picker step


# --- Research a list -----------------------------------------------------------
def test_research_a_list_opens_with_a_credit_warning(app):
    app.session_state["owner"] = True
    app.run()
    button(app, "Research a list").click().run()
    assert not app.exception
    assert "uses more API credits" in app.warning[0].value
    assert app.get("file_uploader")


def test_report_link_opens_a_cached_report(app):
    """The batch table's 'View report' links open ./?report=<id> from the 6-hour cache."""
    research_and_open(app)
    rid = app.session_state["report"]["report_id"]
    fresh = AppTest.from_file("../app.py", default_timeout=30)   # a new visitor (new session)
    fresh.query_params["report"] = rid
    fresh.run()
    assert not fresh.exception
    assert 'class="wn-badge strong"' in html(fresh)


def test_expired_report_link_says_so(app):
    app.query_params["report"] = "doesnotexist"
    app.run()
    assert "expired" in app.info[0].value


def test_batch_results_table_and_csv(app, monkeypatch):
    rows = [{"typed": "acme.com", "company": "Acme", "fit_score": 8, "employees": 300,
             "top_reason": "Raised $5m.", "report_id": "abc", "status": "Done"}]
    app.session_state["owner"] = True
    app.session_state["batch_mode"] = True
    app.session_state["batch_rows"] = rows
    app.session_state["batch_reports"] = [REPORT]
    app.run()
    assert not app.exception
    table = app.dataframe[0].value
    assert list(table.columns) == ["Company", "Fit score", "Employees", "Top reason to reach out", "Report"]
    assert table.iloc[0]["Report"] == "./?report=abc"
    assert [d.proto.label for d in app.get("download_button")] == ["Download all as CSV"]


def test_more_about_section_is_closed_by_default(app, monkeypatch):
    from tests.test_components import MORE
    monkeypatch.setattr(report, "build_report", lambda research, offer=None, **k: dict(REPORT, more_about=MORE))
    research_and_open(app)
    section = [e for e in app.expander if e.label.startswith("More about")]
    assert section and section[0].label == "More about Acme"
    assert section[0].proto.expanded is False



# --- Brand registered under another name (e.g. Gousto) ----------------------------
DISSOLVED_TAKEAWAY = dict(MATCHES[0], name="GOUSTO TAKEAWAY LTD", company_number="999", status="dissolved",
                          close_match=True)
GOUSTO_FOUND = {"number": "07294209", "website": "gousto.co.uk",
                "source": "https://www.gousto.co.uk/terms", "source_title": "gousto.co.uk website"}
GOUSTO_MATCH = dict(MATCHES[0], name="SCHOOLING LIMITED", company_number="07294209", status="active")


def test_brand_found_under_its_registered_name(app, monkeypatch):
    """Real problem: searching 'Gousto' only found a dissolved takeaway."""
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [DISSOLVED_TAKEAWAY])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: GOUSTO_FOUND)
    monkeypatch.setattr(ch, "lookup_registration", lambda found: GOUSTO_MATCH if found == GOUSTO_FOUND else None)
    researched = []
    monkeypatch.setattr(report, "research_company", lambda number, typed, **k: researched.append((number, typed)) or {})
    search(app, "Gousto")
    assert not app.exception
    assert "is registered as **SCHOOLING LIMITED**" in app.success[0].value
    assert "gousto.co.uk/terms" in app.success[0].value              # says where it was found
    page = html(app)
    assert 'Gousto <span class="wn-quiet">(registered as SCHOOLING LIMITED)</span>' in page
    app.button(key="pick_07294209").click().run()
    assert researched == [("07294209", "gousto.co.uk")]               # researched by the website found


def test_no_registered_name_found_says_so_and_flags_closed_only(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [DISSOLVED_TAKEAWAY])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: None)
    search(app, "Gousto")
    # One short message (it used to be two yellow warnings), and closed companies are hidden by default
    assert len(app.warning) + len(app.info) == 1
    assert "only closed ones match" in app.info[0].value
    assert "Turn on 'Include dissolved companies' in Filters" in app.info[0].value
    assert DISSOLVED_TAKEAWAY["name"] in app.info[0].value                 # says which company is hidden
    assert not [b for b in app.button if (b.key or "").startswith("pick_")]


def test_include_dissolved_toggle_shows_closed_companies_again(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [DISSOLVED_TAKEAWAY])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: None)
    app.run()
    app.toggle(key="wn_include_dissolved").set_value(True).run()
    search(app, "Gousto")
    assert len(app.warning) + len(app.info) == 1
    assert "Only closed companies match this name. Tip: add a location in Filters" in app.info[0].value
    assert app.button(key=f"pick_{DISSOLVED_TAKEAWAY['company_number']}")


def test_registered_name_lookup_is_skipped_when_an_active_match_exists(app, monkeypatch):
    calls = []
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: calls.append(brand))
    search(app, "Acme")
    assert calls == []      # no extra credits when Companies House already has the company


def test_company_link_fills_the_search_bar_and_searches(app, monkeypatch):
    """?company=Monzo: the name is typed in and the company list appears, with no free search used
    (that only happens when the visitor clicks 'Research this company')."""
    from whynow import usage
    app.query_params["company"] = "Acme"
    app.run()
    assert not app.exception
    assert app.text_input[0].value == "Acme"
    assert [b.key for b in app.button if (b.key or "").startswith("pick_")] == ["pick_123", "pick_456"]
    assert "report" not in app.session_state
    assert usage.searches_today()["total"] == 0


def test_company_link_never_spends_credits_on_its_own(app, monkeypatch):
    """A link can't trigger the paid public-sources lookup (about 2 Tavily credits); pressing Research can."""
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: pytest.fail("paid lookup from a link"))
    app.query_params["company"] = "Monzo"
    app.run()
    assert not app.exception
    assert app.session_state["matches"] == []
    assert any("No company found" in w.value for w in app.warning)


def test_company_link_collapses_extra_spaces(app):
    app.query_params["company"] = "  Acme   Widgets  "
    app.run()
    assert app.text_input[0].value == "Acme Widgets"


def test_sample_link_wins_over_a_company_link(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: pytest.fail("no search when ?sample= is set"))
    app.query_params["company"] = "Acme"
    app.query_params["sample"] = "paddle"
    app.run()
    assert not app.exception
    assert app.session_state["sample"] is True


@pytest.mark.parametrize("value", ["paddle", "Paddle", "1"])
def test_sample_paddle_link_opens_the_saved_paddle_report(app, value):
    app.query_params["sample"] = value
    app.run()
    assert not app.exception
    assert app.session_state["sample"] is True
    assert "PADDLE" in app.session_state["report"]["company"]["name"].upper()


def test_unknown_sample_name_is_ignored(app):
    app.query_params["sample"] = "monzo"
    app.run()
    assert not app.exception
    assert "report" not in app.session_state


def test_company_link_runs_only_once(app):
    """After arriving, the visitor can clear or change the name; the link does not put it back or search again."""
    app.query_params["company"] = "Paddle"
    app.run()
    app.session_state["wn_company"] = "Cognism"   # the visitor changes the name
    app.run()
    assert app.text_input[0].value == "Cognism"


def test_overlong_company_link_is_trimmed(app):
    app.query_params["company"] = "x" * (settings.MAX_SEARCH_CHARS + 50)
    app.run()
    assert not app.exception
    assert len(app.text_input[0].value) == settings.MAX_SEARCH_CHARS


def test_sample_link_opens_the_paddle_sample(app):
    """The portfolio's 'See a sample report (Paddle)' links to ./?sample=1."""
    app.query_params["sample"] = "1"
    app.run()
    assert not app.exception
    assert app.session_state["sample"] is True
    assert "PADDLE" in app.session_state["report"]["company"]["name"].upper()
