"""
settings.py — all the app's choices in one place.

Change things here (your offer, your ICP, limits) without touching any other code.
No API keys here: those live in the .env file.
"""

# ---------------------------------------------------------------------------
# What you're selling (pre-fills the "What are you selling?" box)
# ---------------------------------------------------------------------------
DEFAULT_OFFER = (
    "An AI tool that helps SDR teams research accounts and personalise "
    "outreach, so reps book more meetings."
)
TARGET_BUYER_ROLES = ["Head of Sales", "VP Sales", "Head of RevOps"]


# ---------------------------------------------------------------------------
# Ideal Customer Profile (ICP) — used for the 1–10 fit score
# ---------------------------------------------------------------------------
ICP_INDUSTRY = "B2B SaaS"
ICP_COUNTRY = "UK"
ICP_MIN_EMPLOYEES = 50      # ideal range starts here
ICP_MAX_EMPLOYEES = 500     # ideal range ends here
TOO_SMALL_BELOW = 50        # bad fit: no SDR team yet
TOO_BIG_ABOVE = 1000        # bad fit: long enterprise buying cycles

# Signals that push the score UP
STRONG_FIT_SIGNALS = [
    "Hiring SDRs, BDRs or other sales roles",
    "Funding round (Series A to C) in the last 12 months",
    "New sales or revenue leader (CRO, VP Sales, Head of Sales) in the last 6 months",
    "Expansion into new markets or offices, or a new product launch",
]

# Signals that push the score DOWN
BAD_FIT_SIGNALS = [
    f"Fewer than {TOO_SMALL_BELOW} or more than {TOO_BIG_ABOVE} employees",
    "Not B2B SaaS (B2C, retail, charity, public sector, local services)",
    "Recent layoffs, hiring freeze or cost-cutting",
]

# How far back signals count
FUNDING_WINDOW_MONTHS = 12
NEW_LEADER_WINDOW_MONTHS = 6

# Companies House statuses that mean "not worth contacting".
# The code flags these itself, so the AI can't get this wrong.
CLOSED_COMPANY_STATUSES = [
    "dissolved",
    "liquidation",
    "administration",
    "receivership",
    "insolvency-proceedings",
    "converted-closed",
    "removed",
    "closed",
]
# "Dormant" isn't a status. It shows in the company's last accounts type.
DORMANT_ACCOUNTS_TYPE = "dormant"

# Industry codes (SIC) that usually mean a software company.
# This is a hint for the AI, not a hard rule: many SaaS firms use other codes.
SOFTWARE_SIC_CODES = {
    "58290": "Other software publishing",
    "62011": "Ready-made interactive leisure and entertainment software development",
    "62012": "Business and domestic software development",
    "62020": "Information technology consultancy activities",
    "62090": "Other information technology service activities",
    "63110": "Data processing, hosting and related activities",
}


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
COMPANY_SEARCH_RESULTS = 5   # how many Companies House matches to show
NEWS_ITEMS = 3               # recent news items in the report
NEWS_CANDIDATES = 8          # news items fetched; the AI picks the 3 really about the company
EMAIL_MAX_WORDS = 120        # outreach email length limit
REQUEST_TIMEOUT_SECONDS = 15 # give up on a slow API after this long


# ---------------------------------------------------------------------------
# AI provider (kept swappable: change these two lines to use another AI)
# ---------------------------------------------------------------------------
AI_PROVIDER = "gemini"
GEMINI_MODEL = "gemini-2.5-flash"   # fast, free-tier model; confirmed available to your key
