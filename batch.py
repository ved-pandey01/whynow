"""
batch.py — the simple parts of "Research a list": reading the uploaded CSV and ranking the results.
(The page itself, with live progress, is in app.py.)
"""

import csv
import io

import settings

# Column names we recognise in an uploaded list. A website is the most accurate way to find a company.
WEBSITE_COLUMNS = ("website", "domain", "url")
NAME_COLUMNS = ("company", "company name", "company_name", "name", "account", "account name")


def parse_company_list(text, limit=settings.BATCH_MAX_COMPANIES):
    """
    Read company names (or websites) from CSV text.
    Uses a 'website'/'domain' column if there is one, else a 'company'/'name' column, else the first column.
    Returns (names, how_many_were_left_out). Blank cells and repeats are skipped.
    """
    rows = [row for row in csv.reader(io.StringIO(text or "")) if any(cell.strip() for cell in row)]
    if not rows:
        return [], 0
    header = [cell.strip().lower() for cell in rows[0]]
    column = next((header.index(c) for c in WEBSITE_COLUMNS if c in header),
                  next((header.index(c) for c in NAME_COLUMNS if c in header), None))
    if column is None:
        column = 0            # no header: the first column holds the companies
    else:
        rows = rows[1:]       # skip the header row
    names = []
    for row in rows:
        name = row[column].strip() if column < len(row) else ""
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    return names[:limit], max(0, len(names) - limit)


def rank_rows(rows):
    """Best fit first; companies we couldn't research go to the bottom."""
    return sorted(rows, key=lambda row: (row["fit_score"] is None, -(row["fit_score"] or 0)))


def research_list(names, find, make, company_name, employees, friendly_errors=(), on_company=None,
                  find_registered=None):
    """
    Research each company in turn. One failure never stops the rest.
      find(typed)              → Companies House matches
      find_registered(typed)   → if there's no active close match: the company a public source says the
                                 brand is registered as (e.g. Gousto → SCA INVESTMENTS LIMITED), or None
      make(match, typed)       → a finished report (cached for 6 hours by the app)
      on_company(n, typed)     → called before each one, for the progress bar
    Returns (rows ranked by fit, the finished reports).
    """
    rows, reports = [], []
    for n, typed in enumerate(names):
        if on_company:
            on_company(n, typed)
        row = {"typed": typed, "company": typed, "fit_score": None, "employees": None,
               "top_reason": "", "report_id": None, "status": ""}
        try:
            matches = find(typed)
            active = [m for m in matches if m["close_match"] and m.get("status", "active") == "active"]
            match = active[0] if active else (find_registered(typed) if find_registered else None)
            closed = [m for m in matches if m["close_match"]]
            if not match and closed:
                match = closed[0]   # really closed: its report says "not worth contacting"
            if not match:
                row["status"] = ("No close match on Companies House, and no registered name found in public sources"
                                 if find_registered else "No close match on Companies House")
            else:
                if match.get("registered_as"):
                    row["registered_as"] = match["name"]
                r = make(match, match.get("typed") or typed)
                reports.append(r)
                name = company_name(r)
                if row.get("registered_as"):
                    name = f"{name} (registered as {row['registered_as']})"
                row.update(company=name, fit_score=r["fit_score"], employees=employees(r) or None,
                           top_reason=(r["signals"][0]["text"] if r.get("signals") else ""),
                           report_id=r.get("report_id"), status="Done")
        except friendly_errors as e:
            row["status"] = str(e)
        except Exception as e:
            print(f"Unexpected error: {type(e).__name__}: {e}")
            row["status"] = "Something went wrong with this one."
        rows.append(row)
    return rank_rows(rows), reports
