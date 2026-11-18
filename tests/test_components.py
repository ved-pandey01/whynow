"""Tests for the page pieces in ui/components.py: colours, safety and fallbacks."""

import pytest

from ui import components as ui


@pytest.mark.parametrize("score, kind", [
    (1, "weak"), (3, "weak"), (4, "possible"), (6, "possible"), (7, "strong"), (10, "strong"),
])
def test_fit_badge_colour_bands(score, kind):
    assert f'class="wn-badge {kind}"' in ui.fit_badge(score)


def test_fetched_text_cannot_add_code_to_the_page():
    """A web page title like this must show as plain text, never run."""
    nasty = '<script>alert("hi")</script><img src=x onerror=alert(1)>'
    by_id = {"S1": {"id": "S1", "title": nasty, "url": "https://x.com", "date": None}}
    r = {"news": [{"source": "S1", "why_it_matters": nasty}]}
    out = ui.news_card(r, by_id)
    assert "<script>" not in out and "<img" not in out
    assert "&lt;script&gt;" in out


@pytest.mark.parametrize("url, expected", [
    ("https://paddle.com", "https://paddle.com"),
    ("www.paddle.com", "https://www.paddle.com"),
    ("javascript:alert(1)", "#"),
    (None, "#"),
])
def test_only_web_links_are_allowed(url, expected):
    assert ui.safe_url(url) == expected


def test_logo_falls_back_to_initials_without_a_website():
    out = ui.logo({"brand": {"value": "Sage Group"}, "domain": None})
    assert "SG" in out and "<img" not in out


def test_logo_uses_website_icon_with_initials_behind():
    out = ui.logo({"brand": {"value": "Paddle"}, "domain": "paddle.com"})
    assert "icons.duckduckgo.com/ip3/paddle.com.ico" in out and ">P<img" in out


def test_missing_stat_says_not_found():
    assert "not found" in ui.stat_tile("Revenue", "bar_chart", None, {})


def test_headcount_trend_arrows():
    up = ui.stat_tile("Employees", "groups", {"value": 430, "source": None}, {}, trend="+41 yearly (10.5%)")
    down = ui.stat_tile("Employees", "groups", {"value": 450, "source": None}, {}, trend="-84 yearly (-15.7%)")
    assert "▲" in up and 'wn-trend up' in up
    assert "▼" in down and 'wn-trend down' in down


def test_linkedin_search_link_is_built_and_encoded():
    assert ui.linkedin_search_url("Head of Sales", "Marks & Spencer") == (
        "https://www.linkedin.com/search/results/people/?keywords=Head+of+Sales+Marks+%26+Spencer")


def test_picker_card_shows_typed_website_only_when_it_matches():
    m = {"name": "PADDLE.COM MARKET LIMITED", "status": "active", "founded": "2012-02-01",
         "industry": "Retail", "size": "Large", "city": "London", "source": "https://ch/1"}
    assert "paddle.com" in ui.picker_card(m, "paddle.com")
    assert "paddle.com" not in ui.picker_card(dict(m, name="PADDLE HOLDINGS LIMITED"), "paddle.com")


@pytest.mark.parametrize("title, clean", [
    ("[expansion] Paddle opens in Berlin", "Paddle opens in Berlin"),
    ("Paddle [beta] launch", "Paddle [beta] launch"),
])
def test_internal_tags_are_removed_from_titles(title, clean):
    assert ui.clean_title(title) == clean


def test_model_note_is_short_and_never_shows_errors():
    backup = {"name": "gemini-3.5-flash", "backup": True, "reason": "gemini-3.6-flash failed: Gemini is having problems"}
    note = ui.model_note(backup)
    assert note == '<div class="wn-model">Written by gemini-3.5-flash</div>'
    assert "failed" not in note and "problems" not in note
    assert ui.model_note(None) == ""
    # the reason moves to "Behind the scenes"
    assert ui.model_details(backup, "Report") == ("Report written by gemini-3.5-flash "
                                                  "(backup model; gemini-3.6-flash failed: Gemini is having problems)")


def test_header_has_slogan_and_typing_lines():
    import settings
    out = ui.hero()
    assert "Before you reach out" not in out                       # the old tagline is gone
    assert '<h2 class="wn-headline"><span>Know the fit.</span> <span>Know the moment.</span></h2>' in out
    assert "Know the company" not in out
    for line in settings.APP_TYPING_LINES:
        assert ui.esc(line) in out
    assert "<script" not in out   # the animation is pure CSS


def test_top_bar_has_label_and_author_pill():
    import settings
    out = ui.topbar()
    assert "AI account research agent" in out
    assert f'class="wn-built" href="{settings.AUTHOR_LINKEDIN}" target="_blank"' in out
    assert '<span class="wn-vp">VP</span>' in out
    assert "Built by Ved Prakash Pandey" in out and "Built by Ved<" in out and "↗" in out


def test_landing_background_is_a_safe_embedded_picture():
    out = ui.landing_background()
    assert "data:image/svg+xml;base64," in out and "<script" not in out


def test_preview_card_without_sample_shows_no_company_facts():
    out = ui.preview_card(None)
    assert "Your target company" in out and "Example layout" in out
    assert "<a " not in out              # nothing to open without a saved sample


