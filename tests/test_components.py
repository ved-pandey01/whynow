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


def test_model_note_mentions_backup():
    note = ui.model_note({"name": "gemini-3.5-flash", "backup": True, "reason": "gemini-3.6-flash failed: busy"})
    assert "Report written by gemini-3.5-flash (backup model, because gemini-3.6-flash failed: busy)" in note
    assert ui.model_note(None) == ""


def test_header_has_slogan_and_typing_lines():
    import settings
    out = ui.hero()
    assert "Before you reach out, WhyNow." in out
    assert "Know the company. Know the fit. Know the moment." in out
    for line in settings.APP_TYPING_LINES:
        assert ui.esc(line) in out
    assert "<script" not in out   # the animation is pure CSS


def test_top_bar_has_label_and_byline():
    import settings
    out = ui.topbar()
    assert "AI account research agent" in out
    assert f'href="{settings.AUTHOR_LINKEDIN}"' in out and "by Ved Prakash Pandey" in out


def test_landing_background_is_a_safe_embedded_picture():
    out = ui.landing_background()
    assert "data:image/svg+xml;base64," in out and "<script" not in out


def test_preview_card_without_sample_shows_no_company_facts():
    out = ui.preview_card(None)
    assert "Your target company" in out and "Example layout" in out


def test_preview_card_uses_the_saved_sample():
    sample = {"profile": {"brand": {"value": "Paddle"}, "industry": {"value": "Software Development"},
                          "headquarters": {"value": "London"}, "domain": "paddle.com"},
              "company": {"name": "PADDLE.COM MARKET LIMITED"}, "fit_score": 9,
              "signals": [{"type": "funding"}], "sources": [1, 2, 3]}
    out = ui.preview_card(sample)
    assert "Paddle" in out and "Software Development · London" in out
    assert 'wn-badge strong' in out and ">9<" in out and ">3<" in out


def test_closed_company_card_is_greyed_out():
    m = {"name": "OLD LTD", "status": "dissolved", "source": "https://ch/1"}
    assert 'class="wn-pick closed"' in ui.picker_card(m)
    assert 'class="wn-pick"' in ui.picker_card(dict(m, status="active"))
