"""Tests for whynow/exports.py: the CSV row and the PDF. Built from a fake report, so no API calls."""

import csv
import io

from whynow import exports
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



def test_csv_has_directors_and_competitors():
    from tests.test_components import MORE
    row = exports.csv_row(dict(REPORT, more_about=MORE))
    assert row["directors"] == "Scott Howard Galit (Director)"
    assert row["competitors"] == "chargebee; paypro global; 2checkout"
    blank = exports.csv_row(REPORT)            # an older report without the section
    assert blank["directors"] == "" and blank["competitors"] == ""


def test_pdf_with_more_about_still_builds():
    from tests.test_components import MORE
    sources = REPORT["sources"] + [{"id": i, "kind": "x", "title": "t", "url": f"https://x/{i}", "date": None, "content": ""}
                                   for i in ("S3",)]
    pdf = exports.report_to_pdf(dict(REPORT, more_about=MORE, sources=sources, all_sources=sources))
    assert pdf.startswith(b"%PDF")


def test_pdf_overview_has_the_short_overview_then_the_read_more_sentences(monkeypatch):
    """'Read more' only adds sentences, so the PDF must print both parts, in order (it used to drop the first)."""
    from fpdf import FPDF
    printed = []
    real = FPDF.multi_cell
    monkeypatch.setattr(FPDF, "multi_cell", lambda self, w, h, txt="", *a, **k: (printed.append(txt), real(self, w, h, txt, *a, **k))[1])
    r = dict(REPORT,
             overview=[{"text": "Acme makes billing software.", "sources": ["S1"]}],
             overview_more=[{"text": "Its products include invoicing tools.", "sources": ["S1"]}])
    exports.report_to_pdf(r, EMAIL)
    overview = next(t for t in printed if "Acme makes billing software." in t)
    assert overview.index("Acme makes billing software.") < overview.index("Its products include invoicing tools.")


def test_pdf_never_strands_a_heading_at_the_bottom_of_a_page(monkeypatch):
    """Each section heading moves to a new page if there isn't room under it."""
    from fpdf import FPDF
    pages_at_heading = []
    real = FPDF.multi_cell
    def spy(self, w, h, txt="", *a, **k):
        out = real(self, w, h, txt, *a, **k)
        if txt.startswith("OUTREACH EMAIL"):
            pages_at_heading.append(self.h - self.b_margin - self.get_y())   # room left below the heading
        return out
    monkeypatch.setattr(FPDF, "multi_cell", spy)
    long = [{"text": "A long sentence about the company and its products. " * 6, "sources": ["S1"]}] * 12
    exports.report_to_pdf(dict(REPORT, overview=long), EMAIL)
    assert pages_at_heading and pages_at_heading[0] > 45
