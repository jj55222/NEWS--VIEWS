#!/usr/bin/env python3
"""
NEWS → VIEWS: Artifact Hunter
Searches for body cam, interrogation, and court footage for PASS cases

Usage:
    python artifact_hunter.py              # Process all unassessed cases
    python artifact_hunter.py --limit 5    # Process max 5 cases
    python artifact_hunter.py --check      # Check credentials only
"""

import os
import re
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

# Import jurisdiction-specific portal configuration
try:
    from jurisdiction_portals import (
        get_jurisdiction_config,
        get_search_domains_for_region,
        get_agency_youtube_channels,
        get_transparency_portals,
        build_jurisdiction_queries,
        is_florida_case,
        has_court_video,
        TRUE_CRIME_CHANNELS,
        JURISDICTION_PORTALS,
    )
    HAS_JURISDICTION_DATA = True
except ImportError:
    HAS_JURISDICTION_DATA = False
    print("⚠️  jurisdiction_portals.py not found - using generic searches")

# =============================================================================
# CONFIGURATION
# =============================================================================

SHEET_ID = os.getenv("SHEET_ID")
EXA_API_KEY = os.getenv("EXA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json")

# =============================================================================
# VALIDATION
# =============================================================================

def check_credentials() -> bool:
    errors = []
    if not SHEET_ID:
        errors.append("SHEET_ID not set")
    if not EXA_API_KEY:
        errors.append("EXA_API_KEY not set")
    if not OPENROUTER_API_KEY:
        errors.append("OPENROUTER_API_KEY not set")
    if not Path(SERVICE_ACCOUNT_PATH).exists():
        errors.append(f"Service account not found: {SERVICE_ACCOUNT_PATH}")
    
    if errors:
        print("❌ Configuration errors:")
        for e in errors:
            print(f"   - {e}")
        return False
    
    print("✅ Credentials OK")
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
    from exa_py import Exa
    return Exa(api_key=EXA_API_KEY)


def get_llm_client():
    from openai import OpenAI
    return OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

# =============================================================================
# ARTIFACT SEARCH
# =============================================================================

def search_artifacts(exa, defendant: str, jurisdiction: str,
                     crime_type: str = "", custom_queries: List[str] = None,
                     region_id: str = None, incident_year: str = None) -> Dict:
    """Search for video artifacts using jurisdiction-specific sources."""
    results = {
        "body_cam": [],
        "interrogation": [],
        "court": [],
        "news": [],
        "true_crime_coverage": [],
        "other": []
    }

    defendant = defendant.split(",")[0].strip() if defendant else ""
    jurisdiction = str(jurisdiction).strip() if jurisdiction else ""

    if not defendant and not jurisdiction:
        return results

    # Use jurisdiction-specific queries if available
    if HAS_JURISDICTION_DATA and region_id:
        queries = _build_jurisdiction_specific_queries(
            region_id, defendant, jurisdiction, incident_year, custom_queries
        )
    else:
        queries = _build_generic_queries(defendant, jurisdiction, custom_queries)

    # Execute all searches
    for qtype, query, domains in queries:
        try:
            search_kwargs = {
                "query": query,
                "type": "auto",
                "num_results": 5,
            }

            # Add domain filter if specified
            if domains:
                search_kwargs["include_domains"] = domains

            search_results = exa.search(**search_kwargs)

            for r in search_results.results:
                results[qtype].append({
                    "url": r.url,
                    "title": getattr(r, 'title', ''),
                    "score": getattr(r, 'score', 0),
                    "query": query,
                    "source_type": _classify_source(r.url)
                })

            time.sleep(0.3)

        except Exception as e:
            print(f"      Search error: {e}")

    return results


