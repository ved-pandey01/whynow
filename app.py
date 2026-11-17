"""
app.py — the WhyNow web page.

  1. Landing: type a UK company (and, optionally, what you're selling), or open the sample report.
  2. Pick the right company from the Companies House matches (one card each).
  3. The report: banner, key stats, fit score, who to contact, "why now" signals, news,
     what we couldn't find, sources, and an email written only when you ask.

The look lives in ui/style.css and ui/components.py; this file only arranges the pieces.

Run it with:
    .venv/bin/streamlit run app.py
"""

import json
from pathlib import Path

import streamlit as st

import report
import settings
from ai.provider import AIError
from sources import uk_companies_house as ch
from sources.news_tavily import domain_of
from ui import components as ui

SAMPLE_FILE = Path(__file__).parent / settings.SAMPLE_REPORT_FILE


# ---------------------------------------------------------------------------
# Fetching (cached, so looking at the same company again costs no API credits)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def find_companies(typed):
    return ch.search_companies(typed)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def make_report(company_number, typed, offer):
    research = report.research_company(company_number, typed)
    return report.build_report(research, offer=offer)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def make_email(r):
    return report.write_email(r)


def load_sample():
    """The saved real report behind "See a sample report" (no API calls, no keys needed)."""
    try:
        return json.loads(SAMPLE_FILE.read_text())
    except (OSError, ValueError):
        return None


def safe(text):
    """Stop '$' in text (e.g. '$50m') being read as a maths formula in Streamlit messages."""
    return (text or "").replace("$", "\\$")


def friendly_error(e):
    """Our own errors already have friendly wording; anything else gets a general message."""
    if isinstance(e, (ch.CompaniesHouseError, AIError)):
        st.error(safe(str(e)))
    else:
        print(f"Unexpected error: {type(e).__name__}: {e}")  # shown in the terminal, for fixing
        st.error("Something unexpected went wrong. Please try again in a minute.")


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------
def search_form():
    """What company, and what are we selling?"""
    with st.form("search"):
        typed = st.text_input("Company name or website", placeholder="e.g. cognism.com or Paddle")
        # The offer keeps its default unless the user opens "Change offer"
        with st.expander("Change offer"):
            offer = st.text_area("What are you selling?", value=settings.DEFAULT_OFFER, height=80)
        submitted = st.form_submit_button("Find company", type="primary", icon=":material/search:",
                                          use_container_width=True)
        st.html(ui.search_tip())
        if submitted:
            for key in ("report", "email", "sample"):
                st.session_state.pop(key, None)
            st.session_state["offer"] = offer
            st.session_state["typed"] = typed
            try:
                st.session_state["matches"] = find_companies(typed)
            except Exception as e:
                st.session_state["matches"] = None
                friendly_error(e)
                return
            st.rerun()   # redraw with the compact header and the picker


def open_sample_from_link():
    """A link ending in ?sample=1 opens the sample report straight away (handy for a portfolio)."""
    if st.query_params.get("sample") == "1" and "report" not in st.session_state:
        sample = load_sample()
        if sample:
            st.session_state.update(report=sample, sample=True, matches=None)


def landing_extras():
    """The report preview, 'How it works' and the sample button, only before any search."""
    sample = load_sample()
    st.html(ui.preview_strip(sample))
    _, middle, _ = st.columns([1, 1.2, 1])
    if middle.button("See a sample report", type="primary", icon=":material/visibility:",
                     disabled=sample is None, use_container_width=True):
        st.session_state.update(report=sample, sample=True, matches=None)
        st.session_state.pop("email", None)
        st.rerun()
    st.html(ui.how_it_works())


# ---------------------------------------------------------------------------
# Company picker
# ---------------------------------------------------------------------------
def pick_company():
    """One card per Companies House match, each with its own button."""
    matches, typed = st.session_state.get("matches"), st.session_state.get("typed")
    if matches is None:
        return
    if not matches:
        st.info(f'No companies found for "{safe(typed)}". Check the spelling or try the website.')
        return
    if not matches[0]["close_match"]:
        st.warning(f'No close match found for "{safe(typed)}". Only pick one below if you\'re sure it\'s right.')
    st.markdown("##### Which company did you mean?")
    st.caption("Biggest first. Size is a guide from the accounts each company files, not a staff count.")
    typed_domain = domain_of(typed) if report.looks_like_website(typed) else None
    for m in matches:
        with st.container(border=True, key=f"wn_box_pick_{m['company_number']}"):
            text, button = st.columns([4, 1.4], vertical_alignment="center")
            text.html(ui.picker_card(m, typed_domain))
            closed = (m.get("status") or "active") != "active"
            if button.button("Research this company", key=f"pick_{m['company_number']}",
                             type="secondary" if closed else "primary", use_container_width=True):
                run_report(m)


