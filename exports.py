"""
exports.py — download a report as a CSV row (for any CRM) or a clean, printable PDF.

Both use only what's already in the report, so nothing new is made up.
The PDF uses the Inter font in assets/fonts (free SIL Open Font Licence), so £, € and dashes print properly.
"""

import csv
import io
import re
from datetime import date
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONTS = Path(__file__).parent / "assets" / "fonts"
CSV_COLUMNS = ["company", "company_number", "website", "employees", "fit_score",
               "top_reasons_to_reach_out", "email_subject", "email_body", "report_date"]

# PDF colours: the app's blue and emerald, readable on white paper
BLUE, EMERALD, INK, GREY = (37, 99, 235), (5, 150, 105), (15, 23, 42), (100, 116, 139)
FIT_COLOURS = {"strong": (4, 120, 87), "possible": (180, 83, 9), "weak": (185, 28, 28)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _value(r, field):
    """A profile fact's value, or '' if it wasn't found."""
    fact = (r.get("profile") or {}).get(field)
    return fact["value"] if fact else ""


def company_name(r):
    return _value(r, "brand") or r["company"]["name"]


def main_total(value):
    """'318.34M USD, 8.00M GBP' → '318.34M USD (also reported: 8.00M GBP)': one clear total, like the app."""
    parts = [x.strip() for x in str(value or "").split(",") if x.strip()]
    if len(parts) < 2:
        return value
    return f"{parts[0]} (also reported: {', '.join(parts[1:])})"


def report_date(r):
    return r.get("created_on") or r.get("saved_on") or ""


def clean_title(title):
    """Drop internal tags, e.g. '[expansion] Paddle opens…' → 'Paddle opens…'."""
    return re.sub(r"^\[\w+\]\s*", "", title or "")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def csv_row(r, email=None):
    """One CRM-ready row. The email columns are blank until the email has been created."""
    email = email or {}
    return {
        "company": company_name(r),
        "company_number": r["company"]["number"],
        "website": _value(r, "website"),
        "employees": _value(r, "headcount"),
        "fit_score": r["fit_score"],
        "top_reasons_to_reach_out": " | ".join(s["text"] for s in r.get("signals", [])),
        "email_subject": email.get("subject", ""),
        "email_body": email.get("body", ""),
        "report_date": report_date(r),
    }


def rows_to_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def report_to_csv(r, email=None):
    return rows_to_csv([csv_row(r, email)])


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
class _ReportPDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Inter", "", 7.5)
        self.set_text_color(*GREY)
        self.cell(0, 5, f"WhyNow · AI account research agent · page {self.page_no()}", align="C")


def _fit_band(score):
    return "strong" if score >= 7 else "possible" if score >= 4 else "weak"


def report_to_pdf(r, email=None, today=None):
    """A clean A4 report: logo, company, key stats, fit, reasons to reach out, news, email, and every source with its link."""
    by_id = {s["id"]: s for s in r.get("all_sources", [])}
    pdf = _ReportPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("Inter", "", str(FONTS / "Inter-Regular.ttf"))
    pdf.add_font("Inter", "B", str(FONTS / "Inter-Bold.ttf"))
    pdf.add_page()

    def text(words, size=10, bold=False, colour=INK, height=5.2):
        pdf.set_font("Inter", "B" if bold else "", size)
        pdf.set_text_color(*colour)
        pdf.multi_cell(0, height, words, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def heading(words):
        pdf.ln(3)
        text(words.upper(), size=8, bold=True, colour=BLUE, height=5)
        pdf.set_draw_color(226, 232, 240)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(1.5)

    def cite(ids):
        return "  " + " ".join(f"[{i}]" for i in ids) if ids else ""

    def bullet(words):
        text(f"•  {words}", height=5.2)

    # Logo line: blue "Why", emerald "Now"
    pdf.set_font("Inter", "B", 20)
    pdf.set_text_color(*BLUE)
    pdf.cell(pdf.get_string_width("Why"), 9, "Why")
    pdf.set_text_color(*EMERALD)
    pdf.cell(pdf.get_string_width("Now") + 3, 9, "Now")
    pdf.set_font("Inter", "", 8.5)
    pdf.set_text_color(*GREY)
    made = report_date(r) or (today or date.today()).strftime("%-d %b %Y")
    pdf.cell(0, 9, f"AI account research agent · report {made}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # Company
    c = r["company"]
    text(company_name(r), size=17, bold=True, height=8)
    text(f"{c['name']} · Companies House {c['number']} · {c['status'] or 'status not found'}", size=9, colour=GREY)
    # Overview: the full "Read more" version if there is one, else the short one
    overview = r.get("overview_more") or r.get("overview") or ([r["what_they_do"]] if r.get("what_they_do") else [])
    if overview:
        pdf.ln(1.5)
        text(" ".join(c["text"] + cite(c["sources"]) for c in overview))
    if r.get("about_link"):
        pdf.set_font("Inter", "", 8.5)
        pdf.set_text_color(*BLUE)
        pdf.cell(0, 5, f"{r['about_link']['label']}: {r['about_link']['url']}", link=r["about_link"]["url"],
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if r.get("not_worth_contacting"):
        pdf.ln(1.5)
        text("Not worth contacting: " + r["fit_reasons"][0]["text"], bold=True, colour=FIT_COLOURS["weak"])

    # Key stats (only facts we found)
    heading("Key facts")
    funding = main_total(_value(r, "total_raised"))
    facts = [("Employees", _value(r, "headcount") and f"{_value(r, 'headcount'):,}"
              + (f" ({_value(r, 'headcount_change')})" if _value(r, "headcount_change") else "")),
             ("Founded", _value(r, "founded")), ("Funding raised", funding), ("Revenue", _value(r, "revenue")),
             ("Industry", _value(r, "industry")), ("Headquarters", _value(r, "headquarters")),
             ("Website", _value(r, "website"))]
    for label, value in facts:
        pdf.set_font("Inter", "B", 9)
        pdf.set_text_color(*GREY)
        pdf.cell(34, 5.2, label)
        text(str(value) if value else "not found", size=9.5)

    # Fit
    heading("ICP fit")
    band = _fit_band(r["fit_score"])
    label = {"strong": "Strong fit", "possible": "Possible fit", "weak": "Weak fit"}[band]
    text(f"{r['fit_score']}/10  ·  {label}", size=13, bold=True, colour=FIT_COLOURS[band], height=7)
    for reason in r.get("fit_reasons", []):
        bullet(reason["text"] + cite(reason["sources"]))
    if r.get("suggested_role"):
        pdf.ln(1)
        text(f"Who to contact: {r['suggested_role']}. {r.get('role_reason') or ''}", size=9.5)

    # Reasons to reach out
    heading("Why reach out now")
    for s in r.get("signals", []) or []:
        dates = [by_id[i]["date"] for i in s["sources"] if i in by_id and by_id[i].get("date")]
        when = f" ({max(dates)})" if dates else ""
        bullet(s["text"] + when + cite(s["sources"]))
    if not r.get("signals"):
        text("No reasons to reach out now were found.", colour=GREY)

    # News
    if r.get("news"):
        heading("Recent news")
        for n in r["news"]:
            s = by_id.get(n["source"], {})
            bullet(f"{clean_title(s.get('title'))} ({s.get('date') or 'no date'}): {n['why_it_matters']}"
                   + cite([n["source"]]))

    if r.get("not_found"):
        heading("What we couldn't find")
        for gap in r["not_found"]:
            bullet(gap)

    # Email, only if it has been created
    if email:
        heading("Outreach email" + (f" · for the {email['persona']}" if email.get("persona") else ""))
        text(f"Subject: {email['subject']}", bold=True)
        pdf.ln(1)
        text(email["body"])
        if email.get("linkedin_note"):
            pdf.ln(2)
            text("LinkedIn connection note", bold=True, size=9.5)
            text(email["linkedin_note"])
        if email.get("call_opener"):
            pdf.ln(2)
            text("Call opener", bold=True, size=9.5)
            text(email["call_opener"])

    # Sources, each with its clickable link
    heading("Sources")
    for s in r.get("sources", []):
        pdf.set_font("Inter", "B", 8.5)
        pdf.set_text_color(*INK)
        pdf.cell(9, 4.6, s["id"])
        pdf.set_font("Inter", "", 8.5)
        dated = f" ({s['date']})" if s.get("date") else ""
        pdf.multi_cell(0, 4.6, clean_title(s["title"]) + dated, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(pdf.l_margin + 9)
        pdf.set_text_color(*BLUE)
        pdf.set_font("Inter", "", 7.5)
        pdf.multi_cell(0, 4, s["url"], link=s["url"], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(0.8)

    if r.get("ai_model"):
        pdf.ln(2)
        text(f"Report written by {r['ai_model']['name']}. Every fact above cites a numbered source.", size=8, colour=GREY)
    return bytes(pdf.output())
