"""
Tests for the Tavily news and signals lookup.
These use FAKE Tavily replies, so they cost no credits.
"""

import pytest

from sources import news_tavily as nt

# A shortened copy of a real Tavily company page (Cognism, Nov 2026)
SAMPLE_CARD = """# Cognism
Website: www.cognism.com
Legal name: Cognism Limited
Headquarters: London, England, United Kingdom
Industry: Software Development
Business model: B2B
SaaS: yes
Founded: 2015
Description: Cognism is a leader in premium sales intelligence.
Headcount
Now: 450 · -84 yearly (-15.7%) · -2 quarterly · -8 monthly
Funding
Rounds: 11
Total raised: 126.66M USD
Total raised: 4.80M GBP
"""


class FakeTavily:
    """Pretends to be Tavily. Returns whatever search results / page text we give it."""

    def __init__(self, search_results=None, page_text=None, error=None):
        self.search_results = search_results or []
        self.page_text = page_text
        self.error = error
        self.searches = []

    def search(self, query, **options):
        if self.error:
            raise self.error
        self.searches.append(query)
        return {"results": self.search_results}

    def extract(self, urls, **options):
        if self.error:
            raise self.error
        return {"results": [{"url": urls[0], "raw_content": self.page_text}] if self.page_text else []}


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")


def use_fake(monkeypatch, **kwargs):
    fake = FakeTavily(**kwargs)
    monkeypatch.setattr(nt, "_client", lambda: fake)
    return fake


# --- Helpers ------------------------------------------------------------
def test_to_iso_date():
    assert nt.to_iso_date("Wed, 13 May 2026 00:00:00 GMT") == "2026-05-13"
    assert nt.to_iso_date(None) is None
    assert nt.to_iso_date("not a date") is None


@pytest.mark.parametrize("website, expected", [
    ("https://www.paddle.com/pricing", "paddle.com"),
    ("www.cognism.com", "cognism.com"),
    ("", None),
])
def test_domain_of(website, expected):
    assert nt.domain_of(website) == expected


def test_mentions_needs_whole_word():
    assert nt.mentions("Sage", "Sage reports results")
    assert not nt.mentions("Sage", "Send a message")  # 'sage' inside 'message'


# --- Company card -------------------------------------------------------
def test_parse_company_card():
    card = nt.parse_company_card(SAMPLE_CARD)
    assert card["legal_name"] == "Cognism Limited"
    assert card["headcount"] == 450
    assert card["headcount_yearly_change"] == "-84 yearly (-15.7%)"
    assert card["business_model"] == "B2B"
    assert card["saas"] == "yes"
    assert card["funding_rounds"] == 11
    assert card["total_raised"] == ["126.66M USD", "4.80M GBP"]


def test_parse_company_card_with_missing_facts():
    card = nt.parse_company_card("# Tiny Co\nWebsite: tiny.co.uk\n")
    assert card["website"] == "tiny.co.uk"
    assert card["headcount"] is None
    assert card["total_raised"] is None


def test_blank_field_does_not_grab_the_next_line():
    """Real bug: the full page has 'Website: ' blank, and we wrongly read 'Legal name: ...' as the website."""
    card = nt.parse_company_card("Website: \nLegal name: Cognism Limited\n")
    assert card["website"] is None
    assert card["legal_name"] == "Cognism Limited"


def test_card_fills_gaps_from_search_snippet(monkeypatch):
    """The full page drops the website; the search snippet has it."""
    full_page = SAMPLE_CARD.replace("Website: www.cognism.com", "Website: ")
    use_fake(monkeypatch,
             search_results=[{"url": "https://tavily.com/knowledge/company/abc",
                              "content": "Website: www.cognism.com\nLegal name: Cognism Limited"}],
             page_text=full_page)
    card = nt.get_company_card("Cognism", legal_name="COGNISM LIMITED")
    assert card["website"] == "www.cognism.com"  # from the snippet
    assert card["headcount"] == 450              # from the full page


def test_unreadable_website_returns_none_instead_of_crashing(monkeypatch):
    use_fake(monkeypatch, error=RuntimeError("site blocked"))
    assert nt.get_website_text("https://blocked.example.com") is None


def test_card_matches_by_legal_name_or_website():
    card = nt.parse_company_card(SAMPLE_CARD)
    assert nt.card_matches(card, legal_name="COGNISM LIMITED")
    assert nt.card_matches(card, website="https://cognism.com")
    assert not nt.card_matches(card, legal_name="COGNIS CONSULTING LTD")
    assert not nt.card_matches(card)  # nothing to check against → don't trust it


