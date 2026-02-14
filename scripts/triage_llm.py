#!/usr/bin/env python3
"""LLM-based triage scoring for pipeline candidates.

Reads NEW candidates, calls the LLM with the complete-case triage rubric,
and updates triage_status / score / rationale.  Implements:
- "Complete case" anchor system (R2): PASS requires >=2 completeness anchors
- Sensitive-inclusive routing (R5): no auto-kill for minors/CSA/trafficking
- Follow-up vectors (R6): every PASS/MAYBE includes actionable next steps
- Threshold-tunable scoring (R7): model_score + anchor count => decision

Usage:
    python -m scripts.triage_llm                        # triage all NEW candidates
    python -m scripts.triage_llm --status NEW            # only NEW
    python -m scripts.triage_llm --limit 50              # cap at 50
    python -m scripts.triage_llm --dry-run               # preview, don't update
"""

import argparse
import json
import sys

from scripts.config_loader import get_openrouter_client, get_policy, setup_logging
from scripts.db import get_connection, get_candidates, init_db, update_triage

logger = setup_logging("triage_llm")

# ── Triage prompt ──────────────────────────────────────────────────────────
TRIAGE_SYSTEM_PROMPT = """\
You are a triage analyst for a factual long-form video channel covering law
enforcement incidents (bodycam, dashcam, court proceedings, critical incidents,
officer misconduct, criminal cases).

## Your job
Evaluate whether a candidate is a COMPLETE CASE worth producing.

## Completeness anchors (critical — count how many are present)
A1. Suspect / defendant / officer NAMED (or uniquely identified)
A2. Charges / arrest / indictment / court hearing EXPLICITLY stated
A3. Jurisdiction is explicit (city/county/state + agency)
A4. Follow-up target is explicit: bodycam release, affidavit, interrogation,
    court filings, dashcam, surveillance, FOIA mention
A5. Multi-source confirmation (two outlets / official + media) OR strong
    official source (DA / PD / AG / court)

## Decision rules
PASS — Complete case (immediately actionable)
  * At least 2 completeness anchors present, AND
  * model_score >= 70 (see scoring below)
  * If anchors >= 3 and model_score >= 60, also PASS (strong anchor evidence
    compensates for slightly lower narrative quality)

MAYBE — Nearly complete (missing exactly 1 key anchor)
  * Clearly a real case (not generic news), AND
  * Missing exactly one major anchor, BUT has a clear path to completion
    (e.g. "arrest warrant issued", "suspect identified but charges pending",
     "court date scheduled but charges unclear")
  * model_score >= 55 with at least 1 anchor
  * OR: anchors >= 2 but 55 <= model_score < 70

KILL — Not actionable
  * Off-topic: sports / weather / politics / economics / general policy
  * Not a discrete case (no specific incident, no parties, no actionable follow-up)
  * Pure "seed" — investigation underway with zero anchors
  * Mostly opinion/analysis with no case facts
  * model_score < 55 OR (model_score < 70 AND zero anchors)

## model_score (0–100) — narrative + case quality
Score the CASE on these dimensions:
  hook_clarity  (0–25): Can viewers immediately understand the incident?
  escalation    (0–25): Do the stakes intensify? (routine -> wild, danger, weapon, chase)
  character     (0–15): Memorable quote, decision, or personality?
  resolution    (0–15): Clear outcome (arrest, twist, reveal)?
  uniqueness    (0–10): Distinct from typical content?
  quality       (0–10): Source credibility, detail richness
model_score = sum of all dimensions (0–100).
Give genuine scores — KILL scores should still vary; don't just output 0.

## Sensitive cases — INCLUDE, do not auto-kill
Sensitive categories (minors, CSA, trafficking, sexual violence, graphic
violence) ARE eligible for PASS/MAYBE.  When present:
  * Set sensitivity_review = true
  * Set sensitivity_type to one of: minors | CSA | trafficking |
    sexual_violence | graphic_violence
  * Do NOT include victim identity if minor / CSA
  * Avoid explicit sexual details
  * Keep output to "facts + procedural status" only
This is a routing/handling flag, not a block.

## Follow-up vectors (required for PASS and MAYBE)
For PASS/MAYBE, provide followup_vectors — a list of 2–5 items from:
  court_docket, charging_docs, affidavit, bodycam, interrogation,
  dashcam, surveillance, 911_audio, press_conference, civil_suit
Also provide:
  why_complete_or_missing: 1–2 sentences explaining case completeness
  missing_anchor: (MAYBE only) exactly one anchor id that's missing

## Routing flags (set these booleans)
  needs_transcript: true if transcript is missing and would help evaluation
  needs_artifact_hunt: true if case is promising but primary artifacts not found

## OUTPUT — Return ONLY valid JSON matching this schema:
{
  "status": "PASS|MAYBE|KILL",
  "model_score": <0-100>,
  "anchors_present": ["A1","A2","A3","A4","A5"],
  "sensitivity_review": <true|false>,
  "sensitivity_type": <string|null>,
  "followup_vectors": ["court_docket","bodycam",...],
  "missing_anchor": <string|null>,
  "why_complete_or_missing": "<1-2 sentences>",
  "reason": "<1-2 sentences factual verdict>",
  "patterns": {
    "hook_clarity": <0-25>,
    "escalation": <0-25>,
    "character": <0-15>,
    "resolution": <0-15>,
    "uniqueness": <0-10>,
    "quality": <0-10>
  },
  "incident_type": "pursuit|dui|domestic|welfare_check|theft|shooting|use_of_force|fraud|assault|missing_person|CSA|trafficking|homicide|unknown",
  "risk_flags": [],
  "needs_transcript": <true|false>,
  "needs_artifact_hunt": <true|false>,
  "artifact_hints": ["bodycam","dashcam","court","affidavit","surveillance","911_audio","FOIA"],
  "shorts_moments": [],
  "facts_to_verify": ["<claim 1>","<claim 2>"]
}

Important:
  * anchors_present must contain only anchor ids actually found (A1–A5)
  * followup_vectors is REQUIRED (non-empty) for PASS and MAYBE
  * missing_anchor is REQUIRED for MAYBE (exactly one item, e.g. "A2")
  * model_score must reflect genuine narrative quality — do NOT assign 0 to all KILLs
  * sensitivity_type is null unless sensitivity_review is true
"""

