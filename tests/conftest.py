"""
Shared test setup. Safety net: no test may call a real API (no credits, no keys, no internet needed).
Tests that need a reply give a fake one; anything not faked fails loudly instead of going online.
"""

import pytest
import requests


class NoRealCalls(Exception):
    """A test tried to call a real API without faking it."""


@pytest.fixture(autouse=True)
def block_real_api_calls(monkeypatch):
    def refuse(*args, **kwargs):
        raise NoRealCalls("Tests must not call real APIs: fake this reply in the test.")
    monkeypatch.setattr(requests, "get", refuse)
    from sources import news_tavily
    # Kept for the one test that checks the "key is missing" message (that check happens before any call)
    monkeypatch.setattr(news_tavily, "real_client_for_tests", news_tavily._client, raising=False)
    monkeypatch.setattr(news_tavily, "_client", refuse)