def test_wrong_company_card_is_rejected(monkeypatch):
    """Like the Paddle canoe-hire mix-up: a card for a different company must not be used."""
    use_fake(monkeypatch,
             search_results=[{"url": "https://tavily.com/knowledge/company/abc", "content": ""}],
             page_text=SAMPLE_CARD)
    assert nt.get_company_card("Cognism", legal_name="PADDLE LIMITED") is None


def test_right_company_card_is_used_with_source(monkeypatch):
    use_fake(monkeypatch,
             search_results=[{"url": "https://tavily.com/knowledge/company/abc", "content": ""}],
             page_text=SAMPLE_CARD)
    card = nt.get_company_card("Cognism", legal_name="COGNISM LIMITED")
    assert card["headcount"] == 450
    assert card["source"] == "https://tavily.com/knowledge/company/abc"


# --- News ---------------------------------------------------------------
def test_news_drops_results_that_dont_mention_the_company(monkeypatch):
    use_fake(monkeypatch, search_results=[
        {"title": "Cognism launches new data product", "url": "https://a.com",
         "content": "...", "published_date": "Mon, 02 Nov 2026 09:00:00 GMT"},
        {"title": "Top 10 sales tools", "url": "https://b.com", "content": "Nothing relevant here"},
        {"title": "Cognism", "url": "https://tavily.com/knowledge/company/x", "content": "Cognism"},
    ])
    news = nt.get_recent_news("Cognism")
    assert [n["source"] for n in news] == ["https://a.com"]
    assert news[0]["date"] == "2026-11-02"


def test_news_keeps_only_headline_mentions_newest_first(monkeypatch):
    use_fake(monkeypatch, search_results=[
        {"title": "Sales tools roundup", "url": "https://old-list.com",
         "content": "Cognism is one option", "published_date": "Tue, 10 Nov 2026 00:00:00 GMT"},
        {"title": "Cognism hires CRO", "url": "https://older.com",
         "content": "...", "published_date": "Thu, 01 Oct 2026 00:00:00 GMT"},
        {"title": "Cognism opens Berlin office", "url": "https://newer.com",
         "content": "...", "published_date": "Sun, 01 Nov 2026 00:00:00 GMT"},
    ])
    news = nt.get_recent_news("Cognism")
    # The roundup only mentions Cognism in passing, so it's dropped
    assert [n["source"] for n in news] == ["https://newer.com", "https://older.com"]


def test_website_and_topic_are_added_to_searches(monkeypatch):
    """Real problem: 'Paddle' news was about canoes and a video game until we added a topic word."""
    fake = use_fake(monkeypatch, search_results=[])
    nt.get_recent_news("Paddle", website="https://www.paddle.com", topic="Merchant of Record Platform")
    nt.get_signals("Paddle", website="paddle.com", topic="Merchant of Record Platform")
    assert all(q.endswith("paddle.com Merchant of Record Platform") for q in fake.searches)
    assert len(fake.searches) == 6  # 1 news + 5 signals


def test_topic_hint_prefers_business_area_then_industry():
    assert nt.topic_hint({"business_areas": ["Merchant of Record Platform", "Billing"],
                          "industry": "Software Development"}) == "Merchant of Record Platform"
    assert nt.topic_hint({"business_areas": None, "industry": "Software Development"}) == "Software Development"
    assert nt.topic_hint(None) is None


def test_news_returns_up_to_8_candidates(monkeypatch):
    use_fake(monkeypatch, search_results=[
        {"title": f"Cognism story {i}", "url": f"https://n{i}.com", "content": "..."} for i in range(10)
    ])
    assert len(nt.get_recent_news("Cognism")) == 8


# --- Signals ------------------------------------------------------------
def test_signals_runs_one_search_per_type_and_tags_results(monkeypatch):
    fake = use_fake(monkeypatch, search_results=[
        {"title": "Cognism raises $50m", "url": "https://x.com", "content": "..."},
    ])
    signals = nt.get_signals("Cognism")
    assert set(signals) == {"hiring_sales", "funding", "new_sales_leader", "expansion", "layoffs"}
    assert len(fake.searches) == 5
    assert signals["funding"][0]["signal_type"] == "funding"


# --- Friendly errors ----------------------------------------------------
def test_missing_key_gives_friendly_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY")
    with pytest.raises(nt.NewsError, match="key is missing"):
        nt.get_recent_news("Cognism")


@pytest.mark.parametrize("error, message", [
    (nt.tavily_errors.InvalidAPIKeyError("bad"), "rejected the API key"),
    (nt.tavily_errors.UsageLimitExceededError("limit"), "credits have run out"),
    (RuntimeError("anything else"), "Couldn't get news"),
])
def test_tavily_failures_give_friendly_errors(monkeypatch, error, message):
    use_fake(monkeypatch, error=error)
    with pytest.raises(nt.NewsError, match=message):
        nt.get_recent_news("Cognism")
