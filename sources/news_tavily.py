"""
news_tavily.py — news, "why now" signals and website info, using Tavily search.

Four main jobs:
  1. get_company_card(brand, legal_name, website)  → size, headcount trend, funding, B2B/SaaS
  2. get_recent_news(brand, website, topic)         → up to 8 recent news candidates
  3. get_signals(brand, website, topic)             → hiring, funding, new leader, expansion, layoffs
  4. get_website_text(website)                      → what the company says it does

Run the company card first: its website and topic_hint(card) make the news and signal
searches much more accurate (e.g. 'Paddle' payments, not canoe paddles).

Every item keeps its source link. Results that don't mention the company are thrown away.

Test it from the terminal (uses about 9 Tavily credits):
    .venv/bin/python -m sources.news_tavily "Cognism" "COGNISM LIMITED"
"""

import os
import re
import sys
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv
from tavily import TavilyClient
from tavily import errors as tavily_errors

import settings
from sources.uk_companies_house import is_exact_match

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class NewsError(Exception):
    """
    A problem with Tavily, with a friendly message to show the user.
    temporary=True means only this one request failed (e.g. too slow): other searches can carry on.
    """

    def __init__(self, message, temporary=False):
        super().__init__(message)
        self.temporary = temporary


# ---------------------------------------------------------------------------
# Talking to Tavily
# ---------------------------------------------------------------------------
def _client():
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise NewsError("Tavily API key is missing. Add TAVILY_API_KEY to your .env file.")
    return TavilyClient(api_key=api_key)


def _friendly(error):
    """Turn any Tavily failure into a message a user can act on."""
    if isinstance(error, (tavily_errors.InvalidAPIKeyError, tavily_errors.MissingAPIKeyError)):
        return NewsError("Tavily rejected the API key. Check TAVILY_API_KEY in your .env file.")
    if isinstance(error, tavily_errors.UsageLimitExceededError):
        return NewsError("Your free Tavily credits have run out for this month.")
    if isinstance(error, tavily_errors.TimeoutError) or "timeout" in type(error).__name__.lower():
        return NewsError(f"Tavily took longer than {settings.TAVILY_TIMEOUT_SECONDS} seconds to reply.",
                         temporary=True)
    return NewsError("Couldn't get news from Tavily right now. Please try again.", temporary=True)


def _search(query, **options):
    """Run one Tavily search and return its list of results."""
    try:
        return _client().search(query, timeout=settings.TAVILY_TIMEOUT_SECONDS, **options).get("results", [])
    except NewsError:
        raise
    except Exception as e:
        raise _friendly(e)


def _extract_text(url):
    """Read one web page as plain text. Returns None if the page can't be read."""
    try:
        result = _client().extract(urls=[url], format="text", timeout=settings.TAVILY_TIMEOUT_SECONDS)
    except NewsError:
        raise
    except Exception as e:
        raise _friendly(e)
    pages = result.get("results", [])
    return pages[0].get("raw_content") if pages else None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def to_iso_date(published):
    """'Wed, 13 May 2026 00:00:00 GMT' → '2026-05-13'. None if missing or unreadable."""
    if not published:
        return None
    try:
        return parsedate_to_datetime(published).date().isoformat()
    except (TypeError, ValueError):
        return None


