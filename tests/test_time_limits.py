"""
Tests for time limits: slow services give clear messages, and one slow search
doesn't stop the whole report. Uses fakes, so no real waiting or API calls.
"""

import pytest

import report
import settings
from ai import provider
from sources import news_tavily as nt


# --- Gemini ---------------------------------------------------------------
class ReadTimeout(Exception):
    """Same name as the real timeout error the web library raises."""


def test_gemini_gets_time_limit_and_no_hidden_retries(monkeypatch):
    from google import genai
    seen = {}

    class FakeClient:
        def __init__(self, api_key, http_options):
            seen["timeout"] = http_options.timeout
            seen["attempts"] = http_options.retry_options.attempts
            self.models = self

        def generate_content(self, **kwargs):
            raise ReadTimeout()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(genai, "Client", FakeClient)
    with pytest.raises(provider.AllModelsBusy, match="all 4 models are busy"):   # every model timed out
        provider.generate_structured("hello", object)
    assert seen == {"timeout": settings.GEMINI_TIMEOUT_SECONDS * 1000, "attempts": 1}


# --- Tavily ---------------------------------------------------------------
def test_tavily_timeout_is_temporary_but_key_problem_is_not():
    assert nt._friendly(nt.tavily_errors.TimeoutError(20)).temporary is True
    assert nt._friendly(nt.tavily_errors.InvalidAPIKeyError("bad")).temporary is False


def test_signals_stop_when_out_of_time(monkeypatch):
    calls = []
    monkeypatch.setattr(nt, "_search", lambda q, **o: calls.append(q) or [])
    ticks = iter([False, False, True])  # time runs out before the 3rd search
    signals = nt.get_signals("Acme", out_of_time=lambda: next(ticks))
    assert list(signals) == ["hiring_sales", "funding"]
    assert len(calls) == 2


# --- research_company -------------------------------------------------------
PROFILE = {"name": "ACME LTD", "company_number": "123", "status": "active", "closed_on": None,
           "founded": "2015-01-01", "address": "Leeds", "sic_codes": [], "sic_descriptions": [],
           "last_accounts_type": "full",
           "company_type": "ltd", "source": "https://ch/acme", "not_worth_contacting": False}


@pytest.fixture
def fake_sources(monkeypatch):
    """Companies House works; every Tavily step returns nothing unless a test changes it."""
    monkeypatch.setattr(report.ch, "get_company_profile", lambda n: dict(PROFILE))
    monkeypatch.setattr(report.ch, "get_recent_directors", lambda n: [])
    monkeypatch.setattr(report.ch, "get_current_directors", lambda n: {"people": [], "total": 0, "source": "https://ch/officers"})
    monkeypatch.setattr(report.ch, "get_owners", lambda n: {"owners": [], "source": "https://ch/psc"})
    monkeypatch.setattr(report.ch, "get_filing_history", lambda n: {"filings": [], "total": 0, "source": "https://ch/filings"})   # fake: no real API calls in tests
    monkeypatch.setattr(report.tavily, "get_job_listings", lambda *a, **k: [])
    monkeypatch.setattr(report.tavily, "get_company_card", lambda *a, **k: None)
    monkeypatch.setattr(report.tavily, "get_website_text", lambda w: None)
    monkeypatch.setattr(report.tavily, "get_recent_news", lambda *a, **k: [])
    monkeypatch.setattr(report.tavily, "get_signals", lambda *a, **k: {})
    return monkeypatch


def test_one_slow_step_is_skipped_and_the_rest_carry_on(fake_sources):
    def slow_news(*a, **k):
        raise nt.NewsError("Tavily took longer than 20 seconds to reply.", temporary=True)

    fake_sources.setattr(report.tavily, "get_recent_news", slow_news)
    fake_sources.setattr(report.tavily, "get_signals", lambda *a, **k: {
        "funding": [{"title": "Acme raises £5m", "source": "https://f", "snippet": "...", "date": None}]})

    research = report.research_company("123", "Acme")
    assert any(w.startswith("News and reasons to reach out are unavailable right now for: news (Tavily took longer")
               for w in research["warnings"])
    assert "https://f" in [s["url"] for s in research["sources"].items]  # signals still ran
    assert "News" in research["timings"] and "Signals" in research["timings"]


def test_key_problem_stops_all_tavily_steps_with_one_message(fake_sources):
    calls = []

    def bad_key(*a, **k):
        raise nt.NewsError("Tavily rejected the API key.", temporary=False)

    fake_sources.setattr(report.tavily, "get_company_card", bad_key)
    fake_sources.setattr(report.tavily, "get_recent_news", lambda *a, **k: calls.append("news") or [])

    research = report.research_company("123", "Acme")
    assert research["warnings"] == ["News and reasons to reach out are unavailable right now (Tavily rejected the "
                                    "API key). The score uses Companies House data only."]
    assert calls == []  # didn't keep trying with a bad key


def test_steps_after_the_time_budget_are_skipped(fake_sources):
    fake_sources.setattr(settings, "RESEARCH_TIME_BUDGET_SECONDS", -1)  # already out of time
    research = report.research_company("123", "Acme")
    skipped = [w for w in research["warnings"] if "skipped to keep the report under a minute" in w]
    assert len(skipped) == 5  # profile, website, news, signals, jobs


