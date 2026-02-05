#!/usr/bin/env python3
"""
YouTube Transcript Analyzer for Artifact Verification

Extracts transcript from a YouTube video, parses case details,
and verifies which artifacts mentioned in the video we can locate.

Usage:
    python transcript_analyzer.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
    python transcript_analyzer.py --url "VIDEO_ID" --json
"""

import os
import re
import json
import argparse
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Try to import youtube_transcript_api
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
    )
    TRANSCRIPT_API_AVAILABLE = True
except ImportError:
    TRANSCRIPT_API_AVAILABLE = False
    print("[WARN] youtube-transcript-api not installed. Run: pip install youtube-transcript-api")

# Try to import our bodycam sources
try:
    from bodycam_sources import (
        BodycamSourceRouter,
        youtube_search,
        calculate_bodycam_likelihood,
        YOUTUBE_API_KEY,
    )
    BODYCAM_SOURCES_AVAILABLE = True
except ImportError:
    BODYCAM_SOURCES_AVAILABLE = False

# Try to import OpenAI for LLM parsing
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


# =============================================================================
# CONFIGURATION
# =============================================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

# Artifact keywords to detect in transcripts
ARTIFACT_KEYWORDS = {
    "bodycam": ["bodycam", "body cam", "body camera", "body-worn camera", "bwc", "police footage"],
    "dashcam": ["dashcam", "dash cam", "dash camera", "cruiser cam", "patrol car camera"],
    "interrogation": ["interrogation", "interview room", "questioning", "confession", "detective interview"],
    "surveillance": ["surveillance", "cctv", "security camera", "security footage", "store camera"],
    "court": ["court", "trial", "testimony", "courtroom", "sentencing", "hearing", "verdict"],
    "911_call": ["911", "nine one one", "emergency call", "dispatch", "dispatcher"],
    "news": ["news footage", "news report", "local news", "breaking news"],
    "documentary": ["documentary", "dateline", "48 hours", "20/20", "true crime"],
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class TranscriptSegment:
    """A segment of transcript with timing."""
    text: str
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def timestamp(self) -> str:
        """Format as MM:SS or HH:MM:SS."""
        total_seconds = int(self.start)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


@dataclass
class CaseDetails:
    """Extracted case details from transcript."""
    defendant_names: List[str] = field(default_factory=list)
    victim_names: List[str] = field(default_factory=list)
    jurisdiction: str = ""
    state: str = ""
    incident_year: str = ""
    crime_type: str = ""
    case_summary: str = ""
    agencies_mentioned: List[str] = field(default_factory=list)


@dataclass
class ArtifactMention:
    """An artifact mentioned in the transcript."""
    artifact_type: str  # bodycam, interrogation, court, etc.
    context: str  # The text around the mention
    timestamp: str  # When in the video
    confidence: str = "medium"  # high, medium, low


@dataclass
class VerificationResult:
    """Result of trying to locate an artifact."""
    artifact_type: str
    mentioned_in_video: bool
    found_by_search: bool
    search_results: List[Dict] = field(default_factory=list)
    notes: str = ""


@dataclass
class AnalysisReport:
    """Full analysis report for a video."""
    video_id: str
    video_title: str = ""
    channel: str = ""
    transcript_length: int = 0
    case_details: CaseDetails = field(default_factory=CaseDetails)
    artifacts_mentioned: List[ArtifactMention] = field(default_factory=list)
    verification_results: List[VerificationResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "video_title": self.video_title,
            "channel": self.channel,
            "transcript_length": self.transcript_length,
            "case_details": asdict(self.case_details),
            "artifacts_mentioned": [asdict(a) for a in self.artifacts_mentioned],
            "verification_results": [asdict(v) for v in self.verification_results],
        }


# =============================================================================
# YOUTUBE TRANSCRIPT EXTRACTION
# =============================================================================

def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from URL or return as-is if already an ID."""
    if not url_or_id:
        return ""

    # Already an ID (11 characters, alphanumeric with - and _)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id

    # YouTube URL patterns
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'[?&]v=([a-zA-Z0-9_-]{11})',
    ]

    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)

    return url_or_id  # Return as-is, might be an ID


def get_transcript(video_id: str) -> Tuple[List[TranscriptSegment], str]:
    """
    Fetch transcript from YouTube video.

    Returns:
        Tuple of (segments, full_text)
    """
    if not TRANSCRIPT_API_AVAILABLE:
        return [], "youtube-transcript-api not installed"

    try:
        # New API (v0.6+): instance-based
        ytt = YouTubeTranscriptApi()
        raw_transcript = ytt.fetch(video_id)

        # Handle FetchedTranscript object
        if hasattr(raw_transcript, 'snippets'):
            # New API returns FetchedTranscript with snippets
            segments = [
                TranscriptSegment(
                    text=item.text if hasattr(item, 'text') else item.get('text', ''),
                    start=item.start if hasattr(item, 'start') else item.get('start', 0),
                    duration=item.duration if hasattr(item, 'duration') else item.get('duration', 0)
                )
                for item in raw_transcript.snippets
            ]
        else:
            # Fallback for list format
            segments = [
                TranscriptSegment(
                    text=item.get('text', '') if isinstance(item, dict) else getattr(item, 'text', ''),
                    start=item.get('start', 0) if isinstance(item, dict) else getattr(item, 'start', 0),
                    duration=item.get('duration', 0) if isinstance(item, dict) else getattr(item, 'duration', 0)
                )
                for item in raw_transcript
            ]

        full_text = " ".join(seg.text for seg in segments)

        return segments, full_text

    except TranscriptsDisabled:
        return [], "Transcripts are disabled for this video"
    except NoTranscriptFound:
        return [], "No transcript found"
    except VideoUnavailable:
        return [], "Video is unavailable"
    except Exception as e:
        error_msg = str(e)
        if "ProxyError" in error_msg or "Tunnel connection failed" in error_msg:
            return [], "Network blocked (proxy/firewall). Run locally to fetch transcripts."
        return [], f"Error fetching transcript: {error_msg}"


def get_video_metadata(video_id: str) -> Dict:
    """Get video title, channel, description using YouTube Data API."""
    if not YOUTUBE_API_KEY:
        return {"title": "", "channel": "", "description": ""}

    import requests

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet",
        "id": video_id,
        "key": YOUTUBE_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("items"):
            snippet = data["items"][0]["snippet"]
            return {
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "description": snippet.get("description", ""),
            }
    except Exception as e:
        print(f"[WARN] Could not fetch video metadata: {e}")

    return {"title": "", "channel": "", "description": ""}


# =============================================================================
# TRANSCRIPT ANALYSIS
# =============================================================================

def detect_artifacts_in_transcript(segments: List[TranscriptSegment],
                                   full_text: str) -> List[ArtifactMention]:
    """Scan transcript for mentions of different artifact types."""
    mentions = []
    full_text_lower = full_text.lower()

    for artifact_type, keywords in ARTIFACT_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in full_text_lower:
                # Find the segment(s) containing this keyword
                for seg in segments:
                    if keyword.lower() in seg.text.lower():
                        # Get context (surrounding text)
                        seg_idx = segments.index(seg)
                        context_start = max(0, seg_idx - 1)
                        context_end = min(len(segments), seg_idx + 2)
                        context = " ".join(s.text for s in segments[context_start:context_end])

                        mentions.append(ArtifactMention(
                            artifact_type=artifact_type,
                            context=context[:300],
                            timestamp=seg.timestamp,
                            confidence="high" if keyword.lower() in seg.text.lower() else "medium",
                        ))
                        break  # Only record first mention per keyword
                break  # Only need one keyword match per type

    return mentions


def extract_case_details_with_llm(transcript: str, video_title: str = "") -> CaseDetails:
    """Use LLM to extract structured case details from transcript."""
    if not OPENAI_AVAILABLE or not OPENROUTER_API_KEY:
        print("[WARN] LLM not available, using basic extraction")
        return extract_case_details_basic(transcript)

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

    # Truncate transcript if too long
    max_chars = 15000
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "... [truncated]"

    prompt = f"""Analyze this true crime video transcript and extract case details.

VIDEO TITLE: {video_title}

TRANSCRIPT:
{transcript}

Extract the following in JSON format:
{{
    "defendant_names": ["list of defendant/suspect names"],
    "victim_names": ["list of victim names"],
    "jurisdiction": "city and state where crime occurred",
    "state": "2-letter state code (e.g., FL, TX)",
    "incident_year": "year the crime occurred",
    "crime_type": "murder, assault, kidnapping, etc.",
    "case_summary": "1-2 sentence summary",
    "agencies_mentioned": ["police departments, sheriff offices mentioned"]
}}

Return ONLY valid JSON, no other text."""

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1000,
        )

        content = response.choices[0].message.content.strip()

        # Extract JSON from response
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            data = json.loads(json_match.group())
            return CaseDetails(
                defendant_names=data.get("defendant_names", []),
                victim_names=data.get("victim_names", []),
                jurisdiction=data.get("jurisdiction", ""),
                state=data.get("state", ""),
                incident_year=data.get("incident_year", ""),
                crime_type=data.get("crime_type", ""),
                case_summary=data.get("case_summary", ""),
                agencies_mentioned=data.get("agencies_mentioned", []),
            )
    except Exception as e:
        print(f"[WARN] LLM extraction failed: {e}")

    return extract_case_details_basic(transcript)


def extract_case_details_basic(transcript: str) -> CaseDetails:
    """Basic regex-based extraction as fallback."""
    details = CaseDetails()

    # Try to find state mentions
    state_pattern = r'\b(Florida|Texas|California|Arizona|Colorado|Washington|FL|TX|CA|AZ|CO|WA)\b'
    state_match = re.search(state_pattern, transcript, re.IGNORECASE)
    if state_match:
        state = state_match.group(1).upper()
        state_map = {"FLORIDA": "FL", "TEXAS": "TX", "CALIFORNIA": "CA",
                     "ARIZONA": "AZ", "COLORADO": "CO", "WASHINGTON": "WA"}
        details.state = state_map.get(state, state)

    # Try to find year mentions (likely incident years)
    year_pattern = r'\b(20[0-2][0-9]|19[89][0-9])\b'
    years = re.findall(year_pattern, transcript)
    if years:
        # Use earliest year as likely incident year
        details.incident_year = min(years)

    return details


# =============================================================================
# ARTIFACT VERIFICATION
# =============================================================================

def verify_artifacts(case_details: CaseDetails,
                     artifacts_mentioned: List[ArtifactMention]) -> List[VerificationResult]:
    """
    For each artifact type mentioned in the video, try to locate it using our pipeline.
    """
    if not BODYCAM_SOURCES_AVAILABLE:
        print("[WARN] bodycam_sources not available for verification")
        return []

    results = []
    router = BodycamSourceRouter()

    # Get defendant names to search
    defendants = case_details.defendant_names or ["unknown"]
    jurisdiction = case_details.jurisdiction or case_details.state or ""
    year = case_details.incident_year or ""

    # Track which artifact types were mentioned
    mentioned_types = set(a.artifact_type for a in artifacts_mentioned)

    for defendant in defendants[:2]:  # Limit to first 2 defendants
        if defendant.lower() == "unknown":
            continue

        print(f"\n[VERIFY] Searching for artifacts related to: {defendant}")

        # Run our search pipeline
        search_result = router.search(defendant, jurisdiction, year)

        # Check bodycam
        if "bodycam" in mentioned_types or "dashcam" in mentioned_types:
            results.append(VerificationResult(
                artifact_type="bodycam",
                mentioned_in_video=True,
                found_by_search=len(search_result.bodycam) > 0,
                search_results=[v.to_dict() for v in search_result.bodycam[:3]],
                notes=f"Found {len(search_result.bodycam)} results" if search_result.bodycam else "Not found via YouTube search",
            ))

        # Check interrogation
        if "interrogation" in mentioned_types:
            results.append(VerificationResult(
                artifact_type="interrogation",
                mentioned_in_video=True,
                found_by_search=len(search_result.interrogation) > 0,
                search_results=[v.to_dict() for v in search_result.interrogation[:3]],
                notes=f"Found {len(search_result.interrogation)} results" if search_result.interrogation else "Not found via YouTube search",
            ))

        # Check court/trial footage
        if "court" in mentioned_types:
            # Do a specific court search
            court_results = youtube_search(f'"{defendant}" trial court video', max_results=5)
            found_court = [r for r in court_results if any(kw in r.get('title', '').lower()
                          for kw in ['trial', 'court', 'sentencing', 'verdict'])]
            results.append(VerificationResult(
                artifact_type="court",
                mentioned_in_video=True,
                found_by_search=len(found_court) > 0,
                search_results=found_court[:3],
                notes=f"Found {len(found_court)} results" if found_court else "Not found via YouTube search",
            ))

        # Check 911 calls
        if "911_call" in mentioned_types:
            call_results = youtube_search(f'"{defendant}" 911 call', max_results=5)
            found_calls = [r for r in call_results if '911' in r.get('title', '').lower()]
            results.append(VerificationResult(
                artifact_type="911_call",
                mentioned_in_video=True,
                found_by_search=len(found_calls) > 0,
                search_results=found_calls[:3],
                notes=f"Found {len(found_calls)} results" if found_calls else "Not found via YouTube search",
            ))

    # Print router stats
    router.print_stats()

    return results


# =============================================================================
# MAIN ANALYSIS FUNCTION
# =============================================================================

def analyze_video(url_or_id: str, verify: bool = True) -> AnalysisReport:
    """
    Full analysis pipeline for a YouTube video.

    1. Extract video ID and metadata
    2. Fetch transcript
    3. Detect artifact mentions
    4. Extract case details
    5. Verify artifacts can be located
    """
    video_id = extract_video_id(url_or_id)

    print("=" * 60)
    print(f"ANALYZING VIDEO: {video_id}")
    print("=" * 60)

    report = AnalysisReport(video_id=video_id)

    # Step 1: Get video metadata
    print("\n[1/5] Fetching video metadata...")
    metadata = get_video_metadata(video_id)
    report.video_title = metadata.get("title", "")
    report.channel = metadata.get("channel", "")
    print(f"    Title: {report.video_title}")
    print(f"    Channel: {report.channel}")

    # Step 2: Get transcript
    print("\n[2/5] Extracting transcript...")
    segments, full_text = get_transcript(video_id)

    if not segments:
        print(f"    ERROR: {full_text}")
        return report

    report.transcript_length = len(full_text)
    print(f"    Transcript length: {len(full_text):,} characters")
    print(f"    Segments: {len(segments)}")

    # Step 3: Detect artifact mentions
    print("\n[3/5] Detecting artifact mentions...")
    report.artifacts_mentioned = detect_artifacts_in_transcript(segments, full_text)

    print(f"    Found {len(report.artifacts_mentioned)} artifact mentions:")
    for artifact in report.artifacts_mentioned:
        print(f"      - [{artifact.timestamp}] {artifact.artifact_type}: \"{artifact.context[:60]}...\"")

    # Step 4: Extract case details
    print("\n[4/5] Extracting case details...")
    report.case_details = extract_case_details_with_llm(full_text, report.video_title)

    print(f"    Defendants: {', '.join(report.case_details.defendant_names) or 'Unknown'}")
    print(f"    Victims: {', '.join(report.case_details.victim_names) or 'Unknown'}")
    print(f"    Jurisdiction: {report.case_details.jurisdiction or 'Unknown'}")
    print(f"    State: {report.case_details.state or 'Unknown'}")
    print(f"    Year: {report.case_details.incident_year or 'Unknown'}")
    print(f"    Crime: {report.case_details.crime_type or 'Unknown'}")
    print(f"    Summary: {report.case_details.case_summary or 'N/A'}")

    # Step 5: Verify artifacts
    if verify and report.artifacts_mentioned:
        print("\n[5/5] Verifying artifacts...")
        report.verification_results = verify_artifacts(
            report.case_details,
            report.artifacts_mentioned
        )
    else:
        print("\n[5/5] Skipping verification (no artifacts mentioned or --no-verify)")

    return report


def print_report(report: AnalysisReport):
    """Print a formatted analysis report."""
    print("\n" + "=" * 60)
    print("ANALYSIS REPORT")
    print("=" * 60)

    print(f"\nVideo: {report.video_title}")
    print(f"Channel: {report.channel}")
    print(f"ID: {report.video_id}")

    print(f"\n--- Case Details ---")
    cd = report.case_details
    print(f"Defendants: {', '.join(cd.defendant_names) or 'Unknown'}")
    print(f"Jurisdiction: {cd.jurisdiction or 'Unknown'}")
    print(f"Year: {cd.incident_year or 'Unknown'}")
    print(f"Crime: {cd.crime_type or 'Unknown'}")

    print(f"\n--- Artifacts Mentioned in Video ---")
    if report.artifacts_mentioned:
        for a in report.artifacts_mentioned:
            print(f"  [{a.timestamp}] {a.artifact_type.upper()}")
    else:
        print("  None detected")

    print(f"\n--- Verification Results ---")
    if report.verification_results:
        for v in report.verification_results:
            status = "FOUND" if v.found_by_search else "NOT FOUND"
            print(f"  {v.artifact_type.upper()}: {status}")
            print(f"    {v.notes}")
            if v.search_results:
                for r in v.search_results[:2]:
                    title = r.get('title', 'Untitled')[:50]
                    url = r.get('url', '')
                    print(f"      - {title}")
                    print(f"        {url}")
    else:
        print("  No verification performed")

    # Summary
    print(f"\n--- Summary ---")
    mentioned = len(set(a.artifact_type for a in report.artifacts_mentioned))
    found = len([v for v in report.verification_results if v.found_by_search])
    total_verified = len(report.verification_results)

    print(f"Artifact types mentioned: {mentioned}")
    print(f"Verified & found: {found}/{total_verified}")

    if total_verified > 0:
        hit_rate = (found / total_verified) * 100
        print(f"Hit rate: {hit_rate:.0f}%")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Analyze YouTube video for artifact verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --url "https://www.youtube.com/watch?v=VIDEO_ID"
    %(prog)s --url "VIDEO_ID" --json
    %(prog)s --url "VIDEO_ID" --no-verify
        """
    )

    parser.add_argument("--url", "-u", required=True,
                        help="YouTube URL or video ID")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip artifact verification")
    parser.add_argument("--transcript-only", action="store_true",
                        help="Only extract and print transcript")

    args = parser.parse_args()

    if args.transcript_only:
        video_id = extract_video_id(args.url)
        segments, full_text = get_transcript(video_id)
        if segments:
            for seg in segments:
                print(f"[{seg.timestamp}] {seg.text}")
        else:
            print(f"Error: {full_text}")
        return

    report = analyze_video(args.url, verify=not args.no_verify)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
