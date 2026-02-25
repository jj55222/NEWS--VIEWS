#!/usr/bin/env python3
"""
NEWS → VIEWS: Incident Selection Scoring

Replaces PASS/KILL triage with a 0-100 scoring rubric.
Scores incidents from qualified BWC channels based on footage quality,
multi-source availability, case documentation, narrative potential,
legal stage, public interest, uniqueness, and jurisdiction strength.

Usage:
    python incident_score.py --check                      # Verify dependencies
    python incident_score.py --video VIDEO_ID             # Score a single video
    python incident_score.py --batch                      # Score recent videos from qualified channels
    python incident_score.py --from-channels              # Scan + score from all qualified channels
    python incident_score.py --list                       # List scored incidents
    python incident_score.py --sync                       # Sync scores to Google Sheets
"""

import os
import re
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

from pipeline_ops import (
    INCIDENT_RUBRIC,
    INCIDENT_SCORE_SCHEMA,
    INCIDENT_SCORES_COLUMNS,
    SHEETS_TABS,
    STRONG_SUNSHINE_STATES,
    SUNSHINE_STATES,
    score_incident_criteria,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
EXA_API_KEY = os.getenv("EXA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")
SHEET_ID = os.getenv("SHEET_ID")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json")

SCORES_PATH = Path("incident_scores.json")


# =============================================================================
# CREDENTIAL CHECK
# =============================================================================

def check_dependencies() -> bool:
    """Verify required API keys and imports."""
    errors = []
    if not YOUTUBE_API_KEY:
        errors.append("YOUTUBE_API_KEY not set in .env")
    if not OPENROUTER_API_KEY:
        errors.append("OPENROUTER_API_KEY not set in .env (needed for narrative + legal stage assessment)")

    # Check imports
    try:
        from transcript_analyzer import get_transcript, extract_case_details_with_llm
        print("  transcript_analyzer: OK")
    except ImportError as e:
        errors.append(f"transcript_analyzer import failed: {e}")

    try:
        from channel_qualify import load_registry
        print("  channel_qualify: OK")
    except ImportError as e:
        errors.append(f"channel_qualify import failed: {e}")

    try:
        from pipeline_ops import INCIDENT_RUBRIC
        print("  pipeline_ops: OK")
    except ImportError as e:
        errors.append(f"pipeline_ops import failed: {e}")

    if errors:
        print("Dependency errors:")
        for e in errors:
            print(f"  - {e}")
        return False

    print("All dependencies available")
    return True


# =============================================================================
# VIDEO METADATA
# =============================================================================

def get_video_metadata(video_id: str) -> Optional[dict]:
    """Fetch video metadata via YouTube Data API."""
    if not YOUTUBE_API_KEY:
        return None

    import requests

    params = {
        "part": "snippet,contentDetails,statistics",
        "id": video_id,
        "key": YOUTUBE_API_KEY,
    }

    try:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params=params, timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("items"):
            return None

        item = data["items"][0]
        snippet = item.get("snippet", {})
        cd = item.get("contentDetails", {})
        stats = item.get("statistics", {})

        duration_str = cd.get("duration", "")
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
        duration_sec = 0
        if match:
            duration_sec = (int(match.group(1) or 0) * 3600 +
                           int(match.group(2) or 0) * 60 +
                           int(match.group(3) or 0))

        return {
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "channel_id": snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt", ""),
            "duration_seconds": duration_sec,
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
        }
    except Exception as e:
        print(f"  [ERROR] Video metadata: {e}")
        return None


# =============================================================================
# ASSESSMENT FUNCTIONS
# =============================================================================

def assess_footage_quality(video_id: str, metadata: dict = None) -> str:
    """
    Assess BWC footage quality based on duration and transcript analysis.

    Returns: "full" | "most" | "partial" | "brief" | "none"
    """
    if metadata is None:
        metadata = get_video_metadata(video_id)
    if not metadata:
        return "none"

    duration_sec = metadata.get("duration_seconds", 0)
    duration_min = duration_sec / 60

    # Get transcript for quality assessment
    try:
        from transcript_analyzer import get_transcript
        segments, full_text = get_transcript(video_id)
        has_transcript = bool(segments)
        transcript_length = len(full_text) if full_text else 0
    except Exception:
        has_transcript = False
        transcript_length = 0

    # Score based on duration + transcript availability
    if duration_min >= 10 and has_transcript and transcript_length > 2000:
        return "full"
    elif duration_min >= 5 and has_transcript:
        return "most"
    elif duration_min >= 2 or (has_transcript and transcript_length > 500):
        return "partial"
    elif duration_sec > 30:
        return "brief"
    else:
        return "none"


def assess_multi_source(video_id: str, case_details: dict) -> int:
    """
    Count how many artifact types are available for this incident.
    Uses transcript artifact detection + search.

    Returns: count of distinct artifact types found (0-5+)
    """
    artifact_types = set()

    # Always count BWC since we're starting from footage
    artifact_types.add("bodycam")

    # Check transcript for mentions of other artifacts
    try:
        from transcript_analyzer import (
            get_transcript, detect_artifacts_in_transcript
        )
        segments, full_text = get_transcript(video_id)
        if segments:
            mentions = detect_artifacts_in_transcript(segments, full_text)
            for m in mentions:
                artifact_types.add(m.artifact_type)
    except Exception:
        pass

    # Quick YouTube search for related artifacts
    defendant = ""
    if case_details.get("defendant_names"):
        defendant = case_details["defendant_names"][0]

    if defendant and YOUTUBE_API_KEY:
        try:
            from bodycam_sources import youtube_search
            # Search for interrogation
            int_results = youtube_search(f'"{defendant}" interrogation', max_results=3)
            if int_results:
                artifact_types.add("interrogation")

            # Search for court/trial
            court_results = youtube_search(f'"{defendant}" trial court', max_results=3)
            court_matches = [r for r in court_results
                             if any(kw in r.get("title", "").lower()
                                    for kw in ["trial", "court", "sentencing"])]
            if court_matches:
                artifact_types.add("court")

            time.sleep(0.3)
        except Exception:
            pass

    return len(artifact_types)


def assess_case_documentation(defendant: str, jurisdiction: str) -> str:
    """
    Search for court records and case documentation.

    Returns: "full" | "partial" | "news_only" | "none"
    """
    if not defendant:
        return "none"

    found_court = False
    found_news = False

    # Try PACER/CourtListener search
    if EXA_API_KEY:
        try:
            from artifact_hunter import search_pacer
            from exa_pipeline import get_exa_client
            exa = get_exa_client()
            pacer_data = search_pacer(exa, defendant, jurisdiction)
            if pacer_data.get("sources"):
                found_court = True
        except Exception:
            pass

    # Try news coverage search
    if EXA_API_KEY and not found_court:
        try:
            from exa_pipeline import get_exa_client
            exa = get_exa_client()
            results = exa.search(
                query=f"{defendant} {jurisdiction} charged arrested",
                num_results=5,
            )
            if results.results:
                found_news = True
        except Exception:
            pass

    if found_court:
        return "partial"  # We found court records but not necessarily "full"
    elif found_news:
        return "news_only"
    return "none"


def assess_narrative(transcript: str, case_details: dict) -> str:
    """
    Use LLM to assess narrative potential.

    Returns: "exceptional" | "strong" | "decent" | "routine" | "none"
    """
    if not OPENROUTER_API_KEY or not transcript:
        return "routine"  # Default when we can't assess

    try:
        from openai import OpenAI
    except ImportError:
        return "routine"

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

    # Truncate transcript
    truncated = transcript[:8000] if len(transcript) > 8000 else transcript

    defendant = ", ".join(case_details.get("defendant_names", [])) or "Unknown"
    crime_type = case_details.get("crime_type", "Unknown")

    prompt = f"""Assess the narrative potential of this true crime case for video content.

DEFENDANT: {defendant}
CRIME TYPE: {crime_type}
CASE SUMMARY: {case_details.get('case_summary', 'N/A')}

TRANSCRIPT EXCERPT:
{truncated}

Rate the narrative potential as one of:
- "exceptional": Betrayal + authority abuse + twist, or extremely disturbing with multiple layers
- "strong": Clear moral abnormality, authority abuse, or compelling betrayal
- "decent": Disturbing but straightforward crime with some hook
- "routine": Standard crime without a compelling narrative hook
- "none": No narrative interest at all

Return ONLY one word from the options above, nothing else."""

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20,
        )
        answer = response.choices[0].message.content.strip().lower().strip('"\'')
        valid = {"exceptional", "strong", "decent", "routine", "none"}
        return answer if answer in valid else "routine"
    except Exception as e:
        print(f"  [WARN] Narrative assessment failed: {e}")
        return "routine"


