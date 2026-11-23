"""
Tests for 'every company at Companies House is findable': company numbers, fuzzy names, spelling
suggestions, short brands, closed companies, filters that never hide silently, the picker details,
the empty state, and reports for small companies with no news. All with FAKE Companies House replies.
"""

from datetime import date

import pytest

from whynow import report
import ui.components as ui
from sources import news_tavily as tavily
from sources import uk_companies_house as ch
from tests.test_app import MATCHES, app, search  # noqa: F401
from tests.test_report import FakeAI, make_draft
from tests.test_uk_companies_house import FakeResponse


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")


def fake_companies_house(monkeypatch, search=None, companies=None):
    """search: {query: [titles or (title, number, status)]}; companies: {number: profile json}."""
    calls = []

    def get(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        if url.endswith("/search/companies"):
            rows = (search or {}).get(params["q"], [])
            items = []
            for k, row in enumerate(rows):
                title, number, status = row if isinstance(row, tuple) else (row, f"N{abs(hash(row)) % 10**6}", "active")
                items.append({"title": title, "company_number": number, "company_status": status,
                              "address_snippet": "1 High Street, Leeds, LS1 4AB", "date_of_creation": "2015-03-12"})
            return FakeResponse(json_data={"items": items})
        if "/company/" in url and not url.endswith(("/officers", "/persons-with-significant-control", "/filing-history")):
            number = url.rsplit("/", 1)[-1]
            data = (companies or {}).get(number)
            return FakeResponse(json_data=data) if data else FakeResponse(404)
        return FakeResponse(404)
    monkeypatch.setattr(ch.requests, "get", get)
    return calls


# --- Names: case, punctuation and 'Ltd' don't matter ---------------------------------
@pytest.mark.parametrize("typed, name", [
    ("adel global", "ADEL GLOBAL LTD"),
    ("Adelgeo Limted", "ADELGEO LTD"),                 # misspelt suffix
    ("Rolls Royce plc", "ROLLS-ROYCE PLC"),             # punctuation
    ("Marks and Spencer", "MARKS & SPENCER PLC"),
    ("Sainsburys Bank", "SAINSBURY'S BANK PLC"),        # apostrophe
])
def test_exact_name_ignores_case_punctuation_and_legal_endings(typed, name):
    assert ch.is_exact_match(typed, name)


def test_a_real_word_like_limitless_is_not_treated_as_limited():
    assert ch._words("Limitless") == ["limitless"]


# --- Company numbers ---------------------------------------------------------------------
@pytest.mark.parametrize("typed, number", [
    ("17100522", "17100522"), ("445790", "00445790"), ("00445790", "00445790"),
    ("sc 123456", "SC123456"), ("OC401234", "OC401234"), ("Tesco", None), ("3M", None),
])
def test_company_numbers_are_recognised(typed, number):
    assert ch.as_company_number(typed) == number


def test_a_company_number_goes_straight_to_that_company(monkeypatch):
    fake_companies_house(monkeypatch, companies={"17100522": {
        "company_name": "ADEL GLOBAL LTD", "company_number": "17100522", "company_status": "active",
        "date_of_creation": "2026-03-12", "registered_office_address": {"locality": "Leeds", "postal_code": "LS16 9JG"}}})
    found = ch.search_companies("17100522")
    assert [m["name"] for m in found] == ["ADEL GLOBAL LTD"]
    assert found[0]["by_number"] and found[0]["postcode"] == "LS16 9JG" and found[0]["exact_match"]


# --- Fuzzy matching ----------------------------------------------------------------------
def test_misspelling_finds_the_company_through_a_spelling_suggestion(monkeypatch):
    """Companies House's own search isn't fuzzy: 'gregs' never returns GREGGS PLC."""
    fake_companies_house(monkeypatch, search={"gregs": ["GREGS CAFE BAR LTD"][:0],
                                              "greggs": [("GREGGS PLC", "00502851", "active")]})
    found = ch.search_companies("Gregs")
    assert found[0]["name"] == "GREGGS PLC" and found[0]["spelling"] == "greggs"


def test_spelling_suggestions_are_capped(monkeypatch):
    calls = fake_companies_house(monkeypatch, search={})
    assert ch.search_companies("Qwertyuiopasdf") == []
    tried = [p["q"] for u, p in calls if u.endswith("/search/companies")]
    assert 1 < len(tried) <= 2 + ch.settings.SPELLING_CHECKS


def test_short_brand_also_searches_its_group_name(monkeypatch):
    calls = fake_companies_house(monkeypatch, search={"bt group": [("BT GROUP PLC", "04190816", "active")]})
    found = ch.search_companies("BT")
    assert "bt group" in [p["q"] for u, p in calls if u.endswith("/search/companies")]
    assert found[0]["name"] == "BT GROUP PLC"


def test_never_no_results_when_companies_house_returned_names(monkeypatch):
    """If no returned name shares a word with the search, show the nearest ones (flagged), not 'no results'."""
    fake_companies_house(monkeypatch, search={"zzqx": ["ALPHA LTD", "BETA LTD"]})
    monkeypatch.setattr(ch, "_spelling_suggestions", lambda q: [])
    found = ch.search_companies("Zzqx")
    assert [m["name"] for m in found] == ["ALPHA LTD", "BETA LTD"]
    assert all(m["nearest"] and not m["close_match"] for m in found)


def test_closed_exact_match_stays_in_the_list(monkeypatch):
    """Real case: CARILLION PLC (in liquidation) fell behind its active subsidiaries and off the list."""
    names = [(f"CARILLION SERVICES {i} LIMITED", f"1{i:07}", "active") for i in range(12)]
    fake_companies_house(monkeypatch, search={"carillion plc": names + [("CARILLION PLC", "03782379", "liquidation")]})
    everything = ch.search_companies("Carillion plc")
    assert everything[0]["name"] == "CARILLION PLC"
    assert "CARILLION PLC" not in [m["name"] for m in ch.search_companies("Carillion plc", active_only=True)]


def test_postcode_is_read_from_the_address():
    assert ch.postcode_of("14 Hall Rise, Bramhope, Leeds, England, LS16 9JG") == "LS16 9JG"
    assert ch.postcode_of("1 More London Place, London, SE1 2AF") == "SE1 2AF"
    assert ch.postcode_of("No postcode here") is None


# --- The page: picker, messages, filters ------------------------------------------------
def test_picker_never_hides_closed_companies_silently(app, monkeypatch):
    closed = dict(MATCHES[0], name="ACME OLD LTD", company_number="999", status="dissolved", close_match=True)
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: MATCHES + [closed])
    search(app, "Acme")
    assert [b.key for b in app.button if (b.key or "").startswith("pick_")] == ["pick_123", "pick_456"]
    note = " ".join(i.value for i in app.info)
    assert "1 closed company with this name is hidden: ACME OLD LTD (dissolved)" in note
    assert "Turn on 'Include dissolved companies' in Filters" in note