def run_report(choice):
    with st.spinner(f"Researching {choice['name']}… this usually takes 20–40 seconds."):
        try:
            r = make_report(choice["company_number"], st.session_state["typed"], st.session_state["offer"])
        except Exception as e:
            friendly_error(e)
            return
    st.session_state.update(report=r, matches=None, sample=False)
    st.session_state.pop("email", None)
    st.rerun()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def show_contact(r):
    """Who to contact, with a LinkedIn people search (we only build the link)."""
    with st.container(border=True, key="wn_box_contact"):
        st.html(f'<div class="wn-card wn-card-head">'
                f'<h4>{ui.icon("person_search")}Who to contact</h4>{ui.role_html(r)}</div>')
        role = r.get("suggested_role")
        if role:
            brand = (r["profile"].get("brand") or {}).get("value") or r["company"]["name"]
            st.link_button(f"Find {role} at {brand} on LinkedIn", ui.linkedin_search_url(role, brand),
                           icon=":material/open_in_new:", use_container_width=True)


def show_email(r, by_id):
    """The email is written only when asked, so we don't spend AI time on companies we skip."""
    with st.container(border=True, key="wn_box_email"):
        st.html(f'<div class="wn-card wn-card-head">'
                f'<h4>{ui.icon("mail")}Outreach email</h4></div>')
        if r["not_worth_contacting"]:
            st.caption("No email: this company isn't worth contacting.")
            return
        button, note = st.columns([2, 3], vertical_alignment="center")
        clicked = button.button("Create email for this company", type="primary", use_container_width=True)
        if r["fit_score"] <= report.LOW_FIT_SCORE:
            note.caption(report.LOW_FIT_NOTE)
        if clicked:
            create_email(r)
        e = st.session_state.get("email")
        if e:
            show_email_text(e, by_id)


def create_email(r):
    with st.spinner("Writing the email…"):
        try:
            st.session_state["email"] = r.get("saved_email") or make_email(r)
        except Exception as e:
            friendly_error(e)


def show_email_text(e, by_id):
    for w in e["warnings"]:
        if w != report.LOW_FIT_NOTE:   # already shown beside the button
            st.warning(safe(w))
    st.markdown(f"**Subject:** {safe(e['subject'])}")
    st.code(e["body"], language=None, wrap_lines=True)
    st.html(f'<div class="wn-quiet">{e["word_count"]} words · opening trigger '
            f'{ui.cite(e["trigger_sources"], by_id) or "none"} · use the copy icon on the email</div>')
    if e.get("ai_model"):
        st.html(ui.model_note(e["ai_model"], "Email"))


def show_behind_the_scenes(r):
    with st.expander("Behind the scenes: code checks and timings"):
        st.markdown("**Code checks** (what code changed in the AI's answer)")
        st.markdown("\n".join(f"- {safe(c)}" for c in r["checks"]) or "None needed.")
        if r.get("timings"):
            st.markdown("**Time taken:** " + " · ".join(f"{k} {v}s" for k, v in r["timings"].items()))


def show_report(r):
    by_id = {s["id"]: s for s in r["all_sources"]}
    if st.session_state.get("sample"):
        st.info(f"Saved example from {r.get('saved_on', 'an earlier run')}. "
                "Search above to research any UK company live.", icon=":material/history:")

    left, right = st.columns([2, 1])
    left.html(ui.banner(r, by_id))
    right.html(ui.profile_card(r, by_id))
    for w in r["warnings"]:
        st.caption(safe(w))
    st.html(ui.stats_row(r, by_id))

    left, right = st.columns([3, 2])
    left.html(ui.fit_card(r, by_id))
    with right:
        show_contact(r)

    st.html(ui.signals_card(r, by_id))
    st.html(ui.news_card(r, by_id))
    st.html(ui.not_found_card(r))
    st.html(ui.sources_card(r))
    if r.get("ai_model"):
        st.html(ui.model_note(r["ai_model"]))
    show_email(r, by_id)
    show_behind_the_scenes(r)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(page_title=f"{settings.APP_NAME} · {settings.APP_SLOGAN}",
                       page_icon=":material/bolt:", layout="wide")
    st.html(ui.css())
    open_sample_from_link()
    has_results = "report" in st.session_state or st.session_state.get("matches") is not None

    if has_results:
        st.html(ui.mini_brand())
        # Once a report is showing, the search folds away so the report gets the space
        with st.expander("New search", icon=":material/search:", expanded="report" not in st.session_state):
            search_form()
    else:
        st.html(ui.hero())
        _, middle, _ = st.columns([1, 4, 1])
        with middle:
            search_form()
        if st.session_state.get("matches") is None:
            landing_extras()

    pick_company()
    if "report" in st.session_state:
        show_report(st.session_state["report"])
    st.html(ui.footer())


main()