# Valid anchor IDs for validation
VALID_ANCHORS = {"A1", "A2", "A3", "A4", "A5"}

# Valid follow-up vector types
VALID_FOLLOWUP = {
    "court_docket", "charging_docs", "affidavit", "bodycam", "interrogation",
    "dashcam", "surveillance", "911_audio", "press_conference", "civil_suit",
}


def build_triage_user_prompt(candidate: dict) -> str:
    """Build the user prompt with candidate details."""
    parts = []
    parts.append(f"TITLE: {candidate.get('title', 'N/A')}")
    parts.append(f"URL: {candidate.get('url', 'N/A')}")
    parts.append(f"PLATFORM: {candidate.get('platform', 'N/A')}")
    parts.append(f"DURATION: {candidate.get('duration_sec', 'unknown')} seconds")
    parts.append(f"PUBLISHED: {candidate.get('published_at', 'unknown')}")

    desc = candidate.get("description", "") or ""
    if desc:
        parts.append(f"DESCRIPTION:\n{desc[:2000]}")

    transcript = candidate.get("transcript_text", "") or ""
    if transcript:
        parts.append(f"TRANSCRIPT (first 4000 chars):\n{transcript[:4000]}")

    entities = candidate.get("entities_json", "[]")
    if isinstance(entities, str):
        try:
            entities = json.loads(entities)
        except json.JSONDecodeError:
            entities = []
    if entities:
        parts.append(f"ENTITIES: {json.dumps(entities)}")

    quality = candidate.get("quality_signals_json", "{}")
    if isinstance(quality, str):
        try:
            quality = json.loads(quality)
        except json.JSONDecodeError:
            quality = {}
    if quality:
        parts.append(f"QUALITY SIGNALS: {json.dumps(quality)}")

    return "\n\n".join(parts)


def parse_triage_response(raw: str) -> dict | None:
    """Parse the LLM's JSON response, handling markdown code blocks."""
    text = raw.strip()
    if "```" in text:
        if "```json" in text:
            text = text.split("```json")[-1].split("```")[0].strip()
        else:
            text = text.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Validate required fields
    if "status" not in data or "model_score" not in data:
        # Fallback: accept "score" if "model_score" missing
        if "score" in data and "model_score" not in data:
            data["model_score"] = data["score"]
        elif "model_score" not in data:
            return None

    # Normalize status
    data["status"] = data["status"].upper()
    if data["status"] not in ("PASS", "MAYBE", "KILL"):
        data["status"] = "MAYBE"

    # Ensure model_score is int
    data["model_score"] = int(data.get("model_score", 0))

    # Keep "score" in sync for DB compatibility (update_triage uses "score")
    data["score"] = data["model_score"]

    # Ensure anchors_present is a valid list
    raw_anchors = data.get("anchors_present", [])
    if isinstance(raw_anchors, list):
        data["anchors_present"] = [a for a in raw_anchors if a in VALID_ANCHORS]
    else:
        data["anchors_present"] = []

    # Ensure patterns exist
    if "patterns" not in data:
        data["patterns"] = {}

    # Ensure routing fields
    data.setdefault("needs_transcript", False)
    data.setdefault("needs_artifact_hunt", False)
    data.setdefault("artifact_hints", [])

    # Ensure sensitivity fields
    data.setdefault("sensitivity_review", False)
    data.setdefault("sensitivity_type", None)

    # Ensure follow-up fields
    data.setdefault("followup_vectors", [])
    data.setdefault("missing_anchor", None)
    data.setdefault("why_complete_or_missing", "")

    return data