def test_location_or_industry_never_hides_silently(app, monkeypatch):
    other = dict(MATCHES[1], name="ACME YORK LTD", company_number="789")

    def fake(typed, location=None, industry=None, **kw):
        return [MATCHES[0]] if location else [MATCHES[0], other]
    monkeypatch.setattr(ch, "search_companies", fake)
    app.run()
    app.text_input(key="wn_location").input("Leeds")
    search(app, "Acme")
    note = " ".join(i.value for i in app.info)
    assert "Your filters (Leeds) hide 1 other company with this name: ACME YORK LTD (active)" in note


def test_spelling_suggestion_is_explained(app, monkeypatch):
    greggs = dict(MATCHES[0], name="GREGGS PLC", company_number="00502851", spelling="greggs")
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [greggs])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: pytest.fail("no paid lookup"))
    search(app, "Gregs")
    assert any('No company matches "Gregs" exactly. Did you mean "GREGGS"? It\'s listed first.' in i.value
               for i in app.info)


def test_empty_state_suggests_spelling_and_company_number(app, monkeypatch):
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [])
    monkeypatch.setattr(tavily, "find_registration", lambda brand, website=None: None)
    search(app, "Adel Force Limited")
    message = " ".join(w.value for w in app.warning)
    assert 'No company found for "Adel Force Limited"' in message
    assert "Check the spelling, or search by the 8-digit company number" in message


def test_picker_card_shows_number_date_and_postcode(app, monkeypatch):
    m = dict(MATCHES[0], postcode="LS1 4AB")
    monkeypatch.setattr(ch, "search_companies", lambda typed, **kw: [m])
    search(app, "Acme")
    page = " ".join(h.proto.body for h in app.get("html"))
    assert "No. 123 · Incorporated 1 Jan 2015 · LS1 4AB" in page


