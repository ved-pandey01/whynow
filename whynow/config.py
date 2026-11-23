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
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent   # the project folder (this file is in whynow/)
load_dotenv(ROOT / ".env")        # local only: .env is git-ignored and never deployed

SECRET_NAMES = ("COMPANIES_HOUSE_API_KEY", "TAVILY_API_KEY", "GEMINI_API_KEY", "OWNER_PASSWORD")


def _from_streamlit_secrets(name):
    """The value from Streamlit's secrets (secrets.toml or the Community Cloud settings), or None."""
    try:
        import streamlit as st
        if not st.secrets.load_if_toml_exists():   # no secrets file: checked quietly, never an error on the page
            return None
        value = st.secrets.get(name)
    except Exception:
        return None
    return str(value).strip() or None if value is not None else None


def get_secret(name):
    """A key or the owner password: Streamlit secrets first (deployed), then .env / the environment (local)."""
    return _from_streamlit_secrets(name) or (os.getenv(name) or "").strip() or None


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
