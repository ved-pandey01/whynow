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
import re
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv

import cache

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


def main_name(text):
    """
    The main company name, without extra words after a spaced dash or in brackets:
    'BT Local Business – Home Counties North' → 'BT Local Business', 'Acme (UK) Ltd' → 'Acme Ltd'.
    Websites and names like 'Rolls-Royce' (no spaces round the dash) are left alone.
    """
    t = (text or "").strip()
    t = re.sub(r"\s*[(\[][^)\]]*[)\]]", " ", t)        # remove (…) and […]
    t = re.split(r"[(\[]", t)[0]                           # and anything after an unclosed bracket
    t = re.split(r"\s[-–—]+\s|\s*[–—]\s*", t)[0]          # cut at ' - ', or any en/em dash
    return " ".join(t.split()) or (text or "").strip()


def search_terms(text):
    """
    What to search for.
    - A website: the full domain first ('paddle.com'), because many UK companies register under it
      (e.g. PADDLE.COM MARKET LIMITED), then the short name ('paddle').
    - A name: the main name first ('BT Local Business'), then the full text if it was different.
    """
    full = _strip_website(text)
    if "." in full and " " not in full:
        short = clean_query(text)
        return [full, short] if full != short else [short]
    main = main_name(text).lower()
    whole = " ".join((text or "").split()).lower()
    return [main, whole] if whole != main else [main]


# Legal endings that aren't really part of a company's name
_LEGAL_WORDS = {"ltd", "limited", "plc", "llp", "lp", "llc", "inc", "cic", "cio", "the"}
# Extra words that don't help tell companies apart (incl. website endings)
_FILLER_WORDS = _LEGAL_WORDS | {"group", "uk", "holdings", "co", "and", "com", "io", "net", "org"}


def _words(text):
    """
    Split a name into plain lower-case words, ignoring punctuation and case: 'The Sage Group plc.' →
    ['the', 'sage', 'group', 'plc']. Apostrophes join ("Sainsbury's" = "Sainsburys"), '&' means 'and',
    and a misspelt 'Limited' at the end ('Limted', 'Limitd') counts as 'limited'.
    """
    text = (text or "").lower().replace("'", "").replace("\u2019", "").replace("&", " and ")
    words = "".join(c if c.isalnum() else " " for c in text).split()
    if words and words[-1] not in _LEGAL_WORDS and len(words[-1]) >= 5 and _edit_distance(words[-1], "limited") <= 2:
        words[-1] = "limited"
    return words


def _edit_distance(a, b):
    """How many single-letter changes turn a into b (used for 'Limted' and spelling suggestions)."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


# Common words that don't identify a company on their own ("BT Local Business": only "BT" really counts)
GENERIC_WORDS = {
    "business", "businesses", "service", "services", "solution", "solutions", "local", "consulting", "consultancy",
    "consultants", "system", "systems", "management", "international", "global", "national", "enterprise",
    "enterprises", "trading", "partner", "partners", "associates", "company", "companies", "industries",
    "technology", "technologies", "digital", "media", "network", "networks", "centre", "center", "home",
    "counties", "county", "north", "south", "east", "west", "london", "england", "britain", "british", "europe",
    "new", "first", "one", "best", "pro", "&",
}


def main_words(text):
    """
    The words that identify a company: no legal endings, filler or generic words.
    'BT Local Business' → {'bt'}; 'paddle.com' → {'paddle'}. If only generic words were typed, those count.
    """
    words = [w for w in _words(_strip_website(main_name(text))) if w not in _FILLER_WORDS]
    core = [w for w in words if w not in GENERIC_WORDS]
    return set(core or words)


def shares_main_word(query, company_name):
    """True if the company's name has at least one of the main words searched for (so unrelated names are hidden)."""
    return bool(main_words(query) & set(_words(company_name)))