def assess_legal_stage(case_details: dict) -> str:
    """
    Determine the legal stage of the case.

    Returns: "sentenced" | "convicted" | "trial_done" | "trial_active" | "pre_trial" | "investigation"
    """
    summary = case_details.get("case_summary", "").lower()
    crime_type = case_details.get("crime_type", "").lower()

    # Check keywords in summary
    if any(kw in summary for kw in ["sentenced to", "serving", "life sentence", "death row"]):
        return "sentenced"
    if any(kw in summary for kw in ["found guilty", "convicted of", "guilty verdict"]):
        return "convicted"
    if any(kw in summary for kw in ["trial concluded", "verdict", "jury found"]):
        return "trial_done"
    if any(kw in summary for kw in ["trial", "testif", "jury deliberat"]):
        return "trial_active"
    if any(kw in summary for kw in ["charged with", "indicted", "arraign", "plea"]):
        return "pre_trial"

    return "investigation"  # Default


def assess_public_interest(video_id: str, metadata: dict, case_details: dict) -> str:
    """
    Assess public interest based on video views and case coverage.

    Returns: "viral" | "significant" | "moderate" | "minimal"
    """
    view_count = metadata.get("view_count", 0) if metadata else 0

    # View-based assessment
    if view_count >= 1_000_000:
        return "viral"
    elif view_count >= 100_000:
        return "significant"
    elif view_count >= 10_000:
        return "moderate"
    else:
        return "minimal"


