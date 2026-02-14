#!/usr/bin/env python3
"""Source health tracking for RSS feeds and webpage sources.

Maintains a .source_health.json file in the data/ directory that tracks
consecutive failures, cooldowns, and dead URLs per source_id.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.config_loader import DATA_DIR

HEALTH_PATH = DATA_DIR / ".source_health.json"

# Defaults
BOZO_THRESHOLD = 3          # consecutive bozo/empty before auto-disable
BLOCKED_COOLDOWN_HOURS = 24  # hours to wait after 403 before retrying


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_health() -> dict:
    """Load health data from disk. Returns empty dict if file doesn't exist."""
    if HEALTH_PATH.exists():
        try:
            with open(HEALTH_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_health(health: dict) -> None:
    """Write health data to disk."""
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_PATH, "w") as f:
        json.dump(health, f, indent=2)


def health_get(source_id: str) -> dict:
    """Get health record for a single source. Returns defaults if not found."""
    health = load_health()
    return health.get(source_id, {
        "disabled": False,
        "consecutive_bozo": 0,
        "dead_url": False,
        "blocked_until": None,
        "last_ok_ts": None,
        "last_fail_ts": None,
        "last_reason": None,
    })


def record_success(source_id: str) -> None:
    """Record a successful fetch for a source."""
    health = load_health()
    rec = health.get(source_id, {})
    rec["consecutive_bozo"] = 0
    rec["disabled"] = False
    rec["dead_url"] = False
    rec["blocked_until"] = None
    rec["last_ok_ts"] = _now_iso()
    health[source_id] = rec
    save_health(health)


def record_bozo(source_id: str, reason: str = "bozo/empty",
                threshold: int = BOZO_THRESHOLD) -> bool:
    """Record a bozo/empty result. Returns True if source was auto-disabled."""
    health = load_health()
    rec = health.get(source_id, {"consecutive_bozo": 0, "disabled": False})
    rec["consecutive_bozo"] = rec.get("consecutive_bozo", 0) + 1
    rec["last_fail_ts"] = _now_iso()
    rec["last_reason"] = reason

    disabled = False
    if rec["consecutive_bozo"] >= threshold:
        rec["disabled"] = True
        disabled = True

    health[source_id] = rec
    save_health(health)
    return disabled


def record_blocked(source_id: str, reason: str = "403 Forbidden",
                   cooldown_hours: int = BLOCKED_COOLDOWN_HOURS) -> None:
    """Record a 403/WAF block with cooldown period."""
    health = load_health()
    rec = health.get(source_id, {})
    until = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
    rec["blocked_until"] = until.isoformat()
    rec["last_fail_ts"] = _now_iso()
    rec["last_reason"] = reason
    health[source_id] = rec
    save_health(health)


def record_dead_url(source_id: str, reason: str = "404 Not Found") -> None:
    """Mark a source URL as dead (404)."""
    health = load_health()
    rec = health.get(source_id, {})
    rec["dead_url"] = True
    rec["last_fail_ts"] = _now_iso()
    rec["last_reason"] = reason
    health[source_id] = rec
    save_health(health)


def is_source_ok(source_id: str) -> tuple[bool, str]:
    """Check if a source is OK to fetch. Returns (ok, reason)."""
    rec = health_get(source_id)

    if rec.get("disabled"):
        return False, f"disabled after {rec.get('consecutive_bozo', '?')} consecutive failures"

    if rec.get("dead_url"):
        return False, "URL marked dead (404)"

    blocked_until = rec.get("blocked_until")
    if blocked_until:
        try:
            until_dt = datetime.fromisoformat(blocked_until)
            if datetime.now(timezone.utc) < until_dt:
                return False, f"blocked until {blocked_until}"
        except (ValueError, TypeError):
            pass

    return True, ""
