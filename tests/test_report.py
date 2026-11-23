"""
Tests for whynow/report.py: the code that CHECKS the AI's work.

We use a fake AI that deliberately misbehaves (invents sources, writes long emails)
to prove the checks catch it. No real AI or API calls are made.
"""

from datetime import date

import pytest

from whynow import report
from whynow.models import AIReport, Claim, NewsPick, OutreachDraft, Signal


def make_research(headcount=300, status="active", accounts="full"):
    """A pretend research bundle with 4 sources: S1 Companies House, S2 card, S3 news, S4 signal."""
    sources = report.SourceList()
    sources.add("companies_house", "Companies House: ACME LTD", "https://ch/acme", "Status: active")
    sources.add("company_card", "Company profile: Acme", "https://card/acme", f"Employees: {headcount}")
    sources.add("news", "Acme launches product", "https://news/1", "Acme launched X", "2026-11-01")
    sources.add("signal", "[hiring_sales] SDR at Acme", "https://jobs/1", "Acme is hiring an SDR")
    profile = {"name": "ACME LTD", "company_number": "123", "status": status, "closed_on": None,
               "founded": "2015-01-01", "last_accounts_type": accounts, "source": "https://ch/acme",
               "not_worth_contacting": status != "active" or accounts == "dormant"}
    return {"profile": profile, "card": {"headcount": headcount, "source": "https://card/acme"},
            "website": "https://acme.com", "sources": sources, "warnings": [], "typed": "Acme"}


def make_draft(**changes):
    """A well-behaved AI answer. Tests change parts of it to misbehave."""
    fields = dict(
        overview=[Claim(text="Acme makes sales software.", source_ids=["S2"])],
        overview_more=[Claim(text="Acme was founded in 2015.", source_ids=["S1"])],
        snapshot=[Claim(text="Founded in 2015.", source_ids=["S1"])],
        news=[NewsPick(source_id="S3", why_it_matters="New product means new pipeline goals.")],
        signals=[Signal(signal_type="hiring_sales", text="Hiring an SDR.", source_ids=["S4"])],
        fit_score=8,
        fit_reasons=[Claim(text="300 staff, in the ideal range.", source_ids=["S2"])],
        suggested_role="Head of Sales", role_reason="Owns the SDR team.",
        not_found=["No funding news in the last 12 months"],
    )
    fields.update(changes)
    return AIReport(**fields)


