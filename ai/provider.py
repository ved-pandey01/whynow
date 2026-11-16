"""
provider.py — the ONE place that talks to the AI.

The rest of the app only calls generate_structured(prompt, Shape).
To swap Gemini for another AI later, add a new _ask_<provider> function here
and change AI_PROVIDER in settings.py. Nothing else needs to change.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

import settings

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class AIError(Exception):
    """A problem with the AI, with a friendly message to show the user."""


def generate_structured(prompt, shape):
    """Ask the AI and get back a filled-in Pydantic object of the given shape."""
    if settings.AI_PROVIDER == "gemini":
        return _ask_gemini(prompt, shape)
    raise AIError(f"Unknown AI provider '{settings.AI_PROVIDER}' in settings.py.")


def _ask_gemini(prompt, shape):
    from google import genai
    from google.genai import errors, types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise AIError("Gemini API key is missing. Add GEMINI_API_KEY to your .env file.")

    # Keep the client in a variable: if it's created and used in one line,
    # Python throws it away too early and the library says "client has been closed".
    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=shape,   # forces the answer into our Pydantic shape
                temperature=0.3,         # low = more factual, less creative
            ),
        )
    except errors.APIError as e:
        raise _friendly(e)
    except (ConnectionError, TimeoutError, OSError):
        raise AIError("Couldn't reach Gemini. Check your internet connection and try again.")
    except Exception as e:
        # Name the error type (never the key) so problems are easy to diagnose
        raise AIError(f"Something went wrong talking to Gemini ({type(e).__name__}). Please try again.")

    # Gemini fills .parsed when the answer fits the shape. If not, check it ourselves.
    if isinstance(response.parsed, shape):
        return response.parsed
    try:
        return shape.model_validate_json(response.text or "")
    except ValidationError:
        raise AIError("The AI's answer came back in the wrong format. Please try again.")


def _friendly(error):
    code = getattr(error, "code", None)
    if code in (401, 403):
        return AIError("Gemini rejected the API key. Check GEMINI_API_KEY in your .env file.")
    if code == 404:
        return AIError(
            f"Gemini model '{settings.GEMINI_MODEL}' isn't available. "
            "Google retires models over time: update GEMINI_MODEL in settings.py."
        )
    if code == 429:
        return AIError("Gemini's free-tier limit was reached. Wait a minute and try again.")
    if code and code >= 500:
        return AIError("Gemini is having problems right now. Try again in a few minutes.")
    return AIError("Gemini couldn't answer this request. Please try again.")
