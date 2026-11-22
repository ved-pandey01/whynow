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


def fake_api(monkeypatch, search_items, accounts):
    """Search returns search_items; each /company/{n} returns that company's accounts type and company type."""
    def get(url, params=None, **kwargs):
        if "/search/" in url:
            return FakeResponse(json_data={"items": search_items})
        number = url.rsplit("/", 1)[-1]
        accounts_type, company_type = accounts.get(number, (None, "ltd"))
        return FakeResponse(json_data={"type": company_type, "sic_codes": ["62012"],
                                       "registered_office_address": {"locality": "NEWCASTLE UPON TYNE"},
                                       "accounts": {"last_accounts": {"type": accounts_type}}})
    monkeypatch.setattr(ch.requests, "get", get)


def test_big_companies_are_listed_first(monkeypatch):
    """Real problem: for 'Howden', small local firms filled the picker and the big Howden companies were buried."""
    fake_api(monkeypatch,
             search_items=[{"title": "HOWDEN ACTION GROUP CIC", "company_number": "1", "company_status": "active"},
                           {"title": "HOWDEN BLACKSMITHS LTD", "company_number": "2", "company_status": "active"},
                           {"title": "HOWDEN GROUP HOLDINGS LIMITED", "company_number": "3",
                            "company_status": "active"},
                           {"title": "HOWDEN JOINERY GROUP PLC", "company_number": "4",
                            "company_status": "active", "company_type": "plc"}],
             accounts={"1": ("total-exemption-full", "ltd"), "2": ("micro-entity", "ltd"),
                       "3": ("group", "ltd"), "4": ("group", "plc")})
    matches = ch.search_companies("Howden")
    assert [m["company_number"] for m in matches] == ["4", "3", "1", "2"]
    assert matches[0]["size"] == "Large: public company (plc)"
    assert matches[3]["size"] == "Very small (micro)"
    # Picker cards also get the main industry and the city, from the same call
    assert matches[0]["industry"] == "Business and domestic software development"
    assert matches[0]["city"] == "Newcastle Upon Tyne"


def test_typed_website_company_beats_bigger_lookalikes(monkeypatch):
    fake_api(monkeypatch,
             search_items=[{"title": "PADDLE HOLDINGS LIMITED", "company_number": "1", "company_status": "active"},
                           {"title": "PADDLE.COM MARKET LIMITED", "company_number": "2", "company_status": "active"}],
             accounts={"1": ("group", "plc"), "2": ("full", "ltd")})
    assert ch.search_companies("paddle.com")[0]["name"] == "PADDLE.COM MARKET LIMITED"


@pytest.mark.parametrize("accounts_type, company_type, size", [
    ("group", "ltd", "Large: parent of a group"),
    ("full", "ltd", "Medium to large: full accounts"),
    ("micro-entity", "ltd", "Very small (micro)"),
    ("micro-entity", "plc", "Large: public company (plc)"),
    (None, "ltd", None),                       # no accounts filed yet → size unknown
])
def test_size_from_accounts(accounts_type, company_type, size):
    assert ch.size_from_accounts(accounts_type, company_type)[1] == size


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


def test_closed_companies_go_to_the_bottom_unless_the_name_is_exact(monkeypatch):
    """Closed lookalikes go last; a closed exact-name match ranks by size (live check: 'Carillion plc' fell
    outside the list behind its active subsidiaries)."""
    fake_api(monkeypatch,
             search_items=[{"title": "ACME OLD THINGS LIMITED", "company_number": "1", "company_status": "dissolved"},
                           {"title": "ACME WIDGETS LTD", "company_number": "2", "company_status": "active"},
                           {"title": "ACME PLC", "company_number": "3", "company_status": "liquidation"}],
             accounts={"1": ("group", "ltd"), "2": ("micro-entity", "ltd"), "3": ("group", "plc")})
    assert [m["company_number"] for m in ch.search_companies("Acme")] == ["3", "2", "1"]
    assert [m["company_number"] for m in ch.search_companies("Acme", active_only=True)] == ["2"]


# --- Key people and owners ('More about') --------------------------------------
def test_current_directors_only_newest_first_names_the_right_way_round(monkeypatch):
    fake_reply(monkeypatch, json_data={"items": [
        {"name": "BOWDEN, Laurel Charmaine", "officer_role": "director", "appointed_on": "2019-09-10"},
        {"name": "GALIT, Scott Howard", "officer_role": "director", "appointed_on": "2024-01-02"},
        {"name": "OLD, Gone", "officer_role": "director", "appointed_on": "2015-01-01", "resigned_on": "2020-01-01"},
        {"name": "SECRETARY, Some", "officer_role": "secretary", "appointed_on": "2025-01-01"},
    ]})
    d = ch.get_current_directors("08172165")
    assert [p["name"] for p in d["people"]] == ["Scott Howard Galit", "Laurel Charmaine Bowden"]
    assert d["people"][0]["role"] == "Director" and d["total"] == 2
    assert d["source"].endswith("/company/08172165/officers")


