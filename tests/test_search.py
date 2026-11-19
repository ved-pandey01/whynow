"""
Tests for the improved company search: cleaning the text, hiding unrelated names, the Filters
(advanced search), Active only, and the one short message. All with FAKE Companies House replies.
"""

import pytest

from sources import uk_companies_house as ch
from sources import news_tavily as tavily
from tests.test_app import MATCHES, app, search  # noqa: F401
from tests.test_uk_companies_house import FakeResponse


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")


def record_calls(monkeypatch, replies):
    """Fake Companies House: replies maps a path part ('/search/', '/advanced-search/') to its items."""
    calls = []

    def get(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        for part, items in replies.items():
            if part in url:
                return FakeResponse(json_data={"items": items(params) if callable(items) else items})
        return FakeResponse(404)        # company pages (sizes): not needed here
    monkeypatch.setattr(ch.requests, "get", get)
    return calls


# --- 2. Cleaning the search text -------------------------------------------------------
@pytest.mark.parametrize("typed, main", [
    ("BT Local Business – Home Counties North", "BT Local Business"),
    ("BT Local Business - Home Counties North", "BT Local Business"),
    ("BT Local Business — Home Counties North", "BT Local Business"),
    ("Acme (UK) Ltd", "Acme Ltd"),
    ("Acme [Bedford branch]", "Acme"),
    ("Rolls-Royce", "Rolls-Royce"),                 # no spaces round the dash: part of the name
    ("Cognism", "Cognism"),
])
def test_main_name_drops_words_after_a_dash_or_in_brackets(typed, main):
    assert ch.main_name(typed) == main


def test_search_tries_the_main_name_first_then_the_full_text():
    assert ch.search_terms("BT Local Business – Home Counties North") == [
        "bt local business", "bt local business – home counties north"]
    assert ch.search_terms("home-counties.co.uk") == ["home-counties.co.uk", "home-counties"]   # websites unchanged


# --- 4. Only related companies --------------------------------------------------------
def test_main_words_ignore_legal_filler_and_generic_words():
    assert ch.main_words("BT Local Business – Home Counties North") == {"bt"}
    assert ch.main_words("paddle.com") == {"paddle"}
    assert ch.main_words("The Sage Group plc") == {"sage"}
    assert ch.main_words("Local Business Services") == {"local", "business", "services"}   # only generic words: keep them


def test_unrelated_companies_are_never_listed(monkeypatch):
    record_calls(monkeypatch, {"/search/": [
        {"title": "LOCAL BUSINESS SOLUTIONS LTD", "company_number": "1", "company_status": "active"},
        {"title": "BEDFORD BUSINESS CENTRE LIMITED", "company_number": "2", "company_status": "active"},
        {"title": "BT LOCAL BUSINESS BEDFORD LTD", "company_number": "3", "company_status": "active"},
    ]})
    matches = ch.search_companies("BT Local Business – Home Counties North")
    assert [m["company_number"] for m in matches] == ["3"]
    assert matches[0]["close_match"] is True        # judged on the main name, not the words after the dash


# --- 1. Filters: the advanced search --------------------------------------------------
def test_filters_use_the_advanced_search_with_location_industry_and_status(monkeypatch):
    calls = record_calls(monkeypatch, {"/advanced-search/": [
        {"company_name": "BT LOCAL BUSINESS (HOME COUNTIES) LIMITED", "company_number": "9", "company_status": "active",
         "registered_office_address": {"address_line_1": "1 High St", "locality": "Bedford", "postal_code": "MK40 1AA"}},
    ]})
    matches = ch.search_companies("BT Local Business – Home Counties North", location="Bedford",
                                  industry="62012", active_only=True)
    url, params = calls[0]
    assert url.endswith("/advanced-search/companies")
    assert params["company_name_includes"] == "BT Local Business"
    assert params["location"] == "Bedford"
    assert params["sic_codes"] == "62012"
    assert params["company_status"] == "active"
    assert not any("/search/companies" in u and "advanced" not in u for u, _ in calls)   # the normal search isn't used
    assert matches[0]["name"] == "BT LOCAL BUSINESS (HOME COUNTIES) LIMITED"
    assert matches[0]["address"] == "1 High St, Bedford, MK40 1AA"


def test_advanced_search_retries_with_just_the_main_words(monkeypatch):
    def by_name(params):
        return ([{"company_name": "BT BEDFORD LTD", "company_number": "7", "company_status": "active"}]
                if params["company_name_includes"] == "BT" else [])
    calls = record_calls(monkeypatch, {"/advanced-search/": by_name})
    matches = ch.search_companies("BT Local Business", location="Bedford")
    assert [p["company_name_includes"] for u, p in calls if "advanced" in u] == ["BT Local Business", "BT"]
    assert matches[0]["company_number"] == "7"


def test_no_status_filter_when_active_only_is_off(monkeypatch):
    calls = record_calls(monkeypatch, {"/advanced-search/": []})
    ch.search_companies("Acme", location="Bedford", active_only=False)
    assert "company_status" not in calls[0][1]


def test_industry_keyword_becomes_sic_codes():
    codes = ch.sic_codes_for("software")
    assert "62012" in codes and "58290" in codes           # business software development, software publishing
    assert ch.sic_codes_for("62012, 62020") == ["62012", "62020"]
    assert ch.sic_codes_for("") == []
    with pytest.raises(ch.CompaniesHouseError, match="No industry matches"):
        ch.sic_codes_for("zzqxwidgetry")


def test_active_only_hides_closed_companies_in_the_normal_search(monkeypatch):
    record_calls(monkeypatch, {"/search/": [
        {"title": "ACME LIMITED", "company_number": "1", "company_status": "dissolved"},
        {"title": "ACME TRADING LIMITED", "company_number": "2", "company_status": "active"},
    ]})
    assert [m["company_number"] for m in ch.search_companies("Acme", active_only=True)] == ["2"]
    assert {m["company_number"] for m in ch.search_companies("Acme")} == {"1", "2"}


# --- The page --------------------------------------------------------------------------
def test_filters_are_collapsed_with_active_only_on_by_default(app):
    app.run()
    filters = [e for e in app.expander if e.label == "Filters"]
    assert filters and not filters[0].proto.expanded
    assert app.checkbox(key="wn_active_only").value is True
    assert app.text_input(key="wn_location").value == ""


def test_filters_are_passed_to_the_search_and_shown(app, monkeypatch):
    seen = []
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: seen.append(kw) or MATCHES)
    app.run()
    app.text_input(key="wn_location").input("Bedford")
    app.text_input(key="wn_industry").input("software")
    search(app, "Acme")
    assert seen[0] == {"location": "Bedford", "industry": "software", "active_only": True}
    assert any("Filtered by: Bedford · software." in c.value for c in app.caption)