def test_preview_card_uses_the_saved_sample():
    sample = {"profile": {"brand": {"value": "Paddle"}, "industry": {"value": "Software Development"},
                          "headquarters": {"value": "London"}, "domain": "paddle.com"},
              "company": {"name": "PADDLE.COM MARKET LIMITED"}, "fit_score": 9, "saved_on": "17 Nov 2026",
              "signals": [{"type": "funding"}], "sources": [1, 2, 3]}
    out = ui.preview_card(sample)
    assert "Paddle" in out and "Software Development · London" in out
    assert "Why reach out now" in out
    assert "EXAMPLE REPORT" in out and "WHAT YOU GET" not in out
    assert out.startswith('<a class="wn-preview wn-preview-link" href="./?sample=1"')   # whole card opens the sample
    assert "View full report →" in out and "From the saved" not in out
    assert "Real result for Paddle, saved 17 Nov 2026" in out
    assert 'wn-badge strong' in out and ">9<" in out and ">3<" in out


def test_closed_company_card_is_greyed_out():
    m = {"name": "OLD LTD", "status": "dissolved", "source": "https://ch/1"}
    assert 'class="wn-pick closed"' in ui.picker_card(m)
    assert 'class="wn-pick"' in ui.picker_card(dict(m, status="active"))


def test_logo_and_back_link_return_home():
    assert 'class="wn-home" href="./" target="_self"' in ui.topbar()
    assert ui.back_link() == '<a class="wn-back" href="./" target="_self">← Back to home</a>'


def test_headline_is_one_line_on_desktop():
    css = ui.CSS_FILE.read_text()
    assert "white-space: nowrap" in css.split(".wn-headline {")[1].split("}")[0]


@pytest.mark.parametrize("value, main, note", [
    ("318.34M USD, 8.00M GBP", "318.34M USD", "Also reported: 8.00M GBP"),
    ("5M GBP", "5M GBP", None),
])
def test_funding_shows_one_main_total(value, main, note):
    assert ui.split_totals(value) == (main, note)
    out = ui.stats_row({"profile": {"total_raised": {"value": value, "source": None}}}, {})
    assert f">{main}<" in out
    assert (note in out) if note else ("Also reported" not in out)


def test_progress_card_shows_ticks_spinner_and_waiting_steps():
    out = ui.progress_card("Acme <Ltd>", ["One", "Two", "Three"], ["done", "active", "todo"])
    assert 'class="wn-step-row done"><span class="icon" aria-hidden="true">check_circle</span>' in out
    assert 'class="wn-step-row active"><span class="wn-spin"' in out
    assert 'class="wn-step-row todo"><span class="icon" aria-hidden="true">radio_button_unchecked</span>' in out
    assert "Researching Acme &lt;Ltd&gt;" in out and "1 of 3 done" in out


# --- Signal freshness ------------------------------------------------------------
from datetime import date as _date


@pytest.mark.parametrize("when, words", [
    ("2026-11-18", "today"), ("2026-11-17", "yesterday"), ("2026-11-13", "5 days ago"),
    ("2026-11-11", "1 week ago"), ("2026-10-28", "3 weeks ago"), ("2026-07-18", "4 months ago"),
    ("2025-10-01", "1 year ago"),
])
def test_time_ago(when, words):
    assert ui.time_ago(when, today=_date(2026, 11, 18)) == words


@pytest.mark.parametrize("when", [None, "", "not a date", "2026-12-25"])   # missing, broken or future
def test_time_ago_shows_nothing_without_a_real_past_date(when):
    assert ui.time_ago(when, today=_date(2026, 11, 18)) is None


def test_signal_shows_age_of_its_newest_dated_source():
    by_id = {"S1": {"id": "S1", "title": "a", "url": "https://a", "date": "2026-10-20"},
             "S2": {"id": "S2", "title": "b", "url": "https://b", "date": "2026-10-28"},
             "S3": {"id": "S3", "title": "job", "url": "https://c", "date": None}}
    funding = {"type": "funding", "text": "Raised £5m.", "sources": ["S1", "S2"]}
    hiring = {"type": "hiring_sales", "text": "Hiring SDRs.", "sources": ["S3"]}
    today = _date(2026, 11, 18)
    assert "Funding · 3 weeks ago" in ui.signal_tile(funding, by_id, today)
    assert ">Hiring for sales<" in ui.signal_tile(hiring, by_id, today)   # undated: no age shown


def test_overview_with_read_more_and_about_link():
    by_id = {"S1": {"id": "S1", "title": "a", "url": "https://a", "date": None}}
    r = {"overview": [{"text": "Acme makes billing software.", "sources": ["S1"]}],
         "overview_more": [{"text": "It sells to SaaS firms.", "sources": ["S1"]}],
         "about_link": {"url": "https://acme.com/about", "label": "About page"}}
    out = ui.overview_html(r, by_id)
    assert "Acme makes billing software." in out and '<details class="wn-more"><summary>Read more</summary>' in out
    assert "It sells to SaaS firms." in out and ">About page</a>" in out
    assert "<script" not in out


def test_overview_falls_back_to_one_line_summary():
    r = {"what_they_do": {"text": "Old one-liner.", "sources": []}}
    assert "Old one-liner." in ui.overview_html(r, {}) and "Read more" not in ui.overview_html(r, {})
    assert "not found" in ui.overview_html({}, {})


def test_stylesheet_has_no_angle_brackets():
    """Real problem: a comment mentioning <details> made Streamlit's safety filter drop the whole stylesheet."""
    assert "<" not in ui.CSS_FILE.read_text()
