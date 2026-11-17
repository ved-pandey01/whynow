"""
uk_companies_house.py — official UK company data from Companies House.

Three main jobs:
  1. search_companies(name)        → top matches, so the user can pick the right one
  2. get_company_profile(number)   → status, industry, founded date, address
  3. get_recent_directors(number)  → directors appointed recently (a "new leader" signal)

Every fact comes with a "source" link to the public Companies House page,
so the report can always show where it came from.

Test it from the terminal:
    .venv/bin/python -m sources.uk_companies_house "Cognism"
"""

import csv
import os
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv

import settings

# Load API keys from the .env file in the project folder
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_BASE = "https://api.company-information.service.gov.uk"
PUBLIC_SITE = "https://find-and-update.company-information.service.gov.uk"


SIC_CODES_FILE = Path(__file__).resolve().parent.parent / "data" / "sic_codes.csv"


class CompaniesHouseError(Exception):
    """A problem with Companies House, with a friendly message to show the user."""


@lru_cache(maxsize=1)
def _sic_table():
    """Load the official SIC code list once (from resources.companieshouse.gov.uk/sic/)."""
    with open(SIC_CODES_FILE, encoding="utf-8") as f:
        return {row["code"]: row["description"] for row in csv.DictReader(f)}


def sic_description(code):
    """The official meaning of a SIC code, e.g. '70100' → 'Activities of head offices'. None if unknown."""
    return _sic_table().get(str(code))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def clean_query(text):
    """Turn a website like 'https://www.cognism.com/' into a searchable name like 'cognism'."""
    q = _strip_website(text)
    # If it looks like a website (has a dot and no spaces), keep just the name part
    if "." in q and " " not in q:
        q = q.split(".")[0]
    return q.strip()


