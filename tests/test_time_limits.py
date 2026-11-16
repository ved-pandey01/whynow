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
    with pytest.raises(provider.AIError, match="took longer than 60 seconds"):
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
    assert any(w.startswith("News skipped: Tavily took longer") for w in research["warnings"])
    assert "https://f" in [s["url"] for s in research["sources"].items]  # signals still ran
    assert "News" in research["timings"] and "Signals" in research["timings"]


def test_key_problem_stops_all_tavily_steps_with_one_message(fake_sources):
    calls = []

    def bad_key(*a, **k):
        raise nt.NewsError("Tavily rejected the API key.", temporary=False)

    fake_sources.setattr(report.tavily, "get_company_card", bad_key)
    fake_sources.setattr(report.tavily, "get_recent_news", lambda *a, **k: calls.append("news") or [])

    research = report.research_company("123", "Acme")
    assert research["warnings"] == ["News and signals unavailable: Tavily rejected the API key."]
    assert calls == []  # didn't keep trying with a bad key


def test_steps_after_the_time_budget_are_skipped(fake_sources):
    fake_sources.setattr(settings, "RESEARCH_TIME_BUDGET_SECONDS", -1)  # already out of time
    research = report.research_company("123", "Acme")
    skipped = [w for w in research["warnings"] if "skipped to keep the report under a minute" in w]
    assert len(skipped) == 4  # profile, website, news, signals
