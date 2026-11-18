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
GROWTH_SIGNALS = {"headcount_growth", "funding", "expansion", "hiring_sales"}


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


def topbar():
    """Slim top bar: brand and label on the left, author on the right."""
    return (f'<div class="wn-topbar"><div class="wn-topbar-brand">'
            f'<a class="wn-home" href="{HOME_URL}" target="_self" title="Back to home">{wordmark("wn-mini")}</a>'
            f'<span class="wn-topbar-label">· {esc(settings.APP_LABEL)}</span></div>'
            f'{author_pill()}</div>')


def author_pill():
    """Top-right pill: 'VP' badge, 'Built by …' and ↗, linking to LinkedIn in a new tab."""
    return (f'<a class="wn-built" href="{safe_url(settings.AUTHOR_LINKEDIN)}" target="_blank" rel="noopener noreferrer">'
            f'<span class="wn-vp">{esc(settings.AUTHOR_INITIALS)}</span>'
            f'<span class="wn-built-full">Built by {esc(settings.AUTHOR_NAME)}</span>'
            f'<span class="wn-built-short">Built by {esc(settings.AUTHOR_SHORT_NAME)}</span>'
            f'<span class="wn-arrow" aria-hidden="true">↗</span></a>')


HOME_URL = "./"   # the home page without "?sample=1"; loading it starts fresh, so the report is cleared
SAMPLE_URL = "./?sample=1"   # opens the saved sample report


def back_link():
    """'← Back to home' above every report (live or sample)."""
    return f'<a class="wn-back" href="{HOME_URL}" target="_self">← Back to home</a>'


def hero():
    """Left column: wordmark, headline, and the animated 'Because…' line (pure CSS)."""
    typing = "".join(f"<span>{esc(line)}</span>" for line in settings.APP_TYPING_LINES[:4])
    # Each sentence in its own span, so a line only ever breaks between sentences
    sentences = [x.strip() for x in settings.APP_HEADLINE.replace(". ", ".|").split("|")]
    headline = " ".join(f"<span>{esc(x)}</span>" for x in sentences)
    return (f'<div class="wn-hero"><div class="wn-title">{wordmark()}'
            f'<h2 class="wn-headline">{headline}</h2></div>'
            f'<div class="wn-typing" aria-label="{esc(settings.APP_TYPING_LINES[0])}">{typing}</div></div>')


def _background_svg():
    """
    The landing background, drawn as one picture: a faint grid that fades towards the edges,
    a soft blue-green glow behind the wordmark, and a thin glowing line chart rising
    from bottom-left to top-right with 4 dots. Being a background, it can never cover content.
    """
    dots = [(200, 742), (560, 598), (1300, 262), (1500, 148)]   # none under the "What you get" card
    dot_svg = "".join(f'<circle cx="{x}" cy="{y}" r="11" fill="url(#dotglow)"/>'
                      f'<circle cx="{x}" cy="{y}" r="3.5" fill="#E2F8F0"/>' for x, y in dots)
    path = "M 30 780 C 260 740, 380 650, 560 598 S 820 560, 940 470 S 1180 380, 1300 262 S 1460 160, 1600 110"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="860" viewBox="0 0 1600 860">
<defs>
  <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
    <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#94A3B8" stroke-opacity="0.09" stroke-width="1"/></pattern>
  <radialGradient id="fade" cx="50%" cy="40%" r="60%">
    <stop offset="0%" stop-color="#fff" stop-opacity="1"/><stop offset="100%" stop-color="#fff" stop-opacity="0"/></radialGradient>
  <mask id="gridmask"><rect width="1600" height="860" fill="url(#fade)"/></mask>
  <radialGradient id="auroraBlue" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="#3B82F6" stop-opacity="0.32"/><stop offset="100%" stop-color="#3B82F6" stop-opacity="0"/></radialGradient>
  <radialGradient id="auroraGreen" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="#10B981" stop-opacity="0.24"/><stop offset="100%" stop-color="#10B981" stop-opacity="0"/></radialGradient>
  <linearGradient id="line" x1="0" y1="1" x2="1" y2="0">
    <stop offset="0%" stop-color="#3B82F6" stop-opacity="0.15"/><stop offset="45%" stop-color="#3B82F6" stop-opacity="0.75"/>
    <stop offset="100%" stop-color="#10B981" stop-opacity="0.9"/></linearGradient>
  <radialGradient id="dotglow" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="#34D399" stop-opacity="0.75"/><stop offset="100%" stop-color="#34D399" stop-opacity="0"/></radialGradient>
  <filter id="blur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter>
