"""
Tests for running costs: caches, the call log, daily limits and owner mode. All mocked: no API calls.
"""

import time

import pytest
from streamlit.testing.v1 import AppTest

from whynow import cache
from whynow import report
from whynow import settings
from whynow import usage
from sources import news_tavily as nt
from sources import uk_companies_house as ch
from tests.test_app import EMAIL, MATCHES, REPORT, button, html, search


# --- Disk cache ---------------------------------------------------------------------
def test_cache_returns_fresh_answers_and_forgets_old_ones(monkeypatch):
    cache.put("demo", ["q"], {"a": 1})
    assert cache.get("demo", ["q"], 60) == {"a": 1}
    later = time.time() + 120
    monkeypatch.setattr(time, "time", lambda: later)
    assert cache.get("demo", ["q"], 60) is cache.MISSING


def test_companies_house_is_called_once_then_cached_for_7_days(monkeypatch):
    from tests.test_uk_companies_house import FakeResponse
    calls = []
    monkeypatch.setattr(ch.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(200, {"items": []}))
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    ch._call_api("/search/companies", {"q": "acme"})
    ch._call_api("/search/companies", {"q": "acme"})
    assert calls == [1]


def test_companies_house_errors_are_never_cached(monkeypatch):
    from tests.test_uk_companies_house import FakeResponse
    replies = iter([FakeResponse(500), FakeResponse(200, {"ok": True})])
    monkeypatch.setattr(ch.requests, "get", lambda *a, **k: next(replies))
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    with pytest.raises(ch.CompaniesHouseError):
        ch._call_api("/company/1")
    assert ch._call_api("/company/1") == {"ok": True}       # the failure wasn't remembered


class CountingTavily:
    def __init__(self):
        self.searches, self.reads = 0, 0

    def search(self, query, **options):
        self.searches += 1
        return {"results": [{"url": "https://x", "title": "Acme news"}]}

    def extract(self, urls, **options):
        self.reads += 1
        return {"results": [{"url": u, "raw_content": "text"} for u in urls]}


def test_no_tavily_request_is_ever_paid_for_twice(monkeypatch):
    """Real problem: find_registration and get_company_card both searched '"Gousto" company'."""
    fake = CountingTavily()
    monkeypatch.setattr(nt, "_client", lambda: fake)
    nt._search('"Gousto" company', max_results=5)
    nt._search('"Gousto" company', max_results=5)
    nt._extract_text("https://gousto.co.uk")
    nt._extract_text("https://gousto.co.uk")
    assert fake.searches == 1 and fake.reads == 1
    totals = usage.today_totals()
    assert totals["tavily_searches"] == 1 and totals["tavily_credits"] == 2 and totals["cached"] == 2


def test_gemini_requests_are_logged_per_model(monkeypatch):
    from whynow.models import Claim
    from tests.test_time_limits import FakeGemini, busy
    FakeGemini({settings.GEMINI_MODEL: busy()}).install(monkeypatch)
    from ai import provider
    provider.generate_structured("hi", Claim)
    assert usage.today_totals()["gemini"] == {settings.GEMINI_MODEL: 1, settings.GEMINI_FALLBACK_MODELS[0]: 1}


# --- One Gemini request for all the outreach ---------------------------------------
def test_long_email_is_trimmed_by_code_not_a_second_ai_request():
    from tests.test_report import FakeAI, build, make_draft, make_email, make_research
    body = ("Hi [First name], saw Acme is hiring an SDR. " + "This middle sentence adds detail about our tool. " * 30
            + "Worth a quick chat? [Your name]")
    r, _ = build(make_research(), make_draft())
    ai = FakeAI(make_email(body=body))
    email = report.write_email(r, ai=ai)
    assert len(ai.prompts) == 1                                     # no hidden 'shorten it' request
    assert email["word_count"] <= settings.EMAIL_MAX_WORDS
    assert email["body"].startswith("Hi [First name], saw Acme is hiring an SDR.")   # the trigger stays
    assert email["body"].endswith("Worth a quick chat? [Your name]")                 # the question and sign-off stay
    assert any("trimmed" in c for c in email["checks"])


