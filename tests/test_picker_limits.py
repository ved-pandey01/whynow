"""
Tests for the picker and the daily limits: only 'Research this company' starts research, the
'X free searches left today' line, when a search counts, the list collapsing while research runs,
the scrolling, and 'Choose a different company'. All with fake data (no paid APIs).
"""

from whynow import report
from whynow import settings
from whynow import usage
import ui.components as ui
from tests.test_app import MATCHES, button, html, search  # noqa: F401
from tests.test_costs import page, research  # noqa: F401


def counts():
    return usage.searches_today()["total"]


def pick_buttons(at):
    return [b for b in at.button if (b.key or "").startswith("pick_")]


# --- 1. Only 'Research this company' starts research ------------------------------------
def test_company_name_and_card_are_not_clickable():
    card = ui.picker_card(dict(MATCHES[0]))
    assert card.count("<a ") == 1                                  # only the small 'Companies House' source link
    assert 'href="https://ch/acme"' in card and ">Companies House</a>" in card
    assert "ACME LTD" in card.split("<a ")[0]                      # the name comes before it, outside any link
    assert "onclick" not in card and "cursor" not in card


def test_each_card_has_one_button_research_this_company(page):
    at = search(page(), "Acme")
    assert [b.label for b in pick_buttons(at)] == ["Research this company"] * len(MATCHES)
    assert counts() == 0


# --- 2. 'X free searches left today' -----------------------------------------------------
def test_free_searches_left_line_for_visitors(page):
    at = page()
    at.run()
    assert f"{settings.VISITOR_LIVE_SEARCHES_PER_DAY} free searches left today" in html(at)
    research(at, "Acme", "123")
    assert "1 free search left today" in html(at)


def test_free_searches_left_shows_none_left():
    assert "No free searches left today" in ui.searches_left_line(0)
    assert "2 free searches left today" in ui.searches_left_line(2)


def test_free_searches_left_uses_everyones_limit_when_lower(page, monkeypatch):
    monkeypatch.setattr(settings, "TOTAL_LIVE_SEARCHES_PER_DAY", 1)
    assert usage.searches_left("someone") == 1
    usage.record_live_search("someone-else")
    assert usage.searches_left("someone") == 0


def test_free_searches_line_is_hidden_in_owner_mode(page, monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD", "pw")
    at = page()
    at.session_state["owner"] = True
    at.run()
    assert "free search" not in html(at)


# --- 3. When a search counts ---------------------------------------------------------------
def test_showing_the_list_does_not_count(page):
    search(page(), "Acme")
    assert counts() == 0


def test_fresh_research_counts_once(page):
    research(page(), "Acme", "123")
    assert counts() == 1


def test_cached_company_and_sample_do_not_count(page):
    research(page(), "Acme", "123")                 # fresh: counts
    research(page(), "Acme", "123")                 # same company again, from the 24-hour cache: free
    at = page()
    at.run()
    button(at, "See a sample report").click().run()  # the sample: free
    assert counts() == 1


def test_search_is_counted_as_research_starts(page, monkeypatch):
    seen = []
    monkeypatch.setattr(report, "research_company",
                        lambda number, typed, **k: seen.append(counts()) or {"number": number})
    research(page(), "Acme", "123")
    assert seen == [1]          # already counted while the research was running


def test_failed_research_gives_the_search_back(page, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(report, "research_company", broken)
    at = research(page(), "Acme", "123")
    assert counts() == 0
    assert not at.exception
    assert pick_buttons(at)     # the list comes back, so the user can try again or pick another


# --- 4. Collapse, progress, scrolling, 'Choose a different company' ---------------------
def test_list_collapses_and_progress_shows_with_a_scroll_while_research_runs(page, monkeypatch):
    def broken(*a, **k):        # stop mid-research so we can look at the page as it was
        raise RuntimeError("boom")
    monkeypatch.setattr(report, "research_company", broken)
    at = research(page(), "Acme", "123")
    page_html = html(at)
    assert "wn-progress" in page_html                                  # the live progress card
    assert 'scrollIntoView' in page_html and ".st-key-wn_progress" in page_html


def test_report_replaces_the_list_and_scrolls_to_its_top(page):
    at = research(page(), "Acme", "123")
    assert not pick_buttons(at)                                        # the list is collapsed
    assert ".st-key-wn_report_links" in html(at)                       # scroll to the top of the report
    at.run()
    assert "scrollIntoView" not in html(at)                            # only once, not on every redraw


def test_choose_a_different_company_brings_the_list_back(page):
    at = research(page(), "Acme", "123")
    link = at.button(key="wn_choose_again")
    assert link.label == "Choose a different company"
    link.click().run()
    assert "report" not in at.session_state
    assert [b.key for b in pick_buttons(at)] == ["pick_123", "pick_456"]
    at.button(key="pick_456").click().run()                           # pick another one from the same list
    assert "report" in at.session_state


def test_no_choose_again_link_for_the_sample_or_a_try_chip(page):
    at = page()
    at.run()
    button(at, "See a sample report").click().run()
    assert not [b for b in at.button if b.key == "wn_choose_again"]
    at = page()
    at.run()
    at.button(key=f"wn_try_{settings.TRY_COMPANIES[0][1]}").click().run()
    assert not [b for b in at.button if b.key == "wn_choose_again"]


def test_scroll_script_has_no_less_than_sign():
    """A '<' inside the script stops Streamlit loading it (found in the browser: the page didn't scroll)."""
    script = ui.scroll_script(".st-key-wn_progress")
    inside = script[len("<script>"):-len("</script>")]
    assert "<" not in inside and "scrollIntoView" in inside and ".st-key-wn_progress" in inside