</defs>
<ellipse cx="430" cy="290" rx="420" ry="190" fill="url(#auroraBlue)"/>
<ellipse cx="650" cy="320" rx="380" ry="170" fill="url(#auroraGreen)"/>
<rect width="1600" height="860" fill="url(#grid)" mask="url(#gridmask)"/>
<path d="{path}" fill="none" stroke="url(#line)" stroke-width="7" stroke-opacity="0.35" filter="url(#blur)"/>
<path d="{path}" fill="none" stroke="url(#line)" stroke-width="2.2" stroke-linecap="round"/>
{dot_svg}
</svg>"""


def landing_background():
    """CSS that puts the background picture behind the top of the landing page only."""
    import base64
    data = base64.b64encode(_background_svg().encode()).decode()
    return (f'<style>.stApp {{ background-image: url("data:image/svg+xml;base64,{data}"); '
            f'background-repeat: no-repeat; background-position: center -40px; background-size: 1600px auto; }}</style>')


def how_it_works():
    """Three compact step cards, one short line each."""
    steps = [("search", "Find the company", "Official UK company data"),
             ("radar", "Research the signals", "Hiring, funding, new leaders"),
             ("speed", "Score the fit", "1–10 with reasons")]
    tiles = "".join(f'<div class="wn-step">{icon(i)}<div><div class="wn-step-text">{esc(t)}</div>'
                    f'<div class="wn-step-sub">{esc(sub)}</div></div></div>' for i, t, sub in steps)
    return f'<div class="wn-steps">{tiles}</div>'


def preview_card(sample=None):
    """
    Right column: what a report looks like. With the saved sample, it shows that real report's facts;
    without it, a neutral example layout with no company facts at all.
    """
    if sample:
        p = sample["profile"]
        name = (p.get("brand") or {}).get("value") or sample["company"]["name"]
        meta = " · ".join(esc(f["value"]) for f in (p.get("industry"), p.get("headquarters")) if f) or "UK company"
        score = sample["fit_score"]
        signals = [SIGNALS.get(s["type"], (s["type"], "bolt")) for s in sample["signals"][:3]]
        source_count = len(sample["sources"])
        logo_html = logo(p)
        subtitle = f"Real result for {esc(name)}, saved {esc(sample.get('saved_on', ''))}"
        note = '<div class="wn-pv-view">View full report →</div>'
    else:
        name, meta, score, source_count = "Your target company", "Industry · Headquarters", 8, 12
        signals = [SIGNALS["hiring_sales"], SIGNALS["funding"], SIGNALS["new_sales_leader"]]
        logo_html = f'<div class="wn-logo">{icon("domain")}</div>'
        subtitle = "Example layout. Every report is built from live, sourced data."
        note = ""
    kind, label = fit_band(score)
    lines = "".join(f'<div class="wn-pv-line">{icon(i)}{esc(t)}</div>' for t, i in signals) \
        or '<div class="wn-pv-line wn-empty">None found</div>'
    opening = (f'<a class="wn-preview wn-preview-link" href="{SAMPLE_URL}" title="Open the full sample report">'
               if sample else '<div class="wn-preview">')
    closing = "</a>" if sample else "</div>"
    return (f'{opening}<div class="wn-preview-title">EXAMPLE REPORT</div>'
            f'<div class="wn-preview-sub">{subtitle}</div>'
            f'<div class="wn-pv"><div class="wn-pv-company">{logo_html}'
            f'<div><div class="wn-pv-name">{esc(name)}</div><div class="wn-pv-meta">{meta}</div></div></div></div>'
            f'<div class="wn-pv-grid">'
            f'<div class="wn-pv"><div class="wn-pv-label">Fit score</div><div class="wn-pv-score">'
            f'<div class="wn-badge {kind}"><div class="wn-badge-circle">{int(score)}</div></div>'
            f'<div class="wn-badge {kind}"><div class="wn-badge-label">{esc(label)}</div></div></div></div>'
            f'<div class="wn-pv"><div class="wn-pv-label">{esc(settings.SIGNALS_TITLE)}</div>{lines}</div>'
            f'<div class="wn-pv"><div class="wn-pv-label">Sources</div><div class="wn-pv-big">{source_count}</div>'
            f'<div class="wn-pv-meta">every fact linked</div></div>'
            f'</div>{note}{closing}')


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
    closed = " closed" if (m.get("status") or "active") != "active" else ""
    return (f'<div class="wn-pick{closed}"><div class="wn-pick-name">{esc(m["name"])} {status_chip(m.get("status"))}</div>'
            f'<div class="wn-pick-meta">{meta} · {link(m["source"], "Companies House")}</div></div>')


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


def stat_tile(label, icon_name, fact, by_id, value=None, trend=None, note=None):
    if fact:
        shown = f'<div class="wn-stat-value">{value or esc(fact["value"])}{fact_cite(fact, by_id)}</div>'
    else:
        shown = '<div class="wn-stat-value missing">not found</div>'
    trend_html = ""
    if trend:
        direction = "down" if trend.strip().startswith("-") else "up"
        arrow = "▼" if direction == "down" else "▲"
        trend_html = f'<div class="wn-trend {direction}">{arrow} {esc(trend)}</div>'
    note_html = f'<div class="wn-stat-note">{esc(note)}</div>' if note and fact else ""
    return (f'<div class="wn-stat"><div class="wn-stat-label">{icon(icon_name)}{esc(label)}</div>'
            f'{shown}{trend_html}{note_html}</div>')


def split_totals(value):
    """'318.34M USD, 8.00M GBP' → ('318.34M USD', 'Also reported: 8.00M GBP'). The first listed total is the main one."""
    parts = [x.strip() for x in str(value or "").split(",") if x.strip()]
    if len(parts) < 2:
        return value, None
    return parts[0], "Also reported: " + ", ".join(parts[1:])


def stats_row(r, by_id):
    p = r["profile"]
    headcount = p.get("headcount")
    change = (p.get("headcount_change") or {}).get("value")
    tiles = [
        stat_tile("Employees", "groups", headcount, by_id,
                  value=f"{headcount['value']:,}" if headcount else None, trend=change),
        stat_tile("Founded", "calendar_month", p.get("founded"), by_id),
        stat_tile("Funding raised", "savings", p.get("total_raised"), by_id,
                  value=esc(split_totals(p["total_raised"]["value"])[0]) if p.get("total_raised") else None,
                  note=split_totals(p["total_raised"]["value"])[1] if p.get("total_raised") else None),
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
    warn = " warn" if s["type"] in WARNING_SIGNALS else (" grow" if s["type"] in GROWTH_SIGNALS else "")
    return (f'<div class="wn-signal{warn}"><div class="wn-signal-head">{icon(icon_name)}{esc(label)}</div>'
            f'<div class="wn-signal-text">{esc(s["text"])}{cite(s["sources"], by_id)}</div></div>')


def signals_card(r, by_id):
    if not r["signals"]:
        body = f'<p class="wn-empty">No reasons to reach out now were found.</p>'
    else:
        body = f'<div class="wn-signals">{"".join(signal_tile(s, by_id) for s in r["signals"])}</div>'
    return card(settings.SIGNALS_TITLE, "bolt", body)


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


def progress_card(company_name, steps, states):
    """Live research steps: a tick when done, a small spinner on the current one, later ones dimmed."""
    rows = []
    for step, state in zip(steps, states):
        if state == "done":
            mark = icon("check_circle")
        elif state == "active":
            mark = '<span class="wn-spin" aria-hidden="true"></span>'
        else:
            mark = icon("radio_button_unchecked")
        rows.append(f'<li class="wn-step-row {state}">{mark}<span>{esc(step)}</span></li>')
    done = states.count("done")
    return (f'<div class="wn-card wn-progress"><h4>{icon("travel_explore")}Researching {esc(company_name)}</h4>'
            f'<ul class="wn-progress-list">{"".join(rows)}</ul>'
            f'<div class="wn-quiet">{done} of {len(steps)} done · usually 20–40 seconds</div></div>')
