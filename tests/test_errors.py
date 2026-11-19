"""
Step 7: every failure shows a friendly message (what happened and what to try next), never a crash.
All mocked: no API calls.
"""

import pytest
import requests

import batch
import report
import settings
from ai import provider
from sources import news_tavily as nt
from sources import uk_companies_house as ch
from tests.test_app import MATCHES, REPORT, app, button, html, research_and_open, search  # noqa: F401
from tests.test_uk_companies_house import FakeResponse


def no_page_crash(at):
    assert not at.exception, at.exception


# --- Searching ------------------------------------------------------------------------
def test_company_not_found(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [])
    monkeypatch.setattr(nt, "find_registration", lambda brand, website=None: None)
    search(app, "Qwertyuiop Holdings")
    no_page_crash(app)
    assert any("No UK company found" in w.value and "type the company's website" in w.value for w in app.warning)


def test_misspelled_name_suggests_the_website(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [dict(MATCHES[1], close_match=False)])
    monkeypatch.setattr(nt, "find_registration", lambda brand, website=None: None)
    search(app, "Cognisn")
    no_page_crash(app)
    assert any("No close match" in w.value and "type the company's website" in w.value for w in app.warning)


def test_only_dissolved_companies(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [dict(MATCHES[0], status="dissolved")])
    monkeypatch.setattr(nt, "find_registration", lambda brand, website=None: None)
    search(app, "Old Co")
    no_page_crash(app)
    assert any("Untick 'Active companies only' in Filters" in i.value for i in app.info)


def test_over_long_search_is_capped_and_never_crashes(app):
    app.run()
    app.text_input[0].set_value("x" * (settings.MAX_SEARCH_CHARS + 20))
    button(app, "Research →").click().run()
    no_page_crash(app)
    assert len(app.text_input[0].value) <= settings.MAX_SEARCH_CHARS     # the box itself stops longer text
    assert "max_chars=settings.MAX_SEARCH_CHARS" in open("app.py").read()
    assert "That's too long for a company name. Type the name or website only." in open("app.py").read()  # backstop



def test_search_bar_hides_counter_and_enter_hint():
    """The "0/100" counter and "Press Enter to submit form" hint are hidden; the cap and Enter still work."""
    css = open("ui/style.css").read()
    assert '.st-key-wn_search [data-testid="InputInstructions"] { display: none !important; }' in css
    code = open("app.py").read()
    assert 'with st.form("search"' in code              # a form, so Enter submits the search
    assert "max_chars=settings.MAX_SEARCH_CHARS" in code  # the quiet 100-character cap
    assert settings.MAX_SEARCH_CHARS == 100

# --- Companies House ----------------------------------------------------------------
@pytest.mark.parametrize("reply, words", [
    (FakeResponse(503), "Companies House isn't responding right now"),
    (requests.exceptions.Timeout(), "Companies House isn't responding right now"),
    (requests.exceptions.ConnectionError(), "Couldn't connect to the internet"),
])
def test_companies_house_down_or_no_internet(monkeypatch, reply, words):
    def get(*a, **k):
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(ch.requests, "get", get)
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    with pytest.raises(ch.CompaniesHouseError, match=words):
        ch.search_companies("Cognism")


def test_companies_house_down_shows_message_on_the_page(app, monkeypatch):
    def down(typed, **kw):
        raise ch.CompaniesHouseError(ch.CH_DOWN)
    monkeypatch.setattr(ch, "search_companies", down)
    search(app, "Cognism")
    no_page_crash(app)
    assert "Try again in a few minutes; the sample report still works." in app.error[0].value


