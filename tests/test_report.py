"""
Tests for report.py: the code that CHECKS the AI's work.

We use a fake AI that deliberately misbehaves (invents sources, writes long emails)
to prove the checks catch it. No real AI or API calls are made.
"""

from datetime import date

import pytest

import report
from models import AIReport, Claim, EmailDraft, NewsPick, Signal


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
        what_they_do=Claim(text="Acme makes sales software.", source_ids=["S2"]),
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
                  trigger_source_ids=["S4"])
    fields.update(changes)
    return EmailDraft(**fields)


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
    r, _ = build(make_research(), make_draft(what_they_do=Claim(text="Guess.", source_ids=[])))
    assert r["what_they_do"] is None  # shown as "not found"


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
    assert [n["source"] for n in r["news"]] == ["S5"]
    assert r["news"][0]["why_it_matters"] == "Expanding into Sweden."


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
    assert [n["source"] for n in r["news"]] == ["S8", "S7", "S5"]  # S5 kept, still newest first


@pytest.mark.parametrize("story_date", [None, "2025-12-01"])  # undated, or older than 6 months
def test_old_or_undated_signal_stories_are_not_news(story_date):
    r, _ = build(research_with_signal_story(story_date), make_draft(
        news=[],
        signals=[Signal(signal_type="expansion", text="Expanding into Sweden.", source_ids=["S5"])]))
    assert r["news"] == []


def test_unknown_signal_types_are_removed():
    r, _ = build(make_research(), make_draft(signals=[
        Signal(signal_type="vibes", text="They seem nice.", source_ids=["S4"])]))
    assert r["signals"] == []


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
    """S3 was fetched, but the report doesn't use it, so the email can't use it either."""
    email, _ = email_for(make_draft(news=[]), make_email(trigger_source_ids=["S3"]))
    assert any("couldn't be matched to a source" in w for w in email["warnings"])


def test_low_fit_email_gets_the_note():
    email, _ = email_for(make_draft(fit_score=2), make_email())
    assert email["warnings"][0] == report.LOW_FIT_NOTE


def test_long_email_is_shortened():
    short = Claim(text="Hi [First name], short version. [Your name]", source_ids=[])
    email, ai = email_for(make_draft(), make_email(body="word " * 150), short)
    assert email["word_count"] <= 120
    assert len(ai.prompts) == 2
    assert any("shortened" in c for c in email["checks"])


def test_email_shortening_is_timed_and_failures_are_noted():
    """Real problem: a failing shortening call added ~58 hidden seconds to the Sage report."""
    r, _ = build(make_research(), make_draft())

    def failing_ai(prompt, shape):
        if shape is EmailDraft:
            return make_email(body="word " * 150)
        raise report.AIError("Gemini is having problems right now.")

    email = report.write_email(r, ai=failing_ai)
    assert "Email shortening" in email["timings"]
    assert any("Couldn't shorten the email" in c for c in email["checks"])
    assert any("over the 120-word limit" in w for w in email["warnings"])


def test_email_still_too_long_gets_a_warning():
    email, _ = email_for(make_draft(), make_email(body="word " * 150), Claim(text="still " * 130, source_ids=[]))
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
])
def test_brand_from(typed, brand):
    assert report.brand_from(typed) == brand