def is_exact_match(query, company_name):
    """True if the name is exactly what was typed, ignoring Ltd/PLC/The: 'Sage Group' = 'THE SAGE GROUP PLC'."""
    full = _strip_website(query)
    typed_text = clean_query(query) if "." in full and " " not in full else full   # 'gocardless.com' → 'gocardless'
    typed = [w for w in _words(typed_text) if w not in _LEGAL_WORDS]
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


CH_DOWN = ("Companies House isn't responding right now. Try again in a few minutes; "
           "the sample report still works.")
NO_INTERNET = "Couldn't connect to the internet. Check your connection and try again."


def _call_api(path, params=None):
    """
    Call Companies House and return the JSON. Turns every failure into a friendly error.
    Good answers (including 'not found') are reused for COMPANIES_HOUSE_CACHE_DAYS; errors are never cached.
    """
    request = [path, params or {}]
    saved = cache.get("companies_house", request, settings.COMPANIES_HOUSE_CACHE_DAYS * 86400)
    if saved is not cache.MISSING:
        return saved
    answer = _call_api_live(path, params)
    cache.put("companies_house", request, answer)
    return answer


def _call_api_live(path, params=None):
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
        raise CompaniesHouseError(CH_DOWN)
    except requests.exceptions.ConnectionError:
        raise CompaniesHouseError(NO_INTERNET)
    except requests.exceptions.RequestException:
        raise CompaniesHouseError(CH_DOWN)

    if response.status_code == 401:
        raise CompaniesHouseError("Companies House rejected the API key. Check it in your .env file.")
    if response.status_code == 404:
        return None  # "not found" is normal, not an error
    if response.status_code == 429:
        raise CompaniesHouseError("Too many Companies House requests. Wait a few minutes and try again.")
    if not response.ok:
        raise CompaniesHouseError(CH_DOWN)
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
def sic_codes_for(industry):
    """
    Turn the Industry filter into SIC codes: '62012' or '62012, 62020' are used as they are;
    a keyword like 'software' finds every official SIC code whose description has that word.
    """
    text = (industry or "").strip()
    if not text:
        return []
    codes = [c for c in re.split(r"[,\s]+", text) if c]
    if all(c.isdigit() for c in codes):
        return codes
    word = re.compile(rf"\b{re.escape(text.lower())}", re.IGNORECASE)
    found = [code for code, meaning in _sic_table().items() if word.search(meaning)]
    if not found:
        raise CompaniesHouseError(f'No industry matches "{text}". Try a simpler word (e.g. software) '
                                  "or a SIC code (e.g. 62012).")
    return found[: settings.MAX_SIC_CODES]


_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b")


def postcode_of(address):
    """The UK postcode at the end of an address line, or None: '1 High St, Bedford, MK40 1AA' → 'MK40 1AA'."""
    found = _POSTCODE.findall((address or "").upper())
    return found[-1] if found else None


def _from_search(item):
    """One result from the normal search, in our own shape."""
    return {"name": item.get("title"), "company_number": item.get("company_number"),
            "status": item.get("company_status"), "founded": item.get("date_of_creation"),
            "address": item.get("address_snippet"), "company_type": item.get("company_type"),
            "postcode": postcode_of(item.get("address_snippet"))}


def _from_advanced_search(item):
    """One result from the advanced search (it names things differently), in our own shape."""
    address = item.get("registered_office_address") or {}
    return {"name": item.get("company_name"), "company_number": item.get("company_number"),
            "status": item.get("company_status"), "founded": item.get("date_of_creation"),
            "address": _format_address(address), "company_type": item.get("company_type"),
            "postcode": address.get("postal_code") or postcode_of(_format_address(address))}


