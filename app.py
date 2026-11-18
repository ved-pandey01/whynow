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

import hashlib
import json
import time
from pathlib import Path

import streamlit as st

import exports
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


REPORT_CACHE_SECONDS = 6 * 3600


@st.cache_resource
def report_store():
    """Finished reports, shared by everyone using this app for 6 hours: {report_id: (saved_at, report)}.
    Our own cache (not st.cache_data) so the live progress can be shown while a report is made."""
    return {}


def report_id(company_number, typed, offer):
    return hashlib.sha1(f"{company_number}|{typed}|{offer}".encode()).hexdigest()[:12]


def cached_report(rid):
    saved = report_store().get(rid)
    if saved and time.time() - saved[0] < REPORT_CACHE_SECONDS:
        return saved[1]
    return None


def make_report(company_number, typed, offer, on_step=report._no_progress):
    """A report from the cache if we made it in the last 6 hours (no API credits), otherwise a new one."""
    rid = report_id(company_number, typed, offer)
    r = cached_report(rid)
    if r is None:
        research = report.research_company(company_number, typed, on_step=on_step)
        r = report.build_report(research, offer=offer, on_step=on_step)
        r["report_id"] = rid
        report_store()[rid] = (time.time(), r)
    else:
        for step in range(len(report.STEPS)):   # cached: every step is already done
            on_step(step, report.DONE)
    return r


def live_progress(company_name):
    """Show the steps as a card that ticks off live. Returns the on_step function for the report code."""
    box = st.empty()
    states = ["todo"] * len(report.STEPS)

    def on_step(step, state):
        states[step] = "done" if state == report.DONE else "active"
        box.html(ui.progress_card(company_name, report.STEPS, states))

    box.html(ui.progress_card(company_name, report.STEPS, states))
    return on_step


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def make_email(r, persona):
    return report.write_email(r, persona=persona)


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
    """One rounded search bar with the button inside, and a tiny "Change offer" link under it."""
    with st.container(key="wn_search"):
        with st.form("search", border=False):
            text, button = st.columns([5, 1.3], vertical_alignment="center")
            typed = text.text_input("Company name or website", placeholder=settings.SEARCH_PLACEHOLDER,
                                    label_visibility="collapsed")
            submitted = button.form_submit_button("Research →", type="primary")
    tried = try_chips()
    # The offer keeps its default unless the user opens "Change offer"
    with st.expander("Change offer", key="wn_offer"):
        st.text_area("What are you selling?", value=settings.DEFAULT_OFFER, height=80, key="offer_text")
        st.caption(settings.OFFER_HINT)
    if tried:
        run_try(tried)
    if submitted:
        for key in ("report", "email", "emails", "sample"):
            st.session_state.pop(key, None)
        st.session_state["offer"] = st.session_state.get("offer_text") or settings.DEFAULT_OFFER
        st.session_state["typed"] = typed
        try:
            st.session_state["matches"] = find_companies(typed)
        except Exception as e:
            st.session_state["matches"] = None
            friendly_error(e)
            return
        st.rerun()   # redraw with the picker


def try_chips():
    """'Try: Cognism · Darktrace · Paddle' as small chips. Returns the website clicked, or None."""
    clicked = None
    with st.container(key="wn_try", horizontal=True, vertical_alignment="center", gap="small"):
        st.html('<span class="wn-try-label">Try:</span>')
        for name, website in settings.TRY_COMPANIES:
            if st.button(name, key=f"wn_try_{website}", type="tertiary"):
                clicked = website
    return clicked


def run_try(website):
    """Research a 'Try' company straight away: search by its website and take the top close match."""
    for key in ("report", "email", "emails", "sample", "matches"):
        st.session_state.pop(key, None)
    st.session_state["offer"] = st.session_state.get("offer_text") or settings.DEFAULT_OFFER
    st.session_state["typed"] = website
    try:
        matches = find_companies(website)
    except Exception as e:
        friendly_error(e)
        return
    if not matches or not matches[0]["close_match"]:
        st.info(f'No close match found for "{safe(website)}" on Companies House.')
        return
    run_report(matches[0])


def open_sample_from_link():
    """A link ending in ?sample=1 opens the sample report straight away (handy for a portfolio)."""
    if st.query_params.get("sample") == "1" and "report" not in st.session_state:
        sample = load_sample()
        if sample:
            st.session_state.update(report=sample, sample=True, matches=None)


def landing():
    """Two columns: wordmark, lines and search on the left; the report preview on the right.
    Then the three step cards and the sample button."""
    sample = load_sample()
    st.html(ui.landing_background())
    left, right = st.columns([1.5, 1], gap="large", vertical_alignment="center")
    with left:
        st.html(ui.hero())
        search_form()
    right.html(ui.preview_card(sample))
    st.html(ui.how_it_works())
    _, middle, _ = st.columns([1, 1, 1])
    if middle.button("See a sample report", type="primary", icon=":material/visibility:",
                     disabled=sample is None, use_container_width=True):
        st.session_state.update(report=sample, sample=True, matches=None)
        st.session_state.pop("email", None)
        st.session_state.pop("emails", None)
        st.rerun()


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
    chosen = None
    for m in matches:
        with st.container(border=True, key=f"wn_box_pick_{m['company_number']}"):
            text, button = st.columns([4, 1.4], vertical_alignment="center")
            text.html(ui.picker_card(m, typed_domain))
            closed = (m.get("status") or "active") != "active"
            if button.button("Research this company", key=f"pick_{m['company_number']}",
                             type="secondary" if closed else "primary", use_container_width=True):
                chosen = m
    if chosen:
        run_report(chosen)   # after the cards, so the live progress shows as its own card


