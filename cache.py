"""
cache.py — a tiny file cache, so we never pay for the same API call twice.

Each answer is saved as a small JSON file in .cache/, named by a hash of the request,
with the time it was saved. Reading it back checks it's still fresh enough.
Used for Companies House (7 days), Tavily (24 hours) and finished reports (24 hours).
Only good answers are saved: errors are never cached.
"""

import hashlib
import json
import time
from pathlib import Path

import settings

MISSING = object()   # "not in the cache" (different from a cached None)


def _folder():
    return Path(__file__).resolve().parent / settings.CACHE_FOLDER / "api"


def _path(service, request):
    key = hashlib.sha256(json.dumps([service, request], sort_keys=True, default=str).encode()).hexdigest()[:32]
    return _folder() / service / f"{key}.json"


def get(service, request, max_age_seconds):
    """The saved answer for this request, or MISSING if there isn't a fresh one."""
    path = _path(service, request)
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return MISSING
    if time.time() - saved["saved_at"] > max_age_seconds:
        return MISSING
    return saved["answer"]


def put(service, request, answer):
    """Save an answer. A problem writing the cache never stops the app."""
    path = _path(service, request)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"saved_at": time.time(), "answer": answer}, default=str))
    except OSError:
        pass
