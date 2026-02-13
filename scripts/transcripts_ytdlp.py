#!/usr/bin/env python3
"""Fetch YouTube captions/subtitles via yt-dlp for candidates missing transcripts.

Uses human captions first, then auto-generated captions. Converts VTT to plain
text and stores in the candidates.transcript_text column.

Usage:
    python -m scripts.transcripts_ytdlp                     # all PASS+MAYBE without transcript
    python -m scripts.transcripts_ytdlp --status NEW         # only NEW candidates
    python -m scripts.transcripts_ytdlp --limit 50           # cap at 50
    python -m scripts.transcripts_ytdlp --dry-run            # preview, don't update
"""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

from scripts.config_loader import setup_logging
from scripts.db import get_connection, init_db, now_iso

logger = setup_logging("transcripts_ytdlp")


def _vtt_to_text(vtt: str) -> str:
    """Convert VTT subtitle content to clean plain text."""
    lines = []
    for line in vtt.split("\n"):
        line = line.strip()
        # Skip headers, timestamps, and sequence numbers
        if not line or line.startswith("WEBVTT") or "-->" in line or re.match(r"^\d+$", line):
            continue
        # Remove VTT formatting tags
        clean = re.sub(r"<[^>]+>", "", line)
        # Deduplicate consecutive identical lines (VTT often repeats)
        if clean and clean not in lines[-1:]:
            lines.append(clean)
    return " ".join(lines)


def fetch_transcript(video_url: str) -> str | None:
    """Fetch YouTube captions via yt-dlp. Tries human subs first, then auto.

    Returns:
        Plain text transcript, or None if no captions available.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = str(Path(tmpdir) / "%(id)s")

        # Try human subtitles first, then auto-generated
        for sub_flag in (["--write-subs"], ["--write-auto-subs"]):
            try:
                cmd = [
                    "yt-dlp",
                    "--skip-download",
                    *sub_flag,
                    "--sub-langs", "en.*",
                    "--sub-format", "vtt",
                    "--output", out_template,
                    "--no-warnings",
                    "--quiet",
                    video_url,
                ]
                subprocess.run(cmd, capture_output=True, text=True, timeout=60)

                # Look for any .vtt file
                vtt_files = list(Path(tmpdir).glob("*.vtt"))
                if vtt_files:
                    vtt_text = vtt_files[0].read_text(errors="replace")
                    transcript = _vtt_to_text(vtt_text)
                    if len(transcript) > 50:
                        sub_type = "human" if "--write-subs" in sub_flag else "auto"
                        logger.debug("Got %s captions for %s (%d chars)", sub_type, video_url, len(transcript))
                        return transcript
                    # Clean up for next attempt
                    for f in vtt_files:
                        f.unlink()

            except subprocess.TimeoutExpired:
                logger.debug("yt-dlp timed out for %s", video_url)
            except FileNotFoundError:
                logger.warning("yt-dlp not found — install with: pip install yt-dlp")
                return None

    return None


def fetch_transcripts(status: str | None = None, limit: int = 200,
                      dry_run: bool = False) -> dict:
    """Fetch transcripts for YouTube candidates missing them.

    Args:
        status: If set, only process candidates with this triage_status.
                If None, processes PASS and MAYBE candidates.
        limit: Max candidates to process.
        dry_run: If True, don't update DB.

    Returns:
        dict with keys: processed, transcripts_added, already_had, skipped_non_yt, errors
    """
    init_db()
    conn = get_connection()

    if status:
        rows = conn.execute(
            """SELECT candidate_id, url, platform, title, transcript_text
               FROM candidates
               WHERE triage_status = ? AND platform = 'youtube'
               ORDER BY triage_score DESC
               LIMIT ?""",
            (status, limit),
        ).fetchall()
    else:
        # Default: PASS and MAYBE candidates without transcripts
        rows = conn.execute(
            """SELECT candidate_id, url, platform, title, transcript_text
               FROM candidates
               WHERE triage_status IN ('PASS', 'MAYBE')
                 AND platform = 'youtube'
                 AND (transcript_text IS NULL OR transcript_text = '')
               ORDER BY triage_score DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

    candidates = [dict(r) for r in rows]
    stats = {
        "processed": 0,
        "transcripts_added": 0,
        "already_had": 0,
        "skipped_non_yt": 0,
        "errors": 0,
    }

    logger.info("Found %d YouTube candidates to fetch transcripts for.", len(candidates))

    for cand in candidates:
        cid = cand["candidate_id"]
        title = (cand.get("title") or "")[:60]

        # Skip if already has transcript
        if cand.get("transcript_text"):
            stats["already_had"] += 1
            continue

        # Skip non-YouTube
        if cand.get("platform") != "youtube":
            stats["skipped_non_yt"] += 1
            continue

        logger.info("Fetching transcript: %s — %s", cid, title)

        try:
            transcript = fetch_transcript(cand["url"])
            stats["processed"] += 1

            if transcript:
                stats["transcripts_added"] += 1
                logger.info("  Got transcript (%d chars)", len(transcript))

                if not dry_run:
                    conn.execute(
                        """UPDATE candidates SET
                             transcript_text = ?,
                             updated_at = ?
                           WHERE candidate_id = ?""",
                        (transcript, now_iso(), cid),
                    )
                    conn.commit()
                else:
                    logger.info("[DRY RUN] Would store %d chars for %s", len(transcript), cid)
            else:
                logger.info("  No captions available")

        except Exception as exc:
            logger.error("Error fetching transcript for %s: %s", cid, exc)
            stats["errors"] += 1

    conn.close()
    logger.info(
        "Transcript fetch complete: %d processed, %d added, %d already had, %d errors",
        stats["processed"], stats["transcripts_added"],
        stats["already_had"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube transcripts via yt-dlp.")
    parser.add_argument("--status", default=None,
                        help="Candidate triage_status to process (default: PASS+MAYBE).")
    parser.add_argument("--limit", type=int, default=200, help="Max candidates to process.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = fetch_transcripts(status=args.status, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