class FakeAI:
    """Returns the answers we give it, in order, and records each prompt."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt, shape):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def make_email(**changes):
    fields = dict(subject="Your new SDR hire",
                  body="Hi [First name], saw Acme is hiring an SDR. Worth a quick chat? [Your name]",
                  linkedin_note="Hi [First name], saw Acme is hiring an SDR. Would be good to connect.",
                  call_opener="[Your name] here, from [Your company]. I saw Acme is hiring an SDR.\nHow are you ramping new reps?",
                  trigger_source_ids=["S4"])
    fields.update(changes)
    return OutreachDraft(**fields)


def news_urls(r):
    """The web addresses of the news shown (source numbers change when the report is renumbered)."""
    by_id = {x["id"]: x for x in r["all_sources"]}
    return [by_id[n["source"]]["url"] for n in r["news"]]


def build(research, *answers):
    ai = FakeAI(*answers)
    return report.build_report(research, ai=ai, today=date(2026, 11, 16)), ai


# --- A normal, well-behaved answer passes through -------------------------
def test_good_answer_passes_all_checks():
    r, ai = build(make_research(), make_draft())
    assert r["fit_score"] == 8
    assert r["news"][0]["source"] == "S3"
    assert r["signals"][0]["type"] == "hiring_sales"
    assert r["checks"] == []
    assert len(ai.prompts) == 1   # the report only: no email until the user asks
    assert "email" not in r
    # Only cited sources are listed under "Sources used"
    assert {s["id"] for s in r["sources"]} == {"S1", "S2", "S3", "S4"}


def test_prompt_contains_sources_offer_and_rules():
    _, ai = build(make_research(), make_draft())
    prompt = ai.prompts[0]
    assert "[S3] Acme launches product" in prompt
    assert "An AI tool that helps SDR teams" in prompt   # default offer
    assert "Do not use your own knowledge" in prompt


# --- Invented sources are removed -----------------------------------------
def test_claims_with_made_up_sources_are_removed():
    r, _ = build(make_research(), make_draft(
        snapshot=[Claim(text="Real fact.", source_ids=["S1"]),
                  Claim(text="Invented fact.", source_ids=["S99"])],
    ))
    assert [c["text"] for c in r["snapshot"]] == ["Real fact."]
    assert any("Invented fact" in c for c in r["checks"])


def test_claim_with_no_sources_is_removed():
    r, _ = build(make_research(), make_draft(overview=[Claim(text="Guess.", source_ids=[])]))
    assert r["what_they_do"] is None and r["overview"] == []  # shown as "not found"


def test_fake_source_ids_are_stripped_but_real_ones_kept():
    r, _ = build(make_research(), make_draft(
        fit_reasons=[Claim(text="Mixed.", source_ids=["S2", "S42"])]))
    assert r["fit_reasons"][0]["sources"] == ["S2"]


def test_news_picks_must_be_real_news_sources():
    r, _ = build(make_research(), make_draft(news=[
        NewsPick(source_id="S1", why_it_matters="Not news, it's Companies House"),
        NewsPick(source_id="S3", why_it_matters="Real news"),
        NewsPick(source_id="S3", why_it_matters="Duplicate"),
    ]))
    assert [n["source"] for n in r["news"]] == ["S3"]


def research_with_signal_story(story_date):
    """make_research plus S5: a dated expansion story found by the signal search, not the news search."""
    research = make_research()
    research["sources"].add("signal", "[expansion] Acme opens in Sweden", "https://news/sweden",
                            "Acme expands to Sweden", story_date)
    return research


def test_recent_story_used_as_signal_also_appears_in_news():
    """Real problem (Howden): 'Recent news' said none found, but 'Why now' showed a 20 Oct news story."""
    r, _ = build(research_with_signal_story("2026-10-20"), make_draft(
        news=[],
        signals=[Signal(signal_type="expansion", text="Expanding into Sweden.", source_ids=["S5"])]))
    assert news_urls(r) == ["https://news/sweden"]
    assert r["news"][0]["why_it_matters"] == "Expanding into Sweden."


def test_sources_are_numbered_in_order_with_no_gaps():
    """Real problem: the Sources list read S1, S3, S5… because unused sources kept their numbers."""
    research = make_research()
    research["sources"].add("news", "Unused story", "https://news/unused", "...", "2026-11-02")   # S5, never cited
    research["sources"].add("signal", "[funding] Acme raises £5m", "https://f", "Acme raised £5m", "2026-11-03")  # S6
    r, _ = build(research, make_draft(
        snapshot=[], news=[], fit_reasons=[Claim(text="Good fit.", source_ids=["S1"])],
        signals=[Signal(signal_type="funding", text="Raised £5m.", source_ids=["S6"])]))
    shown = [x["id"] for x in r["sources"]]
    assert shown == [f"S{n}" for n in range(1, len(shown) + 1)]           # S1, S2, S3… no gaps
    by_id = {x["id"]: x for x in r["all_sources"]}
    funding = r["signals"][0]
    assert by_id[funding["sources"][0]]["url"] == "https://f"            # the tag follows its source
    assert funding["sources"][0] in shown
    assert "https://news/unused" not in [x["url"] for x in r["sources"]]


def test_old_events_are_left_out_of_reasons_to_reach_out():
    """Real problem (Darktrace): 'Expansion · 9 months ago' was shown as a reason to reach out."""
    research = research_with_signal_story("2026-02-03")   # 9 months before the report date
    r, _ = build(research, make_draft(
        news=[], signals=[Signal(signal_type="expansion", text="Launched SECURE AI.", source_ids=["S5"])]))
    assert ai_signals(r) == []
    assert any("older than 6 months" in c for c in r["checks"])


def test_old_search_results_never_reach_the_ai(monkeypatch):
    monkeypatch.setattr(report.ch, "get_company_profile", lambda n: {
        "name": "ACME LTD", "company_number": "1", "status": "active", "closed_on": None, "founded": "2015-01-01",
        "address": "Leeds", "sic_codes": [], "sic_descriptions": [], "last_accounts_type": "full", "company_type": "ltd",
        "source": "https://ch/acme", "not_worth_contacting": False})
    monkeypatch.setattr(report.ch, "get_recent_directors", lambda n: [])
    monkeypatch.setattr(report.ch, "get_current_directors", lambda n: {"people": [], "total": 0, "source": "https://ch/o"})
    monkeypatch.setattr(report.ch, "get_owners", lambda n: {"owners": [], "source": "https://ch/p"})
    monkeypatch.setattr(report.ch, "get_filing_history", lambda n: {"filings": [], "total": 0, "source": "https://ch/filings"})   # fake: no real API calls in tests
    monkeypatch.setattr(report.tavily, "get_job_listings", lambda *a, **k: [])
    monkeypatch.setattr(report.tavily, "get_company_card", lambda *a, **k: None)
    monkeypatch.setattr(report.tavily, "get_website_text", lambda w: None)
    monkeypatch.setattr(report.tavily, "get_recent_news", lambda *a, **k: [])
    monkeypatch.setattr(report.tavily, "get_signals", lambda *a, **k: {"expansion": [
        {"title": "Old launch", "source": "https://old", "snippet": "...", "date": "2025-01-01"},
        {"title": "New office", "source": "https://new", "snippet": "...", "date": date.today().isoformat()}]})
    research = report.research_company("1", "Acme")
    urls = [x["url"] for x in research["sources"].items]
    assert "https://new" in urls and "https://old" not in urls
    assert research["left_out_old"] == 1


def test_size_reason_appears_only_once():
    """Real problem (Darktrace): code and the AI both gave the company-size reason."""
    r, _ = build(make_research(headcount=2604), make_draft(fit_score=9, fit_reasons=[
        Claim(text="Employee headcount is 2,604, placing it in the 2,001 to 10,000 band.", source_ids=["S2"]),
        Claim(text="Actively hiring SDRs.", source_ids=["S4"])]))
    texts = [c["text"] for c in r["fit_reasons"]]
    assert len([t for t in texts if "2,604" in t]) == 1          # just the code's size reason
    assert "Actively hiring SDRs." in texts
    assert any("repeated company-size reason" in c for c in r["checks"])


def test_ai_can_pick_a_recent_signal_story_as_news():
    r, _ = build(research_with_signal_story("2026-10-20"), make_draft(
        news=[NewsPick(source_id="S5", why_it_matters="Sweden"), NewsPick(source_id="S3", why_it_matters="Launch")]))
    assert [n["source"] for n in r["news"]] == ["S3", "S5"]  # newest first: 1 Nov, then 20 Oct


def test_signal_stories_take_priority_when_news_is_full():
    """Real problem (Howden, 2nd try): 3 newer stories pushed the CCO-hire signal out of the news card."""
    research = research_with_signal_story("2026-10-20")       # S5, the signal story
    for i in (6, 7, 8):                                        # S6–S8, newer ordinary news
        research["sources"].add("news", f"Story {i}", f"https://news/{i}", "...", f"2026-11-0{i}")
    r, _ = build(research, make_draft(
        news=[NewsPick(source_id=f"S{i}", why_it_matters="...") for i in (6, 7, 8)],
        signals=[Signal(signal_type="expansion", text="Expanding into Sweden.", source_ids=["S5"])]))
    assert news_urls(r) == ["https://news/8", "https://news/7", "https://news/sweden"]  # kept, newest first


@pytest.mark.parametrize("story_date", [None, "2025-12-01"])  # undated, or older than 6 months
def test_old_or_undated_signal_stories_are_not_news(story_date):
    r, _ = build(research_with_signal_story(story_date), make_draft(
        news=[],
        signals=[Signal(signal_type="expansion", text="Expanding into Sweden.", source_ids=["S5"])]))
    assert r["news"] == []


def test_unknown_signal_types_are_removed():
    r, _ = build(make_research(), make_draft(signals=[
        Signal(signal_type="vibes", text="They seem nice.", source_ids=["S4"])]))
    assert ai_signals(r) == []


# --- Hard ICP rules applied by code ---------------------------------------
def test_score_is_kept_between_1_and_10():
    r, _ = build(make_research(), make_draft(fit_score=15))
    assert r["fit_score"] == 10


@pytest.mark.parametrize("headcount, band", [
    (49, "weak"), (50, "good"), (199, "good"),
    (200, "best"), (1500, "best"), (2000, "best"),
    (2001, "good"), (10000, "good"), (10001, "weak"),
])
def test_size_bands_including_edges(headcount, band):
    assert report.size_band(headcount) == band


@pytest.mark.parametrize("headcount", [300, 2000])
def test_best_fit_size_keeps_the_ai_score(headcount):
    r, _ = build(make_research(headcount=headcount), make_draft(fit_score=10))
    assert r["fit_score"] == 10
    assert r["checks"] == []


@pytest.mark.parametrize("headcount, side", [(120, "smaller"), (5000, "larger")])
def test_good_fit_size_caps_at_8_and_gives_reason(headcount, side):
    r, _ = build(make_research(headcount=headcount), make_draft(fit_score=10))
    assert r["fit_score"] == 8
    assert f"{side} than the best-fit" in r["fit_reasons"][0]["text"]
    assert r["fit_reasons"][0]["sources"] == ["S2"]  # cites the company profile


def test_good_fit_size_still_gives_reason_when_score_already_low():
    r, _ = build(make_research(headcount=120), make_draft(fit_score=6))
    assert r["fit_score"] == 6
    assert "slightly lower" in r["fit_reasons"][0]["text"]


@pytest.mark.parametrize("headcount, side", [(12, "under 50"), (25000, "over 10,000")])
def test_weak_fit_size_caps_at_3(headcount, side):
    r, _ = build(make_research(headcount=headcount), make_draft(fit_score=9))
    assert r["fit_score"] == 3
    assert side in r["fit_reasons"][0]["text"]


def test_unknown_headcount_has_no_size_rule():
    """Without a headcount, the AI judges size from the sources (no code cap)."""
    research = make_research()
    research["card"] = None
    research["profile"]["company_type"] = "plc"
    r, _ = build(research, make_draft(fit_score=7))
    assert r["fit_score"] == 7




def test_closed_company_skips_ai_and_scores_1():
    ai = FakeAI()  # no answers: the test fails if the AI is called
    r = report.build_report(make_research(status="dissolved"), ai=ai)
    assert r["not_worth_contacting"] is True
    assert r["fit_score"] == 1
    assert report.write_email(r, ai=ai) is None   # no email for a closed company
    assert ai.prompts == []


def test_dormant_company_is_flagged():
    r = report.build_report(make_research(accounts="dormant"), ai=FakeAI())
    assert r["not_worth_contacting"] is True
    assert "dormant" in r["fit_reasons"][0]["text"]


# --- Email (written only when the user asks) ------------------------------
def email_for(report_draft, *email_answers, research=None):
    """Build a report, then write its email with the given fake AI answers."""
    r, _ = build(research or make_research(), report_draft)
    ai = FakeAI(*email_answers)
    return report.write_email(r, ai=ai, today=date(2026, 11, 16)), ai


def test_email_uses_only_facts_from_the_report():
    email, ai = email_for(make_draft(), make_email())
    assert email["trigger_sources"] == ["S4"]
    assert email["warnings"] == []
    prompt = ai.prompts[0]
    assert "Hiring an SDR. [S4]" in prompt                 # the report's own signal, with its source
    assert "Do not use your own knowledge" in prompt


def test_email_cannot_cite_sources_outside_the_report():
    """The news story was fetched, but the report doesn't use it, so the email can't use it either."""
    r, _ = build(make_research(), make_draft(news=[]))
    unused = next(x["id"] for x in r["all_sources"] if x["url"] == "https://news/1")
    assert unused not in {x["id"] for x in r["sources"]}
    email = report.write_email(r, ai=FakeAI(make_email(trigger_source_ids=[unused])))
    assert any("couldn't be matched to a source" in w for w in email["warnings"])


def test_low_fit_email_gets_the_note():
    email, _ = email_for(make_draft(fit_score=2), make_email())
    assert email["warnings"][0] == report.LOW_FIT_NOTE


def test_long_email_with_no_sentences_keeps_a_warning():
    """Code can only remove whole sentences; if it can't, the over-limit warning stays (no second AI request)."""
    email, ai = email_for(make_draft(), make_email(body="word " * 150))
    assert len(ai.prompts) == 1
    assert any("over the 120-word limit" in w for w in email["warnings"])