def assess_uniqueness(case_details: dict) -> str:
    """
    Check if major true crime channels have already covered this case.

    Returns: "uncovered" | "lightly" | "moderate_new_angle" | "heavily_covered"
    """
    defendant = ""
    if case_details.get("defendant_names"):
        defendant = case_details["defendant_names"][0]

    if not defendant or not YOUTUBE_API_KEY:
        return "uncovered"  # Assume uncovered if we can't check

    try:
        from bodycam_sources import youtube_search
    except ImportError:
        return "uncovered"

    # Search for defendant in YouTube
    results = youtube_search(f'"{defendant}" true crime case', max_results=10)

    # Check for major channels
    try:
        from jurisdiction_portals import TRUE_CRIME_CHANNELS
        major_channel_names = [ch["name"].lower() for ch in TRUE_CRIME_CHANNELS]
    except ImportError:
        major_channel_names = [
            "jcs", "matt orchard", "dreading", "law&crime",
            "court tv", "that chapter", "coffeehouse crime",
        ]

    major_coverage = 0
    total_coverage = 0

    for r in results:
        channel = r.get("channel", "").lower()
        title = r.get("title", "").lower()

        # Check if this result is actually about our defendant
        if defendant.lower() not in title:
            continue

        total_coverage += 1
        if any(mc in channel for mc in major_channel_names):
            major_coverage += 1

    if major_coverage >= 3:
        return "heavily_covered"
    elif major_coverage >= 1 or total_coverage >= 5:
        return "moderate_new_angle"
    elif total_coverage >= 1:
        return "lightly"
    return "uncovered"


def assess_jurisdiction_type(state: str) -> str:
    """
    Classify jurisdiction strength for FOIA/records access.

    Returns: "sunshine" | "good_foia" | "limited" | "restricted"
    """
    if not state:
        return "limited"

    state_upper = state.upper()
    if state_upper in STRONG_SUNSHINE_STATES:
        return "sunshine"
    elif state_upper in SUNSHINE_STATES:
        return "good_foia"
    else:
        return "limited"


# =============================================================================
# MAIN SCORING PIPELINE
# =============================================================================