def _advanced_search(query, location, sic_codes):
    """
    Companies House advanced search, used when a Location or Industry filter is set.
    Tries the main name first ('BT Local Business'), then just its main words ('BT') if that finds nothing.
    No status filter here: closed companies are hidden afterwards, so the page can say how many were hidden.
    """
    tries = [query]
    core = " ".join(w for w in re.findall(r"[^\W_]+", query) if w.lower() in main_words(query))   # keeps capitals
    if core and core.lower() != query.lower():
        tries.append(core)
    for name in tries:
        params = {"company_name_includes": name, "size": settings.ADVANCED_SEARCH_SIZE}
        if location:
            params["location"] = location.strip()
        if sic_codes:
            params["sic_codes"] = ",".join(sic_codes)
        items = (_call_api("/advanced-search/companies", params) or {}).get("items", [])
        if items:
            return [_from_advanced_search(i) for i in items]
    return []


# An 8-digit company number (leading zeros optional), or 2 letters and 6 digits (e.g. SC123456, OC401234)
_COMPANY_NUMBER = re.compile(r"^(?:[A-Z]{2}\d{6}|\d{5,8})$")


def as_company_number(text):
    """'445790' or '00445790' → '00445790'; 'sc 123456' → 'SC123456'; anything else → None."""
    t = re.sub(r"[\s-]", "", text or "").upper()
    if not _COMPANY_NUMBER.match(t):
        return None
    return t.zfill(8) if t.isdigit() else t


def _by_number(number):
    """The one company with this number, as a search match (free: one Companies House call). [] if none."""
    data = _call_api(f"/company/{number}")
    if not data:
        return []
    m = {"name": data.get("company_name"), "company_number": data.get("company_number") or number,
         "status": data.get("company_status"), "founded": data.get("date_of_creation"),
         "address": _format_address(data.get("registered_office_address")), "company_type": data.get("type"),
         "postcode": (data.get("registered_office_address") or {}).get("postal_code"),
         "exact_match": True, "close_match": True, "vehicle": False, "by_number": True,
         "source": company_page_url(number)}
    m.update(company_summary(number))
    return [m]


def _spelling_variants(word):
    """Small spelling changes, most likely first: a doubled letter (gregs → greggs), a missing one, a swap."""
    doubled = [word[:i + 1] + word[i] + word[i + 1:] for i in range(len(word))]
    missing = [word[:i] + word[i + 1:] for i in range(len(word))]
    swapped = [word[:i] + word[i + 1] + word[i] + word[i + 2:] for i in range(len(word) - 1)]
    seen, out = {word}, []
    for v in doubled + missing + swapped:
        if v not in seen and len(v) >= 3:
            seen.add(v)
            out.append(v)
    return out


def _spelling_suggestions(query):
    """
    Companies House's search isn't fuzzy ('Gregs' never finds GREGGS PLC), so when nothing matches closely
    we try small spelling changes to the main words and keep companies whose name matches the corrected
    spelling. At most SPELLING_CHECKS quick Companies House calls (free, cached for 7 days).
    """
    words = [w for w in _words(_strip_website(main_name(query))) if w not in _LEGAL_WORDS]
    core = [w for w in words if w in main_words(query) and len(w) >= 4]
    if not words or not core or len(words) > 3:
        return []
    found, calls = [], 0
    for word in core:
        for variant in _spelling_variants(word):
            if calls >= settings.SPELLING_CHECKS:
                return found
            corrected = " ".join(variant if w == word else w for w in words)
            calls += 1
            items = (_call_api("/search/companies", {"q": corrected, "items_per_page": 20}) or {}).get("items", [])
            for item in items:
                title = item.get("title")
                if is_exact_match(corrected, title) or (len(words) > 1 and is_close_match(corrected, title)):
                    found.append(dict(_from_search(item), spelling=corrected))
    return found


def _raw_search(terms):
    return [_from_search(item) for term in terms
            for item in (_call_api("/search/companies", {"q": term, "items_per_page": 100}) or {}).get("items", [])]


