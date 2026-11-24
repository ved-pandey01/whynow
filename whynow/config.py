"""
whynow/config.py: where WhyNow gets its keys and where it saves its caches.

Keys and the owner password:
  - Deployed on Streamlit Community Cloud: from the app's Secrets (App settings → Secrets), read with st.secrets.
  - On your own computer: from the .env file, as before.
Nothing is ever printed or logged.

Caches and usage logs: inside the project folder if it can be written to, otherwise the server's
temporary folder, so caching and the daily search limits keep working online.
"""

import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent   # the project folder (this file is in whynow/)
load_dotenv(ROOT / ".env")        # local only: .env is git-ignored and never deployed

SECRET_NAMES = ("COMPANIES_HOUSE_API_KEY", "TAVILY_API_KEY", "GEMINI_API_KEY", "OWNER_PASSWORD")


# What visitors see if a key is missing or rejected. The details go to the server log only.
SETUP_PROBLEM = "Research is temporarily unavailable. Please try again later."


def _streamlit_secrets():
    """Streamlit's secrets as a plain dict ({} if there's no secrets file). Checked quietly, never an error."""
    try:
        import streamlit as st
        if not st.secrets.load_if_toml_exists():
            return {}
        return {key: st.secrets[key] for key in st.secrets.keys()}
    except Exception as e:
        print(f"WhyNow setup: couldn't read Streamlit secrets ({type(e).__name__}).", file=sys.stderr)
        return {}


def _from_streamlit_secrets(name):
    """
    The value from Streamlit's secrets (secrets.toml, or the app's Secrets on Streamlit Community Cloud), or None.
    Accepts the name at the top level (the expected format), in any capitals, or inside a section such as
    [secrets] or [default], because a section heading hides it from st.secrets.get and from the environment.
    """
    secrets = _streamlit_secrets()
    wanted = name.lower()
    places = [secrets] + [v for v in secrets.values() if hasattr(v, "keys")]
    for place in places:
        for key in place.keys():
            if str(key).lower() == wanted and not hasattr(place[key], "keys"):
                value = str(place[key]).strip()
                return value or None
    return None


def get_secret(name):
    """A key or the owner password: Streamlit secrets first (deployed), then .env / the environment (local)."""
    return _from_streamlit_secrets(name) or (os.getenv(name) or "").strip() or None


def log_setup_problem(problem, name=None):
    """
    Explain a missing or rejected key in the server log (Streamlit Cloud: Manage app → logs), never on the page.
    Lists secret NAMES only, never values, so a typo or a section heading is easy to spot.
    """
    detail = ""
    if name:
        secrets = _streamlit_secrets()
        sections = [k for k, v in secrets.items() if hasattr(v, "keys")]
        names = [k for k, v in secrets.items() if not hasattr(v, "keys")]
        detail = (f" Looked for {name} in Streamlit secrets (file {'found' if secrets else 'not found'}; "
                  f"top-level names: {names or 'none'}; sections: {sections or 'none'}) and in the environment.")
    print(f"WhyNow setup: {problem}.{detail}", file=sys.stderr)


def data_folder(name):
    """
    A folder for caches or logs that WhyNow can write to, created if needed: the project folder first
    (e.g. .cache/), otherwise a 'whynow' folder in the server's temporary folder.
    """
    preferred = ROOT / name                       # an absolute name (as the tests use) stays as it is
    for folder in (preferred, Path(tempfile.gettempdir()) / "whynow" / Path(name).name):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if os.access(folder, os.W_OK):
                return folder
        except OSError:
            continue
    return preferred
