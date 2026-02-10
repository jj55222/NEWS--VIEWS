#!/usr/bin/env python3
"""Render packaged cases into final video exports.

Downloads source video, cuts to timeline, adds captions, applies blur/bleep
where needed, and exports longform + shorts.

Usage:
    python -m scripts.render_ffmpeg                      # render all PACKAGED cases
    python -m scripts.render_ffmpeg --case-id <id>       # render a specific case
    python -m scripts.render_ffmpeg --shorts-only        # only render shorts
    python -m scripts.render_ffmpeg --dry-run            # preview, don't render
"""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from scripts.config_loader import (
    CASE_BUNDLES_DIR,
    EXPORTS_DIR,
    get_policy,
    setup_logging,
)
from scripts.db import get_cases, get_connection, init_db, update_case_fields, update_case_status

logger = setup_logging("render_ffmpeg")


# ── Dependency checks ──────────────────────────────────────────────────────
def check_dependencies() -> dict:
    """Check for required external tools."""
    deps = {}
    for tool in ["ffmpeg", "ffprobe", "yt-dlp"]:
        deps[tool] = shutil.which(tool) is not None
    return deps


# ── Download ───────────────────────────────────────────────────────────────
def download_video(url: str, output_path: Path, max_height: int = 1080) -> bool:
    """Download video using yt-dlp."""
    try:
        cmd = [
            "yt-dlp",
            "-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]",
            "--merge-output-format", "mp4",
            "-o", str(output_path),
            "--no-playlist",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error("yt-dlp failed: %s", result.stderr[:500])
            return False
        return output_path.exists()
    except subprocess.TimeoutExpired:
        logger.error("Download timed out for %s", url)
        return False
    except FileNotFoundError:
        logger.error("yt-dlp not found. Install with: pip install yt-dlp")
        return False


# ── FFmpeg operations ──────────────────────────────────────────────────────
def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        return 0.0


def cut_clip(input_path: Path, output_path: Path, start_sec: float, end_sec: float) -> bool:
    """Cut a clip from a video."""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-i", str(input_path),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("FFmpeg cut failed: %s", exc)
        return False


def burn_captions(input_path: Path, output_path: Path, srt_path: Path,
                  font: str = "Arial", font_size: int = 24) -> bool:
    """Burn SRT captions into video."""
    if not srt_path.exists():
        # No captions to burn — just copy
        shutil.copy2(input_path, output_path)
        return True

    try:
        # Escape path for ffmpeg filter
        srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", f"subtitles={srt_escaped}:force_style='FontName={font},FontSize={font_size}'",
            "-c:a", "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("Caption burn failed: %s", exc)
        return False


def apply_face_blur(input_path: Path, output_path: Path) -> bool:
    """Apply basic face blur using ffmpeg's drawbox or external tool.

    This is a v1 placeholder — real face detection requires OpenCV/mediapipe.
    For v1, we skip blur and just copy the file with a warning.
    """
    logger.warning("Face blur is v1 placeholder — copying without blur. Upgrade to OpenCV later.")
    shutil.copy2(input_path, output_path)
    return True


def generate_srt_from_narration(narration: str, duration_sec: float, output_path: Path) -> None:
    """Generate a basic SRT file from narration text (split into chunks)."""
    words = narration.split()
    if not words:
        return

    # Rough estimate: 150 words per minute
    words_per_sec = 2.5
    chunk_size = 12  # words per subtitle line
    chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]

    time_per_chunk = duration_sec / max(len(chunks), 1)

    with open(output_path, "w") as f:
        for i, chunk in enumerate(chunks):
            start = i * time_per_chunk
            end = min((i + 1) * time_per_chunk, duration_sec)
            f.write(f"{i + 1}\n")
            f.write(f"{_format_srt_time(start)} --> {_format_srt_time(end)}\n")
            f.write(f"{chunk}\n\n")