def test_email_without_a_real_trigger_gets_a_warning():
    email, _ = email_for(make_draft(), make_email(trigger_source_ids=["S77"]))
    assert any("couldn't be matched to a source" in w for w in email["warnings"])


def test_email_spelling_is_made_british():
    """Real problem: a Sage email said 'personalization'."""
    email, _ = email_for(make_draft(), make_email(
        subject="Personalized outreach",
        body="Hi [First name], manual research can slow down personalization. [Your name]"))
    assert email["subject"] == "Personalised outreach"
    assert "personalisation" in email["body"]
    assert any(c.startswith("British spelling") for c in email["checks"])


@pytest.mark.parametrize("us, uk", [
    ("optimize", "optimise"), ("Organizations", "Organisations"), ("analyzing", "analysing"),
    ("prioritized", "prioritised"), ("favorite", "favourite"), ("colors", "colours"),
])
def test_to_british(us, uk):
    assert report.to_british(us)[0] == uk


@pytest.mark.parametrize("word", ["Colorado", "size", "Centerline", "organic", "already British: personalise"])
def test_to_british_leaves_other_words_alone(word):
    assert report.to_british(word) == (word, [])


# --- Helpers ----------------------------------------------------------------
def test_source_list_does_not_repeat_the_same_page():
    s = report.SourceList()
    assert s.add("news", "A", "https://x", "...") == "S1"
    assert s.add("signal", "A again", "https://x", "...") == "S1"
    assert s.add("news", "B", "https://y", "...") == "S2"


