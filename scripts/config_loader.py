#!/usr/bin/env python3
"""Central configuration loader for the FOIA-Free Content Pipeline.

Loads policy.yaml, sources_registry.json, and environment variables.
All other scripts import from here.
"""

import json
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
EXPORTS_DIR = OUTPUTS_DIR / "exports"
CASE_BUNDLES_DIR = OUTPUTS_DIR / "case_bundles"
DB_PATH = DATA_DIR / "pipeline.db"

POLICY_PATH = CONFIG_DIR / "policy.yaml"
SOURCES_PATH = CONFIG_DIR / "sources_registry.json"
ENV_PATH = PROJECT_ROOT / ".env"

# ── Load .env ──────────────────────────────────────────────────────────────
load_dotenv(ENV_PATH)


def get_env(key: str, default: str | None = None, required: bool = False) -> str | None:
    """Retrieve an environment variable, optionally raising if missing."""
    val = os.getenv(key, default)
    if required and not val:
        raise EnvironmentError(f"Required environment variable {key!r} is not set.")
    return val


# ── Policy ─────────────────────────────────────────────────────────────────
_policy_cache: dict | None = None


def load_policy() -> dict:
    """Load and cache policy.yaml."""
    global _policy_cache
    if _policy_cache is not None:
        return _policy_cache
    if not POLICY_PATH.exists():
        raise FileNotFoundError(f"Policy file not found: {POLICY_PATH}")
    with open(POLICY_PATH, "r") as f:
        _policy_cache = yaml.safe_load(f)
    return _policy_cache


def get_policy(section: str, key: str | None = None, default=None):
    """Get a policy value.  ``get_policy('triage', 'pass_threshold')``"""
    policy = load_policy()
    sec = policy.get(section, {})
    if key is None:
        return sec
    return sec.get(key, default)


# ── Sources ────────────────────────────────────────────────────────────────
_sources_cache: list | None = None


def load_sources() -> list[dict]:
    """Load and cache sources_registry.json."""
    global _sources_cache
    if _sources_cache is not None:
        return _sources_cache
    if not SOURCES_PATH.exists():
        raise FileNotFoundError(f"Sources registry not found: {SOURCES_PATH}")
    with open(SOURCES_PATH, "r") as f:
        _sources_cache = json.load(f)
    return _sources_cache


def get_enabled_sources(source_type: str | None = None, tier: str | None = None) -> list[dict]:
    """Return enabled sources, optionally filtered by type and/or tier."""
    sources = load_sources()
    out = [s for s in sources if s.get("enabled", False)]
    if source_type:
        out = [s for s in out if s.get("type") == source_type]
    if tier:
        out = [s for s in out if s.get("tier", "").upper() == tier.upper()]
    return out


# ── Logging ────────────────────────────────────────────────────────────────
def setup_logging(name: str = "pipeline", level: str | None = None) -> logging.Logger:
    """Configure and return a logger.  Reads level from policy if not given."""
    if level is None:
        try:
            level = get_policy("logging", "level", "INFO")
        except FileNotFoundError:
            level = "INFO"

    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(name)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(log_dir / f"{name}.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ── API clients ────────────────────────────────────────────────────────────
def get_openrouter_client():
    """Return an OpenAI-compatible client pointed at OpenRouter."""
    from openai import OpenAI

    api_key = get_env("OPENROUTER_API_KEY", required=True)
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)


def get_youtube_api_key() -> str:
    """Return the YouTube Data API v3 key."""
    return get_env("YOUTUBE_API_KEY", required=True)


def get_brave_api_key() -> str:
    """Return the Brave Search API key."""
    return get_env("BRAVE_API_KEY", required=True)


# ── Helpers ────────────────────────────────────────────────────────────────
def ensure_dirs():
    """Create all required directories if they don't exist."""
    for d in [DATA_DIR, OUTPUTS_DIR, EXPORTS_DIR, CASE_BUNDLES_DIR,
              EXPORTS_DIR / "longform", EXPORTS_DIR / "shorts", EXPORTS_DIR / "metadata",
              DATA_DIR / "logs"]:
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    ensure_dirs()
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Policy loaded: {len(load_policy())} sections")
    print(f"Sources loaded: {len(load_sources())} entries")
    print(f"Enabled sources: {len(get_enabled_sources())}")
    print("Config loader OK.")