def _build_jurisdiction_specific_queries(region_id: str, defendant: str,
                                          jurisdiction: str, incident_year: str,
                                          custom_queries: List[str]) -> List[tuple]:
    """Build targeted queries using jurisdiction portal data."""
    queries = []
    config = get_jurisdiction_config(region_id)

    if not config:
        return _build_generic_queries(defendant, jurisdiction, custom_queries)

    # Get agency info
    agencies = config.get("agencies", [])
    agency_abbrevs = [a.get("abbrev", a["name"]) for a in agencies]
    primary_agency = agency_abbrevs[0] if agency_abbrevs else ""

    year_suffix = f" {incident_year}" if incident_year else ""

    # VIDEO PLATFORMS for bodycam/interrogation/court
    video_domains = ["youtube.com", "vimeo.com", "youtu.be"]

    # 1. BODYCAM SEARCHES - Agency-specific
    if primary_agency:
        queries.append(("body_cam",
            f"{primary_agency} bodycam {defendant}{year_suffix}",
            video_domains))
        queries.append(("body_cam",
            f"{defendant} body camera {primary_agency} footage",
            video_domains))

    # Check if agency has YouTube channel - search there specifically
    agency_channels = get_agency_youtube_channels(region_id)
    if agency_channels:
        queries.append(("body_cam",
            f"{defendant} site:youtube.com bodycam OR \"body camera\"",
            None))  # No domain filter, using site: in query

    # Florida cases - stronger public records, more likely to have footage
    if is_florida_case(region_id):
        queries.append(("body_cam",
            f"{defendant} Florida bodycam released",
            video_domains))

    # 2. INTERROGATION SEARCHES
    queries.append(("interrogation",
        f"{defendant} interrogation interview",
        video_domains))
    queries.append(("interrogation",
        f"{defendant} police interview confession",
        video_domains))

    # Search true crime channels that feature interrogations
    queries.append(("interrogation",
        f"{defendant} interrogation site:youtube.com JCS OR \"Matt Orchard\" OR Dreading",
        None))

    # 3. COURT VIDEO SEARCHES
    queries.append(("court",
        f"{defendant} trial court video",
        video_domains))
    queries.append(("court",
        f"{defendant} sentencing hearing verdict",
        video_domains))

    # If jurisdiction has court video, search specifically
    if has_court_video(region_id):
        state = config.get("state", "")
        queries.append(("court",
            f"{defendant} {state} trial Law Crime Network OR Court TV",
            video_domains))

    # 4. NEWS SEARCHES - Local outlets
    news_domains = config.get("search_domains", [])
    if news_domains and defendant:
        queries.append(("news",
            f"{defendant} arrest charged",
            news_domains[:3]))  # Top 3 local news sites

    # 5. TRUE CRIME COVERAGE CHECK
    queries.append(("true_crime_coverage",
        f"{defendant} true crime documentary",
        video_domains))
    queries.append(("true_crime_coverage",
        f"{defendant} case explained analysis",
        video_domains))

    # 6. CUSTOM QUERIES
    for q in (custom_queries or [])[:3]:
        queries.append(("other", q, video_domains))

    return queries


def _build_generic_queries(defendant: str, jurisdiction: str,
                           custom_queries: List[str]) -> List[tuple]:
    """Fallback generic queries when jurisdiction data unavailable."""
    queries = []
    video_domains = ["youtube.com", "vimeo.com", "youtu.be", "facebook.com", "twitter.com"]

    if jurisdiction:
        queries.append(("body_cam", f"{jurisdiction} police body camera footage", video_domains))
        queries.append(("body_cam", f"{jurisdiction} bodycam video incident", video_domains))

    if defendant:
        queries.append(("interrogation", f"{defendant} interrogation video police interview", video_domains))
        queries.append(("interrogation", f"{defendant} confession interview recording", video_domains))
        queries.append(("court", f"{defendant} court video trial sentencing", video_domains))

    for q in (custom_queries or [])[:3]:
        queries.append(("other", q, video_domains))

    return queries


def _classify_source(url: str) -> str:
    """Classify the source type from URL."""
    url_lower = url.lower()

    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        # Check for official channels
        if any(ch in url_lower for ch in ["policeactivity", "lawcrime", "courttv"]):
            return "official_channel"
        return "youtube"
    elif "vimeo.com" in url_lower:
        return "vimeo"
    elif any(news in url_lower for news in [".gov", "police.org", "sheriff"]):
        return "official_govt"
    elif any(news in url_lower for news in ["news", "chronicle", "times", "post"]):
        return "news_outlet"
    else:
        return "other"


