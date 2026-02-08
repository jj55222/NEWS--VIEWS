#!/usr/bin/env python3
"""
NEWS → VIEWS: Artifact Hunter
Searches for body cam, interrogation, and court footage for PASS cases

Usage:
    python artifact_hunter.py              # Process all unassessed cases
    python artifact_hunter.py --test       # Test mode (3 cases)
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

from jurisdiction_portals import (
    build_jurisdiction_queries,
    extract_domain,
    get_agency_youtube_channels,
    get_search_domains_for_region,
    get_transparency_portals,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

SHEET_ID = os.getenv("SHEET_ID")
EXA_API_KEY = os.getenv("EXA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v3.2")
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

def extract_subreddit(url: str) -> str:
    """Extract subreddit name from a Reddit URL."""
    if not url:
        return ""
    match = re.search(r"reddit\.com/r/([^/]+)", url)
    return match.group(1) if match else ""


def check_for_video_links(text: str) -> bool:
    """Check if text mentions video platforms."""
    if not text:
        return False
    platforms = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "facebook.com")
    return any(platform in text.lower() for platform in platforms)


def search_reddit_cases(exa, defendant: str, jurisdiction: str) -> Dict:
    """Search Reddit true crime communities for case discussion."""
    results = {"discussions": [], "ama": [], "updates": []}

    queries = [
        f"site:reddit.com {defendant} case",
        f"site:reddit.com {jurisdiction} murder {defendant}",
    ]

    for query in queries:
        try:
            search_results = exa.search(query=query, num_results=10)
        except Exception as e:
            print(f"      Reddit search error: {e}")
            continue

        for r in search_results.results:
            post_data = {
                "url": r.url,
                "title": getattr(r, "title", ""),
                "subreddit": extract_subreddit(r.url),
                "has_video_links": check_for_video_links(getattr(r, "text", "")),
                "upvotes": None,
            }
            results["discussions"].append(post_data)

    return results


def search_pacer(exa, defendant: str, jurisdiction: str, case_type: str = "cr") -> Dict:
    """Search federal court records via CourtListener (free PACER data)."""
    query = f"site:courtlistener.com {defendant} {jurisdiction} {case_type}"
    case_data = {
        "case_number": "",
        "court": "",
        "judge": "",
        "filing_date": "",
        "docket_entries": [],
        "has_transcripts": False,
        "has_exhibits": False,
        "sources": [],
    }

    try:
        results = exa.search(query=query, num_results=10)
    except Exception as e:
        print(f"      PACER search error: {e}")
        return case_data

    for r in results.results:
        case_data["sources"].append({
            "url": r.url,
            "title": getattr(r, "title", ""),
            "score": getattr(r, "score", 0),
        })

    return case_data


def search_artifacts(exa, defendant: str, jurisdiction: str,
                     crime_type: str = "", custom_queries: List[str] = None,
                     region_id: str = None, incident_year: str = None) -> Dict:
    """Search for video artifacts."""
    results = {
        "body_cam": [],
        "interrogation": [],
        "court": [],
        "other": [],
        "portal": [],
        "reddit": [],
        "pacer": [],
    }
    
    defendant = defendant.split(",")[0].strip() if defendant else ""
    jurisdiction = jurisdiction.strip() if jurisdiction else ""
    
    if not defendant and not jurisdiction:
        return results
    
    video_domains = [
        "youtube.com", "vimeo.com", "youtu.be", "facebook.com", "twitter.com"
    ]
    queries = []
    
    # Body cam
    if jurisdiction:
        queries.append(("body_cam", f"{jurisdiction} police body camera footage", video_domains))
        queries.append(("body_cam", f"{jurisdiction} bodycam video incident", video_domains))
    
    # Interrogation
    if defendant:
        queries.append(("interrogation", f"{defendant} interrogation video police interview", video_domains))
        queries.append(("interrogation", f"{defendant} confession interview recording", video_domains))
    
    # Court
    if defendant:
        queries.append(("court", f"{defendant} court video trial sentencing", video_domains))
    
    # Custom queries
    for q in (custom_queries or [])[:3]:
        queries.append(("other", q, video_domains))

    # Jurisdiction-aware queries
    if region_id:
        jurisdiction_queries = build_jurisdiction_queries(region_id, defendant, incident_year)
        region_domains = get_search_domains_for_region(region_id)
        for q in jurisdiction_queries.get("bodycam", []):
            queries.append(("body_cam", q, list(set(video_domains + region_domains))))
        for q in jurisdiction_queries.get("interrogation", []):
            queries.append(("interrogation", q, list(set(video_domains + region_domains))))
        for q in jurisdiction_queries.get("court", []):
            queries.append(("court", q, list(set(video_domains + region_domains))))
        for q in jurisdiction_queries.get("news", []):
            queries.append(("portal", q, region_domains))

        for channel in get_agency_youtube_channels(region_id)[:3]:
            queries.append((
                "body_cam",
                f"{defendant} site:youtube.com {channel.get('name', '')}",
                ["youtube.com"],
            ))

        for portal in get_transparency_portals(region_id):
            domain = extract_domain(portal.get("url", ""))
            if domain:
                portal_query = f"site:{domain} {defendant} video"
                queries.append(("portal", portal_query, [domain]))
    
    # Execute searches
    for qtype, query, include_domains in queries:
        try:
            search_results = exa.search(
                query=query,
                type="auto",
                use_autoprompt=True,
                num_results=5,
                include_domains=include_domains,
            )
            
            for r in search_results.results:
                results[qtype].append({
                    "url": r.url,
                    "title": getattr(r, 'title', ''),
                    "score": getattr(r, 'score', 0),
                    "query": query
                })
            
            time.sleep(0.3)
            
        except Exception as e:
            print(f"      Search error: {e}")

    if defendant or jurisdiction:
        reddit_results = search_reddit_cases(exa, defendant, jurisdiction)
        results["reddit"] = reddit_results.get("discussions", [])

        pacer_results = search_pacer(exa, defendant, jurisdiction)
        results["pacer"] = pacer_results.get("sources", [])
    
    return results


def assess_artifacts(llm, case_info: Dict, search_results: Dict) -> Dict:
    """Use LLM to assess artifact availability."""
    prompt = f"""Assess whether video artifacts exist for this case:

