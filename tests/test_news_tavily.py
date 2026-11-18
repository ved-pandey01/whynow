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
LinkedIn:  · 131,651 followers
Founded: 2015
Description: Cognism is a leader in premium sales intelligence.
Headcount
Now: 450 · -84 yearly (-15.7%) · -2 quarterly · -8 monthly
Funding
Rounds: 11
Total raised: 126.66M USD
Total raised: 4.80M GBP
Financials
Annual revenue: 32.93M GBP
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
    assert card["linkedin_followers"] == 131651
    assert card["annual_revenue"] == "32.93M GBP"


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


# A shortened copy of Sage's real card: no legal name, blank website on the full page
SAGE_CARD = """# Sage
Website:
Headquarters: Newcastle upon Tyne, United Kingdom
Industry: Software Development
Headcount
Now: 15,886 · +1,163 yearly (7.9%) · +270 quarterly
"""
SAGE_ADDRESS = "1 Example Road, Newcastle Upon Tyne, NE1 1AA"  # pretend address in the right city


def test_card_confirmed_by_name_and_head_office_city():
    card = nt.parse_company_card(SAGE_CARD)
    reason = nt.card_match_reason(card, legal_name="THE SAGE GROUP PLC.",
                                  registered_address=SAGE_ADDRESS, card_name="Sage")
    assert reason == "name and head office city (Newcastle upon Tyne) match Companies House"


@pytest.mark.parametrize("card_name, address", [
    ("Sage", "1 High Street, Leeds, LS1 1AA"),        # right name, wrong city
    ("Sagent", SAGE_ADDRESS),                          # right city, different name
])
def test_card_rejected_if_name_or_city_does_not_match(card_name, address):
    card = nt.parse_company_card(SAGE_CARD)
    assert not nt.card_matches(card, legal_name="THE SAGE GROUP PLC.",
                               registered_address=address, card_name=card_name)


def test_short_name_search_is_tried_when_first_search_misses(monkeypatch):
    """Real problem: '"Sage Group" company' found the wrong card; '"Sage" company' finds Sage."""
    searches = []

    def fake_search(query, **options):
        searches.append(query)
        if query == '"Sage" company':
            return [{"url": "https://tavily.com/knowledge/company/sage", "title": "Sage",
                     "content": "Website: www.sage.com"}]
        return [{"url": "https://tavily.com/knowledge/company/other", "title": "SAGE Group", "content": ""}]

    monkeypatch.setattr(nt, "_search", fake_search)
    monkeypatch.setattr(nt, "_extract_text",
                        lambda url: SAGE_CARD if url.endswith("/sage") else "# Other\nHeadquarters: Paris, France")
    card = nt.get_company_card("Sage Group", legal_name="THE SAGE GROUP PLC.", registered_address=SAGE_ADDRESS)
    assert searches == ['"Sage Group" company', '"Sage" company']
    assert card["headcount"] == 15886
    assert card["website"] == "www.sage.com"  # filled in from the search snippet


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
    monkeypatch.setattr(nt, "_client", nt.real_client_for_tests)   # checks the key before calling anything
    monkeypatch.delenv("TAVILY_API_KEY")
    with pytest.raises(nt.NewsError, match="key is missing"):
        nt.get_recent_news("Cognism")


@pytest.mark.parametrize("error, message", [
    (nt.tavily_errors.InvalidAPIKeyError("bad"), "rejected the API key"),
    (nt.tavily_errors.UsageLimitExceededError("limit"), "credits have run out"),
    (RuntimeError("anything else"), "Tavily isn't responding right now"),
])
def test_tavily_failures_give_friendly_errors(monkeypatch, error, message):
    use_fake(monkeypatch, error=error)
    with pytest.raises(nt.NewsError, match=message):
        nt.get_recent_news("Cognism")


# A shortened copy of Paddle's real company page sections (Nov 2026)
PADDLE_EXTRA = """Investors: Notion Capital, FTV Capital, Kohlberg Kravis Roberts & Co., CIBC Innovation Banking
Last round: Debt Financing 2025-08-17 25.00M USD
Products: Merchant of Record Platform, Billing Software, Checkout Software
Open roles: 25
Similar companies
chargebee
stripe
weavr
fastspring
2checkout
"""


