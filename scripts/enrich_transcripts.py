#!/usr/bin/env python3
"""Enrich candidates with transcripts, entity extraction, and quality signals.

For YouTube candidates: fetch auto-captions or run Whisper.
For all candidates: extract entities (names, places, agencies) and estimate quality.

Usage:
    python -m scripts.enrich_transcripts                    # enrich all NEW candidates
    python -m scripts.enrich_transcripts --status NEW       # only NEW
    python -m scripts.enrich_transcripts --limit 50         # cap at 50
    python -m scripts.enrich_transcripts --whisper          # use Whisper for missing transcripts
    python -m scripts.enrich_transcripts --dry-run          # preview, don't update
"""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

import requests

from scripts.config_loader import setup_logging, get_openrouter_client, get_policy
from scripts.db import get_connection, init_db, now_iso

logger = setup_logging("enrich_transcripts")


# ── YouTube captions ───────────────────────────────────────────────────────
def fetch_youtube_captions(video_url: str) -> str | None:
    """Attempt to fetch YouTube auto-generated captions via yt-dlp."""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--skip-download",
                "--write-auto-sub",
                "--sub-lang", "en",
                "--sub-format", "vtt",
                "--output", "%(id)s",
                "--print", "%(requested_subtitles)s",
                video_url,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Try to find the subtitle file
        video_id = video_url.split("v=")[-1].split("&")[0] if "v=" in video_url else ""
        for ext in [".en.vtt", ".en.auto.vtt"]:
            vtt_path = Path(f"{video_id}{ext}")
            if vtt_path.exists():
                text = vtt_path.read_text()
                vtt_path.unlink()  # Clean up
                return _vtt_to_text(text)

        # Fallback: try yt-dlp --get-subtitles approach
        result2 = subprocess.run(
            ["yt-dlp", "--skip-download", "--print-json", video_url],
            capture_output=True, text=True, timeout=60,
        )
        if result2.returncode == 0:
            info = json.loads(result2.stdout)
            # Check for automatic_captions
            auto_caps = info.get("automatic_captions", {})
            if "en" in auto_caps:
                for fmt in auto_caps["en"]:
                    if fmt.get("ext") == "vtt":
                        cap_url = fmt["url"]
                        resp = requests.get(cap_url, timeout=30)
                        if resp.ok:
                            return _vtt_to_text(resp.text)

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
        logger.debug("Caption fetch failed for %s: %s", video_url, exc)

    return None


def _vtt_to_text(vtt: str) -> str:
    """Convert VTT subtitle content to plain text."""
    lines = []
    for line in vtt.split("\n"):
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or re.match(r"^\d+$", line):
            continue
        # Remove VTT tags
        clean = re.sub(r"<[^>]+>", "", line)
        if clean and clean not in lines[-1:]:
            lines.append(clean)
    return " ".join(lines)