def score_incident(video_id: str) -> dict:
    """
    Full incident scoring pipeline for a single video.

    1. Fetch video metadata
    2. Extract transcript + case details
    3. Run all 8 assessments
    4. Score against INCIDENT_RUBRIC
    5. Return scored entry
    """
    print(f"\n{'='*60}")
    print(f"SCORING INCIDENT: {video_id}")
    print(f"{'='*60}")

    # Step 1: Video metadata
    print("\n[1/8] Fetching video metadata...")
    metadata = get_video_metadata(video_id)
    if not metadata:
        print("  ERROR: Could not fetch video metadata")
        return {}

    print(f"  Title: {metadata['title']}")
    print(f"  Channel: {metadata['channel_title']}")
    print(f"  Duration: {metadata['duration_seconds'] // 60}:{metadata['duration_seconds'] % 60:02d}")
    print(f"  Views: {metadata['view_count']:,}")

    # Step 2: Transcript + case details
    print("\n[2/8] Extracting transcript and case details...")
    transcript = ""
    case_details = {}
    try:
        from transcript_analyzer import get_transcript, extract_case_details_with_llm
        segments, full_text = get_transcript(video_id)
        if segments:
            transcript = full_text
            print(f"  Transcript: {len(transcript):,} characters")
            case_details_obj = extract_case_details_with_llm(transcript, metadata["title"])
            # Convert dataclass to dict
            if hasattr(case_details_obj, '__dict__'):
                from dataclasses import asdict
                case_details = asdict(case_details_obj)
            elif isinstance(case_details_obj, dict):
                case_details = case_details_obj
            print(f"  Defendant: {', '.join(case_details.get('defendant_names', [])) or 'Unknown'}")
            print(f"  Crime: {case_details.get('crime_type', 'Unknown')}")
            print(f"  Jurisdiction: {case_details.get('jurisdiction', 'Unknown')}")
            print(f"  State: {case_details.get('state', 'Unknown')}")
        else:
            print(f"  No transcript available: {full_text}")
    except Exception as e:
        print(f"  Transcript error: {e}")

    # Step 3-8: Run assessments
    print("\n[3/8] Assessing footage quality...")
    footage_q = assess_footage_quality(video_id, metadata)
    print(f"  Footage quality: {footage_q}")

    print("\n[4/8] Assessing multi-source availability...")
    artifact_count = assess_multi_source(video_id, case_details)
    print(f"  Artifact types found: {artifact_count}")

    print("\n[5/8] Checking case documentation...")
    defendant = case_details.get("defendant_names", [""])[0] if case_details.get("defendant_names") else ""
    jurisdiction = case_details.get("jurisdiction", "")
    case_docs = assess_case_documentation(defendant, jurisdiction)
    print(f"  Case docs: {case_docs}")

    print("\n[6/8] Assessing narrative potential...")
    narrative = assess_narrative(transcript, case_details)
    print(f"  Narrative: {narrative}")

    print("\n[7/8] Determining legal stage...")
    legal_stage = assess_legal_stage(case_details)
    print(f"  Legal stage: {legal_stage}")

    public_interest = assess_public_interest(video_id, metadata, case_details)
    print(f"  Public interest: {public_interest}")

    print("\n[8/8] Checking uniqueness...")
    uniqueness = assess_uniqueness(case_details)
    print(f"  Uniqueness: {uniqueness}")

    jurisdiction_type = assess_jurisdiction_type(case_details.get("state", ""))
    print(f"  Jurisdiction strength: {jurisdiction_type}")

    # Score
    incident_data = {
        "footage_quality": footage_q,
        "artifact_types_found": artifact_count,
        "case_docs_level": case_docs,
        "narrative_level": narrative,
        "legal_stage": legal_stage,
        "public_interest": public_interest,
        "uniqueness": uniqueness,
        "jurisdiction_type": jurisdiction_type,
    }

    score_result = score_incident_criteria(incident_data)

    # Build entry
    entry = {
        "video_id": video_id,
        "video_title": metadata.get("title", ""),
        "channel_id": metadata.get("channel_id", ""),
        "channel_name": metadata.get("channel_title", ""),
        "scored_at": datetime.utcnow().isoformat() + "Z",
        "score": score_result["total_score"],
        "classification": score_result["classification"],
        "action": score_result["action"],
        "score_breakdown": {b["name"]: b["awarded"] for b in score_result["breakdown"]},
        "incident_data": incident_data,
        "case_details": case_details,
        "metadata": {
            "duration_seconds": metadata.get("duration_seconds", 0),
            "view_count": metadata.get("view_count", 0),
            "published_at": metadata.get("published_at", ""),
        },
        "notes": "",
    }

    # Print result
    print(f"\n{'='*60}")
    print(f"SCORE: {score_result['total_score']}/100 -> {score_result['classification']}")
    print(f"Action: {score_result['action']}")
    print(f"\nBreakdown:")
    for b in score_result["breakdown"]:
        print(f"  {b['name']}: {b['awarded']}/{b['max']}")
    print(f"{'='*60}")

    return entry


