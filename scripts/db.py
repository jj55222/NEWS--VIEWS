#!/usr/bin/env python3
"""SQLite database initialization and helpers for the FOIA-Free Content Pipeline.

Tables:
  candidates  — each newly discovered item
  cases       — approved stories ready for packaging / rendering
  corroboration_sources — supporting sources gathered per case
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.config_loader import DB_PATH, DATA_DIR, setup_logging

logger = setup_logging("db")

# ── Schema ─────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id       TEXT PRIMARY KEY,
    source_id          TEXT NOT NULL,
    url                TEXT NOT NULL,
    platform           TEXT NOT NULL DEFAULT 'youtube',  -- youtube | web | rss
    published_at       TEXT,
    title              TEXT,
    description        TEXT,
    duration_sec       INTEGER,
    transcript_text    TEXT,
    entities_json      TEXT DEFAULT '[]',
    incident_type      TEXT DEFAULT 'unknown',
    quality_signals_json TEXT DEFAULT '{}',
    triage_status      TEXT NOT NULL DEFAULT 'NEW',      -- NEW | PASS | MAYBE | KILL
    triage_score       INTEGER DEFAULT 0,
    triage_rationale   TEXT,
    triage_patterns_json TEXT DEFAULT '{}',
    risk_flags_json    TEXT DEFAULT '[]',
    shorts_moments_json TEXT DEFAULT '[]',
    facts_to_verify_json TEXT DEFAULT '[]',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(triage_status);
CREATE INDEX IF NOT EXISTS idx_candidates_source ON candidates(source_id);
CREATE INDEX IF NOT EXISTS idx_candidates_score  ON candidates(triage_score DESC);

CREATE TABLE IF NOT EXISTS cases (
    case_id                TEXT PRIMARY KEY,
    primary_candidate_id   TEXT NOT NULL REFERENCES candidates(candidate_id),
    case_title_working     TEXT,
    facts_json             TEXT DEFAULT '[]',
    timeline_json          TEXT DEFAULT '[]',
    narration_draft        TEXT,
    shorts_plan_json       TEXT DEFAULT '[]',
    asset_paths_json       TEXT DEFAULT '[]',
    status                 TEXT NOT NULL DEFAULT 'APPROVED',  -- APPROVED | PACKAGED | RENDERED | READY_TO_PUBLISH
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);

CREATE TABLE IF NOT EXISTS corroboration_sources (
    id                TEXT PRIMARY KEY,
    candidate_id      TEXT NOT NULL,
    case_id           TEXT,           -- set later when candidate is promoted to case
    url               TEXT NOT NULL,
    source_type       TEXT,           -- press_release | news_article | court_record | da_statement
    title             TEXT,
    snippet           TEXT,
    verified          INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_corr_candidate ON corroboration_sources(candidate_id);
CREATE INDEX IF NOT EXISTS idx_corr_case ON corroboration_sources(case_id);
"""


# ── Connection ─────────────────────────────────────────────────────────────
def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Return a connection to the pipeline database, creating it if needed."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create all tables if they don't exist."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", db_path or DB_PATH)


