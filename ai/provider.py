"""
provider.py — the ONE place that talks to the AI.

The rest of the app only calls generate_structured(prompt, Shape).
If the main Gemini model is busy, slow, rate-limited or retired, the same request is tried
with each backup model in turn (GEMINI_FALLBACK_MODELS), once each. last_model_used() says which answered.
To swap Gemini for another AI later, add a new _ask_<provider> function here
and change AI_PROVIDER in settings.py. Nothing else needs to change.
"""

import threading

from pydantic import ValidationError

import config
import settings
import usage



NO_INTERNET = "Couldn't connect to the internet. Check your connection and try again."


class AIError(Exception):
    """A problem with the AI, with a friendly message to show the user."""

    def __init__(self, message, try_backup=False):
        super().__init__(message)
        self.try_backup = try_backup   # True if another model might work (busy, slow, retired)


class AllModelsBusy(AIError):
    """Every Gemini model (main and backups) failed for a temporary reason."""


# Which model answered the last request, kept per user session (each session runs in its own thread)
_last = threading.local()


def last_model_used():
    """e.g. {"name": "gemini-3.5-flash", "backup": True, "reason": "gemini-3.6-flash was busy…"}"""
    return getattr(_last, "model", None)


def generate_structured(prompt, shape):
    """Ask the AI and get back a filled-in Pydantic object of the given shape."""
    if settings.AI_PROVIDER != "gemini":
        raise AIError(f"Unknown AI provider '{settings.AI_PROVIDER}' in settings.py.")
    models = [settings.GEMINI_MODEL] + [m for m in settings.GEMINI_FALLBACK_MODELS if m != settings.GEMINI_MODEL]
    failed = []
    for model in models:
        try:
            answer = _ask_gemini(prompt, shape, model)
        except AIError as e:
            usage.log_call("gemini", "generate", model=model)   # a failed request may still be billed
            if not e.try_backup:
                raise    # e.g. a key problem: another model wouldn't help
            if model == models[-1]:
                if str(e) == NO_INTERNET:
                    raise
                raise AllModelsBusy(f"The AI couldn't answer right now: all {len(models)} models are busy. "
                                    "Try again in a few minutes.") from e
            failed.append(f"{model} failed: {e}")
            continue
        usage.log_call("gemini", "generate", model=model)
        _last.model = {"name": model, "backup": bool(failed), "reason": "; ".join(failed) or None}
        return answer


def _ask_gemini(prompt, shape, model):
    from google import genai
    from google.genai import errors, types

    api_key = config.get_secret("GEMINI_API_KEY")
    if not api_key:
        raise AIError("Gemini API key is missing. Add GEMINI_API_KEY to your .env file.")

    # Keep the client in a variable: if it's created and used in one line,
    # Python throws it away too early and the library says "client has been closed".
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=settings.GEMINI_TIMEOUT_SECONDS * 1000,      # the library counts in milliseconds
            retry_options=types.HttpRetryOptions(attempts=1),     # no hidden retries that add waiting
        ),
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=shape,   # forces the answer into our Pydantic shape
                temperature=0.3,         # low = more factual, less creative
            ),
        )
    except errors.APIError as e:
        raise _friendly(e, model)
    except (ConnectionError, TimeoutError, OSError):
        raise AIError(NO_INTERNET, try_backup=True)
    except Exception as e:
        if "connect" in type(e).__name__.lower():   # e.g. the web library's ConnectError
            raise AIError(NO_INTERNET, try_backup=True)
        if "timeout" in type(e).__name__.lower():
            raise AIError(f"Gemini took longer than {settings.GEMINI_TIMEOUT_SECONDS} seconds. Please try again.",
                          try_backup=True)
        # Name the error type (never the key) so problems are easy to diagnose
        raise AIError(f"Something went wrong talking to Gemini ({type(e).__name__}). Please try again.")

    # Gemini fills .parsed when the answer fits the shape. If not, check it ourselves.
    if isinstance(response.parsed, shape):
        return response.parsed
    try:
        return shape.model_validate_json(response.text or "")
    except ValidationError:
        raise AIError("The AI's answer came back in the wrong format. Please try again.", try_backup=True)


def _friendly(error, model):
    """A plain-English message. Key problems never try the backup model (it wouldn't help)."""
    code = getattr(error, "code", None)
    if code in (401, 403):
        return AIError("Gemini rejected the API key. Check GEMINI_API_KEY in your .env file.")
    if code == 404:
        return AIError(f"Gemini model '{model}' isn't available. "
                       "Google retires models over time: update GEMINI_MODEL in settings.py.", try_backup=True)
    if code == 429:
        return AIError("Gemini's free-tier limit was reached. Wait a minute and try again.", try_backup=True)
    if code and code >= 500:
        return AIError("Gemini is having problems right now. Try again in a few minutes.", try_backup=True)
    return AIError("Gemini couldn't answer this request. Please try again.")
