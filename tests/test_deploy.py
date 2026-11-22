"""
Tests for running on Streamlit Community Cloud: secrets from st.secrets (with .env as the local fallback),
the secrets template, .gitignore, pinned requirements, and cache/log folders that create themselves.
All offline: no real keys, no real API calls.
"""

import ast
import re
import tomllib
from pathlib import Path

import cache
import config
import usage

ROOT = Path(__file__).resolve().parent.parent
REAL_FROM_STREAMLIT_SECRETS = config._from_streamlit_secrets   # saved before conftest swaps it out for tests


# --- Secrets: st.secrets when deployed, .env locally -------------------------------------
def test_streamlit_secrets_win_when_deployed(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    monkeypatch.setattr(config, "_from_streamlit_secrets", lambda name: {"GEMINI_API_KEY": "from-secrets"}.get(name))
    assert config.get_secret("GEMINI_API_KEY") == "from-secrets"


def test_env_is_the_fallback_locally(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "  from-env  ")
    assert config.get_secret("TAVILY_API_KEY") == "from-env"        # conftest: no secrets file in tests
    monkeypatch.delenv("TAVILY_API_KEY")
    assert config.get_secret("TAVILY_API_KEY") is None


def test_empty_secret_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD", "local-pw")
    monkeypatch.setattr(config, "_from_streamlit_secrets", lambda name: None)
    assert config.get_secret("OWNER_PASSWORD") == "local-pw"


class FakeSecrets:
    """Stands in for st.secrets (which can't be changed in tests)."""
    def __init__(self, values=None):
        self.values = values

    def load_if_toml_exists(self):
        return self.values is not None

    def get(self, name, default=None):
        if self.values is None:
            raise FileNotFoundError("no secrets.toml")
        return self.values.get(name, default)


def test_no_secrets_file_is_never_an_error(monkeypatch):
    """With no secrets.toml at all, st.secrets is checked quietly: no error, just None."""
    import streamlit as st
    monkeypatch.setattr(st, "secrets", FakeSecrets(None))
    assert REAL_FROM_STREAMLIT_SECRETS("GEMINI_API_KEY") is None


def test_secrets_file_values_are_read(monkeypatch):
    import streamlit as st
    monkeypatch.setattr(st, "secrets", FakeSecrets({"GEMINI_API_KEY": " cloud-key "}))
    assert REAL_FROM_STREAMLIT_SECRETS("GEMINI_API_KEY") == "cloud-key"
    assert REAL_FROM_STREAMLIT_SECRETS("TAVILY_API_KEY") is None


def test_every_key_and_the_owner_password_go_through_get_secret():
    """No module reads a key from the environment directly, so st.secrets always works when deployed."""
    for path in ["app.py", "ai/provider.py", "sources/news_tavily.py", "sources/uk_companies_house.py"]:
        code = (ROOT / path).read_text()
        assert "os.getenv(" not in code and "load_dotenv" not in code, path
    for name in config.SECRET_NAMES:
        assert any(f'config.get_secret("{name}")' in (ROOT / p).read_text()
                   for p in ["app.py", "ai/provider.py", "sources/news_tavily.py", "sources/uk_companies_house.py"]), name


def test_owner_login_works_with_the_password_from_cloud_secrets(monkeypatch):
    """Deployed, OWNER_PASSWORD exists only in st.secrets (no .env): owner mode must still unlock."""
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    monkeypatch.setattr(config, "_from_streamlit_secrets", lambda name: {"OWNER_PASSWORD": "cloud-pw"}.get(name))
    at = AppTest.from_file("../app.py", default_timeout=30)
    at.query_params["owner"] = "1"
    at.run()
    at.text_input(key="wn_owner_password").input("cloud-pw")
    next(b for b in at.button if b.label == "Unlock").click().run()
    assert not at.exception
    assert any(b.label == "Research a list" for b in at.button)


def test_secrets_example_has_placeholder_names_only():
    example = ROOT / ".streamlit" / "secrets.toml.example"
    values = tomllib.loads(example.read_text())
    assert set(values) == set(config.SECRET_NAMES)
    assert all(v == "" for v in values.values())                     # names only, never real values


def test_real_secrets_and_local_data_are_git_ignored():
    ignored = (ROOT / ".gitignore").read_text().split()
    for entry in [".env", ".streamlit/secrets.toml", ".cache/", ".usage/"]:
        assert entry in ignored, entry


# --- requirements.txt ------------------------------------------------------------------------
# The import name each pinned package provides
IMPORT_TO_PACKAGE = {"pandas": "pandas", "streamlit": "streamlit", "requests": "requests", "dotenv": "python-dotenv",
                     "google": "google-genai", "tavily": "tavily-python", "pydantic": "pydantic",
                     "pytest": "pytest", "fpdf": "fpdf2"}


def _requirements():
    pins = {}
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            name, _, version = line.partition("==")
            pins[name.strip().lower()] = version.strip()
    return pins


def test_every_requirement_is_pinned_to_an_exact_version():
    pins = _requirements()
    assert pins and all(re.fullmatch(r"\d+(\.\d+)*", v) for v in pins.values()), pins


def test_every_third_party_import_is_in_requirements():
    """Every package the app's code imports is pinned, so the cloud installs exactly what runs locally."""
    local = {p.stem for p in ROOT.glob("*.py")} | {p.name for p in ROOT.iterdir() if p.is_dir()}
    stdlib = set(__import__("sys").stdlib_module_names)
    used = set()
    for path in ROOT.rglob("*.py"):
        if any(part.startswith(".") for part in path.relative_to(ROOT).parts) or "scratchpad" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                used |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                used.add(node.module.split(".")[0])
    third_party = used - stdlib - local
    pins = _requirements()
    missing = [m for m in third_party if IMPORT_TO_PACKAGE.get(m) not in pins]
    assert not missing, f"imported but not pinned in requirements.txt: {missing}"


# --- Folders create themselves ---------------------------------------------------------------
def test_cache_and_usage_folders_are_created_automatically(monkeypatch, tmp_path):
    import settings
    monkeypatch.setattr(settings, "CACHE_FOLDER", str(tmp_path / "new" / "cache"))
    monkeypatch.setattr(settings, "USAGE_FOLDER", str(tmp_path / "new" / "usage"))
    cache.put("companies_house", ["x"], {"ok": True})
    usage.log_call("tavily", "search", cost=1)
    usage.record_live_search("someone")
    assert cache.get("companies_house", ["x"], 60) == {"ok": True}
    assert usage.today_totals()["tavily_credits"] == 1 and usage.searches_today()["total"] == 1


def test_unwritable_project_folder_falls_back_to_the_temp_folder(monkeypatch, tmp_path):
    """If the app folder can't be written to online, caching and the daily limits still work."""
    monkeypatch.setattr(config.tempfile, "gettempdir", lambda: str(tmp_path))
    real_access = config.os.access
    monkeypatch.setattr(config.os, "access", lambda p, mode: False if "blocked" in str(p) else real_access(p, mode))
    folder = config.data_folder(str(tmp_path / "blocked" / ".usage"))
    assert folder == tmp_path / "whynow" / ".usage" and folder.is_dir()