@pytest.mark.parametrize("typed, brand", [
    ("https://www.paddle.com", "Paddle"),
    ("Sage Group plc", "Sage Group"),
    ("cognism", "Cognism"),
    ("BT Local Business", "BT Local Business"),                          # the user's capitals are kept
    ("BT Local Business – Home Counties North", "BT Local Business"),    # words after the dash dropped
    ("COGNISM LIMITED", "Cognism"),                                      # a Companies House name is tidied
    ("SAGE GROUP PLC", "Sage Group"),
])
def test_brand_from(typed, brand):
    assert report.brand_from(typed) == brand


# --- Profile block for the banner and stat tiles --------------------------
def test_profile_facts_come_with_sources():
    research = make_research()
    research["card"].update(headquarters="Leeds, United Kingdom", industry="Software Development",
                            founded="2015", website="www.acme.com", headcount_yearly_change="+30 yearly (11%)",
                            total_raised=["5M GBP"], annual_revenue=None, linkedin_followers=1200)
    research["profile"]["sic_codes"] = ["62012"]
    r, _ = build(research, make_draft())
    p = r["profile"]
    assert p["headcount"] == {"value": 300, "source": "S2"}
    assert p["legal_name"] == {"value": "ACME LTD", "source": "S1"}
    assert p["linkedin_followers"]["value"] == 1200
    assert p["domain"] == "acme.com"
    assert p["revenue"] is None                       # not found → None, never guessed
    assert p["brand"]["value"] == "Acme"


