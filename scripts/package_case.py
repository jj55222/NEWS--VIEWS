#!/usr/bin/env python3
"""Package approved cases into production-ready bundles.

For each APPROVED case (or PASS candidate promoted to case):
- Build timeline_json with timestamped beats
- Generate narration_draft
- Create shorts_plan_json with clip moments
- Save a clean case_bundle folder

Usage:
    python -m scripts.package_case                       # package all APPROVED cases
    python -m scripts.package_case --promote             # auto-promote PASS candidates to cases
    python -m scripts.package_case --case-id <id>        # package a specific case
    python -m scripts.package_case --dry-run             # preview, don't write
"""

import argparse
import json
import os
import uuid
from pathlib import Path

from scripts.config_loader import (
    CASE_BUNDLES_DIR,
    get_openrouter_client,
    get_policy,
    setup_logging,
)
from scripts.db import (
    get_candidates,
    get_cases,
    get_connection,
    get_corroborations,
    init_db,
    insert_case,
    now_iso,
    update_case_fields,
    update_case_status,
)

logger = setup_logging("package_case")

# ── Timeline extraction prompt ─────────────────────────────────────────────
TIMELINE_SYSTEM_PROMPT = """\
You are a video editor's assistant. Given a law enforcement incident video's
transcript and metadata, extract a timeline of "beats" — key moments that
structure the story.

Standard beat types (use these labels):
- dispatch: Call/dispatch information
- first_contact: Officer arrives / first interaction
- identification: ID check, license request
- first_lie: Subject gives false info (if applicable)
- escalation: Situation intensifies
- confrontation: Physical altercation, weapon drawn
- chase: Vehicle or foot pursuit
- struggle: Physical struggle
- backup: Additional officers arrive
- evidence: Key evidence found
- arrest: Subject taken into custody
- medical: Medical attention needed
- aftermath: Scene aftermath, follow-up
- interview: Witness/suspect interview
- twist: Unexpected development
- resolution: Final outcome

Return ONLY valid JSON:
{
  "beats": [
    {
      "timestamp_sec": <number>,
      "beat_type": "<type from list above>",
      "description": "<1 sentence>",
      "quote": "<memorable quote if any, else null>"
    }
  ]
}

Aim for beats every 30-90 seconds of video. Focus on moments that drive the
narrative forward. Include timestamps if the transcript has time markers,
otherwise estimate based on content flow.
"""

# ── Narration prompt ───────────────────────────────────────────────────────
NARRATION_SYSTEM_PROMPT = """\
You are a scriptwriter for a factual law enforcement incident video channel.
Write narration following this template:

1. COLD OPEN (1-2 sentences): "This started as ___ and ended with ___."
2. SET SCENE: Time, place, initial circumstances
3. WALK THE BEATS: Narrate each timeline beat with context + sources
4. CLOSE: Outcome (if known) + sourcing note

Rules:
- State only what is visible in the video or confirmed by official sources
- Use "allegedly" or "according to [source]" for unconfirmed claims
- No humiliation, no "guilty" language pre-conviction
- Keep tone factual, engaging, not sensational
- Reference specific timestamps: "[At 3:42]" or "[Timestamp: 3:42]"
- End with: "Sources for this video are listed in the description."

Return the narration as plain text (not JSON). Target 800-2000 words for
an 8-25 minute video.
"""

# ── Shorts planning prompt ────────────────────────────────────────────────
SHORTS_SYSTEM_PROMPT = """\
You are a shorts/clips editor. Given a story's timeline beats, identify the
3-10 best moments for 15-60 second short-form clips.

For each clip, provide:
- Which beat(s) it covers
- Start and end timestamps
- A 1-line context caption for the viewer
- An end hook ("Part 2 on channel" or "Full story in longform")
- Why this moment works as a standalone clip

Return ONLY valid JSON:
{
  "shorts": [
    {
      "clip_number": 1,
      "title": "<catchy short title>",
      "start_sec": <number>,
      "end_sec": <number>,
      "beats_covered": ["<beat_type>"],
      "context_caption": "<1 line>",
      "end_hook": "<call to action>",
      "why": "<why this works as a clip>"
    }
  ]
}

Prioritize:
- Quote moments (memorable lines)
- Escalation moments (routine -> wild transition)
- Twist/reveal moments
- Chase/action moments
"""