def search_transparency_portals(region_id: str, defendant: str) -> List[Dict]:
    """Search transparency portals for a region (returns portal URLs to check manually)."""
    if not HAS_JURISDICTION_DATA:
        return []

    portals = get_transparency_portals(region_id)
    results = []

    for portal in portals:
        results.append({
            "agency": portal["name"],
            "portal_type": portal["type"],
            "url": portal["url"],
            "search_suggestion": f"Search for: {defendant}",
            "notes": "Manual search required - FOIA portals don't allow API access"
        })

    return results


def check_existing_coverage(exa, defendant: str) -> Dict:
    """Check if case already has true crime coverage."""
    coverage = {
        "has_documentary": False,
        "has_podcast": False,
        "has_youtube_coverage": False,
        "coverage_sources": []
    }

    try:
        # Check for documentary/series coverage
        doc_results = exa.search(
            query=f"{defendant} documentary Netflix Hulu true crime series",
            type="auto",
            num_results=5
        )

        for r in doc_results.results:
            if any(kw in r.url.lower() for kw in ["netflix", "hulu", "hbo", "documentary"]):
                coverage["has_documentary"] = True
                coverage["coverage_sources"].append({
                    "type": "documentary",
                    "url": r.url,
                    "title": getattr(r, 'title', '')
                })

        # Check for podcast coverage
        pod_results = exa.search(
            query=f"{defendant} podcast episode true crime",
            type="auto",
            num_results=5,
            include_domains=["spotify.com", "apple.com", "podbean.com", "stitcher.com"]
        )

        if pod_results.results:
            coverage["has_podcast"] = True
            for r in pod_results.results:
                coverage["coverage_sources"].append({
                    "type": "podcast",
                    "url": r.url,
                    "title": getattr(r, 'title', '')
                })

        # Check YouTube true crime channels
        yt_results = exa.search(
            query=f"{defendant} site:youtube.com true crime case",
            type="auto",
            num_results=10
        )

        if len(yt_results.results) > 3:
            coverage["has_youtube_coverage"] = True
            for r in yt_results.results[:5]:
                coverage["coverage_sources"].append({
                    "type": "youtube",
                    "url": r.url,
                    "title": getattr(r, 'title', '')
                })

        time.sleep(0.5)

    except Exception as e:
        print(f"      Coverage check error: {e}")

    return coverage