def search_companies(name, location=None, industry=None, active_only=False, limit=None):
    """
    The best matches for a company name, website ('greggs.co.uk') or company number ('00502851', 'SC123456'),
    best first: up to `limit` (COMPANY_SEARCH_RESULTS by default). With a Location or Industry filter, the
    Companies House advanced search is used. Each match can carry a flag for the page:
      'spelling' - found through a spelling suggestion ('Gregs' → 'greggs')
      'nearest'  - nothing matched closely, so this is just one of the nearest names Companies House returned
      'by_number'- found directly by its company number
    """
    if not clean_query(name):
        raise CompaniesHouseError("Please type a company name, website or company number.")
    limit = limit or settings.COMPANY_SEARCH_RESULTS
    filtered = bool((location or "").strip() or (industry or "").strip())
    number = as_company_number(name)
    everything = _by_number(number) if number and not filtered else []
    if not everything:
        everything = _ranked(name, location, industry)["matches"]
        if not filtered and not any(m["close_match"] for m in everything):
            suggested = _ranked(name, spelling=True)
            if suggested and suggested["matches"]:
                everything = suggested["matches"]
        elif not filtered and _worth_a_spelling_check(name, everything):
            # 'Gregs' finds small 'GREGS …' companies, but the user almost certainly meant GREGGS PLC:
            # a large company one spelling change away goes first, labelled as a suggestion
            suggested = _ranked(name, spelling=True) or {"matches": []}
            numbers = {m["company_number"] for m in everything}
            big = [m for m in suggested["matches"] if m["size_rank"] <= BIG_COMPANY_RANK and m["company_number"] not in numbers]
            everything = big[:2] + everything
    if active_only:
        everything = [m for m in everything if (m["status"] or "active") == "active"]
    return everything[:limit]


BIG_COMPANY_RANK = 2   # a plc, or a company filing group or full accounts


def _worth_a_spelling_check(name, matches):
    """A one-word search with no exact-name match and no big company in the list: maybe a misspelt big brand."""
    if as_company_number(name) or "." in _strip_website(name):
        return False
    return (len(main_words(name)) == 1 and not any(m["exact_match"] for m in matches)
            and not any(m.get("size_rank", SIZE_UNKNOWN) <= BIG_COMPANY_RANK for m in matches))


def hidden_by_filters(name, shown, include_dissolved=False):
    """Close matches that Location / Industry hid, so the page never hides results silently."""
    numbers = {m["company_number"] for m in shown}
    return [m for m in search_companies(name, active_only=not include_dissolved)
            if m["close_match"] and m["company_number"] not in numbers][:5]


def find_companies(name, location=None, industry=None, include_dissolved=False):
    """
    Everything the picker needs in one dict (used by the search check script):
    matches, hidden_closed, hidden_by_filters, spelling and nearest. See search_companies().
    """
    everything = search_companies(name, location, industry, limit=settings.COMPANY_SEARCH_RESULTS * 3)
    active = [m for m in everything if (m["status"] or "active") == "active"]
    shown = (everything if include_dissolved else active)[: settings.COMPANY_SEARCH_RESULTS]
    filtered = bool((location or "").strip() or (industry or "").strip())
    return {"matches": shown,
            "hidden_closed": [] if include_dissolved else [m for m in everything if m not in active and m["close_match"]][:5],
            "hidden_by_filters": hidden_by_filters(name, everything, include_dissolved) if filtered else [],
            "spelling": next((m["spelling"] for m in shown if m.get("spelling")), None),
            "nearest": bool(shown) and all(m.get("nearest") for m in shown)}


