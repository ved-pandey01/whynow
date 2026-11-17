"""
Save one real report (and its email) as the "See a sample report" example on the landing page.
Run it once (about 10 Tavily credits + 2 Gemini requests). If the AI step fails,
the research is kept, so running it again costs no Tavily credits:
    .venv/bin/python -m scripts.save_sample
Only the report is saved: no API keys are ever written to the file.
"""

import json
import pickle
import time
from datetime import date
from pathlib import Path

import report
import settings
from ai.provider import AIError
from sources import uk_companies_house as ch

COMPANY = "paddle.com"
# The research is kept here (git-ignored), so if the AI step fails, a rerun costs no Tavily credits
RESEARCH_CACHE = Path(__file__).resolve().parent.parent / ".cache" / "sample_research.pkl"


def with_retries(step, attempts=4, wait_seconds=20):
    """Gemini is sometimes briefly busy; try the AI step again rather than redo the research."""
    for attempt in range(1, attempts + 1):
        try:
            return step()
        except AIError as e:
            if attempt == attempts:
                raise
            print(f"{e} Trying again in {wait_seconds} seconds ({attempt}/{attempts - 1})…")
            time.sleep(wait_seconds)


def load_or_run_research():
    if RESEARCH_CACHE.exists():
        print("Using the research saved last time (no Tavily credits used).")
        return pickle.loads(RESEARCH_CACHE.read_bytes())
    match = ch.search_companies(COMPANY)[0]
    print(f"Researching {match['name']}…")
    research = report.research_company(match["company_number"], COMPANY)   # the credits are spent here
    RESEARCH_CACHE.parent.mkdir(exist_ok=True)
    RESEARCH_CACHE.write_bytes(pickle.dumps(research))
    return research


def main():
    research = load_or_run_research()
    r = with_retries(lambda: report.build_report(research))
    r["saved_email"] = with_retries(lambda: report.write_email(r))
    r["saved_on"] = date.today().strftime("%-d %b %Y")
    path = Path(__file__).resolve().parent.parent / settings.SAMPLE_REPORT_FILE
    path.write_text(json.dumps(r, indent=2, ensure_ascii=False))
    RESEARCH_CACHE.unlink(missing_ok=True)   # done: next run does fresh research
    print(f"Saved to {path} (score {r['fit_score']}/10, email {r['saved_email']['word_count']} words)")


if __name__ == "__main__":
    main()