def test_owners_are_current_only_in_plain_english(monkeypatch):
    """Real cases: Darktrace Holdings is owned by Darktrace Limited; Paddle's owners have all ceased."""
    fake_reply(monkeypatch, json_data={"items": [
        {"kind": "corporate-entity-person-with-significant-control", "name": "Darktrace Limited",
         "natures_of_control": ["ownership-of-shares-75-to-100-percent", "right-to-appoint-and-remove-directors"],
         "notified_on": "2021-05-04"},
        {"kind": "individual-person-with-significant-control", "name": "Mr Old Owner",
         "natures_of_control": ["ownership-of-shares-25-to-50-percent"], "ceased_on": "2017-11-20"},
    ]})
    owners = ch.get_owners("08562035")["owners"]
    assert owners == [{"name": "Darktrace Limited", "kind": "Company", "since": "2021-05-04",
                       "control": ["owns 75–100% of shares", "can appoint or remove directors"]}]


@pytest.mark.parametrize("nature, words", [
    ("voting-rights-50-to-75-percent", "holds 50–75% of voting rights"),
    ("ownership-of-shares-25-to-50-percent-as-trust", "owns 25–50% of shares (as a trustee)"),
    ("significant-influence-or-control", "has significant influence or control"),
])
def test_control_in_words(nature, words):
    assert ch.control_in_words(nature) == words



def test_deal_vehicles_rank_after_the_main_company(monkeypatch):
    """Real problem: DARKTRACE MIDCO I LIMITED (group accounts) ranked above DARKTRACE HOLDINGS LIMITED."""
    fake_api(monkeypatch,
             search_items=[{"title": "DARKTRACE MIDCO I LIMITED", "company_number": "15662253", "company_status": "active"},
                           {"title": "DARKTRACE HOLDINGS LIMITED", "company_number": "08562035", "company_status": "active"},
                           {"title": "DARKTRACE BIDCO LIMITED", "company_number": "5", "company_status": "active"}],
             accounts={"15662253": ("group", "ltd"), "08562035": ("full", "ltd"), "5": ("group", "ltd")})
    assert [m["company_number"] for m in ch.search_companies("darktrace.com")][:1] == ["08562035"]
    names = [m["name"] for m in ch.search_companies("Darktrace")]
    assert names.index("DARKTRACE HOLDINGS LIMITED") < names.index("DARKTRACE MIDCO I LIMITED")


def test_typed_vehicle_name_is_not_pushed_down(monkeypatch):
    fake_api(monkeypatch,
             search_items=[{"title": "DARKTRACE HOLDINGS LIMITED", "company_number": "08562035", "company_status": "active"},
                           {"title": "DARKTRACE MIDCO I LIMITED", "company_number": "15662253", "company_status": "active"}],
             accounts={"15662253": ("group", "ltd"), "08562035": ("full", "ltd")})
    assert ch.search_companies("Darktrace Midco I")[0]["company_number"] == "15662253"


@pytest.mark.parametrize("name, vehicle", [
    ("DARKTRACE MIDCO I LIMITED", True), ("ACME BIDCO LTD", True), ("ACME TOPCO LIMITED", True),
    ("ACME HOLDCO LTD", True), ("DARKTRACE HOLDINGS LIMITED", False), ("MIDCOAST LTD", False),
])
def test_is_vehicle(name, vehicle):
    assert ch.is_vehicle(name) is vehicle



def test_lookup_registration_prefers_the_company_number(monkeypatch):
    monkeypatch.setattr(ch, "get_company_profile", lambda n: {
        "name": "SCHOOLING LIMITED", "company_number": n, "status": "active", "founded": "2010-06-01",
        "address": "London", "company_type": "ltd", "source": f"https://ch/{n}"})
    monkeypatch.setattr(ch, "company_summary", lambda n: {"size_rank": 2, "size": "Large", "industry": "Food", "city": "London"})
    m = ch.lookup_registration({"number": "07294209", "name": "Something Else Ltd"})
    assert m["company_number"] == "07294209" and m["name"] == "SCHOOLING LIMITED" and m["close_match"]


def test_lookup_registration_by_name_needs_an_exact_active_match(monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda name: [
        {"name": "SCHOOLSPLASH HOLDINGS LTD", "exact_match": False, "status": "active", "company_number": "1"},
        {"name": "SCHOOLSPLASH LTD", "exact_match": True, "status": "dissolved", "company_number": "2"}])
    assert ch.lookup_registration({"name": "Schoolsplash Ltd"}) is None      # no exact, active match: no guess
    assert ch.lookup_registration(None) is None