def _ranked(name, location=None, industry=None, spelling=False):
    """Search, keep related names, rank best first, and look up sizes. Returns {'matches', 'spelling', 'nearest'}."""
    terms = search_terms(name)
    is_website = "." in terms[0] and " " not in terms[0]
    domain = terms[0] if is_website and len(terms) > 1 else None   # "paddle.com" is searched, then "paddle"
    query = name if is_website else main_name(name)                   # matching uses the main name
    result = {"matches": [], "spelling": None, "nearest": False}
    if spelling:
        found = _spelling_suggestions(query)
        if not found:
            return None
        result["spelling"] = found[0]["spelling"]
    elif (location or "").strip() or (industry or "").strip():
        found = _advanced_search(clean_query(name) if is_website else query, location, sic_codes_for(industry))
    else:
        core = sorted(main_words(query))
        if not is_website and len(core) == 1 and len(core[0]) <= 4:
            terms = terms + [f"{core[0]} group"]      # very short brands: 'BT' → BT GROUP PLC is buried otherwise
        found = _raw_search(terms)

    matches, seen = [], set()
    for m in found:
        target = m.get("spelling") or query
        if m["company_number"] in seen or not shares_main_word(target, m["name"]):
            continue   # already found by an earlier search, or an unrelated name
        seen.add(m["company_number"])
        m.update(_match_flags(target, name, m))
        matches.append(m)
    if not matches and found and not spelling:
        # Companies House returned names, but none share a main word: show the nearest ones, never "no results"
        result["nearest"] = True
        for m in found:
            if m["company_number"] not in seen:
                seen.add(m["company_number"])
                m.update(_match_flags(query, name, m), nearest=True)
                matches.append(m)
        matches = matches[: settings.COMPANY_SEARCH_RESULTS]

    # Companies House lists similar names roughly alphabetically, so big companies get buried
    # (real problem: HOWDEN GROUP HOLDINGS was 41st of 373 "Howden" results).
    # 1. Quick pre-sort using what the search already tells us (no extra calls).
    matches.sort(key=lambda m: _likely_big_first(m, domain))
    # 2. Look up the real size (from filed accounts) of the top few only, then sort properly.
    for i, m in enumerate(matches):
        checked = i < settings.COMPANY_SIZE_CHECKS and (m["close_match"] or m.get("spelling"))
        m.update(company_summary(m["company_number"]) if checked else _NO_SUMMARY)
    matches.sort(key=lambda m: _best_first(m, domain))
    result["matches"] = matches
    return result


def _match_flags(target, typed, m):
    return {"exact_match": is_exact_match(target, m["name"]),
            "close_match": is_exact_match(target, m["name"]) or is_close_match(target, m["name"]),
            # A finance/holding vehicle (Midco, Bidco…) goes after the main company, unless that's what was typed
            "vehicle": is_vehicle(m["name"]) and not is_vehicle(typed),
            "source": company_page_url(m["company_number"])}


def former_business(brand, registered_name, brand_founded=None):
    """
    Did this brand used to be run by a different company that has since closed? Returns that closed
    company (a search match) or None. Used when a public source says the brand now belongs to a company
    with a different name (live test: Cazoo → MOTORS.CO.UK LIMITED, while CAZOO GROUP LTD is closed).
    All three must be true, so brands that were always registered under another name aren't flagged:
      1. the registered company's name doesn't include the brand ('MOTORS.CO.UK' has no 'cazoo'),
      2. Companies House has a closed company named after the brand, and
      3. it was set up around the brand's own founding year (from its company profile): from 1 year
         before to FORMER_BUSINESS_YEARS after. (Gousto's closed lookalike is an unrelated 2024 takeaway.)
    """
    words = main_words(brand)
    year = str(brand_founded or "")[:4]
    if not words or words & set(_words(registered_name)) or not year.isdigit():
        return None
    try:
        matches = search_companies(brand)
    except CompaniesHouseError:
        return None
    for m in matches:
        founded = (m.get("founded") or "")[:4]
        if (m["close_match"] and (m.get("status") or "active") != "active" and founded.isdigit()
                and -1 <= int(founded) - int(year) <= settings.FORMER_BUSINESS_YEARS):
            return m
    return None


