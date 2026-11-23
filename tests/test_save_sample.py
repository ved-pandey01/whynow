"""The sample can be remade from the saved report without any Tavily calls. Mocked: no API calls."""

import json

from whynow import report
from whynow import settings
from scripts import save_sample
from sources import uk_companies_house as ch
from tests.test_report import FakeAI, make_draft


def test_research_is_rebuilt_from_the_saved_sample(monkeypatch):
    saved = json.loads((save_sample.ROOT / settings.SAMPLE_REPORT_FILE).read_text())
    monkeypatch.setattr(ch, "get_company_profile", lambda n: {
        "name": "PADDLE.COM MARKET LIMITED", "company_number": n, "status": "active", "closed_on": None,
        "founded": "2012-08-06", "address": "London", "sic_codes": [], "sic_descriptions": [],
        "last_accounts_type": "group", "company_type": "ltd", "source": saved["company"]["source"],
        "not_worth_contacting": False})
    monkeypatch.setattr(ch, "get_current_directors", lambda n: {"people": [], "total": 0, "source": "https://ch/o"})
    monkeypatch.setattr(ch, "get_owners", lambda n: {"owners": [], "source": "https://ch/p"})
    monkeypatch.setattr(ch, "get_filing_history", lambda n: {"filings": [], "total": 0, "source": "https://ch/filings"})   # fake: no real API calls in tests
    research = save_sample.research_from_saved(saved)        # Tavily is blocked in tests: none was needed
    assert [s["url"] for s in research["sources"].items] == \
        [s["url"] for s in sorted(saved["all_sources"], key=lambda s: int(s["id"][1:]))]
    card = research["card"]
    assert card["headcount"] == saved["profile"]["headcount"]["value"]
    assert card["similar_companies"][:4] == saved["more_about"]["competitors"]["names"]   # the report shows 4
    r = report.build_report(research, ai=FakeAI(make_draft(overview=[], snapshot=[], news=[], signals=[],
                                                           fit_reasons=[])))
    assert r["profile"]["headcount"]["value"] == saved["profile"]["headcount"]["value"]


def test_research_saved_before_the_move_into_whynow_still_loads(monkeypatch, tmp_path):
    """Old saved research names the module 'report'; it must still load (no paid re-run)."""
    import pickle
    from scripts import save_sample
    from whynow import report
    old = pickle.dumps(report.SourceList(), protocol=0).replace(b"whynow.report", b"report")   # text format: safe to edit
    assert b"whynow.report" not in old
    path = tmp_path / "sample_research.pkl"
    path.write_bytes(old)
    monkeypatch.setattr(save_sample, "RESEARCH_CACHE", path)
    assert isinstance(save_sample.load_or_run_research(), report.SourceList)
