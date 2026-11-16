"""
Tests for the Companies House lookup.

These use FAKE replies instead of calling the real API, so they're fast,
free and always give the same result.
Run all tests with:  .venv/bin/python -m pytest
"""

from datetime import date

import pytest

from sources import uk_companies_house as ch


class FakeResponse:
    """Pretends to be a reply from Companies House."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    """Give every test a pretend key, so no real key is ever needed."""
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")


def fake_reply(monkeypatch, status_code=200, json_data=None):
    """Make the next API call return this fake reply."""
    monkeypatch.setattr(ch.requests, "get", lambda *a, **k: FakeResponse(status_code, json_data))


# --- Cleaning what the user types ---------------------------------------
@pytest.mark.parametrize("typed, expected", [
    ("Cognism", "cognism"),
    ("  Paddle  ", "paddle"),
    ("https://www.cognism.com/", "cognism"),
    ("beamery.com/about", "beamery"),
    ("Sage Group plc", "sage group plc"),
])
def test_clean_query(typed, expected):
    assert ch.clean_query(typed) == expected


@pytest.mark.parametrize("typed, expected", [
    ("Cognism", ["cognism"]),
    ("https://www.paddle.com/pricing", ["paddle.com", "paddle"]),
])
def test_search_terms_tries_full_domain_first(typed, expected):
    assert ch.search_terms(typed) == expected


@pytest.mark.parametrize("typed, company_name, expected", [
    ("Cognism", "COGNISM LIMITED", True),
    ("paddle.com", "PADDLE.COM MARKET LIMITED", True),
    ("Sage Group", "THE SAGE GROUP PLC", True),
    ("Zzqx Widgets Ltd", "AESTHETIC WIDGETS LTD", False),  # the made-up test company
    ("Cognism", "COGNIS CONSULTING LTD", False),
    ("Sage Group", "GLOBAL PROSPECT SAGEQUAY INTERNATIONAL GROUP LIMITED", False),  # 'sage' inside a word
])
def test_is_close_match(typed, company_name, expected):
    assert ch.is_close_match(typed, company_name) is expected


@pytest.mark.parametrize("typed, company_name, expected", [
    ("Sage Group", "THE SAGE GROUP PLC.", True),
    ("Sage Group", "SAGE GROUP HOLDINGS LIMITED", False),
    ("Cognism", "COGNISM LIMITED", True),
    ("Greggs", "GREGGS PLC", True),
])
def test_is_exact_match(typed, company_name, expected):
    assert ch.is_exact_match(typed, company_name) is expected


def test_exact_match_beats_companies_house_order(monkeypatch):
    """The real-life Sage problem: the right company was 10th in Companies House's list."""
    fake_reply(monkeypatch, json_data={"items": [
        {"title": "SAGE GROUP DEVELOPMENTS LIMITED", "company_number": "1", "company_status": "active"},
        {"title": "SAGE GROUP MD LTD", "company_number": "2", "company_status": "dissolved"},
        {"title": "THE SAGE GROUP PLC.", "company_number": "02231246", "company_status": "active"},
    ]})
    matches = ch.search_companies("Sage Group")
    assert matches[0]["company_number"] == "02231246"
    assert matches[0]["exact_match"] is True


def test_close_matches_are_listed_first(monkeypatch):
    fake_reply(monkeypatch, json_data={"items": [
        {"title": "AESTHETIC WIDGETS LTD", "company_number": "1"},
        {"title": "ZZQX WIDGETS LTD", "company_number": "2"},
    ]})
    matches = ch.search_companies("Zzqx Widgets")
    assert matches[0]["name"] == "ZZQX WIDGETS LTD"
    assert matches[0]["close_match"] is True
    assert matches[1]["close_match"] is False


def test_empty_search_gives_friendly_error():
    with pytest.raises(ch.CompaniesHouseError, match="type a company name"):
        ch.search_companies("   ")


