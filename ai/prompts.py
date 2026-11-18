"""
prompts.py — the instructions we give the AI.

The AI only sees the numbered sources we fetched. It must cite source IDs
for every claim, and code checks those citations afterwards (see report.py).
"""

import settings


def build_report_prompt(company_name, offer, sources_text, today):
    """The full instruction for writing the research report and fit score (not the email)."""
    strong = "\n".join(f"  - {s}" for s in settings.STRONG_FIT_SIGNALS)
    weak = "\n".join(f"  - {s}" for s in settings.BAD_FIT_SIGNALS)
    roles = ", ".join(settings.TARGET_BUYER_ROLES)
    from datetime import date, timedelta
    six_months_ago = (date.fromisoformat(today) - timedelta(days=30 * settings.EVENT_WINDOW_MONTHS)).isoformat()

    return f"""You are a careful B2B sales researcher helping an SDR (sales development rep).
Today's date is {today}. Write in British English.

COMPANY: {company_name}
WHAT THE SDR IS SELLING: {offer}

=== THE ONLY FACTS YOU MAY USE ===
Each source has an ID like [S1]. Use ONLY these sources. Do not use your own knowledge
about the company, even if you think you know it.

{sources_text}
=== END OF SOURCES ===

STRICT RULES
1. Every claim must cite the source IDs it comes from, in its source_ids field. No source = don't say it.
   Never type source IDs like [S2] inside the text itself.
2. Never guess or invent numbers, names, dates or events. If something isn't in the
   sources, add it to "not_found" instead (e.g. "Employee count not found").
3. Some search results are about a DIFFERENT company or thing with a similar name
   (e.g. canoe paddles instead of Paddle the payments company). Use the company
   description and website text to spot these, and ignore them completely.
4. Search results are only candidates. A result listed under "funding" only counts as a
   funding signal if it actually describes funding. Comparison pages, reviews and
   "what is X" explainers are NOT signals.
5. Industry codes (SIC) come with their official meaning. Quote that meaning exactly and
   never reinterpret it. For example, "70100: Activities of head offices" means the registered
   company is a head office or holding company. It does NOT mean "software development".

ONLY RECENT EVENTS COUNT
A reason to reach out (signal) and any event used in the fit score must have happened in the last
{settings.EVENT_WINDOW_MONTHS} months (after {six_months_ago}). Ignore older events completely, even if a source mentions them.
Use the date the event happened, not the date of the page. For funding, the company profile's
"Last funding round" date wins: a news page dated this month about an older round is NOT a recent round.

NEWS
Pick up to {settings.NEWS_ITEMS} items that are really about this company, from the NEWS CANDIDATES
or from SIGNAL CANDIDATES that are dated news stories from the last {settings.NEWS_WINDOW_MONTHS} months.
If you use a recent dated story as a signal, include it in news too, so the report is consistent.
Newest first. If none are relevant, return an empty list and say so in not_found.

ICP FIT SCORE (1–10)
Ideal customer: {settings.ICP_INDUSTRY}, {settings.ICP_COUNTRY}.
Company size (employees):
  - Best fit: {settings.BEST_FIT_MIN_EMPLOYEES:,}–{settings.BEST_FIT_MAX_EMPLOYEES:,}
  - Good fit: {settings.GOOD_FIT_MIN_EMPLOYEES:,}–{settings.BEST_FIT_MIN_EMPLOYEES - 1:,} or {settings.BEST_FIT_MAX_EMPLOYEES + 1:,}–{settings.GOOD_FIT_MAX_EMPLOYEES:,} (score slightly lower, at most {settings.GOOD_FIT_MAX_SCORE})
  - Weak fit: under {settings.GOOD_FIT_MIN_EMPLOYEES:,} or over {settings.GOOD_FIT_MAX_EMPLOYEES:,} (score at most {settings.WEAK_FIT_MAX_SCORE})
Strong-fit signals (push the score up):
{strong}
Bad-fit signals (push the score down):
{weak}
Score guide: 8–10 = ideal profile plus at least one strong signal; 5–7 = good profile,
no strong signal; 1–4 = poor profile or a bad-fit signal. A falling headcount counts
against the company. Give 2–3 reasons, each with sources.
SIZE: follow the size bands above, however strong the signals are. If the employee count
isn't in the sources, judge size only from clear evidence in the sources (e.g. "over 10,000
staff", "operates in 20 countries"), say which band you think it's in and why, and add
"Employee count not found" to not_found. Don't assume a size from the company's fame.
YOUR SCORE MUST MATCH YOUR OWN REASONS. If any reason you give says the company is
enterprise-scale, very large or global (e.g. hundreds of thousands of customers), you are
saying it's a weak fit on size, so the score must be {settings.WEAK_FIT_MAX_SCORE} or lower.

KEEP IT SHORT
OVERVIEW: "overview" is 3–4 sentences (what they do and their main products, who their customers are,
one notable fact such as size, growth, ownership or recent news). "overview_more" is shown under "Read more"
AFTER the overview, so it must only ADD 2–4 new sentences (overview + Read more = 5–7 in total): never repeat
or reword an overview sentence. One sentence per claim, each with its own source IDs, each under 30 words. If the sources
don't support that many sentences, write fewer: never pad or guess. Every other claim, reason and signal is
under 20 words. Plain words, no filler.

SUGGESTED ROLE
Suggest ONE job title to contact, usually one of: {roles}. Give a role, never a person's name.

"""


def build_email_prompt(company_name, offer, facts_text, today, persona=None):
    """The instruction for the outreach email, for one persona. It may only use facts already in the report."""
    persona = persona or settings.DEFAULT_PERSONA
    angle = settings.EMAIL_PERSONAS.get(persona, "")
    return f"""You are writing a short cold email for an SDR (sales development rep).
Today's date is {today}. Write in British English.

COMPANY: {company_name}
WHAT THE SDR IS SELLING: {offer}

=== THE ONLY FACTS YOU MAY USE (from the research report) ===
Each fact cites source IDs like [S1]. Use ONLY these. Do not use your own knowledge.

{facts_text}
=== END OF FACTS ===

READER: the {persona} at {company_name}. Write for what they care about: {angle}.
Link the trigger to that, in their language. Don't mention the other personas.

EMAIL
- Under {settings.EMAIL_MAX_WORDS} words in the body. Short, plain and human.
- The FIRST sentence must mention a real, specific trigger from the sources
  (a hire, launch, funding round, growth figure). Cite it in trigger_source_ids.
- Link the trigger to what the SDR is selling in one sentence.
- End with a low-pressure question (e.g. "Worth a quick chat?").
- No flattery ("I love what you're doing"), no "I hope this finds you well", no buzzwords.
- Prefer a UK trigger over one from another country when both are available.
- Use British English spelling: personalise, organisation, optimise, prioritise, analyse,
  programme, centre, favourite. Never American spelling (personalize, organization…).
- Start with "Hi [First name]," and sign off with "[Your name]".

LINKEDIN NOTE (linkedin_note)
- A connection request note, under {settings.LINKEDIN_NOTE_MAX_CHARS} characters including spaces.
- Mention the same real trigger, one line on why you're connecting, no hard sell, no link.
- Start with "Hi [First name]," and don't sign off.

CALL OPENER (call_opener)
- Exactly 2 short lines, one per line, for the first 10 seconds of a cold call.
- Line 1: who you are ("[Your name] here, from [Your company]") and the real trigger.
  You do NOT work at {company_name}: always write "[Your company]", never "{company_name}", for the caller's company.
- Line 2: one open question that links the trigger to what they care about.

All three use only the facts above, British English, and the same trigger (cite it in trigger_source_ids).
"""
