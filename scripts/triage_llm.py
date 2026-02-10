#!/usr/bin/env python3
"""LLM-based triage scoring for pipeline candidates.

Reads NEW candidates, calls the LLM with the triage prompt, and updates
triage_status / score / rationale using the defined JSON schema.

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
You are a content triage analyst for a factual long-form video channel focused on
law enforcement incidents (bodycam, dashcam, court proceedings, critical incidents).

Your job: evaluate whether a candidate video/article is worth producing into a
high-retention story. Score it on these dimensions:

SCORING (0-100 total):
- hook_clarity (0-25): Can viewers understand what's happening in the first 15 seconds?
- stakes (0-25): Are the stakes obvious? (danger, chase, weapon, missing person, serious crash)
  This maps to "escalation" in the score.
- escalation (0-25): Does it intensify from routine to wild?
- character (0-15): Is there a memorable quote, decision, or personality?
- resolution (0-15): Is there a clear outcome (arrest, twist, reveal)?

Note: "stakes" and "escalation" share the 0-25 range. Combine them: the
"escalation" field in your output should reflect BOTH stakes intensity AND
escalation arc. Total max = 25 for hook_clarity + 25 for escalation + 15 character + 15 resolution + 10 quality + 10 uniqueness = 100.

HARD KILL rules (auto score 0, status KILL):
- Duration < 60 seconds (unless clearly a short candidate)
- No transcript AND description is too vague AND audio likely poor
- Risk flags: minors in sensitive contexts, explicit sexual violence, extreme gore

VIRAL PATTERNS (a good candidate has >= 3 of these):
1) Confusion -> clarity in first 15 seconds
2) Stakes are obvious
3) Escalation (routine -> wild)
4) Character moment (memorable quote/decision)
5) Resolution (arrest/twist/reveal/outcome)

OUTPUT: Return ONLY valid JSON matching this exact schema:
{
  "status": "PASS|MAYBE|KILL",
  "score": <0-100>,
  "reason": "<1-3 sentences explaining the verdict>",
  "patterns": {
    "hook_clarity": <0-25>,
    "stakes": <0-25>,
    "escalation": <0-25>,
    "character": <0-15>,
    "resolution": <0-15>
  },
  "incident_type": "pursuit|dui|domestic|welfare_check|theft|shooting|use_of_force|fraud|assault|missing_person|unknown",
  "risk_flags": ["minors", "doxxing_risk", "graphic_injury", "sexual_violence", "extreme_gore"],
  "shorts_moments": [
    {"start_sec": <number>, "end_sec": <number>, "why": "<brief reason>"}
  ],
  "facts_to_verify": ["<claim 1>", "<claim 2>"]
}

PASS threshold: score >= 70
MAYBE: score 50-69
KILL: score < 50

Be strict. Only PASS truly compelling stories. Most candidates should be MAYBE or KILL.
"""


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
        # Extract from code block
        if "```json" in text:
            text = text.split("```json")[-1].split("```")[0].strip()
        else:
            text = text.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Validate required fields
    if "status" not in data or "score" not in data:
        return None

    # Normalize status
    data["status"] = data["status"].upper()
    if data["status"] not in ("PASS", "MAYBE", "KILL"):
        data["status"] = "MAYBE"

    # Ensure score is int
    data["score"] = int(data.get("score", 0))

    # Ensure patterns exist
    if "patterns" not in data:
        data["patterns"] = {}

    return data


def apply_hard_filters(candidate: dict, policy: dict) -> dict | None:
    """Apply hard-filter rules. Returns a KILL triage dict if filtered, else None."""
    duration = candidate.get("duration_sec") or 0
    min_dur = policy.get("triage", {}).get("min_duration_sec", 60)

    if 0 < duration < min_dur:
        return {
            "status": "KILL",
            "score": 0,
            "reason": f"Duration {duration}s is below minimum {min_dur}s.",
            "patterns": {},
            "incident_type": "unknown",
            "risk_flags": [],
            "shorts_moments": [],
            "facts_to_verify": [],
        }

    desc = candidate.get("description", "") or ""
    transcript = candidate.get("transcript_text", "") or ""
    min_text = policy.get("triage", {}).get("min_text_length", 80)

    if not transcript and len(desc) < min_text:
        quality = candidate.get("quality_signals_json", "{}")
        if isinstance(quality, str):
            try:
                quality = json.loads(quality)
            except json.JSONDecodeError:
                quality = {}
        audio_guess = quality.get("audio_quality_guess", "unknown")
        if audio_guess in ("poor", "unknown") and not transcript:
            return {
                "status": "KILL",
                "score": 0,
                "reason": "No transcript, vague description, and likely poor audio.",
                "patterns": {},
                "incident_type": "unknown",
                "risk_flags": [],
                "shorts_moments": [],
                "facts_to_verify": [],
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
    pass_threshold = policy.get("pass_threshold", 70)
    maybe_threshold = policy.get("maybe_threshold", 50)

    client = get_openrouter_client()
    model = get_policy("llm", "triage_model", "openai/gpt-4o")
    temperature = get_policy("llm", "triage_temperature", 0.2)
    max_tokens = get_policy("llm", "triage_max_tokens", 1500)

    stats = {"processed": 0, "pass_count": 0, "maybe_count": 0, "kill_count": 0, "errors": 0}

    for cand in candidates:
        cid = cand["candidate_id"]
        title = (cand.get("title") or "")[:60]
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

            # Override status based on thresholds (in case LLM is inconsistent)
            score = triage_result["score"]
            if score >= pass_threshold:
                triage_result["status"] = "PASS"
            elif score >= maybe_threshold:
                triage_result["status"] = "MAYBE"
            else:
                triage_result["status"] = "KILL"

            logger.info(
                "  -> %s (score=%d) %s",
                triage_result["status"], score, triage_result.get("reason", "")[:80],
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
        "Triage complete: %d processed — PASS=%d, MAYBE=%d, KILL=%d, errors=%d",
        stats["processed"], stats["pass_count"], stats["maybe_count"],
        stats["kill_count"], stats["errors"],
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