def assess_artifacts(llm, case_info: Dict, search_results: Dict) -> Dict:
    """Use LLM to assess artifact availability."""

    # Prepare search results summary
    body_cam_results = search_results.get('body_cam', [])[:5]
    interrogation_results = search_results.get('interrogation', [])[:5]
    court_results = search_results.get('court', [])[:5]
    news_results = search_results.get('news', [])[:3]
    coverage_results = search_results.get('true_crime_coverage', [])[:3]

    prompt = f"""Assess whether video artifacts exist for this TRUE CRIME case.

CASE:
- Defendant: {case_info.get('defendant', 'Unknown')}
- Jurisdiction: {case_info.get('jurisdiction', 'Unknown')}
- Crime Type: {case_info.get('crime_type', 'Unknown')}

SEARCH RESULTS:

BODY CAM / DASH CAM:
{json.dumps(body_cam_results, indent=2) if body_cam_results else "No results found"}

INTERROGATION / INTERVIEW:
{json.dumps(interrogation_results, indent=2) if interrogation_results else "No results found"}

COURT / TRIAL VIDEO:
{json.dumps(court_results, indent=2) if court_results else "No results found"}

NEWS COVERAGE:
{json.dumps(news_results, indent=2) if news_results else "No results found"}

EXISTING TRUE CRIME COVERAGE:
{json.dumps(coverage_results, indent=2) if coverage_results else "No results found"}

ASSESSMENT INSTRUCTIONS:
1. Check if URLs/titles actually match this specific defendant and case
2. "YES" = Confident this is footage of THIS case
3. "MAYBE" = Could be related but needs verification
4. "NO" = No relevant footage found
5. Prioritize official sources (police departments, courts, news outlets)

Return JSON:
{{
    "body_cam_exists": "YES/MAYBE/NO",
    "body_cam_sources": ["url1", "url2"],
    "body_cam_confidence": "Explanation of why you think this is/isn't the right case",
    "interrogation_exists": "YES/MAYBE/NO",
    "interrogation_sources": ["url1"],
    "interrogation_confidence": "Explanation",
    "court_video_exists": "YES/MAYBE/NO",
    "court_sources": ["url1"],
    "court_confidence": "Explanation",
    "has_existing_coverage": true/false,
    "coverage_competition": "NONE/LOW/MEDIUM/HIGH",
    "overall_assessment": "ENOUGH/BORDERLINE/INSUFFICIENT",
    "content_potential": "Brief note on content creation viability",
    "notes": "Key observations about available evidence"
}}

JSON only:"""

    try:
        response = llm.chat.completions.create(
            model=OPENROUTER_MODEL,
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

def run_artifact_hunter(limit: int = None):
    """Hunt for artifacts for cases in CASE ANCHOR."""
    print("=" * 60)
    print("NEWS → VIEWS: Artifact Hunter")
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
    
    stats = {"processed": 0, "enough": 0, "borderline": 0, "insufficient": 0, "errors": 0}
    
    for row_idx, case in enumerate(cases, start=2):
        # Skip already assessed
        if str(case.get("Footage Assessment", "")).strip():
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
            region_id = str(intake_row.get("Region_ID", "")).strip()
            # Try to extract year from publication date or incident
            pub_year = str(intake_row.get("Pub_Year", "")).strip()
            if pub_year and pub_year.isdigit():
                incident_year = pub_year

        # Show jurisdiction-specific info if available
        if HAS_JURISDICTION_DATA and region_id:
            config = get_jurisdiction_config(region_id)
            if config:
                print(f"    Region: {region_id} ({config.get('name', 'Unknown')}, {config.get('state', '')})")

                # Show transparency portals for manual checking
                portals = get_transparency_portals(region_id)
                if portals:
                    print(f"    📁 Transparency portals to check:")
                    for p in portals[:2]:
                        print(f"       - {p['name']}: {p['url']}")

        # Search with jurisdiction-specific queries
        search_results = search_artifacts(
            exa, defendant, jurisdiction, crime_type, custom_queries,
            region_id=region_id, incident_year=incident_year
        )
        total = sum(len(v) for v in search_results.values())
        print(f"    Found {total} potential sources")

        # Show breakdown by type
        for stype, sresults in search_results.items():
            if sresults:
                print(f"      - {stype}: {len(sresults)} results")
        
        # Assess
        assessment = assess_artifacts(llm, {
            "defendant": defendant,
            "jurisdiction": jurisdiction,
            "crime_type": crime_type
        }, search_results)
        
        if not assessment:
            stats["errors"] += 1
            continue
        
        # Update sheet
        try:
            ws_anchor.update_cell(row_idx, 7, assessment.get("body_cam_exists", ""))
            ws_anchor.update_cell(row_idx, 8, assessment.get("interrogation_exists", ""))
            ws_anchor.update_cell(row_idx, 9, assessment.get("court_video_exists", ""))
            
            all_sources = (
                assessment.get("body_cam_sources", []) +
                assessment.get("interrogation_sources", []) +
                assessment.get("court_sources", [])
            )
            ws_anchor.update_cell(row_idx, 10, "\n".join(all_sources[:5]))
            
            overall = assessment.get("overall_assessment", "INSUFFICIENT")
            ws_anchor.update_cell(row_idx, 11, overall)
            
            stats["processed"] += 1
            if overall == "ENOUGH":
                stats["enough"] += 1
                print(f"    ✅ ENOUGH")
            elif overall == "BORDERLINE":
                stats["borderline"] += 1
                print(f"    ⚠️ BORDERLINE")
            else:
                stats["insufficient"] += 1
                print(f"    ❌ INSUFFICIENT")
                
        except Exception as e:
            print(f"    Sheet update error: {e}")
            stats["errors"] += 1
        
        time.sleep(1)
    
    # Report
    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"Processed:    {stats['processed']}")
    print(f"  ENOUGH:     {stats['enough']}")
    print(f"  BORDERLINE: {stats['borderline']}")
    print(f"  INSUFFICIENT: {stats['insufficient']}")
    print(f"Errors:       {stats['errors']}")
    
    return stats

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Artifact Hunter")
    parser.add_argument("--limit", type=int, help="Max cases to process")
    parser.add_argument("--check", action="store_true", help="Check credentials only")
    
    args = parser.parse_args()
    
    if args.check:
        check_credentials()
        return
    
    run_artifact_hunter(limit=args.limit)


if __name__ == "__main__":
    main()
