#!/usr/bin/env python3
"""Forward triaged PASS + MAYBE candidates to case_leads for artifact hunting.

Bridges the gap between triage (candidates table) and artifact hunt (case_leads
table) by promoting triaged candidates to case_leads.  Every MAYBE is treated
as first-class alpha — not logged-and-forgotten.

Toggle:  policy.yaml  forwarding.route_maybes_to_artifact_hunt  (default true)
Filter:  policy.yaml  forwarding.maybe_score_min_to_forward     (default 0)

Usage:
    python -m scripts.forward_candidates                # forward PASS+MAYBE
    python -m scripts.forward_candidates --dry-run      # preview
    python -m scripts.forward_candidates --limit 100    # cap at 100
"""

import argparse
import json

from scripts.config_loader import get_policy, setup_logging
from scripts.db import get_connection, init_db, insert_lead, now_iso

logger = setup_logging("forward")


def forward(limit: int = 500, dry_run: bool = False) -> dict:
    """Promote PASS (and optionally MAYBE) candidates to case_leads.

    Returns:
        dict with forwarding stats per ticket logging requirements.
    """
    init_db()
    conn = get_connection()

    # ── Config ────────────────────────────────────────────────────────
    fwd_cfg = get_policy("forwarding") or {}
    route_maybes = fwd_cfg.get("route_maybes_to_artifact_hunt", True)
    maybe_score_min = fwd_cfg.get("maybe_score_min_to_forward", 0)

    # ── Query: PASS (always) + MAYBE (if toggled) not yet promoted ───
    if route_maybes:
        statuses = ("PASS", "MAYBE")
    else:
        statuses = ("PASS",)

    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""SELECT c.* FROM candidates c
            LEFT JOIN case_leads cl ON cl.promoted_from = c.candidate_id
            WHERE c.triage_status IN ({placeholders})
              AND cl.lead_id IS NULL
            ORDER BY
              (CASE WHEN c.triage_status = 'PASS' THEN 0 ELSE 1 END),
              c.triage_score DESC
            LIMIT ?""",
        (*statuses, limit),
    ).fetchall()
    candidates = [dict(r) for r in rows]

    if not candidates:
        logger.info("No un-forwarded PASS/MAYBE candidates to promote.")
        conn.close()
        return {
            "forwarded_pass_count": 0, "forwarded_maybe_count": 0,
            "forwarded_total": 0, "dedupe_forwarded_removed": 0, "errors": 0,
        }

    logger.info("Found %d un-forwarded candidates (route_maybes=%s)", len(candidates), route_maybes)

    # ── Dedupe by URL ─────────────────────────────────────────────────
    seen_urls = set()
    deduped = []
    dedupe_removed = 0
    for c in candidates:
        url = c.get("url", "")
        if url in seen_urls:
            dedupe_removed += 1
            logger.debug("DEDUPE_FORWARD_SKIP: id=%s url=%s", c["candidate_id"], url[:80])
            continue
        seen_urls.add(url)
        deduped.append(c)

    # ── Score filter for MAYBEs ───────────────────────────────────────
    final = []
    for c in deduped:
        tier = "pass" if c["triage_status"] == "PASS" else "maybe"
        score = c.get("triage_score", 0)
        if tier == "maybe" and score < maybe_score_min:
            logger.debug("SKIP_FORWARD: id=%s score=%d below maybe_score_min=%d",
                         c["candidate_id"], score, maybe_score_min)
            continue
        final.append((c, tier))

    stats = {
        "forwarded_pass_count": 0,
        "forwarded_maybe_count": 0,
        "forwarded_total": 0,
        "dedupe_forwarded_removed": dedupe_removed,
        "skipped_kill": 0,
        "errors": 0,
    }

    # ── Promote to case_leads ─────────────────────────────────────────
    for cand, tier in final:
        cid = cand["candidate_id"]
        score = cand.get("triage_score", 0)

        # Parse triage patterns for routing metadata
        patterns_raw = cand.get("triage_patterns_json", "{}")
        if isinstance(patterns_raw, str):
            try:
                patterns = json.loads(patterns_raw)
            except json.JSONDecodeError:
                patterns = {}
        else:
            patterns = patterns_raw or {}

        # Build entities_json with tier + routing info
        entities = {
            "forwarded_tier": tier,
            "anchors_present": patterns.get("anchors_present", []),
            "followup_vectors": patterns.get("followup_vectors", []),
            "sensitivity_review": patterns.get("sensitivity_review", False),
            "sensitivity_type": patterns.get("sensitivity_type"),
            "missing_anchor": patterns.get("missing_anchor"),
            "needs_artifact_hunt": patterns.get("needs_artifact_hunt", True),
            "artifact_hints": patterns.get("artifact_hints", []),
        }

        lead = {
            "lead_id": f"fwd-{cid}",
            "source_id": cand.get("source_id"),
            "title": cand.get("title", ""),
            "url": cand.get("url", ""),
            "published_at": cand.get("published_at"),
            "snippet": (cand.get("description") or "")[:500],
            "entities_json": entities,
            "incident_type": cand.get("incident_type", "unknown"),
            "hook_score": score,
            "risk_flags_json": [],
            "status": "NEW",
            "promoted_from": cid,
        }

        # Parse risk flags from candidate
        risk_raw = cand.get("risk_flags_json", "[]")
        if isinstance(risk_raw, str):
            try:
                lead["risk_flags_json"] = json.loads(risk_raw)
            except json.JSONDecodeError:
                lead["risk_flags_json"] = []

        # ── Per-ticket logging ────────────────────────────────────
        log_parts = [
            f"FORWARD: id={cid}",
            f"label={cand['triage_status']}",
            f"score={score}",
            f"tier={tier}",
        ]
        if tier == "maybe":
            log_parts.append("reason=ROUTE_MAYBES_TO_ARTIFACT_HUNT")
        logger.info("  ".join(log_parts))

        if dry_run:
            stats[f"forwarded_{tier}_count"] += 1
            stats["forwarded_total"] += 1
            continue

        try:
            if insert_lead(conn, lead):
                stats[f"forwarded_{tier}_count"] += 1
                stats["forwarded_total"] += 1
            else:
                # Duplicate — already promoted (race condition guard)
                logger.debug("Lead already exists for candidate %s", cid)
        except Exception as exc:
            logger.error("Forward error for %s: %s", cid, exc)
            stats["errors"] += 1

    conn.close()

    logger.info(
        "Forward complete: forwarded_pass=%d  forwarded_maybe=%d  forwarded_total=%d  "
        "dedupe_removed=%d  errors=%d",
        stats["forwarded_pass_count"], stats["forwarded_maybe_count"],
        stats["forwarded_total"], stats["dedupe_forwarded_removed"],
        stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Forward triaged PASS+MAYBE candidates to case_leads for artifact hunt.")
    parser.add_argument("--limit", type=int, default=500, help="Max candidates to forward.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = forward(limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