def test_profile_falls_back_to_companies_house_without_a_card():
    research = make_research()
    research["card"] = None
    research["website"] = None
    research["profile"]["sic_codes"] = ["62012"]
    r, _ = build(research, make_draft())
    p = r["profile"]
    assert p["industry"] == {"value": "Business and domestic software development", "source": "S1"}
    assert p["founded"] == {"value": "2015", "source": "S1"}
    assert p["headcount"] is None and p["website"] is None and p["domain"] is None


def test_report_and_email_say_which_model_wrote_them():
    backup = {"name": "gemini-3.5-flash", "backup": True, "reason": "gemini-3.6-flash failed: busy"}
    ai = FakeAI(make_draft(), make_email())
    ai.model_used = backup
    r = report.build_report(make_research(), ai=ai, today=date(2026, 11, 16))
    assert r["ai_model"] == backup
    assert report.write_email(r, ai=ai)["ai_model"] == backup


# --- Who the email is for (persona) ---------------------------------------------
@pytest.mark.parametrize("persona, angle_words", [
    ("Head of Sales", "meetings and pipeline"),
    ("Head of RevOps", "tools and data"),
    ("Founder / CEO", "growth and cost"),
])
def test_email_is_written_for_the_chosen_persona(persona, angle_words):
    r, _ = build(make_research(), make_draft())
    ai = FakeAI(make_email())
    email = report.write_email(r, ai=ai, persona=persona)
    assert email["persona"] == persona
    assert f"READER: the {persona}" in ai.prompts[0] and angle_words in ai.prompts[0]


def test_unknown_persona_falls_back_to_head_of_sales():
    r, _ = build(make_research(), make_draft())
    assert report.write_email(r, ai=FakeAI(make_email()), persona="Chief Wizard")["persona"] == "Head of Sales"


# --- LinkedIn note and call opener (same AI request as the email) ------------------
def test_email_note_and_opener_come_from_one_ai_request():
    email, ai = email_for(make_draft(), make_email())
    assert len(ai.prompts) == 1
    assert email["linkedin_note"].startswith("Hi [First name]")
    assert email["call_opener"].count("\n") == 1          # 2 lines


def test_long_linkedin_note_is_cut_at_a_full_sentence():
    note = "Hi [First name], saw Acme is hiring SDRs. " + "This sentence is filler to make it long. " * 10
    email, _ = email_for(make_draft(), make_email(linkedin_note=note))
    assert len(email["linkedin_note"]) <= 300
    assert email["linkedin_note"].endswith(".")
    assert any("LinkedIn note cut" in c for c in email["checks"])


