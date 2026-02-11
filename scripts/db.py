#!/usr/bin/env python3
"""SQLite database initialization and helpers for the FOIA-Free Content Pipeline v2.

Tables:
  candidates           — raw ingested items (YouTube/RSS/web)
  case_leads           — promoted leads with entity/incident extraction
  artifacts            — primary source artifacts found per lead
  case_bundles         — approved stories with timeline/narration/shorts
  cases                — legacy v1 (kept for compatibility)
  corroboration_sources — supporting sources per candidate/lead
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.config_loader import DB_PATH, DATA_DIR, setup_logging

logger = setup_logging("db")

# ── Schema ─────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
-- v1 tables (kept for compatibility) ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id       TEXT PRIMARY KEY,
    source_id          TEXT NOT NULL,
    source_class       TEXT DEFAULT 'secondary',  -- primary | secondary | discovery_only
    url                TEXT NOT NULL,
    platform           TEXT NOT NULL DEFAULT 'youtube',
    published_at       TEXT,
    title              TEXT,
    description        TEXT,
    duration_sec       INTEGER,
    transcript_text    TEXT,
    entities_json      TEXT DEFAULT '[]',
    incident_type      TEXT DEFAULT 'unknown',
    quality_signals_json TEXT DEFAULT '{}',
    triage_status      TEXT NOT NULL DEFAULT 'NEW',
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
    primary_candidate_id   TEXT NOT NULL,
    case_title_working     TEXT,
    facts_json             TEXT DEFAULT '[]',
    timeline_json          TEXT DEFAULT '[]',
    narration_draft        TEXT,
    shorts_plan_json       TEXT DEFAULT '[]',
    asset_paths_json       TEXT DEFAULT '[]',
    status                 TEXT NOT NULL DEFAULT 'APPROVED',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);

CREATE TABLE IF NOT EXISTS corroboration_sources (
    id                TEXT PRIMARY KEY,
    candidate_id      TEXT,
    lead_id           TEXT,
    case_id           TEXT,
    url               TEXT NOT NULL,
    source_type       TEXT,
    title             TEXT,
    snippet           TEXT,
    verified          INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_corr_candidate ON corroboration_sources(candidate_id);
CREATE INDEX IF NOT EXISTS idx_corr_lead ON corroboration_sources(lead_id);
CREATE INDEX IF NOT EXISTS idx_corr_case ON corroboration_sources(case_id);

-- v2 tables ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS case_leads (
    lead_id            TEXT PRIMARY KEY,
    source_id          TEXT,
    title              TEXT NOT NULL,
    url                TEXT NOT NULL,
    published_at       TEXT,
    snippet            TEXT,
    entities_json      TEXT DEFAULT '{}',
    incident_type      TEXT DEFAULT 'unknown',
    location           TEXT,
    date_of_incident   TEXT,
    hook_score         INTEGER DEFAULT 0,
    risk_flags_json    TEXT DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'NEW',  -- NEW | HUNTING | ARTIFACT_FOUND | NO_ARTIFACT | KILL
    promoted_from      TEXT,                         -- candidate_id if promoted from candidates table
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON case_leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_hook   ON case_leads(hook_score DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id        TEXT PRIMARY KEY,
    lead_id            TEXT NOT NULL,
    artifact_type      TEXT DEFAULT 'unknown',  -- bodycam|dashcam|court|interview|press_video|document|audio
    url                TEXT NOT NULL,
    publisher          TEXT,
    source_class       TEXT DEFAULT 'primary',  -- primary | secondary
    confidence         REAL DEFAULT 0.0,
    notes              TEXT,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_lead ON artifacts(lead_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_conf ON artifacts(confidence DESC);

CREATE TABLE IF NOT EXISTS case_bundles (
    bundle_id              TEXT PRIMARY KEY,
    lead_id                TEXT NOT NULL,
    primary_artifact_ids   TEXT DEFAULT '[]',  -- JSON array of artifact_ids
    facts_json             TEXT DEFAULT '[]',
    timeline_json          TEXT DEFAULT '[]',
    narration_draft        TEXT,
    shorts_plan_json       TEXT DEFAULT '[]',
    asset_paths_json       TEXT DEFAULT '[]',
    status                 TEXT NOT NULL DEFAULT 'APPROVED',  -- APPROVED | PACKAGED | RENDERED | READY_TO_PUBLISH
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bundles_status ON case_bundles(status);
CREATE INDEX IF NOT EXISTS idx_bundles_lead   ON case_bundles(lead_id);
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
               (candidate_id, source_id, source_class, url, platform, published_at,
                title, description, duration_sec, transcript_text,
                entities_json, incident_type, quality_signals_json,
                triage_status, triage_score, triage_rationale,
                triage_patterns_json, risk_flags_json,
                shorts_moments_json, facts_to_verify_json,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?, ?,?)""",
            (
                candidate["candidate_id"],
                candidate["source_id"],
                candidate.get("source_class", "secondary"),
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


# ── Case Lead helpers ─────────────────────────────────────────────────────
def insert_lead(conn: sqlite3.Connection, lead: dict) -> bool:
    """Insert a case_lead row. Returns True if inserted, False if duplicate."""
    ts = now_iso()
    try:
        conn.execute(
            """INSERT INTO case_leads
               (lead_id, source_id, title, url, published_at, snippet,
                entities_json, incident_type, location, date_of_incident,
                hook_score, risk_flags_json, status, promoted_from,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?)""",
            (
                lead["lead_id"],
                lead.get("source_id"),
                lead["title"],
                lead["url"],
                lead.get("published_at"),
                lead.get("snippet"),
                json.dumps(lead.get("entities_json", {})),
                lead.get("incident_type", "unknown"),
                lead.get("location"),
                lead.get("date_of_incident"),
                lead.get("hook_score", 0),
                json.dumps(lead.get("risk_flags_json", [])),
                lead.get("status", "NEW"),
                lead.get("promoted_from"),
                ts, ts,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def update_lead_status(conn: sqlite3.Connection, lead_id: str, status: str) -> None:
    """Update a lead's status."""
    conn.execute(
        "UPDATE case_leads SET status = ?, updated_at = ? WHERE lead_id = ?",
        (status, now_iso(), lead_id),
    )
    conn.commit()


def get_leads(conn: sqlite3.Connection, status: str | None = None,
              min_hook_score: int = 0, limit: int = 500) -> list[dict]:
    """Fetch case leads, optionally filtered by status and minimum hook score."""
    if status:
        rows = conn.execute(
            "SELECT * FROM case_leads WHERE status = ? AND hook_score >= ? "
            "ORDER BY hook_score DESC LIMIT ?",
            (status, min_hook_score, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM case_leads WHERE hook_score >= ? "
            "ORDER BY hook_score DESC LIMIT ?",
            (min_hook_score, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Artifact helpers ──────────────────────────────────────────────────────
def insert_artifact(conn: sqlite3.Connection, artifact: dict) -> bool:
    """Insert an artifact. Returns True if inserted, False if duplicate."""
    try:
        conn.execute(
            """INSERT INTO artifacts
               (artifact_id, lead_id, artifact_type, url, publisher,
                source_class, confidence, notes, created_at)
               VALUES (?,?,?,?,?, ?,?,?,?)""",
            (
                artifact["artifact_id"],
                artifact["lead_id"],
                artifact.get("artifact_type", "unknown"),
                artifact["url"],
                artifact.get("publisher"),
                artifact.get("source_class", "primary"),
                artifact.get("confidence", 0.0),
                artifact.get("notes"),
                now_iso(),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_artifacts(conn: sqlite3.Connection, lead_id: str,
                  min_confidence: float = 0.0) -> list[dict]:
    """Fetch artifacts for a lead, optionally filtered by confidence."""
    rows = conn.execute(
        "SELECT * FROM artifacts WHERE lead_id = ? AND confidence >= ? "
        "ORDER BY confidence DESC",
        (lead_id, min_confidence),
    ).fetchall()
    return [dict(r) for r in rows]


def has_primary_artifact(conn: sqlite3.Connection, lead_id: str,
                         min_confidence: float = 0.7) -> bool:
    """Check if a lead has at least one primary artifact with sufficient confidence."""
    row = conn.execute(
        "SELECT COUNT(*) FROM artifacts WHERE lead_id = ? AND source_class = 'primary' "
        "AND confidence >= ?",
        (lead_id, min_confidence),
    ).fetchone()
    return row[0] > 0


# ── Case Bundle helpers ───────────────────────────────────────────────────
def insert_bundle(conn: sqlite3.Connection, bundle: dict) -> None:
    """Insert a case bundle."""
    ts = now_iso()
    conn.execute(
        """INSERT INTO case_bundles
           (bundle_id, lead_id, primary_artifact_ids,
            facts_json, timeline_json, narration_draft,
            shorts_plan_json, asset_paths_json, status,
            created_at, updated_at)
           VALUES (?,?,?, ?,?,?, ?,?,?, ?,?)""",
        (
            bundle["bundle_id"],
            bundle["lead_id"],
            json.dumps(bundle.get("primary_artifact_ids", [])),
            json.dumps(bundle.get("facts_json", [])),
            json.dumps(bundle.get("timeline_json", [])),
            bundle.get("narration_draft", ""),
            json.dumps(bundle.get("shorts_plan_json", [])),
            json.dumps(bundle.get("asset_paths_json", [])),
            bundle.get("status", "APPROVED"),
            ts, ts,
        ),
    )
    conn.commit()


def update_bundle_status(conn: sqlite3.Connection, bundle_id: str, status: str) -> None:
    """Update a bundle's status."""
    conn.execute(
        "UPDATE case_bundles SET status = ?, updated_at = ? WHERE bundle_id = ?",
        (status, now_iso(), bundle_id),
    )
    conn.commit()


def update_bundle_fields(conn: sqlite3.Connection, bundle_id: str, fields: dict) -> None:
    """Update arbitrary fields on a bundle."""
    sets = []
    vals = []
    json_cols = {"facts_json", "timeline_json", "shorts_plan_json",
                 "asset_paths_json", "primary_artifact_ids"}
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(json.dumps(v) if k in json_cols else v)
    sets.append("updated_at = ?")
    vals.append(now_iso())
    vals.append(bundle_id)
    conn.execute(f"UPDATE case_bundles SET {', '.join(sets)} WHERE bundle_id = ?", vals)
    conn.commit()


def get_bundles(conn: sqlite3.Connection, status: str | None = None,
                limit: int = 100) -> list[dict]:
    """Fetch bundles, optionally filtered by status."""
    if status:
        rows = conn.execute(
            "SELECT * FROM case_bundles WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM case_bundles ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Legacy Case helpers (v1 compat) ──────────────────────────────────────
def insert_case(conn: sqlite3.Connection, case: dict) -> None:
    ts = now_iso()
    conn.execute(
        """INSERT INTO cases
           (case_id, primary_candidate_id, case_title_working,
            facts_json, timeline_json, narration_draft,
            shorts_plan_json, asset_paths_json, status,
            created_at, updated_at)
           VALUES (?,?,?, ?,?,?, ?,?,?, ?,?)""",
        (
            case["case_id"], case["primary_candidate_id"],
            case.get("case_title_working", ""),
            json.dumps(case.get("facts_json", [])),
            json.dumps(case.get("timeline_json", [])),
            case.get("narration_draft", ""),
            json.dumps(case.get("shorts_plan_json", [])),
            json.dumps(case.get("asset_paths_json", [])),
            case.get("status", "APPROVED"), ts, ts,
        ),
    )
    conn.commit()


def update_case_status(conn: sqlite3.Connection, case_id: str, status: str) -> None:
    conn.execute("UPDATE cases SET status = ?, updated_at = ? WHERE case_id = ?",
                 (status, now_iso(), case_id))
    conn.commit()


def update_case_fields(conn: sqlite3.Connection, case_id: str, fields: dict) -> None:
    sets, vals = [], []
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
    if status:
        rows = conn.execute(
            "SELECT * FROM cases WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cases ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


# ── Corroboration helpers ────────────────────────────────────────────────
def insert_corroboration(conn: sqlite3.Connection, corr: dict) -> None:
    conn.execute(
        """INSERT INTO corroboration_sources
           (id, candidate_id, lead_id, case_id, url, source_type, title, snippet, verified, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            corr["id"],
            corr.get("candidate_id"),
            corr.get("lead_id"),
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


def get_corroborations(conn: sqlite3.Connection, candidate_id: str | None = None,
                       lead_id: str | None = None) -> list[dict]:
    if lead_id:
        rows = conn.execute(
            "SELECT * FROM corroboration_sources WHERE lead_id = ? ORDER BY created_at",
            (lead_id,)).fetchall()
    elif candidate_id:
        rows = conn.execute(
            "SELECT * FROM corroboration_sources WHERE candidate_id = ? ORDER BY created_at",
            (candidate_id,)).fetchall()
    else:
        rows = []
    return [dict(r) for r in rows]


# ── CLI ────────────────────────────────────────────────────────────────────
ALL_TABLES = ["candidates", "case_leads", "artifacts", "case_bundles",
              "cases", "corroboration_sources"]

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
        for table in ALL_TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count} rows")
        # Lead status distribution
        for status in ["NEW", "HUNTING", "ARTIFACT_FOUND", "NO_ARTIFACT", "KILL"]:
            count = conn.execute(
                "SELECT COUNT(*) FROM case_leads WHERE status = ?", (status,)
            ).fetchone()[0]
            if count > 0:
                print(f"  leads_{status}: {count}")
        conn.close()
    else:
        init_db()
        print("Database ready. Use --stats to inspect or --init to re-create.")