def run_report(choice):
    on_step = live_progress(choice["name"])
    try:
        r = make_report(choice["company_number"], st.session_state["typed"], st.session_state["offer"], on_step)
    except Exception as e:
        friendly_error(e)
        return
    st.session_state.update(report=r, matches=None, sample=False)
    st.session_state.pop("email", None)
    st.session_state.pop("emails", None)
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
        button, choose = st.columns([2, 3], vertical_alignment="center")
        clicked = button.button("Create email for this company", type="primary", use_container_width=True)
        persona = choose.segmented_control("Who is it for?", list(settings.EMAIL_PERSONAS), key="wn_persona",
                                           default=settings.DEFAULT_PERSONA, label_visibility="collapsed")
        persona = persona or settings.DEFAULT_PERSONA
        if r["fit_score"] <= report.LOW_FIT_SCORE:
            st.caption(report.LOW_FIT_NOTE)
        if clicked:
            create_email(r, persona)
        # One email per persona: switching back shows the one already written (free)
        e = st.session_state.setdefault("emails", {}).get(persona)
        st.session_state["email"] = e   # the email the downloads include
        if e:
            show_email_text(e, by_id)


def create_email(r, persona):
    saved = r.get("saved_email")
    if saved and saved.get("persona", settings.DEFAULT_PERSONA) == persona:
        email = saved                       # the sample's saved email: no AI needed
    else:
        with st.spinner(f"Writing the email for the {persona}…"):
            try:
                email = make_email(r, persona)
            except Exception as e:
                friendly_error(e)
                return
    st.session_state.setdefault("emails", {})[persona] = email
    st.session_state["email"] = email
    st.rerun()   # redraw so the downloads at the top include the new email


def show_email_text(e, by_id):
    for w in e["warnings"]:
        if w != report.LOW_FIT_NOTE:   # already shown beside the button
            st.warning(safe(w))
    st.markdown(f"**Subject:** {safe(e['subject'])}")
    st.code(e["body"], language=None, wrap_lines=True)
    st.html(f'<div class="wn-quiet">{e["word_count"]} words · opening trigger '
            f'{ui.cite(e["trigger_sources"], by_id) or "none"}</div>')
    if e.get("ai_model"):
        st.html(ui.model_note(e["ai_model"]))


def show_behind_the_scenes(r):
    with st.expander("Behind the scenes: code checks and timings"):
        st.markdown("**Code checks** (what code changed in the AI's answer)")
        st.markdown("\n".join(f"- {safe(c)}" for c in r["checks"]) or "None needed.")
        if r.get("timings"):
            st.markdown("**Time taken:** " + " · ".join(f"{k} {v}s" for k, v in r["timings"].items()))
        email = st.session_state.get("email") or {}
        models = [ui.model_details(r.get("ai_model"), "Report"), ui.model_details(email.get("ai_model"), "Email")]
        if any(models):
            st.markdown("**AI model:**\n" + "\n".join(f"- {safe(m)}" for m in models if m))


def report_bar(r):
    """'← Back to home' on the left; 'Download PDF' and 'Download CSV' on the right.
    The downloads include the email once it has been created."""
    email = st.session_state.get("email")
    name = "whynow-" + "".join(c if c.isalnum() else "-" for c in exports.company_name(r).lower()).strip("-")
    left, right = st.columns([1, 1], vertical_alignment="center")
    left.html(ui.back_link())
    with right, st.container(key="wn_downloads", horizontal=True, horizontal_alignment="right", gap="small"):
        st.download_button("Download PDF", exports.report_to_pdf(r, email), file_name=f"{name}.pdf",
                           mime="application/pdf", icon=":material/picture_as_pdf:")
        st.download_button("Download CSV", exports.report_to_csv(r, email), file_name=f"{name}.csv",
                           mime="text/csv", icon=":material/table_view:")


def show_report(r):
    by_id = {s["id"]: s for s in r["all_sources"]}
    report_bar(r)
    if st.session_state.get("sample"):
        st.info(f"Saved example from {r.get('saved_on', 'an earlier run')}. "
                "Search above to research any UK company live.", icon=":material/history:")

    with st.container(key="wn_overview"):   # the CSS makes both cards the same height
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
    st.set_page_config(page_title=f"{settings.APP_NAME} · {settings.APP_LABEL}",
                       page_icon=":material/bolt:", layout="wide")
    st.html(ui.css())
    open_sample_from_link()
    st.html(ui.topbar())
    if "report" in st.session_state or st.session_state.get("matches") is not None:
        search_form()          # the same search bar, above the picker or report
        pick_company()
        if "report" in st.session_state:
            show_report(st.session_state["report"])
    else:
        landing()
    st.html(ui.footer())

main()
