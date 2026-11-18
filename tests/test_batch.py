"""Tests for 'Research a list': reading the CSV, researching each company, ranking. No API calls."""

import pytest

import batch
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
