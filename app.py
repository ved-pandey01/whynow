"""
app.py — the web page.

  1. Type a company name or website (and, optionally, what you're selling).
  2. Pick the right company from the Companies House matches.
  3. Get the report as 8 cards: snapshot, news, signals, fit score, who to contact,
     email, what we couldn't find, and sources.

Run it with:
    .venv/bin/streamlit run app.py
"""

import re

import streamlit as st

import report
import settings
from ai.provider import AIError
from sources import uk_companies_house as ch

SIGNAL_LABELS = {
    "hiring_sales": "Hiring for sales", "funding": "Funding", "new_sales_leader": "New sales leader",
    "expansion": "Expansion", "layoffs": "Layoffs / cost-cutting", "headcount_growth": "Headcount growing",
    "headcount_decline": "Headcount shrinking", "new_director": "New director",
}


# ---------------------------------------------------------------------------
# Fetching (cached, so searching the same company again costs no API credits)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def find_companies(typed):
    return ch.search_companies(typed)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def make_report(company_number, typed, offer):
    research = report.research_company(company_number, typed)
    return report.build_report(research, offer=offer)


# ---------------------------------------------------------------------------
# Small helpers for showing text
# ---------------------------------------------------------------------------
def safe(text):
    """Stop '$' in text (e.g. '$50m') being read as a maths formula by Streamlit."""
    return (text or "").replace("$", "\\$")


def cite(ids, sources_by_id):
    """Turn ['S1', 'S3'] into clickable links: [S1] [S3]."""
    links = [f"[{i}]({sources_by_id[i]['url']})" for i in ids if i in sources_by_id]
    return " " + " ".join(links) if links else ""


def claim_line(claim, sources_by_id):
    return f"- {safe(claim['text'])}{cite(claim['sources'], sources_by_id)}"


def card(title):
    """A bordered box with a heading. Use it with 'with card(...):'."""
    box = st.container(border=True)
    box.markdown(f"#### {title}")
    return box


# ---------------------------------------------------------------------------
# The 8 result cards
# ---------------------------------------------------------------------------
def show_header(r):
    c = r["company"]
    st.subheader(c["name"])
    st.caption(f"Company number [{c['number']}]({c['source']}) · status: {c['status'] or 'not found'}")
    if r["not_worth_contacting"]:
        st.error("**Not worth contacting.** " + safe(r["fit_reasons"][0]["text"]))
    for w in r["warnings"]:
        st.warning(safe(w))


def show_snapshot(r, by_id):
    with card("1 · Snapshot"):
        if r["what_they_do"]:
            st.markdown(f"**What they do:** {safe(r['what_they_do']['text'])}"
                        f"{cite(r['what_they_do']['sources'], by_id)}")
        else:
            st.markdown("**What they do:** not found")
        st.markdown("\n".join(claim_line(c, by_id) for c in r["snapshot"]) or "Nothing else found.")


def show_news(r, by_id):
    with card("2 · Recent news"):
        if not r["news"]:
            st.markdown("No relevant news found in the last 6 months.")
        for n in r["news"]:
            s = by_id[n["source"]]
            st.markdown(f"**[{safe(s['title'])}]({s['url']})** · {s['date'] or 'no date'}  \n"
                        f"{safe(n['why_it_matters'])}")


def show_signals(r, by_id):
    with card("3 · \"Why now\" signals"):
        if not r["signals"]:
            st.markdown("No buying signals found.")
        for s in r["signals"]:
            label = SIGNAL_LABELS.get(s["type"], s["type"])
            st.markdown(f"- **{label}:** {safe(s['text'])}{cite(s['sources'], by_id)}")


def show_fit_and_role(r, by_id):
    left, right = st.columns([3, 2])
    with left, card("4 · ICP fit score"):
        st.metric("Fit score", f"{r['fit_score']}/10", label_visibility="collapsed")
        st.markdown("\n".join(claim_line(c, by_id) for c in r["fit_reasons"]))
    with right, card("5 · Who to contact"):
        if r["suggested_role"]:
            st.markdown(f"**{safe(r['suggested_role'])}**  \n{safe(r['role_reason'])}")
        else:
            st.markdown("No one: this company isn't worth contacting.")


