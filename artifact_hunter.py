#!/usr/bin/env python3
"""
NEWS → VIEWS: Artifact Hunter v3
Searches for bodycam, interrogation, court footage, docket documents,
911 dispatch audio, and primary-source records for PASS cases.

Search backends: Google PSE (web), YouTube Data API (video),
Vimeo API (video), with optional Exa fallback.

Usage:
    python artifact_hunter.py              # Process all unassessed cases
    python artifact_hunter.py --limit 5    # Process max 5 cases
    python artifact_hunter.py --dry-run    # Search + assess, don't write
    python artifact_hunter.py --check      # Check credentials only
"""

import os
import re
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Tuple
from dotenv import load_dotenv

load_dotenv()

from jurisdiction_portals import (
    build_jurisdiction_queries,
    extract_domain,
    get_agency_youtube_channels,
    get_search_domains_for_region,
    get_transparency_portals,
    DISPATCH_DOMAINS,
    RECORDS_DOMAINS,
)
from search_backends import (
    web_search_pse,
    youtube_search,
    vimeo_search,
    check_search_credentials,
    print_search_credential_status,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

SHEET_ID = os.getenv("SHEET_ID")
EXA_API_KEY = os.getenv("EXA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json")

# Model split: use a lighter model for artifact assessment
OPENROUTER_MODEL_ARTIFACT = os.getenv(
    "OPENROUTER_MODEL_ARTIFACT",
    os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001"),
)

# Search backend config
ALLOW_EXA_FALLBACK = os.getenv("ALLOW_EXA_FALLBACK", "true").lower() == "true"

# Caps
MAX_RESULTS_PER_BUCKET = 6
MAX_TOTAL_RESULTS_FOR_LLM = 25

# =============================================================================
# VALIDATION
# =============================================================================

def check_credentials() -> bool:
    errors = []
    if not SHEET_ID:
        errors.append("SHEET_ID not set")
    if not OPENROUTER_API_KEY:
        errors.append("OPENROUTER_API_KEY not set")
    if not Path(SERVICE_ACCOUNT_PATH).exists():
        errors.append(f"Service account not found: {SERVICE_ACCOUNT_PATH}")

    if errors:
        print("❌ Configuration errors:")
        for e in errors:
            print(f"   - {e}")
        return False

    print("✅ Core credentials OK")

    # Show search backend status
    print("\n   Search backends:")
    backends = print_search_credential_status()

    if not any(backends.values()) and not EXA_API_KEY:
        print("\n   ⚠️  No search backends configured. Set at least one of:")
        print("      GOOGLE_PSE_API_KEY + GOOGLE_PSE_CX")
        print("      YOUTUBE_API_KEY")
        print("      EXA_API_KEY (fallback)")
        return False

    if EXA_API_KEY:
        print(f"   {'✅' if ALLOW_EXA_FALLBACK else '⚠️'} Exa fallback: {'enabled' if ALLOW_EXA_FALLBACK else 'disabled'}")

    print(f"\n   Assessment model: {OPENROUTER_MODEL_ARTIFACT}")
    return True

# =============================================================================
# CLIENT INITIALIZATION
# =============================================================================

def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
    return gspread.authorize(creds)


def get_exa_client():
    if not EXA_API_KEY:
        return None
    from exa_py import Exa
    return Exa(api_key=EXA_API_KEY)


def get_llm_client():
    from openai import OpenAI
    return OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def _is_relevant(title: str, url: str, defendant: str) -> bool:
    """Check if a search result is actually about this defendant."""
    if not defendant:
        return True
    name_parts = defendant.lower().split()
    target = title.lower() + " " + url.lower()
    return any(part in target for part in name_parts if len(part) >= 3)


def _dedup_results(results_list: List[Dict]) -> List[Dict]:
    """Remove duplicate URLs within a result bucket."""
    seen = set()
    deduped = []
    for r in results_list:
        url = r.get("url", "")
        if url not in seen:
            seen.add(url)
            deduped.append(r)
    return deduped


def _slim_results(results_list: List[Dict], max_per_bucket: int = 3) -> List[Dict]:
    """Trim results for LLM prompt: url + title + short snippet only."""
    slim = []
    for r in results_list[:max_per_bucket]:
        entry = {"url": r.get("url", ""), "title": r.get("title", "")}
        snippet = r.get("snippet", "")
        if snippet:
            entry["text"] = snippet[:300]
        slim.append(entry)
    return slim


# =============================================================================
# STEP 0: PARSE EXISTING SOURCES (FREE)
# =============================================================================

def parse_existing_sources(cell_text: str) -> Dict[str, List[Dict]]:
    """Parse URLs already in Footage Sources column into typed buckets.

    This is free — no API calls needed. If the anchor row already has
    source URLs, classify them before doing any paid search.
    """
    buckets = {
        "body_cam": [], "interrogation": [], "court": [],
        "docket": [], "dispatch": [], "other": [],
    }

    if not cell_text:
        return buckets

    urls = re.findall(r'https?://[^\s<>"]+', str(cell_text))

    for url in urls:
        url_lower = url.lower()
        entry = {"url": url, "title": "", "snippet": "", "source": "existing"}

        if "youtube.com" in url_lower or "youtu.be" in url_lower:
            # Could be bodycam, interrogation, or court — put in other for now
            buckets["body_cam"].append(entry)
        elif "vimeo.com" in url_lower:
            buckets["body_cam"].append(entry)
        elif url_lower.endswith(".pdf"):
            buckets["docket"].append(entry)
        elif "courtlistener.com" in url_lower:
            buckets["docket"].append(entry)
        elif "broadcastify.com" in url_lower or "openmhz.com" in url_lower:
            buckets["dispatch"].append(entry)
        else:
            buckets["other"].append(entry)

    return buckets


# =============================================================================
# STEP 1: VIDEO RETRIEVAL (YouTube + Vimeo APIs)
# =============================================================================

def search_videos(defendant: str, jurisdiction: str,
                  incident_year: str = None,
                  hints: List[str] = None) -> Dict[str, List[Dict]]:
    """Search YouTube and Vimeo for case-related video artifacts."""
    results = {"body_cam": [], "interrogation": [], "court": []}

    backends = check_search_credentials()

    # YouTube
    if backends["youtube"]:
        yt_hits = youtube_search(defendant, jurisdiction, incident_year, hints)
        for hit in yt_hits:
            if not _is_relevant(hit.get("title", ""), hit.get("url", ""), defendant):
                continue
            bucket = _bucketize_result(hit)
            if bucket in results:
                results[bucket].append(hit)
            else:
                results["body_cam"].append(hit)

    # Vimeo
    if backends["vimeo"]:
        vim_hits = vimeo_search(defendant, jurisdiction, incident_year, hints)
        for hit in vim_hits:
            if not _is_relevant(hit.get("title", ""), hit.get("url", ""), defendant):
                continue
            bucket = _bucketize_result(hit)
            if bucket in results:
                results[bucket].append(hit)
            else:
                results["body_cam"].append(hit)

    return results


# =============================================================================
# STEP 2: WEB RETRIEVAL (Google PSE)
# =============================================================================

# Keywords used to classify PSE results into artifact buckets
BUCKET_KEYWORDS = {
    "body_cam": ["body cam", "bodycam", "bwc", "body-worn", "dashcam", "dash cam"],
    "interrogation": ["interrogation", "interview", "confession"],
    "court": ["trial", "hearing", "courtroom", "livestream", "sentencing", "arraignment"],
    "docket": ["docket", "complaint", "affidavit", "probable cause", "charging",
               "indictment", "case number", ".pdf", "filing"],
    "dispatch": ["911", "dispatch", "scanner", "emergency call"],
}


def _bucketize_result(result: Dict) -> str:
    """Classify a search result into an artifact bucket by keywords."""
    text = (result.get("title", "") + " " + result.get("snippet", "") +
            " " + result.get("url", "")).lower()

    for bucket, keywords in BUCKET_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return bucket
    return "other"


def build_web_queries(defendant: str, jurisdiction: str,
                      incident_year: str = None,
                      custom_queries: List[str] = None) -> List[str]:
    """Build 6-8 high-yield web queries for Google PSE."""
    year_str = f" {incident_year}" if incident_year else ""
    queries = []

    if defendant and jurisdiction:
        queries.append(f'"{defendant}" "{jurisdiction}" case number')
        queries.append(f'"{defendant}" "{jurisdiction}" criminal complaint pdf')
        queries.append(f'"{defendant}" probable cause affidavit "{jurisdiction}"')
        queries.append(f'"{defendant}" docket "{jurisdiction}"')
        queries.append(f'"{defendant}" arraignment "{jurisdiction}" video')
        queries.append(f'"{defendant}" "press release" "{jurisdiction}"')
    elif defendant:
        queries.append(f'"{defendant}" criminal case docket')
        queries.append(f'"{defendant}" probable cause affidavit')
        queries.append(f'"{defendant}" bodycam OR "body camera"')
        queries.append(f'"{defendant}" 911 call audio')

    # Add intake artifact queries (capped)
    for q in (custom_queries or [])[:3]:
        queries.append(q)

    return queries[:8]


def search_web(defendant: str, jurisdiction: str,
               incident_year: str = None,
               custom_queries: List[str] = None) -> Dict[str, List[Dict]]:
    """Search Google PSE for case IDs, docket docs, and press releases."""
    results = {
        "body_cam": [], "interrogation": [], "court": [],
        "docket": [], "dispatch": [], "other": [],
    }

    backends = check_search_credentials()
    if not backends["pse"]:
        return results

    queries = build_web_queries(defendant, jurisdiction, incident_year, custom_queries)
    seen_urls = set()

    for query in queries:
        hits = web_search_pse(query, num=5)

        for hit in hits:
            url = hit.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if not _is_relevant(hit.get("title", ""), url, defendant):
                continue

            bucket = _bucketize_result(hit)
            results[bucket].append(hit)

        time.sleep(0.2)

    return results


# =============================================================================
# STEP 3: EXA FALLBACK (optional, capped)
# =============================================================================

def search_exa_fallback(exa, defendant: str, jurisdiction: str,
                        incident_year: str = None) -> Dict[str, List[Dict]]:
    """Run 1-2 Exa searches as semantic rescue when PSE/YouTube found nothing."""
    results = {
        "body_cam": [], "interrogation": [], "court": [],
        "docket": [], "dispatch": [], "other": [],
    }

    if not exa:
        return results

    year_str = f" {incident_year}" if incident_year else ""

    fallback_queries = [
        f'"{defendant}" bodycam OR interrogation OR "court video" OR affidavit{year_str}',
        f'"{defendant}" 911 call OR docket OR "criminal complaint" {jurisdiction}',
    ]

    for query in fallback_queries:
        try:
            search_results = exa.search_and_contents(
                query=query,
                type="auto",
                num_results=3,
                text={"max_characters": 800},
            )

            for r in search_results.results:
                url = r.url
                title = getattr(r, 'title', '')

                if not _is_relevant(title, url, defendant):
                    continue

                hit = {
                    "url": url,
                    "title": title,
                    "snippet": (getattr(r, 'text', '') or '')[:500],
                    "source": "exa_fallback",
                }
                bucket = _bucketize_result(hit)
                results[bucket].append(hit)

            time.sleep(0.3)

        except Exception as e:
            print(f"      Exa fallback error: {e}")

    return results


# =============================================================================
# COMBINED SEARCH ORCHESTRATOR
# =============================================================================

def _merge_results(base: Dict, additions: Dict) -> Dict:
    """Merge result buckets, deduplicating by URL."""
    for key in additions:
        if key in base:
            base[key].extend(additions[key])
    return base


def _count_results(results: Dict) -> int:
    """Count total results across all buckets."""
    return sum(len(v) for v in results.values())


def _cap_results(results: Dict) -> Dict:
    """Cap each bucket to MAX_RESULTS_PER_BUCKET."""
    for key in results:
        results[key] = results[key][:MAX_RESULTS_PER_BUCKET]
    return results


def search_artifacts(defendant: str, jurisdiction: str,
                     crime_type: str = "", custom_queries: List[str] = None,
                     region_id: str = None, incident_year: str = None,
                     existing_sources_text: str = "",
                     exa=None) -> Tuple[Dict, Dict]:
    """Orchestrate the full retrieval funnel for a case.

    Returns:
        (results_dict, telemetry_dict)
    """
    results = {
        "body_cam": [], "interrogation": [], "court": [],
        "docket": [], "dispatch": [], "other": [],
    }

    telemetry = {
        "existing_sources_count": 0,
        "youtube_hits": 0,
        "vimeo_hits": 0,
        "pse_hits": 0,
        "exa_fallback_used": False,
        "exa_fallback_hits": 0,
    }

    defendant_clean = defendant.split(",")[0].strip() if defendant else ""
    jurisdiction_clean = jurisdiction.strip() if jurisdiction else ""

    if not defendant_clean and not jurisdiction_clean:
        return results, telemetry

    # Build search hints from crime type / jurisdiction queries
    hints = []
    if region_id:
        jq = build_jurisdiction_queries(region_id, defendant_clean, incident_year)
        # Grab agency names as hints
        from jurisdiction_portals import get_jurisdiction_config
        config = get_jurisdiction_config(region_id)
        for agency in config.get("agencies", []):
            abbrev = agency.get("abbrev", "")
            if abbrev:
                hints.append(abbrev)

    # ----- Step 0: Parse existing sources (free) -----
    existing = parse_existing_sources(existing_sources_text)
    results = _merge_results(results, existing)
    telemetry["existing_sources_count"] = _count_results(existing)

    if telemetry["existing_sources_count"] > 0:
        print(f"      Step 0: {telemetry['existing_sources_count']} existing sources parsed")

    # ----- Step 1: Video retrieval (YouTube + Vimeo) -----
    video_results = search_videos(defendant_clean, jurisdiction_clean,
                                  incident_year, hints)
    results = _merge_results(results, video_results)
    telemetry["youtube_hits"] = sum(
        1 for bucket in video_results.values()
        for r in bucket if r.get("source") == "youtube"
    )
    telemetry["vimeo_hits"] = sum(
        1 for bucket in video_results.values()
        for r in bucket if r.get("source") == "vimeo"
    )
    print(f"      Step 1: YouTube={telemetry['youtube_hits']} Vimeo={telemetry['vimeo_hits']}")

    # ----- Step 2: Web retrieval (Google PSE) -----
    web_results = search_web(defendant_clean, jurisdiction_clean,
                             incident_year, custom_queries)
    results = _merge_results(results, web_results)
    telemetry["pse_hits"] = _count_results(web_results)
    print(f"      Step 2: PSE={telemetry['pse_hits']}")

    # ----- Step 3: Exa fallback (if still empty) -----
    if _count_results(results) < 3 and ALLOW_EXA_FALLBACK and exa:
        print(f"      Step 3: Exa fallback (low results)")
        exa_results = search_exa_fallback(exa, defendant_clean, jurisdiction_clean,
                                          incident_year)
        results = _merge_results(results, exa_results)
        telemetry["exa_fallback_used"] = True
        telemetry["exa_fallback_hits"] = _count_results(exa_results)
        print(f"      Step 3: Exa fallback={telemetry['exa_fallback_hits']}")

    # Dedup + cap
    for key in results:
        results[key] = _dedup_results(results[key])
    results = _cap_results(results)

    total = _count_results(results)
    print(f"      Total relevant: {total}")

    return results, telemetry


# =============================================================================
# STEP 4: LLM ASSESSMENT (with heuristics to skip when obvious)
# =============================================================================

# Domains that indicate a primary source (not news coverage)
PRIMARY_SOURCE_DOMAINS = {
    "courtlistener.com", "unicourt.com", "pacermonitor.com",
    "broadcastify.com", "openmhz.com",
}


def _has_primary_source(results: Dict) -> bool:
    """Check if any result URL is from a known primary-source domain or is a PDF."""
    for bucket in results.values():
        for r in bucket:
            url = r.get("url", "").lower()
            if url.endswith(".pdf"):
                return True
            if any(domain in url for domain in PRIMARY_SOURCE_DOMAINS):
                return True
    return False


def _count_video_types(results: Dict) -> int:
    """Count how many video artifact types have at least 1 result."""
    video_buckets = ["body_cam", "interrogation", "court"]
    return sum(1 for b in video_buckets if results.get(b))


def heuristic_assess(results: Dict) -> Dict:
    """Try to assess without LLM when the answer is obvious.

    Returns an assessment dict if confident, or empty dict if LLM needed.
    """
    total = _count_results(results)

    # Obviously insufficient: nothing found
    if total == 0:
        return {
            "body_cam_exists": "NO", "body_cam_sources": [],
            "interrogation_exists": "NO", "interrogation_sources": [],
            "court_video_exists": "NO", "court_sources": [],
            "docket_exists": "NO", "docket_sources": [],
            "dispatch_911_exists": "NO", "dispatch_sources": [],
            "primary_source_score": 0, "evidence_depth_score": 0,
            "artifact_types_found": 0,
            "overall_assessment": "INSUFFICIENT",
            "notes": "No results found across any search backend.",
        }

    # Obviously enough: primary source + multiple video types
    has_primary = _has_primary_source(results)
    video_types = _count_video_types(results)

    if has_primary and video_types >= 2:
        # Build source lists
        assessment = {
            "body_cam_exists": "YES" if results.get("body_cam") else "NO",
            "body_cam_sources": [r["url"] for r in results.get("body_cam", [])[:3]],
            "interrogation_exists": "YES" if results.get("interrogation") else "NO",
            "interrogation_sources": [r["url"] for r in results.get("interrogation", [])[:3]],
            "court_video_exists": "YES" if results.get("court") else "NO",
            "court_sources": [r["url"] for r in results.get("court", [])[:3]],
            "docket_exists": "YES" if results.get("docket") else "NO",
            "docket_sources": [r["url"] for r in results.get("docket", [])[:3]],
            "dispatch_911_exists": "YES" if results.get("dispatch") else "NO",
            "dispatch_sources": [r["url"] for r in results.get("dispatch", [])[:3]],
            "primary_source_score": 75,
            "evidence_depth_score": 70,
            "artifact_types_found": sum(1 for b in ["body_cam", "interrogation", "court",
                                                     "docket", "dispatch"]
                                        if results.get(b)),
            "overall_assessment": "ENOUGH",
            "notes": "Auto-assessed: primary source + multiple video types found.",
        }
        return assessment

    # Ambiguous — need LLM
    return {}


def assess_artifacts(llm, case_info: Dict, search_results: Dict) -> Dict:
    """Use LLM to assess artifact availability with depth metrics."""
    prompt = f"""You are assessing primary-source evidence for a true crime case.
We need RAW ARTIFACTS — not news coverage. Read the text snippets carefully.

CASE:
- Defendant: {case_info.get('defendant', 'Unknown')}
- Jurisdiction: {case_info.get('jurisdiction', 'Unknown')}
- Crime: {case_info.get('crime_type', 'Unknown')}

Each result below has a URL, title, and a text snippet from the actual page.
Use the snippet to determine if this is a real primary source or just news.

BODY CAM:
{json.dumps(_slim_results(search_results.get('body_cam', [])), indent=2)}

INTERROGATION:
{json.dumps(_slim_results(search_results.get('interrogation', [])), indent=2)}

COURT VIDEO:
{json.dumps(_slim_results(search_results.get('court', [])), indent=2)}

DOCKET/RECORDS:
{json.dumps(_slim_results(search_results.get('docket', [])), indent=2)}

911/DISPATCH:
{json.dumps(_slim_results(search_results.get('dispatch', [])), indent=2)}

OTHER:
{json.dumps(_slim_results(search_results.get('other', [])), indent=2)}

For each artifact type, read the text snippet and determine:
- "YES" = snippet confirms this IS the actual artifact (video upload, court filing, audio file)
- "MAYBE" = snippet mentions the artifact exists but this link may be news about it
- "NO" = not found, or snippet shows this is about a different person/case

CRITICAL: If the snippet text is about a DIFFERENT person or case than {case_info.get('defendant', 'Unknown')},
mark it NO regardless of the title.

Return JSON:
{{
    "body_cam_exists": "YES/MAYBE/NO",
    "body_cam_sources": ["url1"],
    "interrogation_exists": "YES/MAYBE/NO",
    "interrogation_sources": ["url1"],
    "court_video_exists": "YES/MAYBE/NO",
    "court_sources": ["url1"],
    "docket_exists": "YES/MAYBE/NO",
    "docket_sources": ["url1"],
    "dispatch_911_exists": "YES/MAYBE/NO",
    "dispatch_sources": ["url1"],
    "primary_source_score": 0,
    "evidence_depth_score": 0,
    "artifact_types_found": 0,
    "overall_assessment": "ENOUGH/BORDERLINE/INSUFFICIENT",
    "notes": "Brief explanation"
}}

Scoring:
- primary_source_score (0-100): % of results that are actual primary sources vs news
- evidence_depth_score (0-100): Could a creator build an EWU-level video from these?
  100 = bodycam + interrogation + 911 + docket all confirmed available
  50 = some primary sources but major gaps
  0 = only news coverage, no raw artifacts
- artifact_types_found: count of types with YES or MAYBE (max 5)

JSON only:"""

    try:
        response = llm.chat.completions.create(
            model=OPENROUTER_MODEL_ARTIFACT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            extra_headers={
                "HTTP-Referer": "https://newstoviews.app",
                "X-Title": "NewsToViews-ArtifactHunter",
            }
        )

        content = response.choices[0].message.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        return json.loads(content)

    except Exception as e:
        print(f"      Assessment error: {e}")
        return {}


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_artifact_hunter(limit: int = None, dry_run: bool = False):
    """Hunt for artifacts for cases in CASE ANCHOR."""
    print("=" * 60)
    print("NEWS → VIEWS: Artifact Hunter v3")
    if dry_run:
        print("   (DRY RUN — no sheet writes)")
    print("=" * 60)

    if not check_credentials():
        return {"error": "Invalid credentials"}

    # Initialize
    print("\n[INIT] Connecting...")
    try:
        gc = get_gspread_client()
        exa = get_exa_client()
        llm = get_llm_client()
    except Exception as e:
        print(f"❌ Init failed: {e}")
        return {"error": str(e)}

    # Open sheet
    try:
        sh = gc.open_by_key(SHEET_ID)
        ws_anchor = sh.worksheet("CASE ANCHOR & FOOTAGE CHECK")
        ws_intake = sh.worksheet("NEWS INTAKE")
    except Exception as e:
        print(f"❌ Sheet error: {e}")
        return {"error": str(e)}

    # Get cases
    cases = ws_anchor.get_all_records()
    print(f"[INIT] {len(cases)} cases in CASE ANCHOR")

    # Get intake data for artifact queries
    intake_records = ws_intake.get_all_records()
    intake_by_id = {str(i): r for i, r in enumerate(intake_records, start=2)}

    stats = {
        "processed": 0, "enough": 0, "borderline": 0, "insufficient": 0,
        "errors": 0, "llm_calls": 0, "llm_skipped": 0,
    }

    for row_idx, case in enumerate(cases, start=2):
        # Skip already assessed
        assessment_val = str(case.get("Footage Assessment", "")).strip()
        if assessment_val:
            continue

        if limit and stats["processed"] >= limit:
            print(f"\n[LIMIT] Reached {limit} cases")
            break

        defendant = str(case.get("Defendant Name(s)", "")).strip()
        jurisdiction = str(case.get("Jurisdiction", "")).strip()
        intake_id = str(case.get("Intake_ID", "")).strip()

        print(f"\n[{row_idx}] {defendant[:40]}...")
        print(f"    Jurisdiction: {jurisdiction}")

        # Get custom queries and metadata from intake
        custom_queries = []
        crime_type = ""
        region_id = ""
        incident_year = ""
        if intake_id and intake_id in intake_by_id:
            intake_row = intake_by_id[intake_id]
            queries_str = str(intake_row.get("Artifact Queries", ""))
            if queries_str:
                custom_queries = [q.strip() for q in queries_str.split("|") if q.strip()]
            crime_type = str(intake_row.get("Crime Type", ""))
            region_id = str(
                intake_row.get("Region_ID")
                or intake_row.get("Region ID")
                or intake_row.get("Region")
                or ""
            )
            triage_json = str(intake_row.get("Triage JSON") or intake_row.get("Triage") or "")
            if triage_json:
                try:
                    triage = json.loads(triage_json)
                    incident_year = str(triage.get("incident_year", ""))
                except (json.JSONDecodeError, ValueError):
                    incident_year = ""

        # Get existing sources from sheet
        existing_sources_text = str(case.get("Footage Sources", ""))

        # Search (multi-step funnel)
        search_results, telemetry = search_artifacts(
            defendant=defendant,
            jurisdiction=jurisdiction,
            crime_type=crime_type,
            custom_queries=custom_queries,
            region_id=region_id,
            incident_year=incident_year,
            existing_sources_text=existing_sources_text,
            exa=exa,
        )

        # Assess (heuristic first, LLM only when ambiguous)
        assessment = heuristic_assess(search_results)

        if assessment:
            stats["llm_skipped"] += 1
            print(f"    Heuristic: {assessment['overall_assessment']} (LLM skipped)")
        else:
            assessment = assess_artifacts(llm, {
                "defendant": defendant,
                "jurisdiction": jurisdiction,
                "crime_type": crime_type,
            }, search_results)
            stats["llm_calls"] += 1

        if not assessment:
            stats["errors"] += 1
            continue

        overall = assessment.get("overall_assessment", "INSUFFICIENT")
        depth = assessment.get("evidence_depth_score", 0)
        primary = assessment.get("primary_source_score", 0)
        types_found = assessment.get("artifact_types_found", 0)

        # Telemetry line
        telem_str = (f"yt={telemetry['youtube_hits']} vim={telemetry['vimeo_hits']} "
                     f"pse={telemetry['pse_hits']} exa={'Y' if telemetry['exa_fallback_used'] else 'N'} "
                     f"llm={'Y' if assessment.get('notes', '').startswith('Auto') is False else 'N'}")
        print(f"    [{telem_str}]")

        # Write to sheet (unless dry run)
        if dry_run:
            print(f"    {'✅' if overall == 'ENOUGH' else '⚠️' if overall == 'BORDERLINE' else '❌'} "
                  f"{overall} (depth={depth}, primary={primary}, types={types_found})")
            print(f"    [DRY RUN] Would write to row {row_idx}")
            stats["processed"] += 1
            if overall == "ENOUGH":
                stats["enough"] += 1
            elif overall == "BORDERLINE":
                stats["borderline"] += 1
            else:
                stats["insufficient"] += 1
            continue

        try:
            # Columns G-K (7-11)
            ws_anchor.update_cell(row_idx, 7, assessment.get("body_cam_exists", ""))
            ws_anchor.update_cell(row_idx, 8, assessment.get("interrogation_exists", ""))
            ws_anchor.update_cell(row_idx, 9, assessment.get("court_video_exists", ""))

            all_sources = (
                assessment.get("body_cam_sources", []) +
                assessment.get("interrogation_sources", []) +
                assessment.get("court_sources", []) +
                assessment.get("docket_sources", []) +
                assessment.get("dispatch_sources", [])
            )
            ws_anchor.update_cell(row_idx, 10, "\n".join(all_sources[:8]))
            ws_anchor.update_cell(row_idx, 11, overall)

            # Columns L-P (12-16)
            ws_anchor.update_cell(row_idx, 12, assessment.get("docket_exists", ""))
            ws_anchor.update_cell(row_idx, 13, assessment.get("dispatch_911_exists", ""))
            ws_anchor.update_cell(row_idx, 14, str(primary))
            ws_anchor.update_cell(row_idx, 15, str(depth))
            ws_anchor.update_cell(row_idx, 16, assessment.get("notes", ""))

            stats["processed"] += 1
            if overall == "ENOUGH":
                stats["enough"] += 1
                print(f"    ✅ ENOUGH (depth={depth}, primary={primary}, types={types_found})")
            elif overall == "BORDERLINE":
                stats["borderline"] += 1
                print(f"    ⚠️ BORDERLINE (depth={depth}, primary={primary}, types={types_found})")
            else:
                stats["insufficient"] += 1
                print(f"    ❌ INSUFFICIENT (depth={depth}, primary={primary}, types={types_found})")

        except Exception as e:
            print(f"    Sheet update error: {e}")
            stats["errors"] += 1

        time.sleep(1)

    # Report
    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"Processed:      {stats['processed']}")
    print(f"  ENOUGH:       {stats['enough']}")
    print(f"  BORDERLINE:   {stats['borderline']}")
    print(f"  INSUFFICIENT: {stats['insufficient']}")
    print(f"LLM calls:      {stats['llm_calls']}")
    print(f"LLM skipped:    {stats['llm_skipped']} (heuristic)")
    print(f"Errors:         {stats['errors']}")

    return stats

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Artifact Hunter v3")
    parser.add_argument("--limit", type=int, help="Max cases to process")
    parser.add_argument("--check", action="store_true", help="Check credentials only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Search + assess but don't write to sheet")

    args = parser.parse_args()

    if args.check:
        check_credentials()
        return

    run_artifact_hunter(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