def test_card_reads_last_round_open_roles_and_similar_companies():
    card = nt.parse_company_card(SAMPLE_CARD + PADDLE_EXTRA)
    assert card["last_round"] == {"type": "Debt Financing", "date": "2025-08-17", "amount": "25.00M USD"}
    assert card["open_roles"] == 25
    assert card["similar_companies"] == ["chargebee", "stripe", "weavr", "fastspring", "2checkout"]
    assert card["products"][0] == "Merchant of Record Platform"
    assert "CIBC Innovation Banking" in card["investors"]


def test_card_without_extra_sections_gives_none():
    card = nt.parse_company_card(SAMPLE_CARD)
    assert card["last_round"] is None and card["open_roles"] is None and card["similar_companies"] is None


@pytest.mark.parametrize("title, brand, role", [
    ("[hiring_sales] Business Development Representative at Paddle | Dev Global Jobs", "Paddle", "Business Development Representative"),
    ("Darktrace hiring Sales Development Representative - Expression of Interest | LinkedIn", "Darktrace", "Sales Development Representative"),
    ("Paddle - Senior Backend Engineer", "Paddle", "Senior Backend Engineer"),
    ("2 Aktuelle Jobs bei Cognism GmbH | Stepstone", "Cognism", None),    # a job board page, not a role
    ("Paddle Careers", "Paddle", None),
    ("Head of Data at Stripe", "Paddle", None),                            # a different company's job
])
def test_role_from_title(title, brand, role):
    assert nt.role_from_title(title, brand) == role


def test_sales_roles_are_spotted():
    assert nt.is_sales_role("Senior Account Executive") and nt.is_sales_role("SDR")
    assert not nt.is_sales_role("Senior Backend Engineer")



# --- Registered name from public sources ----------------------------------------
@pytest.mark.parametrize("text, found", [
    ("Gousto is a trading name of Schoolsplash Ltd, registered in England.", {"name": "Schoolsplash Ltd"}),
    ("Paddle.com Market Limited is a company registered in England and Wales with company number 8172165.",
     {"number": "08172165", "name": "Paddle.com Market Limited"}),
    ("© 2026 Cognism Ltd. All rights reserved. Company No. 09392705", {"number": "09392705", "name": "Cognism Ltd"}),
    ("Registered name: Acme Widgets Limited. Registered office: London.", {"name": "Acme Widgets Limited"}),
    ("Company Number SC123456", {"number": "SC123456"}),
    ("We love food and recipes.", None),                       # nothing stated: nothing guessed
])
def test_extract_registration(text, found):
    assert nt.extract_registration(text) == found


def test_find_registration_reads_the_website_footer(monkeypatch):
    monkeypatch.setattr(nt, "_search", lambda q, **o: [{"url": "https://tavily.com/knowledge/company/g", "title": "Gousto",
                                                         "content": "Website: www.gousto.co.uk"}])
    monkeypatch.setattr(nt, "_extract_text", lambda url: "# Gousto\nWebsite: www.gousto.co.uk\n")
    read = []

    def pages(urls):
        read.extend(urls)
        return [{"url": "https://gousto.co.uk", "text": "Recipes"},
                {"url": "https://gousto.co.uk/terms", "text": "Schoolsplash Ltd is a company registered in England, company number 07294209."}]
    monkeypatch.setattr(nt, "_extract_pages", pages)
    found = nt.find_registration("Gousto")
    assert found == {"number": "07294209", "name": "Schoolsplash Ltd", "website": "gousto.co.uk",
                     "source": "https://gousto.co.uk/terms", "source_title": "gousto.co.uk website"}
    assert "https://gousto.co.uk/privacy" in read and "https://gousto.co.uk/terms" in read


def test_find_registration_falls_back_to_the_profile_legal_name(monkeypatch):
    monkeypatch.setattr(nt, "_search", lambda q, **o: [{"url": "https://tavily.com/knowledge/company/g", "title": "Gousto",
                                                         "content": ""}])
    monkeypatch.setattr(nt, "_extract_text", lambda url: "# Gousto\nLegal name: Schoolsplash Ltd\n")
    found = nt.find_registration("Gousto")
    assert found["name"] == "Schoolsplash Ltd" and found["source"] == "https://tavily.com/knowledge/company/g"


def test_find_registration_returns_none_when_no_source_says(monkeypatch):
    monkeypatch.setattr(nt, "_search", lambda q, **o: [{"url": "https://news.com/x", "title": "Gousto recipes"}])
    assert nt.find_registration("Gousto") is None
