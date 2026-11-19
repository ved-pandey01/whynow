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
# Company size bands (employees). Code applies these to the score; the AI can't override them.
#   Best fit: 200–2,000                     → no size limit on the score
#   Good fit: 50–199 or 2,001–10,000        → score slightly lower (max GOOD_FIT_MAX_SCORE)
#   Weak fit: under 50 or over 10,000       → score max WEAK_FIT_MAX_SCORE
BEST_FIT_MIN_EMPLOYEES = 200
BEST_FIT_MAX_EMPLOYEES = 2000
GOOD_FIT_MIN_EMPLOYEES = 50      # below this: too small, usually no SDR team yet
GOOD_FIT_MAX_EMPLOYEES = 10000   # above this: enterprise, long buying cycles
GOOD_FIT_MAX_SCORE = 8
WEAK_FIT_MAX_SCORE = 3

# Signals that push the score UP
STRONG_FIT_SIGNALS = [
    "Hiring SDRs, BDRs or other sales roles",
    "Funding round (Series A to C) in the last 6 months",
    "New sales or revenue leader (CRO, VP Sales, Head of Sales) in the last 6 months",
    "Expansion into new markets or offices, or a new product launch",
]

# Signals that push the score DOWN
BAD_FIT_SIGNALS = [
    f"Fewer than {GOOD_FIT_MIN_EMPLOYEES:,} or more than {GOOD_FIT_MAX_EMPLOYEES:,} employees",
    "Not B2B SaaS (B2C, retail, charity, public sector, local services)",
    "Recent layoffs, hiring freeze or cost-cutting",
]

# How far back signals count
EVENT_WINDOW_MONTHS = 6          # reasons to reach out must have happened in the last 6 months
FUNDING_WINDOW_MONTHS = 6
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
COMPANY_SIZE_CHECKS = 25     # how many matches get their size looked up before ranking (free, ~0.1s each)
ADVANCED_SEARCH_SIZE = 100    # results asked for when a Location or Industry filter is used
MAX_SIC_CODES = 50           # an Industry keyword can match many SIC codes; send at most this many
NEWS_ITEMS = 3               # recent news items in the report
NEWS_WINDOW_MONTHS = 6       # "recent" news means published in the last 6 months
NEWS_CANDIDATES = 8          # news items fetched; the AI picks the 3 really about the company
LINKEDIN_NOTE_MAX_CHARS = 300   # LinkedIn connection notes allow 300 characters
CALL_OPENER_MAX_LINES = 2
COMPETITORS_SHOWN = 4           # "More about": similar companies shown
HIRING_EXAMPLES_SHOWN = 6       # "More about": example open roles shown
OVERVIEW_SENTENCES = 4          # the overview card: 3–4 sentences
OVERVIEW_MORE_SENTENCES = 7     # overview + "Read more" together: 5–7 sentences (Read more only adds new ones)
EMAIL_MAX_WORDS = 120        # outreach email length limit

# ---------------------------------------------------------------------------
# Time limits (seconds). A slow service gives a clear message instead of a long wait.
# ---------------------------------------------------------------------------
COMPANIES_HOUSE_TIMEOUT_SECONDS = 15   # per request
TAVILY_TIMEOUT_SECONDS = 20            # per search or page read
GEMINI_TIMEOUT_SECONDS = 60            # per AI request (no hidden retries)
RESEARCH_TIME_BUDGET_SECONDS = 35      # after this, skip any searches not yet started


# ---------------------------------------------------------------------------
# AI provider (kept swappable: change these two lines to use another AI)
# ---------------------------------------------------------------------------
AI_PROVIDER = "gemini"
# Model history (Nov 2026):
#   gemini-2.5-flash / 2.5-flash-lite: listed, but "no longer available to new users"
#   gemini-3.8-flash / 3.7-flash: failing on Google's side (server errors after ~58s)
#   gemini-3.6-flash: tested working (full report shape in ~6s). Using this for now;
#   try 3.8-flash again once Google's service is stable.
GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_FALLBACK_MODELS = [          # tried in order, once each, if the main model is busy, slow, rate-limited or retired
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",         # each model has its own free daily allowance
    "gemini-3.1-flash-lite",
]


# ---------------------------------------------------------------------------
# Page wording and author links (shown on the web page)
# ---------------------------------------------------------------------------
APP_NAME = "WhyNow"
APP_LABEL = "AI account research agent"
APP_HEADLINE = "Know the fit. Know the moment."
SIGNALS_TITLE = "Why reach out now"   # the heading for "why now" signals, in the report and the landing preview
APP_TYPING_LINES = [             # the animated amber line on the landing page
    "Because they're hiring SDRs…",
    "Because they just raised funding…",
    "Because they hired a new sales leader…",
    "Because they're expanding to new markets…",
]
SEARCH_PLACEHOLDER = "Type a company or website, e.g. cognism.com"
OFFER_HINT = "WhyNow uses this to score the fit and write your email."
EMAIL_PERSONAS = {                 # who the email is for, and what that person cares about
    "Head of Sales": "meetings and pipeline: helping their reps book more meetings and build more pipeline",
    "Head of RevOps": "tools and data: cleaner data, less manual research, and tools that fit the sales stack",
    "Founder / CEO": "growth and cost: growing revenue without adding headcount or cost",
}
DEFAULT_PERSONA = "Head of Sales"
BATCH_MAX_COMPANIES = 10           # "Research a list": the most companies in one upload
TAVILY_CREDITS_PER_REPORT = 10     # roughly, for the credit warning
TRY_COMPANIES = [                  # the "Try:" chips: (name shown, website, fixed Companies House number)
    ("Cognism", "cognism.com", "09392705"),       # COGNISM LIMITED
    ("Darktrace", "darktrace.com", "08562035"),   # DARKTRACE HOLDINGS LIMITED (not the Midco deal vehicle)
    ("Paddle", "paddle.com", "08172165"),         # PADDLE.COM MARKET LIMITED
]
AUTHOR_NAME = "Ved Prakash Pandey"
AUTHOR_SHORT_NAME = "Ved"          # used in the header pill on phones
AUTHOR_INITIALS = "VP"
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/ved-prakash-pandey-954493202/"
AUTHOR_GITHUB = "https://github.com/ved-pandey01"
SAMPLE_REPORT_FILE = "data/sample_report_paddle.json"   # the saved example behind "See a sample report"


# ---------------------------------------------------------------------------
# Running costs: limits and caches (keep WhyNow cheap to run)
# ---------------------------------------------------------------------------
VISITOR_LIVE_SEARCHES_PER_DAY = 2   # fresh reports per visitor per day (the sample and cached companies are free)
TOTAL_LIVE_SEARCHES_PER_DAY = 30    # fresh reports across all visitors per day
COMPANIES_HOUSE_CACHE_DAYS = 7      # Companies House replies are reused for 7 days (it's free, but polite and fast)
TAVILY_CACHE_HOURS = 24             # Tavily searches and page reads are reused for 24 hours (never paid for twice)
REPORT_CACHE_HOURS = 24             # finished reports are reused for 24 hours (no repeat Gemini requests)
CACHE_FOLDER = ".cache"             # git-ignored
USAGE_FOLDER = ".usage"             # git-ignored: the call log and daily search counts
TAVILY_CREDITS_PER_SEARCH = 1       # Tavily's price for a basic search
TAVILY_PAGES_PER_CREDIT = 5         # Tavily's price for reading pages: 1 credit per 5 pages
MAX_SEARCH_CHARS = 100              # longest company name or website accepted