# ── Whisper transcription ─────────────────────────────────────────────────
def transcribe_with_whisper(video_url: str, model_size: str = "base") -> str | None:
    """Download audio and transcribe with OpenAI Whisper."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "audio.mp3"
            # Download audio only
            result = subprocess.run(
                [
                    "yt-dlp",
                    "-x", "--audio-format", "mp3",
                    "--output", str(audio_path),
                    video_url,
                ],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning("Audio download failed for %s", video_url)
                return None

            # Find the actual downloaded file (yt-dlp may add extension)
            audio_files = list(Path(tmpdir).glob("audio*"))
            if not audio_files:
                return None
            actual_path = audio_files[0]

            # Run Whisper
            import whisper
            model = whisper.load_model(model_size)
            result = model.transcribe(str(actual_path))
            return result.get("text", "")

    except ImportError:
        logger.warning("Whisper not installed. Install with: pip install openai-whisper")
        return None
    except Exception as exc:
        logger.error("Whisper transcription failed for %s: %s", video_url, exc)
        return None


# ── Entity extraction ─────────────────────────────────────────────────────
def extract_entities_llm(text: str, client) -> dict:
    """Use LLM to extract entities from transcript/description text."""
    if not text or len(text) < 50:
        return {"names": [], "places": [], "agencies": [], "charges": []}

    # Truncate very long text
    truncated = text[:6000]

    try:
        resp = client.chat.completions.create(
            model=get_policy("llm", "corroboration_model", "openai/gpt-4o-mini"),
            temperature=0.1,
            max_tokens=800,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract entities from this law enforcement/court transcript. "
                        "Return JSON only with keys: names (list of person names), "
                        "places (list of locations), agencies (list of law enforcement/court agencies), "
                        "charges (list of any criminal charges mentioned). "
                        "If none found for a category, return an empty list."
                    ),
                },
                {"role": "user", "content": truncated},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        # Parse JSON from response (handle markdown code blocks)
        if "```" in raw:
            raw = raw.split("```json")[-1].split("```")[0].strip()
            if not raw:
                raw = resp.choices[0].message.content.split("```")[-2].strip()
        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as exc:
        logger.debug("Entity extraction LLM failed: %s", exc)
        return {"names": [], "places": [], "agencies": [], "charges": []}


# ── Quality signals ───────────────────────────────────────────────────────
def estimate_quality(candidate: dict) -> dict:
    """Estimate quality signals from available metadata."""
    signals = candidate.get("quality_signals_json", {})
    if isinstance(signals, str):
        try:
            signals = json.loads(signals)
        except json.JSONDecodeError:
            signals = {}

    desc = candidate.get("description", "") or ""
    transcript = candidate.get("transcript_text", "") or ""
    duration = candidate.get("duration_sec") or 0

    signals["has_transcript"] = bool(transcript)
    signals["transcript_length"] = len(transcript)
    signals["description_length"] = len(desc)
    signals["duration_sec"] = duration

    # Estimate video type from title/description
    title_lower = (candidate.get("title", "") or "").lower()
    if any(kw in title_lower for kw in ["bodycam", "body cam", "body-cam", "bwc"]):
        signals["video_type_guess"] = "bodycam"
    elif any(kw in title_lower for kw in ["dashcam", "dash cam", "dash-cam"]):
        signals["video_type_guess"] = "dashcam"
    elif any(kw in title_lower for kw in ["interrogation", "interview"]):
        signals["video_type_guess"] = "interrogation"
    elif any(kw in title_lower for kw in ["court", "trial", "hearing", "sentencing"]):
        signals["video_type_guess"] = "court"
    elif any(kw in title_lower for kw in ["briefing", "press conference", "press release"]):
        signals["video_type_guess"] = "briefing"
    else:
        signals["video_type_guess"] = "unknown"

    # Audio quality guess (heuristic: official channels tend to be better)
    signals["audio_quality_guess"] = "good" if signals.get("video_type_guess") in (
        "interrogation", "court", "briefing"
    ) else "variable"

    return signals


# ── Main enrichment ───────────────────────────────────────────────────────
def enrich(status: str = "NEW", limit: int = 200, use_whisper: bool = False,
           dry_run: bool = False) -> dict:
    """Enrich candidates with transcripts, entities, and quality signals.

    Returns:
        dict with keys: processed, transcripts_added, entities_extracted, errors
    """
    init_db()
    conn = get_connection()

    rows = conn.execute(
        "SELECT * FROM candidates WHERE triage_status = ? AND transcript_text IS NULL "
        "ORDER BY created_at DESC LIMIT ?",
        (status, limit),
    ).fetchall()
    candidates = [dict(r) for r in rows]

    if not candidates:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE triage_status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        candidates = [dict(r) for r in rows]

    stats = {"processed": 0, "transcripts_added": 0, "entities_extracted": 0, "errors": 0}

    # Initialize LLM client for entity extraction
    client = None
    try:
        client = get_openrouter_client()
    except Exception as exc:
        logger.warning("LLM client not available for entity extraction: %s", exc)

    for cand in candidates:
        cid = cand["candidate_id"]
        logger.info("Enriching candidate: %s — %s", cid, (cand.get("title") or "")[:60])

        try:
            # 1. Transcript
            transcript = cand.get("transcript_text") or ""
            if not transcript and cand.get("platform") == "youtube":
                transcript = fetch_youtube_captions(cand["url"]) or ""
                if not transcript and use_whisper:
                    transcript = transcribe_with_whisper(cand["url"]) or ""
                if transcript:
                    stats["transcripts_added"] += 1

            # 2. Quality signals
            cand["transcript_text"] = transcript
            quality = estimate_quality(cand)

            # 3. Entities (via LLM if available)
            entities = {"names": [], "places": [], "agencies": [], "charges": []}
            combined_text = f"{cand.get('title', '')} {cand.get('description', '')} {transcript}"
            if client and len(combined_text) > 100:
                entities = extract_entities_llm(combined_text, client)
                if any(entities.values()):
                    stats["entities_extracted"] += 1

            if dry_run:
                logger.info(
                    "[DRY RUN] Would update %s: transcript=%d chars, entities=%s, quality=%s",
                    cid, len(transcript), list(entities.keys()),
                    quality.get("video_type_guess", "?"),
                )
            else:
                conn.execute(
                    """UPDATE candidates SET
                        transcript_text = ?,
                        entities_json = ?,
                        quality_signals_json = ?,
                        updated_at = ?
                    WHERE candidate_id = ?""",
                    (
                        transcript or None,
                        json.dumps(entities),
                        json.dumps(quality),
                        now_iso(),
                        cid,
                    ),
                )
                conn.commit()

            stats["processed"] += 1

        except Exception as exc:
            logger.error("Error enriching %s: %s", cid, exc)
            stats["errors"] += 1

    conn.close()
    logger.info(
        "Enrichment complete: %d processed, %d transcripts, %d entities, %d errors",
        stats["processed"], stats["transcripts_added"],
        stats["entities_extracted"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Enrich candidates with transcripts and entities.")
    parser.add_argument("--status", default="NEW", help="Candidate status to process (default: NEW).")
    parser.add_argument("--limit", type=int, default=200, help="Max candidates to process.")
    parser.add_argument("--whisper", action="store_true", help="Use Whisper for missing transcripts.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = enrich(status=args.status, limit=args.limit, use_whisper=args.whisper, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
