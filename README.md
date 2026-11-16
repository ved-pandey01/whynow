# Account Research Agent

Type a UK company name and get, in under a minute:
- a short research report with sources
- "why now" buying signals
- an ICP fit score (1–10) with reasons
- a personalised outreach email (under 120 words)

**Why:** SDRs often spend 20–30 minutes researching each account. This tool aims to cut that to under a minute, and every fact links to its source.

> 🚧 Work in progress: Stage 1 is being built.

## Tech stack
Python · Streamlit · Companies House API · Tavily · Gemini · Pydantic · pytest

## Setup
*(Full setup steps coming as features are added.)*

1. Copy `.env.example` to `.env` and add your API keys.
2. Install packages: `pip install -r requirements.txt`

## Features so far
- **Companies House lookup** ([sources/uk_companies_house.py](sources/uk_companies_house.py)): finds UK companies by name or website, ranks exact name matches first, and fetches status, industry codes, founded date, address and recently appointed directors. Every fact links to its public Companies House page. Dissolved, insolvent or dormant companies are flagged as "not worth contacting" by code, not by the AI.
  - Try it: `.venv/bin/python -m sources.uk_companies_house "Cognism"`
  - Tip: typing the website (e.g. `paddle.com`) finds the right company more reliably than a short name.

- **News, signals and company card** ([sources/news_tavily.py](sources/news_tavily.py)), using Tavily:
  - **Company card:** headcount and its yearly trend, funding rounds, B2B/SaaS, website. It's only used if its legal name or website matches the company we found, so it never falls back to a lookalike company.
  - **News:** recent articles with the company in the headline.
  - **"Why now" signals:** targeted searches for sales hiring, funding, new sales leaders, expansion and layoffs.
  - **Website:** homepage text, so the AI knows what the company actually does.
  - Searches include the website and a topic word from the company card. That stops brands that are ordinary words ("Paddle") pulling in unrelated stories.
  - Try it (about 9 Tavily credits): `.venv/bin/python -m sources.news_tavily "Paddle" "PADDLE.COM MARKET LIMITED" "paddle.com"`

- **AI report** ([report.py](report.py), [ai/](ai/), [models.py](models.py)):
  - Every fetched fact gets a source ID (S1, S2…). Gemini sees **only** those sources and must cite them.
  - **Code checks the AI's work:** claims citing sources we didn't fetch are removed, news picks must be real news items, and the email must be under 120 words and open with a cited trigger. Every change is listed under "Code checks".
  - **Hard rules in code, not AI:**
    - dissolved, insolvent or dormant companies score 1 with no AI call
    - headcount outside 50–1,000 caps the score at 3
    - a PLC with no headcount caps it at 4
  - The AI provider sits behind one function ([ai/provider.py](ai/provider.py)), so swapping Gemini for another model is a settings change.
  - Try it (about 9 Tavily credits + 1–2 Gemini requests): `.venv/bin/python -m report "paddle.com"`

## Running the tests
```
.venv/bin/python -m pytest
```
The tests use fake API replies, so they're free and need no keys.

## Customising
Edit [settings.py](settings.py) to change the default offer, the ICP (industry, size, fit signals), limits and the AI model. No other code needs to change.

## Part of a pair
This is the outbound partner to my [Inbound Lead Engine](https://github.com/ved-pandey01/inbound-lead-engine) (n8n).