def test_bad_industry_shows_a_friendly_message(app, monkeypatch):
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: None)
    # The real industry check runs before any (fake) search
    monkeypatch.setattr(ch, "search_companies", lambda typed, industry=None, **kw: ch.sic_codes_for(industry) and [])
    app.run()
    app.text_input(key="wn_industry").input("zzqxwidgetry")
    search(app, "Acme")
    assert not app.exception
    assert any("No industry matches" in e.value for e in app.error)


def test_one_short_message_without_the_gousto_example(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [dict(MATCHES[1], close_match=False)])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: None)
    search(app, "Zzqx Widgets Ltd")
    messages = [w.value for w in app.warning] + [i.value for i in app.info]
    assert len(messages) == 1
    assert "on Companies House or in public sources" in messages[0]
    assert "Tip: add a location in Filters, or type the company's website." in messages[0]
    assert "gousto" not in open("app.py").read().split("def search_message")[1].split("def pick_company")[0]
    assert "gousto.co.uk" not in " ".join(messages)


def test_registered_name_lookup_uses_the_main_name(app, monkeypatch):
    asked = []
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: asked.append(brand))
    search(app, "Zzqx Widgets – Home Counties North")
    assert asked == ["Zzqx Widgets"]


def test_filters_skip_the_paid_registered_name_lookup_and_say_so(app, monkeypatch):
    """Found in the health check: a filtered search ran the public-sources lookup (2 Tavily credits),
    which ignores the filters, and blamed the company instead of the filters."""
    asked = []
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: asked.append(brand))
    app.run()
    app.text_input(key="wn_location").input("London")
    app.text_input(key="wn_industry").input("software")
    search(app, "Cognism")
    assert asked == []
    messages = [w.value for w in app.warning]
    assert len(messages) == 1
    assert 'No company named "Cognism" matches your filters (London · software)' in messages[0]
    assert "Try removing a filter" in messages[0]