def promote_pass_to_case(conn, candidate: dict) -> str:
    """Create a case from a PASS candidate. Returns case_id."""
    case_id = f"case_{uuid.uuid4().hex[:12]}"
    insert_case(conn, {
        "case_id": case_id,
        "primary_candidate_id": candidate["candidate_id"],
        "case_title_working": candidate.get("title", "Untitled Case"),
        "status": "APPROVED",
    })
    logger.info("Promoted candidate %s to case %s", candidate["candidate_id"], case_id)
    return case_id


def extract_timeline(candidate: dict, client) -> list[dict]:
    """Use LLM to extract timeline beats from candidate."""
    parts = [
        f"TITLE: {candidate.get('title', 'N/A')}",
        f"DURATION: {candidate.get('duration_sec', 'unknown')} seconds",
    ]

    transcript = candidate.get("transcript_text") or ""
    if transcript:
        parts.append(f"TRANSCRIPT:\n{transcript[:8000]}")
    else:
        desc = candidate.get("description") or ""
        parts.append(f"DESCRIPTION:\n{desc[:3000]}")

    model = get_policy("llm", "narration_model", "openai/gpt-4o")
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.3,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": TIMELINE_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```json")[-1].split("```")[0].strip() if "```json" in raw else raw.split("```")[1].split("```")[0].strip()
        data = json.loads(raw)
        return data.get("beats", [])
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Timeline extraction failed: %s", exc)
        return []


def draft_narration(candidate: dict, timeline: list[dict], fact_pack: dict, client) -> str:
    """Use LLM to draft narration from timeline + fact pack."""
    parts = [
        f"TITLE: {candidate.get('title', 'N/A')}",
        f"DURATION: {candidate.get('duration_sec', 'unknown')} seconds",
        f"\nTIMELINE BEATS:\n{json.dumps(timeline, indent=2)}",
    ]

    if fact_pack.get("summary"):
        parts.append(f"\nFACT PACK SUMMARY: {fact_pack['summary']}")
    if fact_pack.get("verified_facts"):
        parts.append(f"\nVERIFIED FACTS: {json.dumps(fact_pack['verified_facts'])}")
    if fact_pack.get("unverified_claims"):
        parts.append(f"\nUNVERIFIED (use 'allegedly'): {json.dumps(fact_pack['unverified_claims'])}")

    model = get_policy("llm", "narration_model", "openai/gpt-4o")
    temperature = get_policy("llm", "narration_temperature", 0.5)
    max_tokens = get_policy("llm", "narration_max_tokens", 4000)

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": NARRATION_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Narration draft failed: %s", exc)
        return ""


def plan_shorts(timeline: list[dict], duration_sec: int, client) -> list[dict]:
    """Use LLM to plan shorts clips from timeline."""
    user_prompt = (
        f"VIDEO DURATION: {duration_sec} seconds\n\n"
        f"TIMELINE BEATS:\n{json.dumps(timeline, indent=2)}"
    )

    model = get_policy("llm", "narration_model", "openai/gpt-4o")
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.4,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": SHORTS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```json")[-1].split("```")[0].strip() if "```json" in raw else raw.split("```")[1].split("```")[0].strip()
        data = json.loads(raw)
        return data.get("shorts", [])
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Shorts planning failed: %s", exc)
        return []


