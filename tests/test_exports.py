"""Tests for exports.py: the CSV row and the PDF. Built from a fake report, so no API calls."""

import csv
import io

import exports
from tests.test_app import EMAIL, REPORT


def test_csv_has_crm_columns_and_one_row():
    rows = list(csv.DictReader(io.StringIO(exports.report_to_csv(REPORT))))
    assert list(rows[0]) == exports.CSV_COLUMNS
    assert len(rows) == 1
    row = rows[0]
    assert row["company"] == "Acme" and row["company_number"] == "123"
    assert row["employees"] == "300" and row["fit_score"] == "8"
    assert row["top_reasons_to_reach_out"] == "Raised $5m."


def test_csv_email_is_blank_until_created():
    blank = next(csv.DictReader(io.StringIO(exports.report_to_csv(REPORT))))
    assert blank["email_subject"] == "" and blank["email_body"] == ""
    filled = next(csv.DictReader(io.StringIO(exports.report_to_csv(REPORT, EMAIL))))
    assert filled["email_subject"] == "Your $5m round"


def test_missing_facts_are_left_blank_not_guessed():
    row = exports.csv_row(dict(REPORT, profile=dict(REPORT["profile"], headcount=None)))
    assert row["employees"] == "" and row["website"] == ""


def test_pdf_is_a_real_pdf_with_clickable_sources():
    pdf = exports.report_to_pdf(REPORT, EMAIL)
    assert pdf.startswith(b"%PDF")
    for source in REPORT["sources"]:
        assert f"/URI ({source['url']})".encode() in pdf   # each source is a clickable link


def test_funding_shows_one_main_total():
    assert exports.main_total("318.34M USD, 8.00M GBP") == "318.34M USD (also reported: 8.00M GBP)"
    assert exports.main_total("5M GBP") == "5M GBP"