def apply_threshold_decision(data: dict, thresholds: dict) -> str:
    """Apply threshold-based decision logic using model_score + anchor count.

    This is the authoritative decision function — overrides whatever the LLM
    chose as "status" to ensure consistency with our operating-point config.

    Thresholds (from policy.yaml):
        pass_score:   minimum model_score for PASS (default 70)
        pass_anchors: minimum anchors for PASS (default 2)
        pass_strong_anchors: anchor count that lowers score requirement (default 3)
        pass_strong_score: lower score threshold when anchors are strong (default 60)
        maybe_score:  minimum model_score for MAYBE (default 55)
        maybe_anchors: minimum anchors for MAYBE (default 1)
    """
    score = data["model_score"]
    n_anchors = len(data.get("anchors_present", []))

    pass_score = thresholds.get("pass_score", 70)
    pass_anchors = thresholds.get("pass_anchors", 2)
    pass_strong_anchors = thresholds.get("pass_strong_anchors", 3)
    pass_strong_score = thresholds.get("pass_strong_score", 60)
    maybe_score = thresholds.get("maybe_score", 55)
    maybe_anchors = thresholds.get("maybe_anchors", 1)

    # PASS: standard path
    if n_anchors >= pass_anchors and score >= pass_score:
        return "PASS"

    # PASS: strong-anchor path (>=3 anchors relaxes score requirement)
    if n_anchors >= pass_strong_anchors and score >= pass_strong_score:
        return "PASS"

    # MAYBE: decent score + at least 2 anchors but below pass_score
    if n_anchors >= pass_anchors and score >= maybe_score:
        return "MAYBE"

    # MAYBE: high score but only 1 anchor (nearly complete)
    if n_anchors >= maybe_anchors and score >= pass_score:
        return "MAYBE"

    # KILL: everything else
    return "KILL"


def apply_hard_filters(candidate: dict, policy: dict) -> dict | None:
    """Apply hard-filter rules. Returns a KILL triage dict if filtered, else None."""
    duration = candidate.get("duration_sec") or 0
    min_dur = policy.get("triage", {}).get("min_duration_sec", 60)

    if 0 < duration < min_dur:
        return {
            "status": "KILL",
            "score": 15,
            "model_score": 15,
            "reason": f"Duration {duration}s is below minimum {min_dur}s.",
            "patterns": {},
            "incident_type": "unknown",
            "risk_flags": [],
            "shorts_moments": [],
            "facts_to_verify": [],
            "anchors_present": [],
            "sensitivity_review": False,
            "sensitivity_type": None,
            "followup_vectors": [],
            "missing_anchor": None,
        }

    return None