# =============================================================================
# BATCH SCORING
# =============================================================================

def scan_channel_recent(channel_id: str, since_days: int = 7) -> List[dict]:
    """Get recent videos from a channel uploaded within since_days."""
    try:
        from channel_qualify import sample_channel_videos
        videos = sample_channel_videos(channel_id, n=20)
    except ImportError:
        return []

    if not videos:
        return []

    cutoff = datetime.utcnow() - timedelta(days=since_days)
    recent = []
    for v in videos:
        pub = v.get("published_at", "")
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if dt.replace(tzinfo=None) >= cutoff:
                    recent.append(v)
            except ValueError:
                continue

    return recent


def batch_score(channel_ids: List[str] = None, since_days: int = 7,
                limit: int = None) -> List[dict]:
    """
    Score recent videos from qualified channels.

    Args:
        channel_ids: Specific channels to scan. If None, uses all qualified channels.
        since_days: Only score videos uploaded within this many days.
        limit: Max incidents to score.
    """
    if channel_ids is None:
        try:
            from channel_qualify import get_qualified_channels
            qualified = get_qualified_channels(min_score=40)
            channel_ids = [ch["channel_id"] for ch in qualified]
        except ImportError:
            print("channel_qualify not available. Provide channel IDs explicitly.")
            return []

    if not channel_ids:
        print("No qualified channels found. Run channel_qualify.py --discover first.")
        return []

    print(f"\n{'='*60}")
    print(f"BATCH SCORING: {len(channel_ids)} channels, last {since_days} days")
    print(f"{'='*60}")

    scores_db = load_scores()
    all_scores = []
    total_scanned = 0

    for ch_id in channel_ids:
        print(f"\nScanning channel: {ch_id}")
        videos = scan_channel_recent(ch_id, since_days)
        print(f"  Found {len(videos)} recent videos")

        for v in videos:
            vid = v.get("video_id", "")
            if not vid:
                continue

            # Skip if already scored recently
            if vid in scores_db:
                scored_at = scores_db[vid].get("scored_at", "")
                if scored_at:
                    try:
                        dt = datetime.fromisoformat(scored_at.replace("Z", "+00:00"))
                        if datetime.utcnow().replace(tzinfo=dt.tzinfo) - dt < timedelta(days=7):
                            print(f"  SKIP {vid} (scored within 7 days)")
                            continue
                    except (ValueError, TypeError):
                        pass

            total_scanned += 1
            entry = score_incident(vid)
            if entry:
                all_scores.append(entry)
                scores_db[vid] = entry

            if limit and len(all_scores) >= limit:
                break

            time.sleep(0.5)

        if limit and len(all_scores) >= limit:
            break

    save_scores(scores_db)

    # Summary
    print(f"\n{'='*60}")
    print("BATCH SCORING SUMMARY")
    print(f"{'='*60}")
    print(f"Channels scanned: {len(channel_ids)}")
    print(f"Videos scanned: {total_scanned}")
    print(f"Incidents scored: {len(all_scores)}")

    # Rank by score
    ranked = sorted(all_scores, key=lambda x: x.get("score", 0), reverse=True)
    if ranked:
        print(f"\nTop incidents:")
        for i, entry in enumerate(ranked[:10]):
            print(f"  {i+1}. [{entry['score']:2d}] {entry['classification']}: {entry['video_title'][:60]}")

    return ranked


# =============================================================================
# SCORES PERSISTENCE
# =============================================================================

def load_scores() -> dict:
    """Load incident scores. Returns dict keyed by video_id."""
    if SCORES_PATH.exists():
        with open(SCORES_PATH, "r") as f:
            return json.load(f)
    return {}


def save_scores(scores: dict):
    """Save scores to incident_scores.json."""
    with open(SCORES_PATH, "w") as f:
        json.dump(scores, f, indent=2)
    print(f"Scores saved to {SCORES_PATH} ({len(scores)} incidents)")