def registration_check_needed(typed, matches):
    """
    Should we look for the registered name in public sources (the website footer, terms, company profile)?
    - Always when there's no active close match (a brand like 'Gousto').
    - For a typed website, also when Companies House alone can't tell which company is behind it: the top
      match doesn't have the website in its name and isn't an exact-name company with filed accounts.
      (Real problems: tide.co → TIDE CONSTRUCTION LIMITED, ravelin.com → RAVELIN LEASING SERVICES,
      huboo.com → a brand-new HUBOO LTD with no accounts.)
    """
    active = [m for m in matches if m["close_match"] and (m.get("status") or "active") == "active"]
    if not active:
        return True
    full = _strip_website(typed)
    if not ("." in full and " " not in full):
        return False                       # a typed name: the user picks from the list as before
    top = active[0]
    has_website = full in (top["name"] or "").lower()
    return not (has_website or (top["exact_match"] and top.get("size")))


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
    Final order: closed or dissolved companies go last unless the name is an exact match (so 'Carillion plc'
    finds CARILLION PLC, in liquidation, when closed companies are included); then the typed website's company,
    close matches, biggest, exact name. Ties keep the order from before.
    """
    return (m["status"] != "active" and not m["exact_match"], _website_first(m, domain), not m["close_match"],
            m.get("vehicle", False), m["size_rank"], not m["exact_match"])


# Words used for finance or holding vehicles created for deals, e.g. DARKTRACE MIDCO I LIMITED
VEHICLE_WORDS = {"midco", "bidco", "topco", "holdco", "finco", "debtco", "propco"}


def is_vehicle(name):
    """True for names like 'DARKTRACE MIDCO I LIMITED': a deal vehicle, not the main trading company."""
    return bool(VEHICLE_WORDS & set(_words(name)))


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
        # Filing record: useful for small companies with no news (all from the same free call)
        "last_accounts_made_up_to": (data.get("accounts") or {}).get("last_accounts", {}).get("made_up_to"),
        "next_accounts_due": (data.get("accounts") or {}).get("next_due"),
        "accounts_overdue": bool((data.get("accounts") or {}).get("overdue")),
        "last_confirmation_statement": (data.get("confirmation_statement") or {}).get("last_made_up_to"),
        "confirmation_statement_overdue": bool((data.get("confirmation_statement") or {}).get("overdue")),
        "has_insolvency_history": data.get("has_insolvency_history", False),
        "not_worth_contacting": is_not_worth_contacting(status, last_accounts_type),
        "source": source,
    }


def filing_in_words(item):
    """A filing in plain English: 'confirmation-statement-with-no-updates' → 'Confirmation statement with no updates'."""
    words = (item.get("description") or item.get("type") or "filing").replace("-", " ").strip()
    words = words.replace("accounts with accounts type ", "accounts: ")
    return words[:1].upper() + words[1:]


def get_filing_history(company_number, limit=5):
    """
    The latest filings at Companies House (free, one call), newest first:
    {"filings": [{"date", "what"}], "total": n, "source": public filing-history page}.
    """
    data = _call_api(f"/company/{company_number}/filing-history", {"items_per_page": limit}) or {}
    filings = [{"date": i.get("date"), "what": filing_in_words(i)} for i in (data.get("items") or [])[:limit]]
    return {"filings": filings, "total": data.get("total_count") or len(filings),
            "source": f"{company_page_url(company_number)}/filing-history"}


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
DIRECTOR_ROLES = {"director": "Director", "corporate-director": "Director (a company)",
                  "llp-member": "LLP member", "llp-designated-member": "LLP designated member"}


def display_name(name):
    """Companies House style 'BOWDEN, Laurel Charmaine' → 'Laurel Charmaine Bowden'. Company names are left as they are."""
    name = (name or "").strip()
    if "," not in name:
        return name
    surname, first = [part.strip() for part in name.split(",", 1)]
    return f"{first} {surname.title()}".strip()


def get_current_directors(company_number, limit=8):
    """
    Current directors (not resigned), newest first, for the 'Key people' card.
    Returns {"people": [...], "total": how many current directors, "source": public officers page}.
    """
    data = _call_api(f"/company/{company_number}/officers", {"items_per_page": 100}) or {}
    current = [o for o in data.get("items", [])
               if not o.get("resigned_on") and o.get("officer_role") in DIRECTOR_ROLES]
    current.sort(key=lambda o: o.get("appointed_on") or "", reverse=True)
    return {
        "people": [{"name": display_name(o.get("name")), "role": DIRECTOR_ROLES[o["officer_role"]],
                    "appointed_on": o.get("appointed_on"), "occupation": o.get("occupation")}
                   for o in current[:limit]],
        "total": len(current),
        "source": company_page_url(company_number) + "/officers",
    }


def control_in_words(nature):
    """'ownership-of-shares-75-to-100-percent' → 'owns 75–100% of shares' (plain English)."""
    via = ""
    for suffix, words in (("-as-trust", " (as a trustee)"), ("-as-firm", " (through a firm)"),
                          ("-as-control-over-trust", " (controls a trust)"), ("-as-control-over-firm", " (controls a firm)")):
        if nature.endswith(suffix):
            nature, via = nature[: -len(suffix)], words
    m = re.match(r"(ownership-of-shares|voting-rights)-(\d+)-to-(\d+)-percent", nature)
    if m:
        what = "owns" if m.group(1) == "ownership-of-shares" else "holds"
        thing = "of shares" if m.group(1) == "ownership-of-shares" else "of voting rights"
        return f"{what} {m.group(2)}–{m.group(3)}% {thing}{via}"
    if nature.startswith("right-to-appoint-and-remove"):
        return "can appoint or remove directors" + via
    if nature.startswith("significant-influence-or-control"):
        return "has significant influence or control" + via
    return nature.replace("-", " ") + via


OWNER_KINDS = {"individual-person-with-significant-control": "Person",
               "corporate-entity-person-with-significant-control": "Company",
               "legal-person-person-with-significant-control": "Organisation",
               "super-secure-person-with-significant-control": "Protected"}


def get_owners(company_number):
    """
    Current people or companies with significant control (Companies House 'PSC' register).
    Ceased owners are left out. Returns {"owners": [...], "source": public PSC page}.
    """
    data = _call_api(f"/company/{company_number}/persons-with-significant-control") or {}
    owners = []
    for item in data.get("items", []):
        if item.get("ceased_on") or item.get("ceased"):
            continue
        kind = OWNER_KINDS.get(item.get("kind"), "Owner")
        owners.append({
            "name": item.get("name") or "Details protected by Companies House",
            "kind": kind,
            "control": [control_in_words(n) for n in item.get("natures_of_control") or []],
            "since": item.get("notified_on"),
        })
    return {"owners": owners, "source": company_page_url(company_number) + "/persons-with-significant-control"}


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


# ---------------------------------------------------------------------------
# Registered company from a brand (when the brand isn't the registered name)
# ---------------------------------------------------------------------------
def lookup_registration(found):
    """
    Turn a registered number or name found in a public source into a Companies House match.
    The number is used first (it's exact). A name only counts if Companies House has an
    exact, active match for it. Returns a match like search_companies() gives, or None.
    """
    if not found:
        return None
    if found.get("number"):
        try:
            p = get_company_profile(found["number"])
        except CompaniesHouseError:
            p = None
        if p:
            match = {"name": p["name"], "company_number": p["company_number"], "status": p["status"],
                     "founded": p["founded"], "address": p["address"], "company_type": p["company_type"],
                     "exact_match": True, "close_match": True, "vehicle": False, "source": p["source"]}
            match.update(company_summary(p["company_number"]))
            return match
    if found.get("name"):
        for m in search_companies(found["name"]):
            if m["exact_match"] and m["status"] == "active":
                return m
    return None