def test_call_opener_is_kept_to_two_lines_and_british():
    email, _ = email_for(make_draft(), make_email(call_opener="Line one.\nWe help you optimize.\nLine three.\nLine four."))
    assert email["call_opener"] == "Line one.\nWe help you optimise."
    assert any("Call opener cut from 4 to 2 lines" in c for c in email["checks"])


# --- Funding signals use the real round date ------------------------------------
PADDLE_LAST_ROUND = {"type": "Debt Financing", "date": "2025-08-17", "amount": "25.00M USD"}


def test_old_round_reported_on_a_new_page_is_not_a_funding_signal():
    """Real problem (Paddle): a Nov 2026 page reported the $25m round the profile dates 17 Aug 2025."""
    research = make_research()
    research["card"]["last_round"] = PADDLE_LAST_ROUND
    research["sources"].add("signal", "[funding] Paddle raises $25m", "https://f", "...", "2026-11-06")   # S5
    r, _ = build(research, make_draft(signals=[
        Signal(signal_type="funding", text="Paddle raised $25 million from CIBC Innovation Banking.", source_ids=["S5"]),
        Signal(signal_type="hiring_sales", text="Hiring an SDR.", source_ids=["S4"])]))
    assert [s["type"] for s in r["signals"]] == ["hiring_sales"]
    assert any("dates that round 2025-08-17" in c for c in r["checks"])


def test_recent_round_is_kept():
    research = make_research()
    research["card"]["last_round"] = {"type": "Series B", "date": "2026-10-01", "amount": "40.00M USD"}
    research["sources"].add("signal", "[funding] Acme raises $40m", "https://f", "...", "2026-10-02")   # S5
    r, _ = build(research, make_draft(signals=[
        Signal(signal_type="funding", text="Acme raised $40m in a Series B.", source_ids=["S5"])]))
    assert [s["type"] for s in ai_signals(r)] == ["funding"]


@pytest.mark.parametrize("text, old", [
    ("Raised $25 million in funding.", True),     # same amount as the old last round
    ("Raised £250m.", False),                     # a different amount: not that round
    ("Raised $2.5m.", False),
])
def test_is_old_round_matches_the_amount(text, old):
    assert report._is_old_round(text, {"last_round": PADDLE_LAST_ROUND}, today=date(2026, 11, 18)) is old


# --- Longer overview ----------------------------------------------------------------
def test_overview_is_capped_and_every_sentence_needs_a_source():
    many = [Claim(text=f"Fact {i}.", source_ids=["S1"]) for i in range(6)]
    r, _ = build(make_research(), make_draft(
        overview=[Claim(text="Unsourced.", source_ids=[])] + many,
        overview_more=[Claim(text=f"More {i}.", source_ids=["S2"]) for i in range(9)]))
    assert [c["text"] for c in r["overview"]] == ["Fact 0.", "Fact 1.", "Fact 2.", "Fact 3."]   # 4 at most
    assert len(r["overview"]) + len(r["overview_more"]) == 7                                      # 7 in total
    assert r["what_they_do"]["text"] == "Fact 0."          # the one-line summary is the first sentence


def test_fewer_sentences_when_sources_support_fewer():
    r, _ = build(make_research(), make_draft(overview=[Claim(text="Only one fact.", source_ids=["S2"])],
                                             overview_more=[]))
    assert len(r["overview"]) == 1 and r["overview_more"] == []


def test_about_link_only_from_a_fetched_source():
    research = make_research()
    research["sources"].add("website", "About Acme", "https://acme.com/about", "...")
    r, _ = build(research, make_draft())
    assert r["about_link"] == {"url": "https://acme.com/about", "label": "About page"}
    plain, _ = build(make_research(), make_draft())
    assert plain["about_link"] == {"url": "https://acme.com", "label": "Visit website"}   # the website source


# --- 'More about' data ---------------------------------------------------------------
def research_with_more_about():
    research = make_research()
    research["card"].update(similar_companies=["chargebee", "stripe", "weavr", "fastspring", "2checkout"], open_roles=25)
    research["directors"] = {"people": [{"name": "Scott Galit", "role": "Director", "appointed_on": "2024-01-02",
                                         "occupation": None}], "total": 6, "source": "https://ch/acme/officers"}
    research["owners"] = {"owners": [], "source": "https://ch/acme/psc"}
    research["sources"].add("signal", "[hiring_sales] Account Executive at Acme | Jobs", "https://jobs/ae", "...")
    research["sources"].add("job", "[jobs] Acme - Senior Backend Engineer", "https://jobs/be", "...")
    research["sources"].add("job", "[jobs] Acme Careers", "https://jobs/careers", "...")
    return research