def save_case_bundle(case_id: str, candidate: dict, timeline: list, narration: str,
                     shorts: list, fact_pack: dict, corroborations: list) -> Path:
    """Save all case artifacts to a bundle directory."""
    bundle_dir = CASE_BUNDLES_DIR / case_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Timeline
    with open(bundle_dir / "timeline.json", "w") as f:
        json.dump(timeline, f, indent=2)

    # Narration
    with open(bundle_dir / "narration_draft.txt", "w") as f:
        f.write(narration)

    # Shorts plan
    with open(bundle_dir / "shorts_plan.json", "w") as f:
        json.dump(shorts, f, indent=2)

    # Fact pack
    with open(bundle_dir / "fact_pack.json", "w") as f:
        json.dump(fact_pack, f, indent=2)

    # Sources list
    sources = [
        {"url": c.get("url", ""), "type": c.get("source_type", ""), "title": c.get("title", "")}
        for c in corroborations
    ]
    sources.insert(0, {"url": candidate.get("url", ""), "type": "primary", "title": candidate.get("title", "")})
    with open(bundle_dir / "sources.json", "w") as f:
        json.dump(sources, f, indent=2)

    # Case metadata
    meta = {
        "case_id": case_id,
        "title": candidate.get("title", ""),
        "url": candidate.get("url", ""),
        "duration_sec": candidate.get("duration_sec"),
        "incident_type": candidate.get("incident_type", "unknown"),
        "triage_score": candidate.get("triage_score", 0),
        "num_shorts": len(shorts),
        "num_corroboration_sources": len(corroborations),
    }
    with open(bundle_dir / "case_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    logger.info("Case bundle saved to %s", bundle_dir)
    return bundle_dir


# ── Main packaging ─────────────────────────────────────────────────────────
def package(promote: bool = False, case_id: str | None = None,
            limit: int = 20, dry_run: bool = False) -> dict:
    """Package approved cases into production bundles.

    Returns:
        dict with keys: cases_packaged, timelines_built, narrations_drafted, errors
    """
    init_db()
    conn = get_connection()

    stats = {"cases_packaged": 0, "timelines_built": 0, "narrations_drafted": 0, "errors": 0}

    # Promote PASS candidates to cases if requested
    if promote:
        pass_candidates = get_candidates(conn, status="PASS", limit=limit)
        for cand in pass_candidates:
            # Check if case already exists for this candidate
            existing = conn.execute(
                "SELECT case_id FROM cases WHERE primary_candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()
            if not existing:
                promote_pass_to_case(conn, cand)

    # Get cases to package
    if case_id:
        cases = [dict(r) for r in conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchall()]
    else:
        cases = get_cases(conn, status="APPROVED", limit=limit)

    if not cases:
        logger.info("No APPROVED cases to package.")
        conn.close()
        return stats

    client = get_openrouter_client()

    for case in cases:
        cid = case["case_id"]
        candidate_id = case["primary_candidate_id"]
        logger.info("Packaging case: %s (candidate: %s)", cid, candidate_id)

        # Fetch the primary candidate
        cand_row = conn.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if not cand_row:
            logger.error("Candidate %s not found for case %s", candidate_id, cid)
            stats["errors"] += 1
            continue

        candidate = dict(cand_row)

        try:
            # 1. Extract timeline
            logger.info("  Extracting timeline...")
            timeline = extract_timeline(candidate, client)
            if timeline:
                stats["timelines_built"] += 1

            # 2. Get fact pack
            fact_pack_raw = candidate.get("facts_to_verify_json", "[]")
            if isinstance(fact_pack_raw, str):
                try:
                    fact_pack = json.loads(fact_pack_raw)
                except json.JSONDecodeError:
                    fact_pack = {}
            else:
                fact_pack = fact_pack_raw
            if isinstance(fact_pack, list):
                fact_pack = {"facts_to_verify": fact_pack}

            # 3. Draft narration
            logger.info("  Drafting narration...")
            narration = draft_narration(candidate, timeline, fact_pack, client)
            if narration:
                stats["narrations_drafted"] += 1

            # 4. Plan shorts
            logger.info("  Planning shorts...")
            duration = candidate.get("duration_sec") or 600
            shorts = plan_shorts(timeline, duration, client)

            # 5. Get corroboration sources
            corroborations = get_corroborations(conn, candidate_id)

            if dry_run:
                logger.info(
                    "[DRY RUN] Case %s: %d beats, %d words narration, %d shorts",
                    cid, len(timeline), len(narration.split()), len(shorts),
                )
            else:
                # Save bundle
                save_case_bundle(cid, candidate, timeline, narration, shorts, fact_pack, corroborations)

                # Update case in DB
                update_case_fields(conn, cid, {
                    "timeline_json": timeline,
                    "narration_draft": narration,
                    "shorts_plan_json": shorts,
                    "facts_json": fact_pack.get("verified_facts", []) if isinstance(fact_pack, dict) else [],
                })
                update_case_status(conn, cid, "PACKAGED")

            stats["cases_packaged"] += 1

        except Exception as exc:
            logger.error("Packaging error for case %s: %s", cid, exc)
            stats["errors"] += 1

    conn.close()
    logger.info(
        "Packaging complete: %d cases, %d timelines, %d narrations, %d errors",
        stats["cases_packaged"], stats["timelines_built"],
        stats["narrations_drafted"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Package approved cases into production bundles.")
    parser.add_argument("--promote", action="store_true", help="Auto-promote PASS candidates to cases.")
    parser.add_argument("--case-id", type=str, default=None, help="Package a specific case.")
    parser.add_argument("--limit", type=int, default=20, help="Max cases to package.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()

    stats = package(promote=args.promote, case_id=args.case_id, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