# --- Small companies: a report from Companies House data alone ---------------------------
def small_company_research(monkeypatch):
    """A sole-director Ltd with no news anywhere: every Tavily search finds nothing."""
    monkeypatch.setattr(tavily, "_search", lambda query, **options: [])
    monkeypatch.setattr(tavily, "_read_pages", lambda urls: [])
    monkeypatch.setattr(ch, "get_company_profile", lambda n: {
        "name": "ADEL GLOBAL LTD", "company_number": n, "status": "active", "company_type": "ltd",
        "founded": "2026-03-12", "closed_on": None, "address": "14 Hall Rise, Leeds, LS16 9JG",
        "sic_codes": ["62012"], "sic_descriptions": ["62012: Business and domestic software development"],
        "is_software_sic": True, "last_accounts_type": None, "has_insolvency_history": False,
        "not_worth_contacting": False, "source": "https://ch/17100522",
        "last_accounts_made_up_to": None, "next_accounts_due": "2027-12-12", "accounts_overdue": False,
        "last_confirmation_statement": None, "confirmation_statement_overdue": False})
    monkeypatch.setattr(ch, "get_recent_directors", lambda n, today=None: [])
    monkeypatch.setattr(ch, "get_current_directors", lambda n: {
        "people": [{"name": "Adel Smith", "role": "Director", "appointed_on": "2026-03-12"}], "total": 1,
        "source": "https://ch/17100522/officers"})
    monkeypatch.setattr(ch, "get_owners", lambda n: {"owners": [], "source": "https://ch/17100522/psc"})
    monkeypatch.setattr(ch, "get_filing_history", lambda n: {
        "filings": [{"date": "2026-03-12", "what": "Incorporation company"}], "total": 1,
        "source": "https://ch/17100522/filing-history"})
    return report.research_company("17100522", "Adel Global Ltd")


def test_small_company_with_no_news_still_gets_a_report(monkeypatch):
    research = small_company_research(monkeypatch)
    r = report.build_report(research, ai=FakeAI(make_draft(news=[], signals=[])), today=date(2026, 11, 22))
    assert r["company"]["name"] == "ADEL GLOBAL LTD"
    assert r["signals"] == [] and r["news"] == []
    filings = r["more_about"]["filings"]
    assert filings["filings"][0]["what"] == "Incorporation company" and filings["source"]
    assert any(s["title"] == "Companies House: filing history" for s in r["all_sources"])
    ch_text = r["all_sources"][0]["content"]
    assert "Next accounts due: 2027-12-12" in ch_text and "62012" in ch_text and "LS16 9JG" in ch_text


def test_small_company_page_says_no_recent_public_signals(monkeypatch):
    research = small_company_research(monkeypatch)
    r = report.build_report(research, ai=FakeAI(make_draft(news=[], signals=[])), today=date(2026, 11, 22))
    by_id = {s["id"]: s for s in r["all_sources"]}
    assert "No recent public signals found." in ui.signals_card(r, by_id)
    cards = ui.more_about_cards(r, by_id)
    assert "Filing history" in cards and "Incorporation company" in cards and "next due 12 Dec 2027" in cards
    assert 'class="wn-mini-card wide"' in cards


def test_filing_history_in_plain_english(monkeypatch):
    monkeypatch.setattr(ch, "_call_api", lambda path, params=None: {"total_count": 9, "items": [
        {"date": "2026-05-01", "description": "confirmation-statement-with-no-updates"},
        {"date": "2025-12-01", "description": "accounts-with-accounts-type-micro-entity"}]})
    f = ch.get_filing_history("17100522")
    assert [x["what"] for x in f["filings"]] == ["Confirmation statement with no updates",
                                                  "Accounts: micro entity"]
    assert f["total"] == 9 and f["source"].endswith("/company/17100522/filing-history")


def test_misspelt_big_brand_goes_first_even_when_small_lookalikes_exist(monkeypatch):
    """Real case: 'Gregs' found only small GREGS … companies, never GREGGS PLC."""
    fake_companies_house(monkeypatch,
                         search={"gregs": [("GREGS COURIERS LIMITED", "11111111", "active")],
                                 "greggs": [("GREGGS PLC", "00502851", "active")]},
                         companies={"00502851": {"company_name": "GREGGS PLC", "type": "plc"},
                                    "11111111": {"company_name": "GREGS COURIERS LIMITED",
                                                 "accounts": {"last_accounts": {"type": "micro-entity"}}}})
    found = ch.search_companies("Gregs")
    assert [m["name"] for m in found] == ["GREGGS PLC", "GREGS COURIERS LIMITED"]
    assert found[0]["spelling"] == "greggs" and not found[1].get("spelling")


def test_no_spelling_check_when_the_exact_company_exists(monkeypatch):
    calls = fake_companies_house(monkeypatch, search={"adelgeo": [("ADELGEO LTD", "13829163", "active")]})
    assert ch.search_companies("Adelgeo")[0]["name"] == "ADELGEO LTD"
    assert [p["q"] for u, p in calls if u.endswith("/search/companies")] == ["adelgeo"]   # no extra calls