def test_more_about_is_built_from_sources_only():
    r, _ = build(research_with_more_about(), make_draft())
    m = r["more_about"]
    by_id = {x["id"]: x for x in r["all_sources"]}
    shown = {x["id"] for x in r["sources"]}
    assert [p["name"] for p in m["directors"]["people"]] == ["Scott Galit"]
    assert by_id[m["directors"]["source"]]["url"] == "https://ch/acme/officers"
    assert m["owners"]["owners"] == [] and m["owners"]["source"] is None        # nothing current: 'Not found'
    assert m["competitors"]["names"] == ["chargebee", "stripe", "weavr", "fastspring"]   # 4 at most
    assert by_id[m["competitors"]["source"]]["url"] == "https://card/acme"
    assert m["hiring"]["open_roles"] == 25
    # sales roles first; "Acme Careers" is a careers page, not a role, so it's left out
    assert [x["title"] for x in m["hiring"]["roles"]] == ["SDR", "Account Executive", "Senior Backend Engineer"]
    assert [x["sales"] for x in m["hiring"]["roles"]] == [True, True, False]
    assert all(x["source"] in shown for x in m["hiring"]["roles"])            # every role is tagged and listed
    assert m["directors"]["source"] in shown and m["competitors"]["source"] in shown


def test_more_about_with_nothing_found():
    research = make_research()
    research["card"] = None
    r, _ = build(research, make_draft())
    m = r["more_about"]
    assert m["directors"]["people"] == [] and m["owners"]["owners"] == [] and m["competitors"]["names"] == []
    assert m["hiring"]["open_roles"] is None and m["competitors"]["source"] is None



def test_read_more_never_repeats_the_overview():
    """Real problem: 'Read more' showed the overview sentences again."""
    r, _ = build(make_research(), make_draft(
        overview=[Claim(text="Acme makes billing software for SaaS firms.", source_ids=["S2"]),
                  Claim(text="Acme employs about 300 people.", source_ids=["S2"])],
        overview_more=[Claim(text="Acme makes billing software for SaaS firms.", source_ids=["S2"]),        # repeat
                       Claim(text="Acme makes the billing software for SaaS firms worldwide.", source_ids=["S2"]),   # reworded
                       Claim(text="Its customers include online retailers.", source_ids=["S2"])]))
    assert [c["text"] for c in r["overview_more"]] == ["Its customers include online retailers."]
    assert sum("repeated the overview" in c for c in r["checks"]) == 2



# --- Numbers with commas ----------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("Darktrace employs 2604 staff.", "Darktrace employs 2,604 staff."),
    ("Headcount grew by 4972 to 16382 in 2026.", "Headcount grew by 4,972 to 16,382 in 2026."),
    ("About 10000 customers and 131651 followers.", "About 10,000 customers and 131,651 followers."),
    ("Raised $25000000.", "Raised $25,000,000."),
    ("Founded in 2013.", "Founded in 2013."),                                 # a year
    ("Company number 08562035.", "Company number 08562035."),                 # starts with 0
    ("Revenue 26.49M GBP, 1500.5 units, dated 2026-10-13, see S12.", "Revenue 26.49M GBP, 1500.5 units, dated 2026-10-13, see S12."),
    ("Already 1,200 staff.", "Already 1,200 staff."),
    ("Call 2038580822 or +441234567.", "Call 2038580822 or +441234567."),     # phone numbers
    ("Its official SIC code 56290 is Other food services.", "Its official SIC code 56290 is Other food services."),  # live Gousto bug
    ("SIC codes 62012 and 70100.", "SIC codes 62012 and 70100."),
    ("Company number 8027386, 1108 staff.", "Company number 8027386, 1,108 staff."),
])
def test_numbers_get_commas(text, expected):
    assert report.with_commas(text) == expected


def test_ai_text_in_the_report_gets_commas():
    r, _ = build(make_research(), make_draft(
        overview=[Claim(text="Acme employs 2604 people.", source_ids=["S2"])],
        signals=[Signal(signal_type="hiring_sales", text="Hiring 1200 SDRs.", source_ids=["S4"])]))
    assert r["overview"][0]["text"] == "Acme employs 2,604 people."
    assert r["signals"][0]["text"] == "Hiring 1,200 SDRs."