# ── Timestamp helper ───────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Candidate helpers ──────────────────────────────────────────────────────
def insert_candidate(conn: sqlite3.Connection, candidate: dict) -> bool:
    """Insert a candidate row.  Returns True if inserted, False if duplicate."""
    ts = now_iso()
    try:
        conn.execute(
            """INSERT INTO candidates
               (candidate_id, source_id, url, platform, published_at,
                title, description, duration_sec, transcript_text,
                entities_json, incident_type, quality_signals_json,
                triage_status, triage_score, triage_rationale,
                triage_patterns_json, risk_flags_json,
                shorts_moments_json, facts_to_verify_json,
                created_at, updated_at)
               VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?, ?,?)""",
            (
                candidate["candidate_id"],
                candidate["source_id"],
                candidate["url"],
                candidate.get("platform", "youtube"),
                candidate.get("published_at"),
                candidate.get("title"),
                candidate.get("description"),
                candidate.get("duration_sec"),
                candidate.get("transcript_text"),
                json.dumps(candidate.get("entities_json", [])),
                candidate.get("incident_type", "unknown"),
                json.dumps(candidate.get("quality_signals_json", {})),
                candidate.get("triage_status", "NEW"),
                candidate.get("triage_score", 0),
                candidate.get("triage_rationale"),
                json.dumps(candidate.get("triage_patterns_json", {})),
                json.dumps(candidate.get("risk_flags_json", [])),
                json.dumps(candidate.get("shorts_moments_json", [])),
                json.dumps(candidate.get("facts_to_verify_json", [])),
                ts, ts,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def update_triage(conn: sqlite3.Connection, candidate_id: str, triage: dict) -> None:
    """Update triage fields for a candidate."""
    conn.execute(
        """UPDATE candidates SET
             triage_status = ?,
             triage_score = ?,
             triage_rationale = ?,
             triage_patterns_json = ?,
             risk_flags_json = ?,
             shorts_moments_json = ?,
             facts_to_verify_json = ?,
             incident_type = ?,
             updated_at = ?
           WHERE candidate_id = ?""",
        (
            triage["status"],
            triage["score"],
            triage.get("reason", ""),
            json.dumps(triage.get("patterns", {})),
            json.dumps(triage.get("risk_flags", [])),
            json.dumps(triage.get("shorts_moments", [])),
            json.dumps(triage.get("facts_to_verify", [])),
            triage.get("incident_type", "unknown"),
            now_iso(),
            candidate_id,
        ),
    )
    conn.commit()


def get_candidates(conn: sqlite3.Connection, status: str | None = None,
                   limit: int = 500) -> list[dict]:
    """Fetch candidates, optionally filtered by status."""
    if status:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE triage_status = ? ORDER BY triage_score DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM candidates ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Case helpers ───────────────────────────────────────────────────────────
def insert_case(conn: sqlite3.Connection, case: dict) -> None:
    """Insert an approved case."""
    ts = now_iso()
    conn.execute(
        """INSERT INTO cases
           (case_id, primary_candidate_id, case_title_working,
            facts_json, timeline_json, narration_draft,
            shorts_plan_json, asset_paths_json, status,
            created_at, updated_at)
           VALUES (?,?,?, ?,?,?, ?,?,?, ?,?)""",
        (
            case["case_id"],
            case["primary_candidate_id"],
            case.get("case_title_working", ""),
            json.dumps(case.get("facts_json", [])),
            json.dumps(case.get("timeline_json", [])),
            case.get("narration_draft", ""),
            json.dumps(case.get("shorts_plan_json", [])),
            json.dumps(case.get("asset_paths_json", [])),
            case.get("status", "APPROVED"),
            ts, ts,
        ),
    )
    conn.commit()


def update_case_status(conn: sqlite3.Connection, case_id: str, status: str) -> None:
    """Update a case's status."""
    conn.execute(
        "UPDATE cases SET status = ?, updated_at = ? WHERE case_id = ?",
        (status, now_iso(), case_id),
    )
    conn.commit()


def update_case_fields(conn: sqlite3.Connection, case_id: str, fields: dict) -> None:
    """Update arbitrary fields on a case (expects JSON-serializable values for json columns)."""
    sets = []
    vals = []
    json_cols = {"facts_json", "timeline_json", "shorts_plan_json", "asset_paths_json"}
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(json.dumps(v) if k in json_cols else v)
    sets.append("updated_at = ?")
    vals.append(now_iso())
    vals.append(case_id)
    conn.execute(f"UPDATE cases SET {', '.join(sets)} WHERE case_id = ?", vals)
    conn.commit()


def get_cases(conn: sqlite3.Connection, status: str | None = None,
              limit: int = 100) -> list[dict]:
    """Fetch cases, optionally filtered by status."""
    if status:
        rows = conn.execute(
            "SELECT * FROM cases WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cases ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Corroboration helpers ──────────────────────────────────────────────────
def insert_corroboration(conn: sqlite3.Connection, corr: dict) -> None:
    """Insert a corroboration source for a candidate (or case)."""
    conn.execute(
        """INSERT INTO corroboration_sources
           (id, candidate_id, case_id, url, source_type, title, snippet, verified, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            corr["id"],
            corr["candidate_id"],
            corr.get("case_id"),
            corr["url"],
            corr.get("source_type", "unknown"),
            corr.get("title"),
            corr.get("snippet"),
            corr.get("verified", 0),
            now_iso(),
        ),
    )
    conn.commit()


def get_corroborations(conn: sqlite3.Connection, candidate_id: str) -> list[dict]:
    """Fetch corroboration sources for a candidate."""
    rows = conn.execute(
        "SELECT * FROM corroboration_sources WHERE candidate_id = ? ORDER BY created_at",
        (candidate_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Initialize / inspect the pipeline database.")
    parser.add_argument("--init", action="store_true", help="Create tables (idempotent).")
    parser.add_argument("--stats", action="store_true", help="Print row counts.")
    parser.add_argument("--dry-run", action="store_true", help="Show SQL without executing.")
    args = parser.parse_args()

    if args.dry_run:
        print("-- Schema SQL that would be executed --")
        print(SCHEMA_SQL)
    elif args.init:
        init_db()
        print("Database initialized.")
    elif args.stats:
        init_db()
        conn = get_connection()
        for table in ["candidates", "cases", "corroboration_sources"]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count} rows")
        conn.close()
    else:
        init_db()
        print("Database ready. Use --stats to inspect or --init to re-create.")
