"""
Draw the README's "How it works" architecture diagram as two SVG files (dark and light), in WhyNow's colours.

    .venv/bin/python -m scripts.make_diagrams

Writes assets/diagrams/how-it-works-dark.svg and how-it-works-light.svg. The SVGs are plain shapes and
text: no web fonts, no scripts and no external files, so GitHub shows them as normal images.
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent.parent / "assets" / "diagrams"
W, H = 1200, 790
FONT = "Inter, 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Helvetica, Arial, sans-serif"

THEMES = {
    "dark": {"bg": "#0B1120", "card": "#111827", "border": "#1F2937", "head": "#FFFFFF", "body": "#E5E7EB",
             "muted": "#94A3B8", "chip": "#0F172A", "chip_border": "#263247", "glow": 0.22, "shadow": "#000000",
             "shadow_opacity": 0.45, "grid": "#FFFFFF", "grid_opacity": 0.025},
    "light": {"bg": "#F8FAFC", "card": "#FFFFFF", "border": "#E2E8F0", "head": "#0F172A", "body": "#1E293B",
              "muted": "#475569", "chip": "#F1F5F9", "chip_border": "#E2E8F0", "glow": 0.10, "shadow": "#0F172A",
              "shadow_opacity": 0.08, "grid": "#0F172A", "grid_opacity": 0.03},
}
BLUE, EMERALD = "#3B82F6", "#10B981"

# Simple outline icons on a 24 x 24 grid (drawn with a stroke, no fill)
ICONS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "doc": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>',
}


def text_width(s, size, bold=False):
    """A safe estimate of how wide text is, so nothing overflows whichever font the viewer has."""
    return len(s) * size * (0.60 if bold else 0.56)


def t(x, y, s, size, fill, weight=400, anchor="start", spacing=0):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{escape(s)}</text>')


def icon(name, x, y, size, colour):
    scale = size / 24
    return (f'<g transform="translate({x} {y}) scale({scale})" fill="none" stroke="{colour}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>')


def chip(x, y, label, c, size=15):
    w = text_width(label, size) + 28
    return (f'<rect x="{x}" y="{y}" width="{w:.0f}" height="34" rx="17" fill="{c["chip"]}" stroke="{c["chip_border"]}"/>'
            + t(x + w / 2, y + 22, label, size, c["body"], 500, "middle")), w


_gradients = []   # one gradient per arrow, fixed to page coordinates (see arrow())


def arrow(x1, y1, x2, y2, width=2.5, head=7, glow=True):
    """
    A blue-to-emerald arrow from (x1, y1) to (x2, y2), with a soft glow.
    The gradient uses page coordinates: a gradient sized to the line itself doesn't show on a perfectly
    straight line (it has no height or width), which hid the lines in the first version.
    """
    gid = f"arrow{len(_gradients)}"
    _gradients.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{x1}" y1="{y1}" x2="{x2}" '
                      f'y2="{y2}"><stop offset="0" stop-color="{BLUE}"/><stop offset="1" stop-color="{EMERALD}"/>'
                      f'</linearGradient>')
    horizontal = y1 == y2
    ex, ey = (x2 - 10, y2) if horizontal else (x2, y2 - 10)
    line = f'M{x1} {y1}L{ex} {ey}'
    tip = (f'M{x2 - head - 5} {y2 - head}L{x2} {y2}L{x2 - head - 5} {y2 + head}' if horizontal
           else f'M{x2 - head} {y2 - head - 5}L{x2} {y2}L{x2 + head} {y2 - head - 5}')
    parts = []
    if glow:
        parts.append(f'<path d="{line}" stroke="url(#{gid})" stroke-width="{width * 3}" stroke-linecap="round" '
                     f'opacity="0.35" filter="url(#blur)"/>')
    parts.append(f'<path d="{line}" stroke="url(#{gid})" stroke-width="{width}" stroke-linecap="round"/>')
    parts.append(f'<path d="{tip}" fill="none" stroke="{EMERALD}" stroke-width="{width}" stroke-linecap="round" '
                 f'stroke-linejoin="round"/>')
    return "".join(parts)


def arrow_right(x1, x2, y):
    return arrow(x1, y, x2, y)


def arrow_down(x, y1, y2):
    return arrow(x, y1, x, y2)


def mini_arrow(x, y):
    """A small arrow inside a card, between two steps."""
    return arrow(x, y, x, y + 22, width=2, head=5, glow=False)


def card(x, y, w, h, c):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{c["card"]}" stroke="{c["border"]}" '
            f'stroke-width="1.5" filter="url(#shadow)"/>')


def stage(x, y, w, h, number, label, heading, c, body, footer_icon, footer):
    parts = [card(x, y, w, h, c)]
    # Number badge and stage label
    parts.append(f'<circle cx="{x + 40}" cy="{y + 42}" r="17" fill="none" stroke="url(#flow)" stroke-width="2"/>')
    parts.append(t(x + 40, y + 48, str(number), 16, c["head"], 700, "middle"))
    parts.append(t(x + 68, y + 48, label, 13, BLUE if number < 3 else EMERALD, 700, spacing=2))
    parts.append(t(x + 28, y + 96, heading, 25, c["head"], 700))
    parts.extend(body)
    # The small line at the bottom, with an outline icon
    parts.append(f'<path d="M{x + 28} {y + h - 62}H{x + w - 28}" stroke="{c["border"]}" stroke-width="1.5"/>')
    parts.append(icon(footer_icon, x + 28, y + h - 41, 18, EMERALD))
    parts.append(t(x + 56, y + h - 27, footer, 14.5, c["muted"], 500))
    return "".join(parts)


def build(theme):
    c = THEMES[theme]
    _gradients.clear()
    cw, ch, top, gap = 330, 336, 108, 55
    xs = [60, 60 + cw + gap, 60 + 2 * (cw + gap)]          # 60, 445, 830: the cards end at 1160
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'role="img" aria-labelledby="title desc">',
           '<title id="title">How WhyNow works</title>',
           '<desc id="desc">Three stages. 1 Find: a company name or website is matched on Companies House, with a '
           'brand-to-legal-name lookup. 2 Research: Tavily finds the company profile, news, buying signals and hiring; '
           'events older than 6 months are filtered out. 3 Write and verify: Gemini writes from fetched sources only, '
           'then code checks every claim, date and size rule. Output: sourced report, 1 to 10 fit score, persona email, '
           'LinkedIn note, call opener, PDF and CSV. About 45 seconds in total.</desc>',
           f'''<defs>
  <linearGradient id="flow" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{BLUE}"/><stop offset="1" stop-color="{EMERALD}"/></linearGradient>
  <linearGradient id="flowdown" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BLUE}"/><stop offset="1" stop-color="{EMERALD}"/></linearGradient>
  <linearGradient id="line" gradientUnits="userSpaceOnUse" x1="60" y1="0" x2="1160" y2="0"><stop offset="0" stop-color="{BLUE}" stop-opacity="0"/><stop offset="0.2" stop-color="{BLUE}"/><stop offset="0.8" stop-color="{EMERALD}"/><stop offset="1" stop-color="{EMERALD}" stop-opacity="0"/></linearGradient>
  <radialGradient id="glowBlue" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="{BLUE}" stop-opacity="{c["glow"]}"/><stop offset="1" stop-color="{BLUE}" stop-opacity="0"/></radialGradient>
  <radialGradient id="glowGreen" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="{EMERALD}" stop-opacity="{c["glow"]}"/><stop offset="1" stop-color="{EMERALD}" stop-opacity="0"/></radialGradient>
  <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="{c["grid"]}" stroke-opacity="{c["grid_opacity"]}" stroke-width="1"/></pattern>
  <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="10" stdDeviation="14" flood-color="{c["shadow"]}" flood-opacity="{c["shadow_opacity"]}"/></filter>
  <filter id="blur" x="-20%" y="-200%" width="140%" height="500%"><feGaussianBlur stdDeviation="4"/></filter>
</defs>''',
           f'<rect width="{W}" height="{H}" rx="28" fill="{c["bg"]}"/>',
           f'<rect width="{W}" height="{H}" rx="28" fill="url(#grid)"/>',
           f'<ellipse cx="260" cy="150" rx="420" ry="260" fill="url(#glowBlue)"/>',
           f'<ellipse cx="980" cy="560" rx="420" ry="260" fill="url(#glowGreen)"/>',
           t(60, 66, "HOW WHYNOW WORKS", 14, BLUE, 700, spacing=3),
           t(1160, 66, "Company in, sourced brief out", 15, c["muted"], 500, "end")]

    # 1. Find
    x = xs[0]
    body = [f'<rect x="{x + 28}" y="{top + 122}" width="{cw - 56}" height="38" rx="10" fill="{c["chip"]}" '
            f'stroke="{c["chip_border"]}"/>',
            t(x + cw / 2, top + 147, "Company name or website", 15, c["body"], 500, "middle"),
            mini_arrow(x + cw / 2, top + 168),
            t(x + 28, top + 214, "Companies House", 17, c["head"], 600),
            t(x + 28, top + 238, "Official UK company data", 15, c["muted"]),
            t(x + 28, top + 260, "Brand-to-legal-name lookup", 15, c["muted"])]
    out.append(stage(x, top, cw, ch, 1, "FIND", "Find the company", c, body, "search",
                     "Matched, never guessed"))

    # 2. Research
    x = xs[1]
    body = [t(x + 28, top + 140, "Tavily", 17, c["head"], 600),
            t(x + 28, top + 164, "Searches public sources for:", 15, c["muted"])]
    for row, labels in enumerate((("Company profile", "News"), ("Buying signals", "Hiring"))):
        cx = x + 28
        for label in labels:
            svg, w = chip(cx, top + 180 + row * 44, label, c)
            body.append(svg)
            cx += w + 10
    out.append(stage(x, top, cw, ch, 2, "RESEARCH", "Research", c, body, "clock",
                     "Older than 6 months filtered out"))

    # 3. Write and verify
    x = xs[2]
    body = [t(x + 28, top + 140, "Gemini", 17, c["head"], 600),
            t(x + 28, top + 164, "Writes from fetched sources only", 15, c["muted"]),
            mini_arrow(x + 40, top + 178),
            t(x + 28, top + 220, "Code checks", 17, c["head"], 600),
            t(x + 28, top + 244, "Every claim, date and size rule", 15, c["muted"])]
    out.append(stage(x, top, cw, ch, 3, "WRITE AND VERIFY", "Write and verify", c, body, "shield",
                     "Unsourced claims removed"))

    # Arrows between the stages
    mid = top + 170
    out.append(arrow_right(xs[0] + cw + 10, xs[1] - 10, mid))
    out.append(arrow_right(xs[1] + cw + 10, xs[2] - 10, mid))

    # Output card (full width), fed from stage 3
    oy, oh = top + ch + 66, 150
    out.append(arrow_down(xs[2] + cw / 2, top + ch + 10, oy - 8))
    out.append(card(60, oy, 1100, oh, c))
    out.append(f'<rect x="60" y="{oy}" width="6" height="{oh}" rx="3" fill="url(#flowdown)"/>')
    out.append(icon("doc", 92, oy + 32, 24, EMERALD))
    out.append(t(126, oy + 51, "OUTPUT", 13, EMERALD, 700, spacing=2))
    out.append(t(92, oy + 92, "A sourced brief,", 24, c["head"], 700))
    out.append(t(92, oy + 122, "ready to use", 24, c["head"], 700))
    chips = (("Sourced report", "1–10 fit score", "Persona email"), ("LinkedIn note", "Call opener", "PDF / CSV"))
    for row, labels in enumerate(chips):
        cx = 430
        for label in labels:
            svg, w = chip(cx, oy + 30 + row * 52, label, c, 16)
            out.append(svg)
            cx += w + 14

    # About 45 seconds, along the bottom
    by = H - 46
    out.append(f'<path d="M60 {by}H1160" stroke="url(#line)" stroke-width="1.5"/>')
    out.append(t(60, by - 12, "Company in", 13, c["muted"], 500))
    out.append(t(1160, by - 12, "Brief out", 13, c["muted"], 500, "end"))
    pw = 196
    out.append(f'<rect x="{600 - pw / 2}" y="{by - 18}" width="{pw}" height="36" rx="18" fill="{c["card"]}" '
               f'stroke="{c["border"]}" stroke-width="1.5"/>')
    out.append(icon("clock", 600 - pw / 2 + 18, by - 9, 18, EMERALD))
    out.append(t(600 + 12, by + 6, "about 45 seconds", 15, c["head"], 600, "middle"))
    out.append("</svg>")
    return "\n".join(out).replace("</defs>", "\n  ".join(_gradients) + "\n</defs>") + "\n"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        path = OUT / f"how-it-works-{theme}.svg"
        path.write_text(build(theme), encoding="utf-8")
        print("Wrote", path)


if __name__ == "__main__":
    main()