@pytest.mark.parametrize("text, clean", [
    ("Paddle sells billing software [S2, S3].", "Paddle sells billing software."),
    ("It launched an AI Toolkit [S9]. Hiring too (S5).", "It launched an AI Toolkit. Hiring too."),
    ("No tags here.", "No tags here."),
    ("Series [A] round.", "Series [A] round."),        # only S-number tags are removed
])
def test_typed_source_tags_are_removed(text, clean):
    assert report.strip_source_tags(text) == clean


def test_report_text_never_shows_typed_tags():
    """Real problem (Paddle sample): '... launched an AI Toolkit [S9].' while its real tag was S8."""
    r, _ = build(make_research(), make_draft(
        overview=[Claim(text="Acme makes sales software [S2].", source_ids=["S2"])],
        signals=[Signal(signal_type="hiring_sales", text="Hiring an SDR [S4, S9].", source_ids=["S4"])]))
    assert r["overview"][0]["text"] == "Acme makes sales software."
    assert r["signals"][0]["text"] == "Hiring an SDR."



def test_call_opener_never_says_the_caller_is_from_the_target_company():
    """Real problem (live, Head of RevOps): '[Your name] here from Paddle, ...'."""
    research = make_research()
    research["typed"] = "Acme"
    r, _ = build(research, make_draft())
    email = report.write_email(r, ai=FakeAI(make_email(
        call_opener="[Your name] here from Acme, I saw you are hiring SDRs. How are you ramping them?")))
    assert email["call_opener"] == "[Your name] here from [Your company], I saw you are hiring SDRs.\nHow are you ramping them?"
    assert any("changed to [Your company]" in c for c in email["checks"])


def test_one_line_opener_is_split_into_two_lines():
    email, _ = email_for(make_draft(), make_email(call_opener="[Your name] here, from [Your company]. Saw the news. Got a minute?"))
    assert email["call_opener"] == "[Your name] here, from [Your company].\nSaw the news. Got a minute?"



def test_all_ai_text_uses_british_spelling():
    """Real case: the Paddle sample's 'Read more' said 'specialized'."""
    r, _ = build(make_research(), make_draft(
        overview=[Claim(text="Acme provides specialized tools to optimize billing.", source_ids=["S2"])]))
    assert r["overview"][0]["text"] == "Acme provides specialised tools to optimise billing."


def test_outreach_dashes_are_spaced_en_dashes():
    """Found in the health check: '[Your company]—I saw' (a tight em dash) in a call opener."""
    assert report.spaced_dashes("[Your name] here, from [Your company]—I saw") == "[Your name] here, from [Your company] – I saw"
    assert report.spaced_dashes("Funding 2020–2024") == "Funding 2020–2024"     # ranges stay tight
    assert report._tidy("Hi — we [S2] help") == "Hi – we help"


def ai_signals(r):
    """The reasons the AI gave, without the 'Hiring for sales' one code adds from job ads."""
    return [s for s in r["signals"] if not s["text"].startswith("Acme has job ads for sales roles")]


# --- Found in the Step 8 live test ------------------------------------------------------
def test_sales_job_ads_become_a_hiring_reason_when_the_ai_skips_them():
    r, _ = build(make_research(), make_draft(signals=[], not_found=["No sales hiring found in the last 6 months."]))
    hiring = [s for s in r["signals"] if s["type"] == "hiring_sales"]
    assert hiring and hiring[0]["text"] == "Acme has job ads for sales roles: SDR."
    assert hiring[0]["sources"]                                         # linked to the job ad
    assert not any("hiring" in x.lower() for x in r["not_found"])      # no contradiction
    assert any("job ads the AI skipped" in c for c in r["checks"])


def test_no_extra_hiring_reason_when_the_ai_already_gave_one():
    r, _ = build(make_research(), make_draft(signals=[
        Signal(signal_type="hiring_sales", text="Acme is hiring SDRs.", source_ids=["S3"])]))
    assert [s["text"] for s in r["signals"] if s["type"] == "hiring_sales"] == ["Acme is hiring SDRs."]


def test_typed_website_wins_over_the_profile_cards_website():
    """Live test: onfido.com was typed, but the card (after the Entrust deal) said entrust.com."""
    research = make_research()
    research["typed"] = "onfido.com"
    research["website"] = "https://onfido.com"
    research["sources"].add("website", "Onfido website", "https://onfido.com", "Identity verification")
    research["card"]["website"] = "www.entrust.com"
    profile = report.company_profile(research)
    assert profile["website"]["value"] == "https://onfido.com"
    assert profile["website"]["source"]
