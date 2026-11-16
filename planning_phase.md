# planning_phase.md — Account Research Agent

**Status:** Planning complete. All sections agreed on 15 November 2026. The Stage 1 build plan (section 12) is approved.

---

## 1. The problem
SDRs often spend 20–30 minutes researching each company before outreach (website, news, LinkedIn, finding the right person, writing the email). This is my estimate from BD experience, not a measured number. The real time saving will be measured in Stage 3. That means fewer accounts contacted and more generic emails that get ignored.

## 2. The user
- **Main user:** an SDR/BDR at a B2B SaaS scale-up (50–500 staff).
- **Scope:** UK companies only in Stage 1. The company-data lookup is built as its own swappable part, so other countries (e.g. US, Europe, India) can be added later.

## 3. What success looks like (Stage 1)
- I type a company name and get results in under 60 seconds.
- Every fact in the report has a source.
- The email is specific enough that I'd actually send it.
- It works for at least 15 out of 20 real UK test companies.
- **Clear "not found" handling:** if a company isn't found, has no news or an API fails, the app shows a friendly message, never a crash. Tested with at least 2 deliberately bad inputs.
- **Recruiter-ready on GitHub:** public repo with README, screenshots and setup steps, and no keys committed. Someone else can clone and run it in about 10 minutes.
- **Fit score feels right:** on most test companies, the score is within about 2 points of my own gut judgement.

## 4. Input
- Company name or website.
- Optional "What are you selling?" box, pre-filled from a settings file and editable for each search.
- **Default offer:** "An AI tool that helps SDR teams research accounts and personalise outreach, so reps book more meetings." Buyer: Head of Sales / RevOps.

## 5. Output (the report)
1. Company snapshot: what they do, industry, size, location, founded (Companies House + website)
2. Recent news: 3 items with dates and links
3. "Why now" signals: hiring, funding, new leaders, expansion (only if found, with sources)
4. ICP fit score 1–10 with 2–3 reasons
5. Suggested **role** to contact. No named person, because finding names would need LinkedIn scraping.
6. Personalised email (under 120 words, opening with a real trigger)
7. **What we couldn't find:** a short list of gaps to check by hand
8. **Sources:** every link used, in one list (as well as links next to each claim)

## 6. My ICP (who is a good fit)
- **Industry:** B2B SaaS
- **Company size** (updated 16 Nov 2026):
  - Best fit: 200–2,000 employees
  - Good fit: 50–199 or 2,001–10,000 employees. The score is slightly lower (max 8), with the reason given.
  - Weak fit: under 50 or over 10,000 employees (max 3)
- **Location:** UK (Stage 1)
- **Strong-fit signals:**
  - Hiring SDRs/BDRs or other sales roles
  - Recent funding round (Series A–C, last 12 months)
  - New sales or revenue leader (CRO, VP Sales, Head of Sales) in the last 6 months
  - Expansion into new markets or offices, or a new product launch
- **Bad-fit signals:**
  - Too small (under 50 staff) or too big (over 10,000 staff)
  - Not B2B SaaS (B2C, retail, charity, public sector, local services)
  - Recent layoffs, hiring freeze or cost-cutting
  - Dissolved, in liquidation or dormant on Companies House. These are flagged straight away as "not worth contacting".

## 7. Data sources
- **Companies House API** (free): official UK company data
- **Tavily** (free tier): recent news and signals, and reading the company website
- **Gemini API** (free tier): writes the report, score and email. Kept easy to swap for another AI.
- I'm OK creating free accounts and API keys for all three. The keys go in `.env`.

## 8. Look and feel
Light theme with a white background and one deep-blue accent colour for buttons and the fit score. One clean page: search box at the top, results in cards below, and a "Copy email" button.

## 9. Out of scope for Stage 1
- Logins, accounts, payments
- HubSpot integration (Stage 2)
- Batch CSV mode (Stage 2)
- Hosting online (Stage 3)
- Scraping LinkedIn (never, because it's against LinkedIn's terms)

## 10. Risks and limits
- Free data is limited, so some signals won't be found. The app must say "not found" instead of guessing.
- AI can make things up, so every claim must link to fetched data.
- Free API limits mean keeping usage low during testing.
- **Wrong company match:** an ambiguous name can match several Companies House entries. The app shows the top matches and I pick the right one.
- **Gemini free-tier privacy:** Google may use free-tier prompts to improve its models. Only public company data goes in, never private client or CRM data. The README will say this.
- **Getting stuck as a non-coder:** each step stays tiny, errors are explained in plain English, and I get exact copy-paste commands to run and test.

## 11. Testing plan
Pick 20 real UK companies (mix of sizes and industries). Run each one and score the output 1–5 on accuracy, relevance and email quality. Compare the fit score with my gut score and record the time taken. Then fix the weakest areas.

**First 5 test companies (to confirm or swap before testing):**
- Expected high fit: Cognism, Paddle, Beamery
- Expected low fit (too big): Sage Group
- Expected low fit (not SaaS): Greggs
- Plus 2 bad inputs: a made-up name (e.g. "Zzqx Widgets Ltd") and a dissolved company

## 12. Stage 1 build plan (approved)
Each step is small. After each one, I get a copy-paste command to test it, and it's committed to git.

0. **Update planning docs.** Write the agreed answers into this file, CLAUDE.md and project_plan.md. No code.
1. **Project setup.** `git init`, `.gitignore` (including `.env`), `.env.example`, `requirements.txt`, a Python virtual environment and a README skeleton. I create the 3 API keys and paste them into `.env`. First commit. Claude asks before installing packages or pushing to GitHub.
2. **Settings.** `settings.py` holds the default offer, the ICP rules, the AI model name and limits (3 news items, 120-word email).
3. **Companies House lookup.** Search by name (top 5 matches), then fetch status, industry codes, founding date, address and recent officer changes. Every fact keeps its source link. Employee count isn't in Companies House, so it comes from Tavily or shows "not found".
4. **News and signals (Tavily).** 3 recent news items, targeted searches for hiring, funding, new leaders, expansion and layoffs, and a summary of the company website. Every result keeps its link.
5. **AI report.** A swappable AI provider (Gemini first). The AI only sees the fetched data, with numbered sources, and must cite a source for every claim. The code removes any claim without a real source. A dissolved or dormant company is flagged by the code, not the AI. The email gets a word-count check.
6. **Streamlit page.** Search box, company picker, pre-filled offer box, 8 result cards and a "Copy email" button, in the light theme.
7. **Error handling.** Friendly messages for no match, a missing API key, an API that's down or over its limit, no news found and a bad AI response. Tested with the 2 bad inputs.
8. **Testing and README.** Run the 5 test companies, record the scores in `testing/results.md`, fix the weakest area, write the full README, then expand to 20 companies.

**Extras:** Pydantic, pytest and caching are accepted for Stage 1, and GitHub Actions is for Stage 3. The 5 test companies are confirmed.
- **Pydantic:** checks the AI's output is in the right shape before it's shown.
- **pytest:** small automated tests.
- **Streamlit caching:** built in, and avoids repeat API calls for the same company.
- **GitHub Actions:** runs the tests on every push. Stage 3.