def domain_of(website):
    """'https://www.paddle.com/pricing' → 'paddle.com'."""
    w = (website or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if w.startswith(prefix):
            w = w[len(prefix):]
    return w.split("/")[0] or None


def mentions(brand, *texts):
    """True if the brand appears as a whole word in any of the texts (so 'Sage' doesn't match 'message')."""
    pattern = re.compile(rf"\b{re.escape(brand)}\b", re.IGNORECASE)
    return any(pattern.search(t or "") for t in texts)


def _months_ago(months, today=None):
    return ((today or date.today()) - timedelta(days=30 * months)).isoformat()


def _to_item(result, signal_type=None):
    """Keep only the parts of a search result the report needs."""
    item = {
        "title": result.get("title"),
        "date": to_iso_date(result.get("published_date")),
        "snippet": (result.get("content") or "")[:400],
        "source": result.get("url"),
    }
    if signal_type:
        item["signal_type"] = signal_type
    return item


def _is_company_card(url):
    return "tavily.com/knowledge/company/" in (url or "")


# ---------------------------------------------------------------------------
# 1. Company card (size, headcount trend, funding, B2B/SaaS)
# ---------------------------------------------------------------------------
def parse_company_card(text):
    """Pull the useful facts out of a Tavily company page. Missing facts are None."""
    def field(name):
        # [ \t]* (not \s*) so a blank field doesn't swallow the next line
        m = re.search(rf"^{name}:[ \t]*(\S.*)$", text, re.MULTILINE)
        return m.group(1).strip() if m else None

    headcount, headcount_change = None, None
    m = re.search(r"^Headcount\s*\nNow:\s*([\d,]+)(.*)$", text, re.MULTILINE)
    if m:
        headcount = int(m.group(1).replace(",", ""))
        yearly = re.search(r"([+-]?[\d,]+ yearly \([+-]?[\d.]+%\))", m.group(2))
        headcount_change = yearly.group(1) if yearly else None

    followers = re.search(r"^LinkedIn:.*?([\d,]+) followers", text, re.MULTILINE)
    rounds = field("Rounds")
    areas = field("Business areas")
    return {
        "website": field("Website"),
        "legal_name": field("Legal name"),
        "headquarters": field("Headquarters"),
        "industry": field("Industry"),
        "business_areas": [a.strip() for a in areas.split(",")] if areas else None,
        "business_model": field("Business model"),
        "saas": field("SaaS"),
        "founded": field("Founded"),
        "headcount": headcount,
        "headcount_yearly_change": headcount_change,
        "funding_rounds": int(rounds) if rounds and rounds.isdigit() else None,
        "total_raised": re.findall(r"^Total raised:\s*(.+)$", text, re.MULTILINE) or None,
        "description": (field("Description") or "")[:500] or None,
        "linkedin_followers": int(followers.group(1).replace(",", "")) if followers else None,
        "annual_revenue": field("Annual revenue"),
    }


def card_match_reason(card, legal_name=None, website=None, registered_address=None, card_name=None):
    """
    Is this card about OUR company? Returns how it was confirmed, or None if it can't be.
    Any ONE of these confirms it:
      1. its legal name matches the Companies House name exactly
      2. its website matches the website we were given
      3. its name is in the Companies House name AND its headquarters city is in the
         Companies House registered address (e.g. 'Sage' + Newcastle upon Tyne for THE SAGE GROUP PLC)
    Otherwise it could be a different company with a similar name, so we don't use it.
    """
    if legal_name and card.get("legal_name") and is_exact_match(card["legal_name"], legal_name):
        return "legal name matches Companies House"
    if website and card.get("website") and domain_of(card["website"]) == domain_of(website):
        return "website matches"
    city = (card.get("headquarters") or "").split(",")[0].strip()
    if (card_name and legal_name and registered_address and city
            and mentions(card_name, legal_name) and city.lower() in registered_address.lower()):
        return f"name and head office city ({city}) match Companies House"
    return None


def card_matches(card, legal_name=None, website=None, registered_address=None, card_name=None):
    return card_match_reason(card, legal_name, website, registered_address, card_name) is not None


def _card_searches(brand, website):
    """
    Searches to try, in order. If the brand has several words ('Sage Group'),
    also try the first word ('Sage'): the card is often listed under the short name.
    """
    first = f'"{brand}" {domain_of(website)}' if website else f'"{brand}" company'
    searches = [first]
    short = brand.split()[0] if len(brand.split()) > 1 else None
    if short:
        searches.append(f'"{short}" {domain_of(website)}' if website else f'"{short}" company')
    return searches


def get_company_card(brand, legal_name=None, website=None, registered_address=None):
    """
    Find and read the company's Tavily profile page. Returns None if not found
    or if it can't be confirmed as the same company (better "not found" than wrong).
    A second, shorter search is only run if the first finds no matching card.
    """
    checked = set()
    for query in _card_searches(brand, website):
        for result in _search(query, max_results=5):
            url = result.get("url")
            if not _is_company_card(url) or url in checked:
                continue
            checked.add(url)
            # The full page has more detail (headcount, funding) but drops link text like the website.
            # The search snippet keeps the website. So read both and fill gaps from the snippet.
            card = parse_company_card(_extract_text(url) or "")
            snippet_card = parse_company_card(result.get("content", ""))
            for key, value in snippet_card.items():
                if card.get(key) is None:
                    card[key] = value
            reason = card_match_reason(card, legal_name, website, registered_address,
                                       card_name=result.get("title"))
            if reason:
                card["source"] = url
                card["match_reason"] = reason
                return card
    return None


# ---------------------------------------------------------------------------
# 2. Recent news
# ---------------------------------------------------------------------------
def _is_about(brand, result):
    """
    Keep a result only if the company is named in the HEADLINE.
    Articles that just mention it in passing (e.g. "Top 10 sales tools") are dropped.
    """
    return not _is_company_card(result.get("url")) and mentions(brand, result.get("title"))


def topic_hint(card):
    """
    A few words saying what the company does, taken from its card:
    the first business area ('Merchant of Record Platform'), or else the industry.
    """
    if not card:
        return None
    if card.get("business_areas"):
        return card["business_areas"][0]
    return card.get("industry")


def _with_context(query, website=None, topic=None):
    """
    Add the website and a topic word to a search. This tells Tavily WHICH 'Paddle' we mean.
    Tested on real searches: adding a topic word took Paddle results from 1 in 10
    about the company (the rest were canoes and a video game) to 5 in 10.
    """
    extras = [x for x in (domain_of(website), topic) if x]
    return " ".join([query] + extras)


def get_recent_news(brand, website=None, topic=None, months=settings.NEWS_WINDOW_MONTHS):
    """
    News CANDIDATES with the company in the headline, newest first.
    Brands that are ordinary words ('Paddle', 'Sage') still pick up some unrelated stories,
    so the AI step picks the ones really about this company (and code checks its picks).
    """
    results = _search(
        _with_context(f'"{brand}"', website, topic), topic="news", start_date=_months_ago(months),
        exact_match=True, max_results=10,
    )
    items = [_to_item(r) for r in results if _is_about(brand, r)]
    # Newest first; undated items last
    items.sort(key=lambda i: (i["date"] is None, _neg_date(i["date"])))
    return items[: settings.NEWS_CANDIDATES]


def _neg_date(iso):
    """Helps sort newest first."""
    return -int(iso.replace("-", "")) if iso else 0


# ---------------------------------------------------------------------------
# 3. "Why now" signals
# ---------------------------------------------------------------------------
# One targeted search per signal type. {b} is replaced by the brand name.
SIGNAL_SEARCHES = {
    "hiring_sales": ('"{b}" jobs careers Sales Development Representative BDR Account Executive',
                     "general", settings.FUNDING_WINDOW_MONTHS),
    "funding": ('"{b}" raises funding round investment Series',
                "news", settings.FUNDING_WINDOW_MONTHS),
    "new_sales_leader": ('"{b}" appoints Chief Revenue Officer VP Sales Head of Sales',
                         "news", settings.NEW_LEADER_WINDOW_MONTHS),
    "expansion": ('"{b}" expansion launches new office new market product launch',
                  "news", settings.FUNDING_WINDOW_MONTHS),
    "layoffs": ('"{b}" layoffs redundancies job cuts restructuring',
                "news", settings.FUNDING_WINDOW_MONTHS),
}


def get_signals(brand, website=None, topic=None, out_of_time=lambda: False):
    """
    Possible "why now" signals, grouped by type. These are CANDIDATES:
    the AI step decides which ones really show the signal, and must cite them.
    If out_of_time() becomes True, the remaining searches are skipped (left out of the result).
    """
    signals = {}
    for signal_type, (query, search_topic, months) in SIGNAL_SEARCHES.items():
        if out_of_time():
            break
        results = _search(
            _with_context(query.format(b=brand), website, topic),
            topic=search_topic, start_date=_months_ago(months),
            exact_match=True, max_results=3,
        )
        signals[signal_type] = [_to_item(r, signal_type) for r in results if _is_about(brand, r)]
    return signals


# ---------------------------------------------------------------------------
# 4. Website text
# ---------------------------------------------------------------------------
def get_website_text(website, max_chars=2000):
    """The start of the company's homepage, so the AI knows what they actually do."""
    if not website:
        return None
    url = website if website.startswith("http") else f"https://{domain_of(website)}"
    try:
        text = _extract_text(url)
    except NewsError:
        return None  # some sites block readers; the report just says "not found"
    if not text:
        return None
    return {"text": " ".join(text.split())[:max_chars], "source": url}


# ---------------------------------------------------------------------------
# Run this file directly to try it out
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    brand = sys.argv[1] if len(sys.argv) > 1 else "Cognism"
    legal = sys.argv[2] if len(sys.argv) > 2 else None
    site = sys.argv[3] if len(sys.argv) > 3 else None
    try:
        card = get_company_card(brand, legal_name=legal, website=site)
        print("COMPANY CARD:", "not found (or couldn't confirm it's the same company)" if not card else "")
        if card:
            for k in ("legal_name", "website", "headquarters", "business_model", "saas",
                      "headcount", "headcount_yearly_change", "funding_rounds", "total_raised"):
                print(f"  {k}: {card[k] if card[k] is not None else 'not found'}")
            print(f"  source: {card['source']}")
            site = site or card["website"]
        topic = topic_hint(card)
        print(f"  search topic word: {topic or 'none'}")

        print("\nRECENT NEWS (candidates; the AI picks the 3 really about the company):")
        for n in get_recent_news(brand, site, topic) or []:
            print(f"  - [{n['date'] or 'no date'}] {n['title'][:100]}\n    {n['source']}")

        print("\nSIGNALS (candidates for the AI to check):")
        for signal_type, items in get_signals(brand, site, topic).items():
            print(f"  {signal_type}: {len(items) or 'none found'}")
            for i in items:
                print(f"    - [{i['date'] or 'no date'}] {i['title'][:90]}")

        web = get_website_text(site)
        print("\nWEBSITE:", f"{web['source']}\n  {web['text'][:300]}..." if web else "not found")
    except NewsError as e:
        print(f"Sorry: {e}")