def _strip_website(text):
    """Lower-case and remove 'https://', 'www.' and any page path: 'https://www.paddle.com/x' → 'paddle.com'."""
    q = (text or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if q.startswith(prefix):
            q = q[len(prefix):]
    return q.split("/")[0].strip()


def search_terms(text):
    """
    What to search for. For a website, try the full domain first ('paddle.com'),
    because many UK companies register under it (e.g. PADDLE.COM MARKET LIMITED),
    then the short name ('paddle').
    """
    full = _strip_website(text)
    short = clean_query(text)
    return [full, short] if full != short else [short]


# Legal endings that aren't really part of a company's name
_LEGAL_WORDS = {"ltd", "limited", "plc", "llp", "the"}
# Extra words that don't help tell companies apart (incl. website endings)
_FILLER_WORDS = _LEGAL_WORDS | {"group", "uk", "holdings", "co", "and", "com", "io", "net", "org"}


def _words(text):
    """Split a name into plain lower-case words: 'The Sage Group plc.' → ['the', 'sage', 'group', 'plc']."""
    cleaned = "".join(c if c.isalnum() else " " for c in (text or "").lower())
    return cleaned.split()


def is_exact_match(query, company_name):
    """True if the name is exactly what was typed, ignoring Ltd/PLC/The: 'Sage Group' = 'THE SAGE GROUP PLC'."""
    typed = [w for w in _words(_strip_website(query)) if w not in _LEGAL_WORDS]
    name = [w for w in _words(company_name) if w not in _LEGAL_WORDS]
    return bool(typed) and typed == name


def is_close_match(query, company_name):
    """True if every meaningful word typed appears as a whole word in the name."""
    typed = [w for w in _words(_strip_website(query)) if w not in _FILLER_WORDS]
    name = set(_words(company_name))
    return bool(typed) and all(w in name for w in typed)


def company_page_url(company_number):
    """The public web page for a company, used as the source link."""
    return f"{PUBLIC_SITE}/company/{company_number}"


def _call_api(path, params=None):
    """Call Companies House and return the JSON. Turns every failure into a friendly error."""
    api_key = os.getenv("COMPANIES_HOUSE_API_KEY")
    if not api_key:
        raise CompaniesHouseError(
            "Companies House API key is missing. Add COMPANIES_HOUSE_API_KEY to your .env file."
        )
    try:
        # Companies House uses the key as a username with a blank password
        response = requests.get(
            API_BASE + path,
            params=params,
            auth=(api_key, ""),
            timeout=settings.COMPANIES_HOUSE_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        raise CompaniesHouseError("Companies House took too long to reply. Please try again.")
    except requests.exceptions.RequestException:
        raise CompaniesHouseError("Couldn't reach Companies House. Check your internet connection.")

    if response.status_code == 401:
        raise CompaniesHouseError("Companies House rejected the API key. Check it in your .env file.")
    if response.status_code == 404:
        return None  # "not found" is normal, not an error
    if response.status_code == 429:
        raise CompaniesHouseError("Too many Companies House requests. Wait a few minutes and try again.")
    if not response.ok:
        raise CompaniesHouseError(f"Companies House had a problem (code {response.status_code}). Try again later.")
    return response.json()


def _format_address(addr):
    """Join the address parts into one line, skipping blanks."""
    if not addr:
        return None
    parts = [addr.get(k) for k in ("address_line_1", "address_line_2", "locality", "postal_code", "country")]
    return ", ".join(p for p in parts if p) or None


# ---------------------------------------------------------------------------
# 1. Search
# ---------------------------------------------------------------------------
def search_companies(name):
    """
    Return the top matches for a company name (or website). Empty list if nothing found.
    Companies House's own ranking can bury the right company (e.g. THE SAGE GROUP PLC is 10th
    for "Sage Group", and small local firms buried the big Howden companies), so we fetch more
    results and re-rank them ourselves: close matches first, active before closed, bigger before
    smaller (from the accounts they file), then exact names. A typed website's exact match always comes first.
    """
    if not clean_query(name):
        raise CompaniesHouseError("Please type a company name or website.")

    matches, seen = [], set()
    terms = search_terms(name)
    domain = terms[0] if len(terms) > 1 else None   # e.g. "paddle.com" is searched as "paddle.com" then "paddle"
    for term in terms:
        data = _call_api("/search/companies", {"q": term, "items_per_page": 100})
        for item in (data or {}).get("items", []):
            number = item.get("company_number")
            if number in seen:
                continue  # already found by an earlier search term
            seen.add(number)
            matches.append({
                "name": item.get("title"),
                "company_number": number,
                "status": item.get("company_status"),
                "founded": item.get("date_of_creation"),
                "address": item.get("address_snippet"),
                "company_type": item.get("company_type"),
                "exact_match": is_exact_match(name, item.get("title")),
                "close_match": is_exact_match(name, item.get("title")) or is_close_match(name, item.get("title")),
                "source": company_page_url(number),
            })

    # Companies House lists similar names roughly alphabetically, so big companies get buried
    # (real problem: HOWDEN GROUP HOLDINGS was 41st of 373 "Howden" results).
    # 1. Quick pre-sort using what the search already tells us (no extra calls).
    matches.sort(key=lambda m: _likely_big_first(m, domain))
    # 2. Look up the real size (from filed accounts) of the top few only, then sort properly.
    for i, m in enumerate(matches):
        checked = i < settings.COMPANY_SIZE_CHECKS and m["close_match"]
        m.update(company_summary(m["company_number"]) if checked else _NO_SUMMARY)
    matches.sort(key=lambda m: _best_first(m, domain))
    return matches[: settings.COMPANY_SEARCH_RESULTS]


def _website_first(m, domain):
    """If a website was typed, the company with it in its name comes first (paddle.com → PADDLE.COM MARKET LIMITED)."""
    return not (domain and domain in (m["name"] or "").lower())


def _likely_big_first(m, domain):
    """Rough order before sizes are known: plc, then names like '... GROUP' or '... HOLDINGS' (often parents)."""
    words = set(_words(m["name"]))
    return (_website_first(m, domain), not m["close_match"], m["status"] != "active",
            m["company_type"] != "plc", not words & {"group", "holdings"}, not m["exact_match"])


def _best_first(m, domain):
    """
    Final order: closed or dissolved companies always last (the page greys them out);
    then the typed website's company, close matches, biggest, exact name. Ties keep the order from before.
    """
    return (m["status"] != "active", _website_first(m, domain), not m["close_match"],
            m["size_rank"], not m["exact_match"])


# Size from the type of accounts a company files with Companies House (official, free).
# Companies House has no staff numbers, but the accounts type is a good guide:
# big groups file "group" accounts; tiny companies file "micro-entity" accounts.
# Lower rank = bigger company = shown first.
ACCOUNTS_SIZES = {
    "group": (1, "Large: parent of a group"),
    "full": (2, "Medium to large: full accounts"),
    "medium": (3, "Medium-sized"),
    "small": (4, "Small"),
    "total-exemption-full": (4, "Small"),
    "total-exemption-small": (4, "Small"),
    "unaudited-abridged": (4, "Small"),
    "audit-exemption-subsidiary": (5, "Subsidiary of a larger group"),
    "filing-exemption-subsidiary": (5, "Subsidiary of a larger group"),
    "micro-entity": (6, "Very small (micro)"),
    "dormant": (8, "Dormant (not trading)"),
}
SIZE_UNKNOWN = 7


def size_from_accounts(accounts_type, company_type=None):
    """(rank, plain-English size) from the accounts type. Public companies (plc) count as large."""
    if company_type == "plc":
        return 1, "Large: public company (plc)"
    return ACCOUNTS_SIZES.get(accounts_type or "", (SIZE_UNKNOWN, None))


_NO_SUMMARY = {"size_rank": SIZE_UNKNOWN, "size": None, "industry": None, "city": None}


def company_summary(company_number):
    """
    Size, main industry and city of one company, for the picker cards.
    One quick call; if it fails, the details are just left blank (it never stops the search).
    """
    try:
        data = _call_api(f"/company/{company_number}") or {}
    except CompaniesHouseError:
        return dict(_NO_SUMMARY)
    accounts_type = (data.get("accounts") or {}).get("last_accounts", {}).get("type")
    rank, size = size_from_accounts(accounts_type, data.get("type"))
    sic_codes = data.get("sic_codes") or []
    city = (data.get("registered_office_address") or {}).get("locality")
    return {"size_rank": rank, "size": size,
            "industry": sic_description(sic_codes[0]) if sic_codes else None,
            "city": city.title() if city and city.isupper() else city}


# ---------------------------------------------------------------------------
# 2. Company profile
# ---------------------------------------------------------------------------
def get_company_profile(company_number):
    """Official details for one company. Missing details are set to None (shown as 'not found')."""
    data = _call_api(f"/company/{company_number}")
    if data is None:
        raise CompaniesHouseError(f"No company found with number {company_number}.")

    source = company_page_url(company_number)
    sic_codes = data.get("sic_codes", [])
    last_accounts_type = data.get("accounts", {}).get("last_accounts", {}).get("type")
    status = data.get("company_status")

    return {
        "name": data.get("company_name"),
        "company_number": data.get("company_number"),
        "status": status,
        "company_type": data.get("type"),
        "founded": data.get("date_of_creation"),
        "closed_on": data.get("date_of_cessation"),
        "address": _format_address(data.get("registered_office_address")),
        "sic_codes": sic_codes,
        "sic_descriptions": [f"{c}: {sic_description(c) or 'description not in the official list'}"
                             for c in sic_codes],
        "is_software_sic": any(code in settings.SOFTWARE_SIC_CODES for code in sic_codes),
        "last_accounts_type": last_accounts_type,
        "has_insolvency_history": data.get("has_insolvency_history", False),
        "not_worth_contacting": is_not_worth_contacting(status, last_accounts_type),
        "source": source,
    }


def is_not_worth_contacting(status, last_accounts_type):
    """True if the company is closed, insolvent or dormant. Decided by code, never by the AI."""
    if (status or "").lower() in settings.CLOSED_COMPANY_STATUSES:
        return True
    if (last_accounts_type or "").lower() == settings.DORMANT_ACCOUNTS_TYPE:
        return True
    return False


# ---------------------------------------------------------------------------
# 3. Recently appointed directors ("new leader" signal)
# ---------------------------------------------------------------------------
def get_recent_directors(company_number, today=None):
    """
    Directors appointed within the new-leader window who haven't resigned.
    Note: these are board directors, not necessarily sales leaders.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=30 * settings.NEW_LEADER_WINDOW_MONTHS)

    data = _call_api(f"/company/{company_number}/officers", {"items_per_page": 100})
    officers = (data or {}).get("items", [])
    source = company_page_url(company_number) + "/officers"

    recent = []
    for officer in officers:
        appointed = officer.get("appointed_on")
        if not appointed or officer.get("resigned_on"):
            continue
        if date.fromisoformat(appointed) >= cutoff:
            recent.append({
                "name": officer.get("name"),
                "role": officer.get("officer_role"),
                "occupation": officer.get("occupation"),
                "appointed_on": appointed,
                "source": source,
            })
    return recent


# ---------------------------------------------------------------------------
# Run this file directly to try it out
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    name = " ".join(sys.argv[1:]) or "Cognism"
    try:
        matches = search_companies(name)
        if not matches:
            print(f'Not found: no UK company matches "{name}".')
            sys.exit(0)

        print(f'Top {len(matches)} matches for "{name}":')
        for i, m in enumerate(matches, 1):
            tag = "exact match" if m["exact_match"] else "close match" if m["close_match"] else "loose match"
            print(f"  {i}. {m['name']} ({m['company_number']}) | {m['status']} | founded {m['founded']} | {tag}")

        if not matches[0]["close_match"]:
            print(f'\nNo close match found for "{name}". Check the spelling, or pick from the list above.')
            sys.exit(0)

        top = matches[0]
        profile = get_company_profile(top["company_number"])
        print(f"\nProfile of top match: {profile['name']}")
        for key in ("status", "founded", "address", "sic_codes", "is_software_sic", "not_worth_contacting"):
            print(f"  {key}: {profile[key] if profile[key] not in (None, []) else 'not found'}")
        print(f"  source: {profile['source']}")

        directors = get_recent_directors(top["company_number"])
        print(f"\nDirectors appointed in the last {settings.NEW_LEADER_WINDOW_MONTHS} months: {len(directors) or 'none found'}")
        for d in directors:
            print(f"  - {d['name']} ({d['role']}, appointed {d['appointed_on']})")
    except CompaniesHouseError as e:
        print(f"Sorry: {e}")
