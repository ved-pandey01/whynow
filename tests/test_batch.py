"""Tests for 'Research a list': reading the CSV, researching each company, ranking. No API calls."""

import pytest

from whynow import batch
from sources import uk_companies_house as ch


@pytest.mark.parametrize("text, names", [
    ("Cognism\nDarktrace\nPaddle\n", ["Cognism", "Darktrace", "Paddle"]),                        # no header
    ("company,notes\nCognism,hot\nPaddle,\n", ["Cognism", "Paddle"]),                             # 'company' column
    ("name,website\nCognism,cognism.com\nPaddle,paddle.com\n", ["cognism.com", "paddle.com"]),    # website preferred
    ("Company\nCognism\n\nCognism\n  \nPaddle\n", ["Cognism", "Paddle"]),                         # blanks and repeats
    ("", []),
])
def test_parse_company_list(text, names):
    assert batch.parse_company_list(text)[0] == names


def test_only_the_first_10_are_used():
    names, left_out = batch.parse_company_list("\n".join(f"Company {i}" for i in range(13)))
    assert len(names) == 10 and left_out == 3


def fake_report(name, fit, signal="Hiring SDRs."):
    return {"company": {"name": name.upper()}, "profile": {"brand": {"value": name}, "headcount": {"value": 300}},
            "fit_score": fit, "signals": [{"text": signal}], "report_id": f"id-{name}"}


def test_research_list_ranks_by_fit_and_keeps_going_after_problems():
    matches = {
        "acme.com": [{"company_number": "1", "name": "ACME", "close_match": True}],
        "zzqx": [{"company_number": "9", "name": "OTHER", "close_match": False}],
        "broken.com": [{"company_number": "2", "name": "BROKEN", "close_match": True}],
        "best.com": [{"company_number": "3", "name": "BEST", "close_match": True}],
    }
    made = {"1": fake_report("Acme", 6), "3": fake_report("Best", 9)}

    def make(match, typed):
        if match["company_number"] == "2":
            raise ch.CompaniesHouseError("Companies House is busy.")
        return made[match["company_number"]]

    seen = []
    rows, reports = batch.research_list(
        ["acme.com", "zzqx", "broken.com", "best.com"], find=matches.get, make=make,
        company_name=lambda r: r["profile"]["brand"]["value"], employees=lambda r: r["profile"]["headcount"]["value"],
        friendly_errors=(ch.CompaniesHouseError,), on_company=lambda n, typed: seen.append(typed))
    assert seen == ["acme.com", "zzqx", "broken.com", "best.com"]          # every company was tried
    assert [r["company"] for r in rows[:2]] == ["Best", "Acme"]            # ranked by fit
    assert rows[0]["report_id"] == "id-Best" and rows[0]["top_reason"] == "Hiring SDRs."
    statuses = {r["typed"]: r["status"] for r in rows}
    assert statuses["zzqx"] == "No close match on Companies House"         # no guessing
    assert statuses["broken.com"] == "Companies House is busy."            # one failure, the rest carried on
    assert len(reports) == 2



def test_batch_finds_brands_registered_under_another_name():
    """Real case: 'Gousto' only matches a dissolved takeaway; its registered name is SCA Investments Limited."""
    dissolved = [{"company_number": "15891239", "name": "LA GOUSTO ELEGANTE LTD", "close_match": True, "status": "dissolved"}]
    sca = {"company_number": "08027386", "name": "SCA INVESTMENTS LIMITED", "close_match": True, "status": "active",
           "registered_as": {"brand": "Gousto"}, "typed": "gousto.co.uk"}
    made = []

    def make(match, typed):
        made.append((match["company_number"], typed))
        return fake_report("Gousto", 5)

    rows, reports = batch.research_list(
        ["Gousto", "Zzqx"], find=lambda t: dissolved if t == "Gousto" else [],
        find_registered=lambda t: sca if t == "Gousto" else None, make=make,
        company_name=lambda r: r["profile"]["brand"]["value"], employees=lambda r: 300)
    assert made == [("08027386", "gousto.co.uk")]                     # the registered company, by its website
    assert rows[0]["company"] == "Gousto (registered as SCA INVESTMENTS LIMITED)"
    assert rows[1]["status"].startswith("No close match on Companies House, and no registered name found")


def test_batch_skips_the_lookup_when_an_active_match_exists():
    calls = []
    batch.research_list(["acme.com"], find=lambda t: [{"company_number": "1", "name": "ACME", "close_match": True,
                                                       "status": "active"}],
                        find_registered=lambda t: calls.append(t), make=lambda m, t: fake_report("Acme", 7),
                        company_name=lambda r: "Acme", employees=lambda r: 1)
    assert calls == []



def test_batch_researches_a_closed_company_when_no_registered_name_is_found():
    closed = [{"company_number": "9", "name": "OLD CO LTD", "close_match": True, "status": "dissolved"}]
    made = []
    rows, _ = batch.research_list(["Old Co"], find=lambda t: closed, find_registered=lambda t: None,
                                  make=lambda m, t: made.append(m["company_number"]) or fake_report("Old Co", 1),
                                  company_name=lambda r: "Old Co", employees=lambda r: None)
    assert made == ["9"] and rows[0]["fit_score"] == 1