def show_email(r, by_id):
    with card("6 · Outreach email"):
        e = r["email"]
        if not e:
            st.markdown("No email written: this company isn't worth contacting.")
            return
        for w in e["warnings"]:
            st.warning(safe(w))
        st.markdown(f"**Subject:** {safe(e['subject'])}")
        st.code(e["body"], language=None, wrap_lines=True)
        trigger = cite(e["trigger_sources"], by_id) or " none"
        st.caption(f"{e['word_count']} words · opening trigger:{trigger} · "
                   "click the copy icon (top right of the email) to copy it")


def show_not_found(r):
    with card("7 · What we couldn't find"):
        st.markdown("\n".join(f"- {safe(x)}" for x in r["not_found"]) or "Nothing missing.")


def show_sources(r):
    with card("8 · Sources"):
        for s in r["sources"]:
            dated = f" · {s['date']}" if s.get("date") else ""
            title = re.sub(r"^\[\w+\]\s*", "", s["title"])  # drop internal tags like "[hiring_sales]"
            st.markdown(f"**{s['id']}** [{safe(title)}]({s['url']}){dated}")


def show_behind_the_scenes(r):
    with st.expander("Behind the scenes: code checks and timings"):
        st.markdown("**Code checks** (what code changed in the AI's answer)")
        st.markdown("\n".join(f"- {safe(c)}" for c in r["checks"]) or "None needed.")
        if r.get("timings"):
            st.markdown("**Time taken:** " + " · ".join(f"{k} {v}s" for k, v in r["timings"].items()))


def show_report(r):
    by_id = {s["id"]: s for s in r["all_sources"]}
    show_header(r)
    show_snapshot(r, by_id)
    show_news(r, by_id)
    show_signals(r, by_id)
    show_fit_and_role(r, by_id)
    show_email(r, by_id)
    show_not_found(r)
    show_sources(r)
    show_behind_the_scenes(r)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def search_form():
    """Step 1: what company, and what are we selling?"""
    with st.form("search"):
        typed = st.text_input("Company name or website", placeholder="e.g. Cognism or paddle.com")
        offer = st.text_area("What are you selling?", value=settings.DEFAULT_OFFER, height=80)
        if st.form_submit_button("Find company", type="primary"):
            st.session_state.pop("report", None)
            st.session_state["offer"] = offer
            st.session_state["typed"] = typed
            try:
                st.session_state["matches"] = find_companies(typed)
            except ch.CompaniesHouseError as e:
                st.session_state["matches"] = None
                st.error(safe(str(e)))


def match_label(m):
    return f"{m['name']} · {m['company_number']} · {m['status'] or 'status unknown'} · {m['address'] or ''}"


def pick_company():
    """Step 2: choose the right company from the Companies House matches."""
    matches, typed = st.session_state.get("matches"), st.session_state.get("typed")
    if matches is None:
        return
    if not matches:
        st.info(f'No companies found for "{safe(typed)}". Check the spelling or try the website.')
        return
    if not matches[0]["close_match"]:
        st.warning(f'No close match found for "{safe(typed)}". Only pick one below if you\'re sure it\'s right.')
    choice = st.radio("Which company did you mean?", matches, format_func=match_label)
    if st.button("Research this company", type="primary"):
        run_report(choice)


def run_report(choice):
    with st.spinner(f"Researching {choice['name']}… this usually takes 20–40 seconds."):
        try:
            st.session_state["report"] = make_report(
                choice["company_number"], st.session_state["typed"], st.session_state["offer"])
        except (ch.CompaniesHouseError, AIError) as e:
            st.error(safe(str(e)))
        except Exception as e:
            print(f"Unexpected error: {type(e).__name__}: {e}")  # shown in the terminal, for fixing
            st.error("Something unexpected went wrong. Please try again in a minute.")


def main():
    st.set_page_config(page_title="Account Research Agent", page_icon="🔎", layout="centered")
    st.title("Account Research Agent")
    st.caption("Type a UK company and get a sourced research report, ICP fit score and outreach email.")
    search_form()
    pick_company()
    if "report" in st.session_state:
        st.divider()
        show_report(st.session_state["report"])


main()
