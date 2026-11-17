"""
prompts.py — the instructions we give the AI.

The AI only sees the numbered sources we fetched. It must cite source IDs
for every claim, and code checks those citations afterwards (see report.py).
"""

import settings


def build_report_prompt(company_name, offer, sources_text, today):
    """The full instruction for writing the research report, score and email."""
    strong = "\n".join(f"  - {s}" for s in settings.STRONG_FIT_SIGNALS)
    weak = "\n".join(f"  - {s}" for s in settings.BAD_FIT_SIGNALS)
    roles = ", ".join(settings.TARGET_BUYER_ROLES)

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
1. Every claim must cite the source IDs it comes from. No source = don't say it.
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

SUGGESTED ROLE
Suggest ONE job title to contact, usually one of: {roles}. Give a role, never a person's name.

EMAIL
- Under {settings.EMAIL_MAX_WORDS} words in the body. Short, plain and human.
- The FIRST sentence must mention a real, specific trigger from the sources
  (a hire, launch, funding round, growth figure). Cite it in email_trigger_source_ids.
- Link the trigger to what the SDR is selling in one sentence.
- End with a low-pressure question (e.g. "Worth a quick chat?").
- No flattery ("I love what you're doing"), no "I hope this finds you well", no buzzwords.
- Prefer a UK trigger over one from another country when both are available.
- Use British English spelling: personalise, organisation, optimise, prioritise, analyse,
  programme, centre, favourite. Never American spelling (personalize, organization…).
- Start with "Hi [First name]," and sign off with "[Your name]".
"""


def build_shorten_prompt(email_body, word_count):
    """Used if the first email is too long."""
    return f"""This sales email is {word_count} words. Rewrite it to under {settings.EMAIL_MAX_WORDS - 10} words.
Keep the same opening trigger, the same facts and the "[First name]" / "[Your name]" placeholders.
Use British English spelling (personalise, organisation, optimise).
Don't add any new facts. Return only the new email body as plain text in the "text" field
and an empty list in "source_ids".

EMAIL:
{email_body}
"""