# --- Daily limits and owner mode ------------------------------------------------------
@pytest.fixture
def page(monkeypatch):
    import streamlit as st
    st.cache_data.clear()
    # In the test tool every session gets a new made-up IP; a real refresh keeps the same IP and browser
    monkeypatch.setattr(usage, "visitor_from", lambda headers, ip: "same-visitor")
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: MATCHES)
    monkeypatch.setattr(report, "research_company", lambda number, typed, **k: {"number": number})
    monkeypatch.setattr(report, "build_report", lambda research, offer=None, **k: REPORT)
    monkeypatch.setattr(report, "write_email", lambda r, **k: EMAIL)
    return lambda: AppTest.from_file("../app.py", default_timeout=30)


def research(at, typed, pick):
    search(at, typed)
    at.button(key=f"pick_{pick}").click().run()
    return at


def test_third_live_search_is_blocked_even_after_a_refresh(page):
    at = research(page(), "Acme", "123")
    assert 'class="wn-badge strong"' in html(at)
    at = research(page(), "Acme two", "456")                 # a new session = a refresh
    assert 'class="wn-badge strong"' in html(at)
    at = research(page(), "Acme three", "123")
    assert not at.exception
    assert "You've used today's free live searches (2 per day)" in at.info[0].value
    assert [b for b in at.button if b.label == "See sample report"]
    assert 'class="wn-badge' not in html(at)


def test_cached_company_is_free_even_after_the_limit(page):
    research(page(), "Acme", "123")
    research(page(), "Beta", "456")
    at = research(page(), "Acme", "123")                     # same company and wording: from the cache
    assert 'class="wn-badge strong"' in html(at)


def test_total_cap_across_all_visitors(page, monkeypatch):
    monkeypatch.setattr(settings, "TOTAL_LIVE_SEARCHES_PER_DAY", 1)
    usage.record_live_search("someone-else")
    at = research(page(), "Acme", "123")
    assert "reached today's limit of live searches for everyone" in at.info[0].value


def test_owner_has_no_limit_and_sees_research_a_list(page, monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD", "open-sesame")
    monkeypatch.setattr(settings, "VISITOR_LIVE_SEARCHES_PER_DAY", 0)
    at = page()
    at.query_params["owner"] = "1"
    at.run()
    assert not [b for b in at.button if b.label == "Research a list"]   # hidden from visitors
    at.text_input(key="wn_owner_password").input("wrong").run()
    button(at, "Unlock").click().run()
    assert "isn't right" in at.error[0].value
    at.text_input(key="wn_owner_password").input("open-sesame").run()
    button(at, "Unlock").click().run()
    assert [b for b in at.button if b.label == "Research a list"]
    search(at, "Acme")
    at.button(key="pick_123").click().run()
    assert 'class="wn-badge strong"' in html(at)                         # no limit for the owner


def test_owner_area_is_hidden_without_owner_1_in_the_address(page, monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD", "open-sesame")
    for params in ({}, {"owner": "0"}, {"owner": "yes"}, {"sample": "1"}):
        at = page()
        for k, v in params.items():
            at.query_params[k] = v
        at.run()
        assert not at.exception
        assert not [e for e in at.expander if e.label == "Owner"]
        assert not at.get("text_input") or not [t for t in at.text_input if t.key == "wn_owner_password"]
        assert "Owner mode" not in " ".join(c.value for c in at.caption)
    at = page()
    at.query_params["owner"] = "1"
    at.run()
    assert [e for e in at.expander if e.label == "Owner"]                # shown with ?owner=1


def test_owner_mode_is_off_without_a_password(page, monkeypatch):
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    at = page()
    at.query_params["owner"] = "1"
    at.run()
    assert not [e for e in at.expander if e.label == "Owner"]


def test_usage_totals_only_for_the_owner(page, monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD", "pw")
    at = research(page(), "Acme", "123")
    assert "Today's usage" not in " ".join(m.value for m in at.markdown)
    at.session_state["owner"] = True
    at.run()
    assert any("Today's usage" in m.value for m in at.markdown)



def test_visitor_id_is_the_same_after_a_refresh_and_never_stores_the_ip():
    headers = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1", "User-Agent": "Chrome"}
    first = usage.visitor_from(headers, "10.0.0.1")
    again = usage.visitor_from(dict(headers), "10.0.0.2")        # same person, different proxy hop
    assert first == again and "203.0.113.7" not in first and len(first) == 16
    assert usage.visitor_from({"User-Agent": "Firefox"}, "203.0.113.7") != first
