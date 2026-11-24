"""
Tests for 'Founded' when the registered company is newer than the brand, usually a holding company
(real cases: ONFIDO HOLDINGS LIMITED registered 2021; RIGHTMOVE PLC registered 2007 for a brand from 2000;
HUBOO TECH LIMITED registered 2024 for a brand from 2017). All offline.
"""

from datetime import date

import pytest

from whynow import exports, report
from whynow.ui import components as ui
from tests.test_report import FakeAI, make_draft, make_research


def research_with(name="ACME LTD", registered="2021-10-21", sic=("64209",), accounts=None, card_founded="2012"):
    research = make_research()
    research["profile"].update(name=name, founded=registered, sic_codes=list(sic), last_accounts_type=accounts)
    if card_founded is None:
        research["card"].pop("founded", None)
    else:
        research["card"]["founded"] = card_founded
    return research


def build(research):
    ai = FakeAI(make_draft())
    return report.build_report(research, ai=ai, today=date(2026, 11, 24)), ai


# --- Spotting a holding company --------------------------------------------------------------
@pytest.mark.parametrize("profile, holding", [
    ({"name": "ONFIDO HOLDINGS LIMITED", "sic_codes": ["64209"]}, True),       # holding-company industry code
    ({"name": "RIGHTMOVE PLC", "sic_codes": ["63120"], "last_accounts_type": "group"}, True),   # group accounts
    ({"name": "ACME GROUP LIMITED", "sic_codes": ["62012"]}, True),             # the name says so
    ({"name": "HUBOO TECH LIMITED", "sic_codes": ["62012"]}, False),
    ({"name": "COGNISM LIMITED", "sic_codes": ["63110"], "last_accounts_type": "full"}, False),
])
def test_holding_companies_are_recognised(profile, holding):
    assert report.is_holding_company(profile) is holding


# --- Founded and the note --------------------------------------------------------------------
def test_holding_company_newer_than_the_brand_shows_the_brands_year_and_a_note():
    r, _ = build(research_with(name="ACME HOLDINGS LIMITED", registered="2021-10-21", card_founded="2012"))
    assert r["profile"]["founded"] == {"value": "2012", "source": "S2"}       # from the company profile (a source)
    assert r["profile"]["founded_note"] == "Holding company registered 2021"


def test_group_parent_like_rightmove_plc_gets_the_note():
    r, _ = build(research_with(name="RIGHTMOVE PLC", registered="2007-11-14", sic=("63120",),
                               accounts="group", card_founded="2000"))
    assert r["profile"]["founded"]["value"] == "2000"
    assert r["profile"]["founded_note"] == "Holding company registered 2007"


def test_newer_ordinary_company_gets_a_plain_note():
    r, _ = build(research_with(name="HUBOO TECH LIMITED", registered="2024-12-18", sic=("62012",), card_founded="2017"))
    assert r["profile"]["founded"]["value"] == "2017"
    assert r["profile"]["founded_note"] == "Company registered 2024"


def test_no_note_when_the_dates_agree():
    r, _ = build(research_with(name="COGNISM LIMITED", registered="2015-01-21", sic=("63110",), card_founded="2015"))
    assert r["profile"]["founded"]["value"] == "2015" and r["profile"]["founded_note"] is None


def test_no_note_for_an_ordinary_company_with_no_brand_year():
    r, _ = build(research_with(name="ADEL GLOBAL LTD", registered="2026-03-12", sic=("62012",), card_founded=None))
    assert r["profile"]["founded"]["value"] == "2026" and r["profile"]["founded_note"] is None


def test_holding_company_with_no_brand_year_never_passes_off_its_registration_year():
    """Real case: ONFIDO HOLDINGS LIMITED's profile had no founding year, so the page showed 2021 as 'Founded'."""
    r, _ = build(research_with(name="ONFIDO HOLDINGS LIMITED", registered="2021-10-21", card_founded=None))
    assert r["profile"]["founded"] is None
    assert r["profile"]["founded_note"] == "Holding company registered 2021"


def test_brand_year_can_come_from_a_founded_in_phrase_in_the_sources():
    research = research_with(name="ACME HOLDINGS LIMITED", registered="2021-10-21", card_founded=None)
    website_id = research["sources"].add("website", "Acme website", "https://acme.com", "Acme was founded in 2012 in London.")
    r, _ = build(research)
    assert r["profile"]["founded"]["value"] == "2012"
    assert r["profile"]["founded_note"] == "Holding company registered 2021"
    cited = r["profile"]["founded"]["source"]
    assert next(s for s in r["all_sources"] if s["id"] == cited)["url"] == "https://acme.com"
    assert website_id   # added as a real source, so the year is cited


def test_the_ai_is_told_the_registration_date_is_not_the_founding_date():
    _, ai = build(research_with(name="ACME HOLDINGS LIMITED", registered="2021-10-21", card_founded="2012"))
    assert "Registration note: Holding company registered 2021; the brand dates from 2012" in ai.prompts[0]


# --- Shown on the page and in the PDF ---------------------------------------------------------
def test_page_and_pdf_show_the_note():
    r, _ = build(research_with(name="ACME HOLDINGS LIMITED", registered="2021-10-21", card_founded="2012"))
    by_id = {s["id"]: s for s in r["all_sources"]}
    tiles = ui.stats_row(r, by_id)
    assert "2012" in tiles and "Holding company registered 2021" in tiles
    assert exports.founded_text(r) == "2012 (Holding company registered 2021)"
    assert exports.report_to_pdf(r).startswith(b"%PDF")


def test_not_found_with_the_note_when_no_brand_year():
    r, _ = build(research_with(name="ONFIDO HOLDINGS LIMITED", registered="2021-10-21", card_founded=None))
    by_id = {s["id"]: s for s in r["all_sources"]}
    tiles = ui.stats_row(r, by_id)
    assert "Holding company registered 2021" in tiles and "not found" in tiles
    assert exports.founded_text(r) == "not found (Holding company registered 2021)"


def test_brand_with_a_new_owner_still_uses_companies_house():
    """Cazoo → MOTORS.CO.UK LIMITED keeps its own rule (the card describes the former business)."""
    research = research_with(name="MOTORS.CO.UK LIMITED", registered="2006-10-25", sic=("62090",), card_founded="2018")
    research["former_business"] = {"name": "CAZOO GROUP LTD", "number": "1", "status": "closed", "source": "https://ch/c"}
    r, _ = build(research)
    assert r["profile"]["founded"]["value"] == "2006" and r["profile"]["founded_note"] is None
