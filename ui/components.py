"""
components.py — small pieces of the page, each returned as HTML text.

Safety: everything we fetched from the internet (headlines, names, descriptions)
goes through esc() before it's put into HTML, so a web page title can never add
code to our page. Links only allow http(s) addresses.
"""

import html
import re
from pathlib import Path
from urllib.parse import quote_plus

import settings

CSS_FILE = Path(__file__).with_name("style.css")

# Signal type → (label, Material icon name)
SIGNALS = {
    "hiring_sales": ("Hiring for sales", "person_add"),
    "funding": ("Funding", "payments"),
    "headcount_growth": ("Growing team", "trending_up"),
    "headcount_decline": ("Shrinking team", "trending_down"),
    "new_sales_leader": ("New sales leader", "military_tech"),
    "expansion": ("Expansion", "public"),
    "layoffs": ("Layoffs or cost-cutting", "warning"),
    "new_director": ("New director", "badge"),
}
WARNING_SIGNALS = {"layoffs", "headcount_decline"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def esc(text):
    """Make fetched text safe to put inside HTML."""
    return html.escape(str(text if text is not None else ""), quote=True)


def safe_url(url):
    """Only allow normal web links; anything else becomes a dead link."""
    url = str(url or "").strip()
    if url and not url.startswith(("http://", "https://")) and "." in url and " " not in url:
        url = "https://" + url   # e.g. 'www.paddle.com'
    return esc(url) if url.startswith(("http://", "https://")) else "#"


def link(url, text):
    return f'<a href="{safe_url(url)}" target="_blank" rel="noopener noreferrer">{esc(text)}</a>'


def icon(name):
    return f'<span class="icon" aria-hidden="true">{esc(name)}</span>'


def clean_title(title):
    """Drop our internal tags, e.g. '[expansion] Paddle opens…' → 'Paddle opens…'."""
    return re.sub(r"^\[\w+\]\s*", "", title or "")


def cite(ids, by_id):
    """Small clickable source tags: S1 S4."""
    return "".join(f'<a class="wn-cite" href="{safe_url(by_id[i]["url"])}" target="_blank" '
                   f'rel="noopener noreferrer" title="{esc(clean_title(by_id[i]["title"]))}">{esc(i)}</a>'
                   for i in ids if i in by_id)


def fact_cite(fact, by_id):
    return cite([fact["source"]], by_id) if fact and fact.get("source") else ""


def bullets(claims, by_id, empty="Nothing found."):
    if not claims:
        return f'<p class="wn-empty">{esc(empty)}</p>'
    return "<ul>" + "".join(f"<li>{esc(c['text'])}{cite(c['sources'], by_id)}</li>" for c in claims) + "</ul>"


def card(title, icon_name, body):
    return f'<div class="wn-card"><h4>{icon(icon_name)}{esc(title)}</h4>{body}</div>'


def css():
    return f"<style>{CSS_FILE.read_text()}</style>"


# ---------------------------------------------------------------------------
# Landing page and footer
# ---------------------------------------------------------------------------
def wordmark(css_class="wn-wordmark"):
    """'WhyNow' with 'Now' in teal (works for any name ending in 'Now')."""
    name = settings.APP_NAME
    if name.endswith("Now"):
        return f'<div class="{css_class}">{esc(name[:-3])}<span>Now</span></div>'
    return f'<div class="{css_class}">{esc(name)}</div>'


def hero():
    return (f'<div class="wn-hero"><div class="wn-label">{esc(settings.APP_LABEL)}</div>{wordmark()}'
            f'<div class="wn-slogan">{esc(settings.APP_SLOGAN)}</div>'
            f'<p class="wn-intro">{esc(settings.APP_INTRO)}</p></div>')


def mini_brand():
    return wordmark("wn-mini")


def how_it_works():
    steps = [("search", "Find the company"), ("radar", "Research the signals"), ("speed", "Score the fit")]
    tiles = "".join(f'<div class="wn-step">{icon(i)}<div class="wn-step-num">STEP {n}</div>'
                    f'<div class="wn-step-text">{esc(t)}</div></div>' for n, (i, t) in enumerate(steps, 1))
    return f'<div class="wn-steps">{tiles}</div>'


def footer():
    dot = '<span class="dot">·</span>'
    return (f'<div class="wn-footer">{esc(settings.AUTHOR_NAME)}{dot}'
            f'{link(settings.AUTHOR_LINKEDIN, "LinkedIn")}{dot}{link(settings.AUTHOR_GITHUB, "GitHub")}</div>')


# ---------------------------------------------------------------------------
# Company picker
# ---------------------------------------------------------------------------
def status_chip(status):
    status = status or "status unknown"
    kind = "ok" if status == "active" else "bad"
    return f'<span class="wn-chip {kind}">{esc(status.capitalize())}</span>'


def picker_card(m, typed_domain=None):
    """One Companies House match: name, status, then one quiet line of details."""
    details = [m.get("industry"), m.get("size"), f"Founded {m['founded'][:4]}" if m.get("founded") else None,
               m.get("city")]
    if typed_domain and typed_domain in (m.get("name") or "").lower():
        details.append(typed_domain)   # the website the user typed, only when it's in this company's name
    meta = " · ".join(esc(d) for d in details if d) or "No details found"
    return (f'<div class="wn-pick-name">{esc(m["name"])} {status_chip(m.get("status"))}</div>'
            f'<div class="wn-pick-meta">{meta} · {link(m["source"], "Companies House")}</div>')


# ---------------------------------------------------------------------------
# Report: banner, profile card, stat tiles
# ---------------------------------------------------------------------------
def initials(name):
    words = [w for w in re.split(r"\W+", name or "") if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def logo(profile):
    """The company's website icon on top of its initials. If the icon fails, the initials show."""
    name = (profile.get("brand") or {}).get("value") or (profile.get("legal_name") or {}).get("value")
    img = ""
    if profile.get("domain"):
        img = f'<img src="https://icons.duckduckgo.com/ip3/{esc(profile["domain"])}.ico" alt="">'
    return f'<div class="wn-logo">{esc(initials(name))}{img}</div>'


def banner(r, by_id):
    p, c = r["profile"], r["company"]
    name = (p.get("brand") or {}).get("value") or c["name"]
    chips = status_chip(c["status"])
    if (p.get("business_model") or {}).get("value"):
        chips += f'<span class="wn-chip">{esc(p["business_model"]["value"])}</span>'
    if ((p.get("saas") or {}).get("value") or "").lower() == "yes":
        chips += '<span class="wn-chip">SaaS</span>'
    what = r.get("what_they_do")
    what_html = (f'<div class="wn-what">{esc(what["text"])}{cite(what["sources"], by_id)}</div>' if what
                 else '<div class="wn-what wn-empty">What they do: not found</div>')
    alert = ""
    if r["not_worth_contacting"]:
        alert = f'<div class="wn-alert">Not worth contacting. {esc(r["fit_reasons"][0]["text"])}</div>'
    return (f'<div class="wn-card"><div class="wn-banner">{logo(p)}<div>'
            f'<div class="wn-name">{esc(name)}</div>'
            f'<div class="wn-legal">{esc(c["name"])} · {link(c["source"], c["number"])}</div>'
            f'<div>{chips}</div></div></div>{what_html}{alert}</div>')


def profile_card(r, by_id):
    p = r["profile"]

    def row(label, fact, value=None):
        if not fact:
            shown = '<span class="wn-empty">not found</span>'
        else:
            shown = value or esc(fact["value"])
        return (f'<div><div class="wn-fact-label">{esc(label)}</div>'
                f'<div class="wn-fact-value">{shown}{fact_cite(fact, by_id)}</div></div>')

    website = p.get("website")
    followers = p.get("linkedin_followers")
    rows = [
        row("Industry", p.get("industry")),
        row("Headquarters", p.get("headquarters")),
        row("Website", website, link(website["value"], p.get("domain") or website["value"]) if website else None),
        row("LinkedIn followers", followers, f"{followers['value']:,}" if followers else None),
    ]
    return f'<div class="wn-card"><div class="wn-facts">{"".join(rows)}</div></div>'


def stat_tile(label, icon_name, fact, by_id, value=None, trend=None):
    if fact:
        shown = f'<div class="wn-stat-value">{value or esc(fact["value"])}{fact_cite(fact, by_id)}</div>'
    else:
        shown = '<div class="wn-stat-value missing">not found</div>'
    trend_html = ""
    if trend:
        direction = "down" if trend.strip().startswith("-") else "up"
        arrow = "▼" if direction == "down" else "▲"
        trend_html = f'<div class="wn-trend {direction}">{arrow} {esc(trend)}</div>'
    return (f'<div class="wn-stat"><div class="wn-stat-label">{icon(icon_name)}{esc(label)}</div>'
            f'{shown}{trend_html}</div>')


def stats_row(r, by_id):
    p = r["profile"]
    headcount = p.get("headcount")
    change = (p.get("headcount_change") or {}).get("value")
    tiles = [
        stat_tile("Employees", "groups", headcount, by_id,
                  value=f"{headcount['value']:,}" if headcount else None, trend=change),
        stat_tile("Founded", "calendar_month", p.get("founded"), by_id),
        stat_tile("Funding raised", "savings", p.get("total_raised"), by_id),
        stat_tile("Revenue", "bar_chart", p.get("revenue"), by_id),
    ]
    return f'<div class="wn-stats">{"".join(tiles)}</div>'


# ---------------------------------------------------------------------------
# Report: fit, contact, signals, news, gaps, sources
# ---------------------------------------------------------------------------
def fit_band(score):
    """(css class, label) for a score: green 7–10, amber 4–6, red 1–3."""
    if score >= 7:
        return "strong", "Strong fit"
    if score >= 4:
        return "possible", "Possible fit"
    return "weak", "Weak fit"


def fit_badge(score):
    kind, label = fit_band(score)
    return (f'<div class="wn-badge {kind}"><div class="wn-badge-circle">{int(score)}<small>/10</small></div>'
            f'<div class="wn-badge-label">{esc(label)}</div></div>')


def fit_card(r, by_id):
    body = f'<div class="wn-fit">{fit_badge(r["fit_score"])}<div>{bullets(r["fit_reasons"], by_id)}</div></div>'
    return card("ICP fit", "target", body)


def role_html(r):
    if not r.get("suggested_role"):
        return '<p class="wn-empty">No one: this company isn\'t worth contacting.</p>'
    return (f'<div class="wn-name" style="font-size:1.15rem">{esc(r["suggested_role"])}</div>'
            f'<p class="wn-quiet" style="margin-top:0.4rem">{esc(r.get("role_reason"))}</p>')


def linkedin_search_url(role, company):
    """A normal LinkedIn people search. We only build the link; nothing is fetched from LinkedIn."""
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{role} {company}")


def signal_tile(s, by_id):
    label, icon_name = SIGNALS.get(s["type"], (s["type"].replace("_", " ").capitalize(), "bolt"))
    warn = " warn" if s["type"] in WARNING_SIGNALS else ""
    return (f'<div class="wn-signal{warn}"><div class="wn-signal-head">{icon(icon_name)}{esc(label)}</div>'
            f'<div class="wn-signal-text">{esc(s["text"])}{cite(s["sources"], by_id)}</div></div>')


def signals_card(r, by_id):
    if not r["signals"]:
        body = '<p class="wn-empty">No buying signals found.</p>'
    else:
        body = f'<div class="wn-signals">{"".join(signal_tile(s, by_id) for s in r["signals"])}</div>'
    return card("Why now", "bolt", body)


def news_card(r, by_id):
    items = []
    for n in r["news"]:
        s = by_id[n["source"]]
        items.append(f'<div class="wn-news-item"><div class="wn-news-title">{link(s["url"], clean_title(s["title"]))}'
                     f' <span class="wn-quiet">· {esc(s.get("date") or "no date")}</span></div>'
                     f'<div class="wn-news-why">{esc(n["why_it_matters"])}</div></div>')
    body = "".join(items) or f'<p class="wn-empty">No relevant news in the last {settings.NEWS_WINDOW_MONTHS} months.</p>'
    return card("Recent news", "newspaper", body)


def not_found_card(r):
    gaps = r["not_found"]
    body = ("<ul>" + "".join(f"<li>{esc(g)}</li>" for g in gaps) + "</ul>") if gaps \
        else '<p class="wn-empty">Nothing missing.</p>'
    return card("What we couldn't find", "search_off", body)


def sources_card(r):
    rows = "".join(f'<li><b>{esc(s["id"])}</b>{link(s["url"], clean_title(s["title"]))}'
                   f'{" · " + esc(s["date"]) if s.get("date") else ""}</li>' for s in r["sources"])
    return card("Sources", "link", f'<ul class="wn-sources">{rows}</ul>')


def model_note(model, what="Report"):
    """A small quiet line, e.g. 'Report written by gemini-3.5-flash (backup: gemini-3.6-flash was busy)'."""
    if not model:
        return ""
    note = f"{what} written by {model['name']}"
    if model.get("backup"):
        note += f" (backup model, because {model['reason']})"
    return f'<div class="wn-quiet" style="margin:0.4rem 0 1rem">{icon("smart_toy")} {esc(note)}</div>'