def _format_srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp (HH:MM:SS,mmm)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ── Render pipeline ───────────────────────────────────────────────────────
def render_case(case: dict, conn, shorts_only: bool = False, dry_run: bool = False) -> dict:
    """Render a single case into longform + shorts exports.

    Returns:
        dict with keys: longform_rendered, shorts_rendered, errors
    """
    case_id = case["case_id"]
    candidate_id = case["primary_candidate_id"]
    bundle_dir = CASE_BUNDLES_DIR / case_id

    stats = {"longform_rendered": False, "shorts_rendered": 0, "errors": 0}

    # Load candidate for URL
    cand = conn.execute(
        "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    if not cand:
        logger.error("Candidate %s not found", candidate_id)
        stats["errors"] += 1
        return stats
    cand = dict(cand)

    video_url = cand.get("url", "")
    if not video_url:
        logger.error("No URL for candidate %s", candidate_id)
        stats["errors"] += 1
        return stats

    # Setup paths
    work_dir = bundle_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_video = work_dir / "raw_source.mp4"

    longform_out = EXPORTS_DIR / "longform" / f"{case_id}.mp4"
    metadata_out = EXPORTS_DIR / "metadata" / f"{case_id}.json"

    render_cfg = get_policy("render") or {}

    if dry_run:
        logger.info("[DRY RUN] Would render case %s from %s", case_id, video_url)
        logger.info("[DRY RUN] Longform -> %s", longform_out)

        # Check shorts plan
        shorts_plan_path = bundle_dir / "shorts_plan.json"
        if shorts_plan_path.exists():
            shorts = json.loads(shorts_plan_path.read_text())
            logger.info("[DRY RUN] Would render %d shorts", len(shorts))
            stats["shorts_rendered"] = len(shorts)

        stats["longform_rendered"] = True
        return stats

    # Download source video
    logger.info("Downloading source video: %s", video_url)
    if not raw_video.exists():
        if not download_video(video_url, raw_video):
            stats["errors"] += 1
            return stats
    else:
        logger.info("Source video already downloaded.")

    actual_duration = get_video_duration(raw_video)
    logger.info("Source duration: %.1f seconds", actual_duration)

    # ── Longform render ──
    if not shorts_only:
        logger.info("Rendering longform...")

        # Generate captions SRT if narration exists
        narration_path = bundle_dir / "narration_draft.txt"
        srt_path = work_dir / "captions.srt"
        if narration_path.exists():
            narration_text = narration_path.read_text()
            generate_srt_from_narration(narration_text, actual_duration, srt_path)

        # Apply captions
        captioned = work_dir / "captioned.mp4"
        if srt_path.exists():
            burn_captions(raw_video, captioned, srt_path,
                          font=render_cfg.get("caption_font", "Arial"),
                          font_size=render_cfg.get("caption_font_size", 24))
        else:
            shutil.copy2(raw_video, captioned)

        # Apply blur (v1 placeholder)
        policy = get_policy("redaction") or {}
        if policy.get("blur_non_officers", True):
            apply_face_blur(captioned, longform_out)
        else:
            shutil.copy2(captioned, longform_out)

        if longform_out.exists():
            stats["longform_rendered"] = True
            logger.info("Longform exported: %s", longform_out)
        else:
            stats["errors"] += 1

    # ── Shorts render ──
    shorts_plan_path = bundle_dir / "shorts_plan.json"
    if shorts_plan_path.exists():
        shorts = json.loads(shorts_plan_path.read_text())
        for clip in shorts:
            clip_num = clip.get("clip_number", 0)
            start = clip.get("start_sec", 0)
            end = clip.get("end_sec", 60)

            short_out = EXPORTS_DIR / "shorts" / f"{case_id}_{clip_num}.mp4"
            logger.info("Rendering short #%d: %.1fs - %.1fs", clip_num, start, end)

            if cut_clip(raw_video, short_out, start, end):
                stats["shorts_rendered"] += 1
            else:
                stats["errors"] += 1

    # ── Export metadata ──
    meta = {
        "case_id": case_id,
        "title": case.get("case_title_working", cand.get("title", "")),
        "description": cand.get("description", ""),
        "duration_sec": actual_duration,
        "longform_path": str(longform_out) if stats["longform_rendered"] else None,
        "shorts_count": stats["shorts_rendered"],
        "sources": [],
    }

    # Load sources
    sources_path = bundle_dir / "sources.json"
    if sources_path.exists():
        meta["sources"] = json.loads(sources_path.read_text())

    metadata_out.parent.mkdir(parents=True, exist_ok=True)
    with open(metadata_out, "w") as f:
        json.dump(meta, f, indent=2)

    return stats


# ── Main render ────────────────────────────────────────────────────────────
def render(case_id: str | None = None, shorts_only: bool = False,
           limit: int = 10, dry_run: bool = False) -> dict:
    """Render packaged cases.

    Returns:
        dict with keys: cases_rendered, longforms, shorts, errors
    """
    init_db()
    conn = get_connection()

    if case_id:
        cases = [dict(r) for r in conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchall()]
    else:
        cases = get_cases(conn, status="PACKAGED", limit=limit)

    if not cases:
        logger.info("No PACKAGED cases to render.")
        conn.close()
        return {"cases_rendered": 0, "longforms": 0, "shorts": 0, "errors": 0}

    # Check dependencies
    deps = check_dependencies()
    missing = [k for k, v in deps.items() if not v]
    if missing:
        logger.error("Missing dependencies: %s. Install them first.", ", ".join(missing))
        conn.close()
        return {"cases_rendered": 0, "longforms": 0, "shorts": 0, "errors": len(cases)}

    totals = {"cases_rendered": 0, "longforms": 0, "shorts": 0, "errors": 0}

    for case in cases:
        cid = case["case_id"]
        logger.info("Rendering case: %s", cid)

        result = render_case(case, conn, shorts_only=shorts_only, dry_run=dry_run)

        if result["longform_rendered"]:
            totals["longforms"] += 1
        totals["shorts"] += result["shorts_rendered"]
        totals["errors"] += result["errors"]

        if result["longform_rendered"] or result["shorts_rendered"] > 0:
            totals["cases_rendered"] += 1
            if not dry_run:
                # Update asset paths
                asset_paths = []
                longform_path = EXPORTS_DIR / "longform" / f"{cid}.mp4"
                if longform_path.exists():
                    asset_paths.append(str(longform_path))
                for i in range(result["shorts_rendered"]):
                    short_path = EXPORTS_DIR / "shorts" / f"{cid}_{i + 1}.mp4"
                    if short_path.exists():
                        asset_paths.append(str(short_path))

                update_case_fields(conn, cid, {"asset_paths_json": asset_paths})
                update_case_status(conn, cid, "RENDERED")

    conn.close()
    logger.info(
        "Render complete: %d cases, %d longforms, %d shorts, %d errors",
        totals["cases_rendered"], totals["longforms"], totals["shorts"], totals["errors"],
    )
    return totals


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Render packaged cases into video exports.")
    parser.add_argument("--case-id", type=str, default=None, help="Render a specific case.")
    parser.add_argument("--shorts-only", action="store_true", help="Only render shorts clips.")
    parser.add_argument("--limit", type=int, default=10, help="Max cases to render.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without rendering.")
    args = parser.parse_args()

    stats = render(case_id=args.case_id, shorts_only=args.shorts_only,
                   limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