# --- Backup model ------------------------------------------------------------
class FakeGemini:
    """Pretends to be Gemini. `failures` maps a model name to the error it raises."""

    def __init__(self, failures):
        self.failures, self.models_asked = failures, []

    def install(self, monkeypatch):
        from google import genai
        fake = self

        class Client:
            def __init__(self, api_key, http_options):
                self.models = self

            def generate_content(self, model, **kwargs):
                fake.models_asked.append(model)
                if model in fake.failures:
                    raise fake.failures[model]

                class Answer:
                    parsed = None
                    text = '{"text": "hello", "source_ids": []}'
                return Answer()

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(genai, "Client", Client)
        return self


def busy():
    from google.genai import errors
    return errors.ServerError(503, {"error": {"message": "high demand"}})


def bad_key():
    from google.genai import errors
    return errors.ClientError(403, {"error": {"message": "bad key"}})


def test_busy_main_model_falls_back_to_backup(monkeypatch):
    """Real problem (17 Nov 2026): gemini-3.6-flash returned '503 high demand' for several minutes."""
    from models import Claim
    gemini = FakeGemini({settings.GEMINI_MODEL: busy()}).install(monkeypatch)
    answer = provider.generate_structured("hi", Claim)
    assert answer.text == "hello"
    assert gemini.models_asked == [settings.GEMINI_MODEL, settings.GEMINI_FALLBACK_MODELS[0]]
    used = provider.last_model_used()
    assert used["name"] == settings.GEMINI_FALLBACK_MODELS[0] and used["backup"] is True
    assert "having problems" in used["reason"]


def test_main_model_used_when_it_works(monkeypatch):
    from models import Claim
    gemini = FakeGemini({}).install(monkeypatch)
    provider.generate_structured("hi", Claim)
    assert gemini.models_asked == [settings.GEMINI_MODEL]
    assert provider.last_model_used() == {"name": settings.GEMINI_MODEL, "backup": False, "reason": None}


def test_key_problem_does_not_try_the_backup(monkeypatch):
    from models import Claim
    gemini = FakeGemini({settings.GEMINI_MODEL: bad_key()}).install(monkeypatch)
    with pytest.raises(provider.AIError, match="rejected the API key"):
        provider.generate_structured("hi", Claim)
    assert gemini.models_asked == [settings.GEMINI_MODEL]


def test_backups_are_tried_in_order_until_one_works(monkeypatch):
    """Real problem (17 Nov 2026): the main model hit its 20-a-day limit and the first backup was busy."""
    from models import Claim
    main, first, second, third = [settings.GEMINI_MODEL] + settings.GEMINI_FALLBACK_MODELS
    gemini = FakeGemini({main: busy(), first: busy()}).install(monkeypatch)
    provider.generate_structured("hi", Claim)
    assert gemini.models_asked == [main, first, second]
    used = provider.last_model_used()
    assert used["name"] == second and main in used["reason"] and first in used["reason"]


def test_all_models_busy_gives_a_friendly_error(monkeypatch):
    from models import Claim
    all_models = [settings.GEMINI_MODEL] + settings.GEMINI_FALLBACK_MODELS
    gemini = FakeGemini({m: busy() for m in all_models}).install(monkeypatch)
    with pytest.raises(provider.AllModelsBusy, match="all 4 models are busy. Try again in a few minutes"):
        provider.generate_structured("hi", Claim)
    assert gemini.models_asked == all_models   # each model once, no endless loop


class ConnectError(Exception):
    """Same name as the web library's error when the internet connection drops."""


def test_dropped_connection_gets_a_plain_message(monkeypatch):
    """Real problem (17 Nov 2026): the connection dropped and the message said 'ConnectError'."""
    from models import Claim
    FakeGemini({m: ConnectError() for m in [settings.GEMINI_MODEL] + settings.GEMINI_FALLBACK_MODELS}).install(monkeypatch)
    with pytest.raises(provider.AIError, match="Couldn't connect to the internet"):
        provider.generate_structured("hi", Claim)


# --- Live progress -------------------------------------------------------------
def test_progress_steps_start_and_finish_in_order(fake_sources):
    """Each of the 5 steps on the page starts, then finishes, in order, matching the real work."""
    from tests.test_report import FakeAI, make_draft
    events = []
    research = report.research_company("123", "Acme", on_step=lambda step, state: events.append((step, state)))
    report.build_report(research, ai=FakeAI(make_draft()), on_step=lambda step, state: events.append((step, state)))
    expected = [(i, s) for i in range(len(report.STEPS)) for s in (report.START, report.DONE)]
    assert events == expected
    assert report.STEPS[0] == "Finding the company on Companies House"
    assert report.STEPS[-1] == "Writing the report"


def test_closed_company_ticks_every_step_without_research(fake_sources):
    fake_sources.setattr(report.ch, "get_company_profile",
                         lambda n: dict(PROFILE, status="dissolved", not_worth_contacting=True))
    events = []
    research = report.research_company("123", "Acme", on_step=lambda step, state: events.append((step, state)))
    report.build_report(research, on_step=lambda step, state: events.append((step, state)))
    assert {step for step, state in events if state == report.DONE} == set(range(len(report.STEPS)))


def test_directors_source_is_dated_by_newest_appointment(fake_sources):
    fake_sources.setattr(report.ch, "get_recent_directors", lambda n: [
        {"name": "A", "role": "director", "occupation": None, "appointed_on": "2026-09-01", "source": "https://ch/officers"},
        {"name": "B", "role": "director", "occupation": None, "appointed_on": "2026-10-12", "source": "https://ch/officers"}])
    research = report.research_company("123", "Acme")
    directors = next(s for s in research["sources"].items if s["url"] == "https://ch/officers")
    assert directors["date"] == "2026-10-12"