def triage(status: str = "NEW", limit: int = 200, dry_run: bool = False) -> dict:
    """Run LLM triage on candidates.

    Returns:
        dict with keys: processed, pass_count, maybe_count, kill_count, errors
    """
    init_db()
    conn = get_connection()
    candidates = get_candidates(conn, status=status, limit=limit)

    if not candidates:
        logger.info("No candidates with status=%s to triage.", status)
        conn.close()
        return {"processed": 0, "pass_count": 0, "maybe_count": 0, "kill_count": 0, "errors": 0}

    policy = get_policy("triage") or {}

    # Threshold config — tunable via policy.yaml
    thresholds = {
        "pass_score": policy.get("pass_score", policy.get("pass_threshold", 70)),
        "pass_anchors": policy.get("pass_anchors", 2),
        "pass_strong_anchors": policy.get("pass_strong_anchors", 3),
        "pass_strong_score": policy.get("pass_strong_score", 60),
        "maybe_score": policy.get("maybe_score", policy.get("maybe_threshold", 55)),
        "maybe_anchors": policy.get("maybe_anchors", 1),
    }

    client = get_openrouter_client()
    model = get_policy("llm", "triage_model", "openai/gpt-4o")
    temperature = get_policy("llm", "triage_temperature", 0.2)
    max_tokens = get_policy("llm", "triage_max_tokens", 1500)

    stats = {
        "processed": 0, "pass_count": 0, "maybe_count": 0, "kill_count": 0,
        "errors": 0, "deduped": 0, "sensitive": 0,
    }

    # Deduplicate candidates by URL or normalized title+domain
    seen_keys = set()

    for cand in candidates:
        cid = cand["candidate_id"]
        title = (cand.get("title") or "")[:60]

        # Dedupe: skip if we've seen an identical URL or title+domain
        url = cand.get("url", "")
        dedup_key = url if url else f"{title}|{cand.get('source_id', '')}"
        if dedup_key in seen_keys:
            logger.debug("Skipping duplicate: %s — %s", cid, title)
            stats["deduped"] += 1
            continue
        seen_keys.add(dedup_key)

        logger.info("Triaging: %s — %s", cid, title)

        # Hard filters first
        hard_kill = apply_hard_filters(cand, {"triage": policy})
        if hard_kill:
            logger.info("Hard KILL for %s: %s", cid, hard_kill["reason"])
            if not dry_run:
                update_triage(conn, cid, hard_kill)
            stats["kill_count"] += 1
            stats["processed"] += 1
            continue

        # LLM triage
        user_prompt = build_triage_user_prompt(cand)
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = resp.choices[0].message.content
            triage_result = parse_triage_response(raw)

            if not triage_result:
                logger.warning("Failed to parse triage response for %s: %s", cid, raw[:200])
                stats["errors"] += 1
                continue

            # ── Threshold-based decision (authoritative) ──────────
            triage_result["status"] = apply_threshold_decision(triage_result, thresholds)

            # ── Artifact routing (not blocking) ───────────────────
            source_class = cand.get("source_class", "secondary")
            if source_class in ("secondary", "discovery_only"):
                if triage_result["status"] in ("PASS", "MAYBE"):
                    triage_result["needs_artifact_hunt"] = True
                    if source_class == "discovery_only":
                        logger.info("  Routing: discovery_only %s flagged for artifact hunt", cid)

            # ── Sensitivity tracking ──────────────────────────────
            if triage_result.get("sensitivity_review"):
                stats["sensitive"] += 1
                logger.info(
                    "  Sensitive case (%s): %s — %s",
                    triage_result.get("sensitivity_type", "?"), cid, title,
                )

            # ── Store anchor/follow-up data in patterns for DB ────
            # Merge anchor & routing data into patterns so it persists
            patterns = triage_result.get("patterns", {})
            patterns["anchors_present"] = triage_result.get("anchors_present", [])
            patterns["followup_vectors"] = triage_result.get("followup_vectors", [])
            patterns["missing_anchor"] = triage_result.get("missing_anchor")
            patterns["why_complete_or_missing"] = triage_result.get("why_complete_or_missing", "")
            patterns["sensitivity_review"] = triage_result.get("sensitivity_review", False)
            patterns["sensitivity_type"] = triage_result.get("sensitivity_type")
            patterns["needs_transcript"] = triage_result.get("needs_transcript", False)
            patterns["needs_artifact_hunt"] = triage_result.get("needs_artifact_hunt", False)
            patterns["artifact_hints"] = triage_result.get("artifact_hints", [])
            triage_result["patterns"] = patterns

            logger.info(
                "  -> %s (score=%d, anchors=%d) %s",
                triage_result["status"],
                triage_result["model_score"],
                len(triage_result.get("anchors_present", [])),
                triage_result.get("reason", "")[:80],
            )

            if dry_run:
                logger.info("[DRY RUN] Would update %s with %s", cid, triage_result["status"])
            else:
                update_triage(conn, cid, triage_result)

            stats["processed"] += 1
            s = triage_result["status"]
            if s == "PASS":
                stats["pass_count"] += 1
            elif s == "MAYBE":
                stats["maybe_count"] += 1
            else:
                stats["kill_count"] += 1

        except Exception as exc:
            logger.error("LLM triage error for %s: %s", cid, exc)
            stats["errors"] += 1

    conn.close()
    logger.info(
        "Triage complete: %d processed — PASS=%d, MAYBE=%d, KILL=%d, sensitive=%d, errors=%d",
        stats["processed"], stats["pass_count"], stats["maybe_count"],
        stats["kill_count"], stats["sensitive"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Run LLM triage on pipeline candidates.")
    parser.add_argument("--status", default="NEW", help="Candidate status to triage (default: NEW).")
    parser.add_argument("--limit", type=int, default=200, help="Max candidates to triage.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = triage(status=args.status, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
