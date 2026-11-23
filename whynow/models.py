"""
whynow/models.py — the exact shape the AI's answer must have (using Pydantic).

Think of these as forms the AI must fill in. Every claim has a "source_ids" box,
so we can check in code that each claim points to data we really fetched.
If the AI's answer doesn't fit the form, Pydantic rejects it.
"""

from pydantic import BaseModel, Field


class Claim(BaseModel):
    """One fact, with the source numbers it came from (e.g. ["S1", "S4"])."""
    text: str = Field(description="One short factual sentence, in British English.")
    source_ids: list[str] = Field(description="IDs of the sources this fact comes from, e.g. ['S1'].")


class NewsPick(BaseModel):
    """A news item the AI picked because it's really about this company."""
    source_id: str = Field(description="The ID of the news source, e.g. 'S7'.")
    why_it_matters: str = Field(description="One sentence: why this matters to a sales rep.")


class Signal(BaseModel):
    """A 'why now' buying signal."""
    signal_type: str = Field(description=(
        "One of: hiring_sales, funding, new_sales_leader, expansion, layoffs, "
        "headcount_growth, headcount_decline, new_director."
    ))
    text: str = Field(description="One sentence describing the signal, with the date if known.")
    source_ids: list[str]


class AIReport(BaseModel):
    """The research report the AI writes. Code checks all of it before it's shown."""
    overview: list[Claim] = Field(description=(
        "3 to 4 sentences, one per claim: what the company does and its main products, who its customers are, "
        "and one notable fact (size, growth, ownership or recent news). Fewer if the sources don't support more."))
    overview_more: list[Claim] = Field(description=(
        "For 'Read more': 2 to 4 ADDITIONAL sentences, one per claim, with new detail that is NOT already in the "
        "overview (never repeat or reword an overview sentence). Fewer, or none, if the sources don't support more."))
    snapshot: list[Claim] = Field(description="Industry, size, location, founded. Only facts found in the sources.")
    news: list[NewsPick] = Field(description="Up to 3 news items that are really about this company.")
    signals: list[Signal] = Field(description="Only signals clearly shown by a source. Empty list if none.")
    fit_score: int = Field(description="ICP fit score from 1 (poor) to 10 (perfect).")
    fit_reasons: list[Claim] = Field(description="2 or 3 reasons for the score, each with sources.")
    suggested_role: str = Field(description="The job title to contact (a role, never a person's name).")
    role_reason: str = Field(description="One sentence on why this role.")
    not_found: list[str] = Field(description="Useful things we couldn't find, e.g. 'No funding news in the last 12 months'.")


class OutreachDraft(BaseModel):
    """The outreach pack, written only when the user asks: email, LinkedIn note and call opener, in ONE AI request."""
    subject: str = Field(description="Short subject line, under 8 words.")
    body: str = Field(description="The outreach email, under 120 words, signed '[Your name]'.")
    linkedin_note: str = Field(description="LinkedIn connection note, under 300 characters, same trigger, no hard sell.")
    call_opener: str = Field(description="What to say in the first 10 seconds of a call: exactly 2 short lines.")
    trigger_source_ids: list[str] = Field(description="Source IDs for the trigger all three open with.")
