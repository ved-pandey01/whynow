"""
Tests for report.py: the code that CHECKS the AI's work.

We use a fake AI that deliberately misbehaves (invents sources, writes long emails)
to prove the checks catch it. No real AI or API calls are made.
"""

from datetime import date

import pytest

import report
from models import AIReport, Claim, NewsPick, Signal


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
        email_subject="Your new SDR hire",
        email_body="Hi [First name], saw Acme is hiring an SDR. Worth a quick chat? [Your name]",
        email_trigger_source_ids=["S4"],
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


def build(research, *answers):
    ai = FakeAI(*answers)
    return report.build_report(research, ai=ai, today=date(2026, 11, 16)), ai


# --- A normal, well-behaved answer passes through -------------------------
def test_good_answer_passes_all_checks():
    r, ai = build(make_research(), make_draft())
    assert r["fit_score"] == 8
    assert r["news"][0]["source"] == "S3"
    assert r["signals"][0]["type"] == "hiring_sales"
    assert r["email"]["warnings"] == []
    assert r["checks"] == []
    assert len(ai.prompts) == 1
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


def test_unknown_signal_types_are_removed():
    r, _ = build(make_research(), make_draft(signals=[
        Signal(signal_type="vibes", text="They seem nice.", source_ids=["S4"])]))
    assert r["signals"] == []


# --- Hard ICP rules applied by code ---------------------------------------
def test_score_is_kept_between_1_and_10():
    r, _ = build(make_research(), make_draft(fit_score=15))
    assert r["fit_score"] == 10


@pytest.mark.parametrize("headcount", [12, 5000])
def test_too_small_or_too_big_caps_score_at_3(headcount):
    r, _ = build(make_research(headcount=headcount), make_draft(fit_score=9))
    assert r["fit_score"] == 3
    assert "outside the ideal" in r["fit_reasons"][0]["text"]


def test_plc_without_headcount_is_capped_at_4():
    """Real problem: The Sage Group plc scored 5/10 because its headcount wasn't found."""
    research = make_research()
    research["card"] = None
    research["profile"]["company_type"] = "plc"
    r, _ = build(research, make_draft(fit_score=7))
    assert r["fit_score"] == 4
    assert "public limited company" in r["fit_reasons"][0]["text"]


def test_low_score_adds_do_not_contact_warning():
    r, _ = build(make_research(), make_draft(fit_score=2))
    assert r["email"]["warnings"][0].startswith("Low fit")


def test_closed_company_skips_ai_and_scores_1():
    ai = FakeAI()  # no answers: the test fails if the AI is called
    r = report.build_report(make_research(status="dissolved"), ai=ai)
    assert r["not_worth_contacting"] is True
    assert r["fit_score"] == 1
    assert r["email"] is None
    assert ai.prompts == []


def test_dormant_company_is_flagged():
    r = report.build_report(make_research(accounts="dormant"), ai=FakeAI())
    assert r["not_worth_contacting"] is True
    assert "dormant" in r["fit_reasons"][0]["text"]


# --- Email checks ---------------------------------------------------------
def test_long_email_is_shortened():
    long_body = "word " * 150
    short = Claim(text="Hi [First name], short version. [Your name]", source_ids=[])
    r, ai = build(make_research(), make_draft(email_body=long_body), short)
    assert r["email"]["word_count"] <= 120
    assert len(ai.prompts) == 2
    assert any("shortened" in c for c in r["checks"])


def test_email_still_too_long_gets_a_warning():
    long_body = "word " * 150
    r, _ = build(make_research(), make_draft(email_body=long_body),
                 Claim(text="still " * 130, source_ids=[]))
    assert any("over the 120-word limit" in w for w in r["email"]["warnings"])


def test_email_without_a_real_trigger_gets_a_warning():
    r, _ = build(make_research(), make_draft(email_trigger_source_ids=["S77"]))
    assert any("couldn't be matched to a source" in w for w in r["email"]["warnings"])


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