# --- Tavily -------------------------------------------------------------------------------
@pytest.mark.parametrize("error, words", [
    (nt.tavily_errors.UsageLimitExceededError("limit"), "Tavily credits have run out"),
    (RuntimeError("500"), "Tavily isn't responding right now"),
    (ConnectionError(), "Couldn't connect to the internet"),
])
def test_tavily_down_or_out_of_credits_still_gives_a_report(monkeypatch, error, words):
    monkeypatch.setattr(report.ch, "get_company_profile", lambda n: {
        "name": "ACME LTD", "company_number": "1", "status": "active", "closed_on": None, "founded": "2015-01-01",
        "address": "Leeds", "sic_codes": [], "sic_descriptions": [], "last_accounts_type": "full",
        "company_type": "ltd", "source": "https://ch/acme", "not_worth_contacting": False})
    monkeypatch.setattr(report.ch, "get_recent_directors", lambda n: [])
    monkeypatch.setattr(report.ch, "get_current_directors", lambda n: {"people": [], "total": 0, "source": "x"})
    monkeypatch.setattr(report.ch, "get_owners", lambda n: {"owners": [], "source": "y"})

    def broken(*a, **k):
        raise error
    monkeypatch.setattr(nt, "_client", lambda: type("T", (), {"search": broken, "extract": broken})())
    research = report.research_company("1", "Acme")
    message = research["warnings"][-1]
    assert message.startswith("News and reasons to reach out are unavailable right now") and words in message
    assert message.endswith("The score uses Companies House data only.")


def test_tavily_warning_is_shown_clearly_on_the_report(app, monkeypatch):
    warned = dict(REPORT, warnings=[f"{report.NEWS_UNAVAILABLE} (Tavily credits have run out). "
                                    "The score uses Companies House data only."])
    monkeypatch.setattr(report, "build_report", lambda research, offer=None, **k: warned)
    research_and_open(app)
    no_page_crash(app)
    assert any("Tavily credits have run out" in w.value for w in app.warning)


# --- Gemini --------------------------------------------------------------------------------
def test_all_gemini_models_failing_on_a_report(app, monkeypatch):
    def busy(research, offer=None, **k):
        raise provider.AllModelsBusy("all busy")
    monkeypatch.setattr(report, "build_report", busy)
    research_and_open(app)
    no_page_crash(app)
    assert "all models are busy" in app.error[0].value and "won't use search credits" in app.error[0].value


def test_all_gemini_models_failing_on_an_email(app, monkeypatch):
    def busy(r, **k):
        raise provider.AllModelsBusy("all busy")
    monkeypatch.setattr(report, "write_email", busy)
    research_and_open(app)
    button(app, "Create email for this company").click().run()
    no_page_crash(app)
    assert "couldn't write the email right now" in app.error[0].value


def test_no_internet_for_gemini(monkeypatch):
    from models import Claim
    from tests.test_time_limits import ConnectError, FakeGemini
    FakeGemini({m: ConnectError() for m in [settings.GEMINI_MODEL] + settings.GEMINI_FALLBACK_MODELS}).install(monkeypatch)
    with pytest.raises(provider.AIError, match="Couldn't connect to the internet"):
        provider.generate_structured("hi", Claim)


def test_unexpected_error_never_crashes_the_page(app, monkeypatch):
    def boom(research, offer=None, **k):
        raise KeyError("surprise")
    monkeypatch.setattr(report, "build_report", boom)
    research_and_open(app)
    no_page_crash(app)
    assert "Something unexpected went wrong" in app.error[0].value


# --- Research a list (CSV) ----------------------------------------------------------------
@pytest.mark.parametrize("data, words", [
    (b"", "That file is empty"),
    (b"   \n \n", "That file is empty"),
    ("company\n".encode() + b"\xff\xfe\x00bad", "isn't a plain-text CSV"),
    (b"company\n,,\n", "No company names found"),
    (b"x" * 200_000, "too big for a list of companies"),
])
def test_invalid_batch_csv(data, words):
    names, left_out, problem = batch.read_upload(data)
    assert names == [] and words in problem


def test_good_batch_csv_with_too_many_rows():
    data = ("company\n" + "\n".join(f"Company {i}" for i in range(14))).encode()
    names, left_out, problem = batch.read_upload(data)
    assert problem is None and len(names) == settings.BATCH_MAX_COMPANIES and left_out == 4