CASE:
- Defendant: {case_info.get('defendant', 'Unknown')}
- Jurisdiction: {case_info.get('jurisdiction', 'Unknown')}
- Crime: {case_info.get('crime_type', 'Unknown')}

SEARCH RESULTS:
Body Cam: {json.dumps(search_results.get('body_cam', [])[:5], indent=2)}
Interrogation: {json.dumps(search_results.get('interrogation', [])[:5], indent=2)}
Court: {json.dumps(search_results.get('court', [])[:5], indent=2)}
Portal/Local News: {json.dumps(search_results.get('portal', [])[:5], indent=2)}
Reddit: {json.dumps(search_results.get('reddit', [])[:5], indent=2)}
PACER/CourtListener: {json.dumps(search_results.get('pacer', [])[:5], indent=2)}

Based on URLs and titles, return JSON:
{{
    "body_cam_exists": "YES/MAYBE/NO",
    "body_cam_sources": ["url1"],
    "interrogation_exists": "YES/MAYBE/NO",
    "interrogation_sources": ["url1"],
    "court_video_exists": "YES/MAYBE/NO",
    "court_sources": ["url1"],
    "overall_assessment": "ENOUGH/BORDERLINE/INSUFFICIENT",
    "notes": "Brief explanation"
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
        if case.get("Footage Assessment", "").strip():
            continue
        
        if limit and stats["processed"] >= limit:
            print(f"\n[LIMIT] Reached {limit} cases")
            break
        
        defendant = case.get("Defendant Name(s)", "").strip()
        jurisdiction = case.get("Jurisdiction", "").strip()
        intake_id = case.get("Intake_ID", "").strip()
        
        print(f"\n[{row_idx}] {defendant[:40]}...")
        print(f"    Jurisdiction: {jurisdiction}")
        
        # Get custom queries from intake
        custom_queries = []
        crime_type = ""
        region_id = ""
        incident_year = ""
        if intake_id and intake_id in intake_by_id:
            intake_row = intake_by_id[intake_id]
            queries_str = intake_row.get("Artifact Queries", "")
            if queries_str:
                custom_queries = [q.strip() for q in queries_str.split("|") if q.strip()]
            crime_type = intake_row.get("Crime Type", "")
            region_id = (
                intake_row.get("Region_ID")
                or intake_row.get("Region ID")
                or intake_row.get("Region")
                or ""
            )
            triage_json = intake_row.get("Triage JSON") or intake_row.get("Triage") or ""
            if triage_json:
                try:
                    triage = json.loads(triage_json)
                    incident_year = triage.get("incident_year", "")
                except json.JSONDecodeError:
                    incident_year = ""
        
        # Search
        search_results = search_artifacts(
            exa,
            defendant,
            jurisdiction,
            crime_type,
            custom_queries,
            region_id=region_id,
            incident_year=incident_year,
        )
        total = sum(len(v) for v in search_results.values())
        print(f"    Found {total} potential sources")
        
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
    parser.add_argument("--test", action="store_true", help="Test mode (3 cases)")
    parser.add_argument("--limit", type=int, help="Max cases to process")
    parser.add_argument("--check", action="store_true", help="Check credentials only")

    args = parser.parse_args()

    if args.check:
        check_credentials()
        return

    effective_limit = args.limit
    if args.test:
        effective_limit = 3
        print("[TEST] Test mode — processing max 3 cases")

    run_artifact_hunter(limit=effective_limit)


if __name__ == "__main__":
    main()