# --- Search ---------------------------------------------------------------
def test_search_returns_matches_with_source_links(monkeypatch):
    fake_reply(monkeypatch, json_data={"items": [
        {"title": "COGNISM LIMITED", "company_number": "09584831", "company_status": "active",
         "date_of_creation": "2015-05-12", "address_snippet": "London"},
    ]})
    matches = ch.search_companies("Cognism")
    assert matches[0]["name"] == "COGNISM LIMITED"
    assert matches[0]["source"].endswith("/company/09584831")


def test_search_with_no_results_returns_empty_list(monkeypatch):
    fake_reply(monkeypatch, json_data={"items": []})
    assert ch.search_companies("Zzqx Widgets Ltd") == []


# --- Friendly errors ----------------------------------------------------
def test_missing_key_gives_friendly_error(monkeypatch):
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY")
    with pytest.raises(ch.CompaniesHouseError, match="key is missing"):
        ch.search_companies("Cognism")


def test_wrong_key_gives_friendly_error(monkeypatch):
    fake_reply(monkeypatch, status_code=401)
    with pytest.raises(ch.CompaniesHouseError, match="rejected the API key"):
        ch.search_companies("Cognism")


def test_rate_limit_gives_friendly_error(monkeypatch):
    fake_reply(monkeypatch, status_code=429)
    with pytest.raises(ch.CompaniesHouseError, match="Too many"):
        ch.search_companies("Cognism")


def test_unknown_company_number_gives_friendly_error(monkeypatch):
    fake_reply(monkeypatch, status_code=404)
    with pytest.raises(ch.CompaniesHouseError, match="No company found"):
        ch.get_company_profile("00000000")


# --- "Not worth contacting" flag (decided by code, not AI) --------------
@pytest.mark.parametrize("status, accounts_type, expected", [
    ("active", "full", False),
    ("dissolved", "full", True),
    ("liquidation", None, True),
    ("administration", None, True),
    ("active", "dormant", True),
    (None, None, False),
])
def test_not_worth_contacting(status, accounts_type, expected):
    assert ch.is_not_worth_contacting(status, accounts_type) is expected


@pytest.mark.parametrize("code, meaning", [
    ("70100", "Activities of head offices"),          # Sage: NOT software development
    ("62012", "Business and domestic software development"),
    ("99999", "Dormant Company"),
])
def test_sic_codes_use_official_meanings(code, meaning):
    assert ch.sic_description(code) == meaning


def test_unknown_sic_code_is_said_plainly():
    assert ch.sic_description("00000") is None


def test_official_sic_list_is_complete():
    assert len(ch._sic_table()) == 731


def test_profile_includes_sic_meanings(monkeypatch):
    fake_reply(monkeypatch, json_data={"company_name": "THE SAGE GROUP PLC.", "company_number": "02231246",
                                       "company_status": "active", "sic_codes": ["70100", "00000"]})
    profile = ch.get_company_profile("02231246")
    assert profile["sic_descriptions"] == ["70100: Activities of head offices",
                                           "00000: description not in the official list"]


def test_dissolved_profile_is_flagged(monkeypatch):
    fake_reply(monkeypatch, json_data={
        "company_name": "OLD CO LTD", "company_number": "01234567",
        "company_status": "dissolved", "sic_codes": ["62012"],
    })
    profile = ch.get_company_profile("01234567")
    assert profile["not_worth_contacting"] is True
    assert profile["is_software_sic"] is True
    assert profile["address"] is None  # missing → None → shown as "not found"


# --- Recently appointed directors -----------------------------------------
def test_recent_directors_keeps_only_new_and_current(monkeypatch):
    fake_reply(monkeypatch, json_data={"items": [
        {"name": "NEW, Person", "officer_role": "director", "appointed_on": "2026-09-01"},
        {"name": "OLD, Person", "officer_role": "director", "appointed_on": "2019-01-01"},
        {"name": "GONE, Person", "officer_role": "director", "appointed_on": "2026-08-01",
         "resigned_on": "2026-10-01"},
    ]})
    recent = ch.get_recent_directors("09584831", today=date(2026, 11, 16))
    assert [d["name"] for d in recent] == ["NEW, Person"]
    assert recent[0]["source"].endswith("/officers")