def list_scores(min_score: int = 0):
    """List scored incidents sorted by score."""
    scores = load_scores()
    if not scores:
        print("No scored incidents. Run --video or --batch first.")
        return

    entries = sorted(scores.values(), key=lambda x: x.get("score", 0), reverse=True)
    if min_score > 0:
        entries = [e for e in entries if e.get("score", 0) >= min_score]

    print(f"\n{'Score':>5} {'Classification':<18} {'Title'}")
    print("-" * 80)
    for e in entries:
        title = e.get("video_title", "")[:55]
        print(f"{e.get('score', 0):>5} {e.get('classification', ''):.<18} {title}")

    print(f"\nTotal: {len(entries)} incidents")


# =============================================================================
# SHEETS SYNC
# =============================================================================

def sync_to_sheets(scores: dict = None):
    """Push incident scores to Google Sheets 'INCIDENT SCORES' tab."""
    if not SHEET_ID:
        print("SHEET_ID not set, skipping Sheets sync")
        return

    if scores is None:
        scores = load_scores()

    if not scores:
        print("No scores to sync")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SHEET_ID)

        tab_name = SHEETS_TABS["incident_scores"]

        try:
            ws = sheet.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet(title=tab_name, rows=500, cols=len(INCIDENT_SCORES_COLUMNS))

        # Build rows
        rows = [INCIDENT_SCORES_COLUMNS]
        for entry in sorted(scores.values(), key=lambda x: x.get("score", 0), reverse=True):
            cd = entry.get("case_details", {})
            idata = entry.get("incident_data", {})
            rows.append([
                entry.get("video_id", ""),
                entry.get("video_title", "")[:60],
                entry.get("channel_name", ""),
                entry.get("score", 0),
                entry.get("classification", ""),
                idata.get("footage_quality", ""),
                str(idata.get("artifact_types_found", 0)),
                idata.get("case_docs_level", ""),
                idata.get("narrative_level", ""),
                idata.get("legal_stage", ""),
                idata.get("public_interest", ""),
                idata.get("uniqueness", ""),
                idata.get("jurisdiction_type", ""),
                ", ".join(cd.get("defendant_names", [])),
                cd.get("crime_type", ""),
                cd.get("state", ""),
                entry.get("scored_at", ""),
                entry.get("action", ""),
            ])

        ws.clear()
        ws.update(range_name="A1", values=rows)
        print(f"Synced {len(rows) - 1} incidents to '{tab_name}' sheet tab")

    except ImportError:
        print("gspread not installed. Run: pip install gspread google-auth")
    except Exception as e:
        print(f"Sheets sync error: {e}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Incident Selection Scoring (replaces PASS/KILL)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --check                       # Verify dependencies
    %(prog)s --video dQw4w9WgXcQ           # Score a single video
    %(prog)s --batch                       # Score recent from qualified channels
    %(prog)s --from-channels               # Same as --batch
    %(prog)s --list                        # List scored incidents
    %(prog)s --list --min-score 55         # List strong candidates+
    %(prog)s --sync                        # Sync to Google Sheets
        """
    )

    parser.add_argument("--check", action="store_true", help="Check dependencies")
    parser.add_argument("--video", metavar="VIDEO_ID", help="Score a single video")
    parser.add_argument("--batch", action="store_true", help="Batch score from qualified channels")
    parser.add_argument("--from-channels", action="store_true", help="Same as --batch")
    parser.add_argument("--list", action="store_true", help="List scored incidents")
    parser.add_argument("--sync", action="store_true", help="Sync to Google Sheets")
    parser.add_argument("--min-score", type=int, default=0, help="Min score filter")
    parser.add_argument("--limit", type=int, default=None, help="Max incidents to score")
    parser.add_argument("--since-days", type=int, default=7, help="Scan videos from last N days")

    args = parser.parse_args()

    if args.check:
        check_dependencies()
    elif args.video:
        entry = score_incident(args.video)
        if entry:
            scores = load_scores()
            scores[args.video] = entry
            save_scores(scores)
    elif args.batch or args.from_channels:
        batch_score(since_days=args.since_days, limit=args.limit)
    elif args.list:
        list_scores(min_score=args.min_score)
    elif args.sync:
        sync_to_sheets()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
