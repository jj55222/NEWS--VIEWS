#!/usr/bin/env python3
"""
NEWS → VIEWS: Artifact Hunter
Searches for bodycam, interrogation, court footage, docket documents,
911 dispatch audio, and primary-source records for PASS cases.

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

from jurisdiction_portals import (
    build_jurisdiction_queries,
    extract_domain,
    get_agency_youtube_channels,
    get_search_domains_for_region,
    get_transparency_portals,
    DISPATCH_DOMAINS,
    RECORDS_DOMAINS,
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


def _is_relevant(title: str, url: str, defendant: str) -> bool:
    """Check if a search result is actually about this defendant.

    The #1 problem with artifact hunting is contamination — generic agency
    videos, unrelated cases, wrong jurisdiction hits. This filter requires
    at least one defendant name token to appear in the title or URL.
    """
    if not defendant:
        return True  # can't filter without a name

    # Use the last name (most distinctive token)
    name_parts = defendant.lower().split()
    target = title.lower() + " " + url.lower()

    # Match if any name part >= 3 chars appears in title/URL
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
    """Search for video artifacts, primary-source documents, and dispatch audio.

    Key improvements over v1:
    - Uses search_and_contents to get actual page text (not just URL/title)
    - Relevance-filters results by defendant name to cut contamination
    - Deduplicates URLs across queries
    - Consolidates overlapping jurisdiction queries to reduce search count
    - Caps results per bucket to limit token spend in LLM assessment
    """
    results = {
        "body_cam": [],
        "interrogation": [],
        "court": [],
        "docket": [],
        "dispatch": [],
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

    year_str = f" {incident_year}" if incident_year else ""

    # Build queries — one focused query per bucket to reduce search count.
    # Jurisdiction-aware queries are merged in rather than added as duplicates.
    queries = []

    if defendant:
        # Bodycam: one broad query covers defendant + bodycam + agency
        queries.append(("body_cam", f'"{defendant}" bodycam OR "body camera" footage released{year_str}', video_domains))

        # Interrogation
        queries.append(("interrogation", f'"{defendant}" interrogation OR "police interview" video{year_str}', video_domains))

        # Court
        queries.append(("court", f'"{defendant}" trial OR sentencing court video{year_str}', video_domains))

        # Docket — target records domains
        queries.append(("docket", f'"{defendant}" probable cause affidavit OR criminal complaint{year_str}', RECORDS_DOMAINS))
        queries.append(("docket", f'"{defendant}" docket OR "case number" OR "arrest affidavit"', RECORDS_DOMAINS))

        # 911 / Dispatch
        queries.append(("dispatch", f'"{defendant}" 911 call OR dispatch audio{year_str}', DISPATCH_DOMAINS + video_domains))

    # Jurisdiction-aware queries (only the most targeted ones)
    if region_id:
        jurisdiction_queries = build_jurisdiction_queries(region_id, defendant, incident_year)
        region_domains = get_search_domains_for_region(region_id)

        # Pick the best jurisdiction-scoped query per bucket (first one)
        for bucket, jq_key, domains in [
            ("body_cam", "bodycam", list(set(video_domains + region_domains))),
            ("docket", "docket", list(set(RECORDS_DOMAINS + region_domains))),
            ("dispatch", "dispatch", list(set(DISPATCH_DOMAINS + video_domains + region_domains))),
        ]:
            jq_list = jurisdiction_queries.get(jq_key, [])
            if jq_list:
                queries.append((bucket, jq_list[0], domains))

        # One agency YouTube channel search
        for channel in get_agency_youtube_channels(region_id)[:1]:
            queries.append((
                "body_cam",
                f'"{defendant}" site:youtube.com {channel.get("name", "")}',
                ["youtube.com"],
            ))

        # Portal searches (capped at 1)
        for portal in get_transparency_portals(region_id)[:1]:
            domain = extract_domain(portal.get("url", ""))
            if domain:
                queries.append(("portal", f'site:{domain} "{defendant}"', [domain]))

        # News (capped at 1)
        for q in jurisdiction_queries.get("news", [])[:1]:
            queries.append(("portal", q, region_domains))

    # Custom queries from intake triage
    for q in (custom_queries or [])[:2]:
        queries.append(("other", q, video_domains))

    # ---- Execute searches with content retrieval ----
    seen_urls = set()
    total_searches = 0

    for qtype, query, include_domains in queries:
        try:
            search_results = exa.search_and_contents(
                query=query,
                type="auto",
                num_results=3,
                text={"max_characters": 800},
                include_domains=include_domains,
            )

            for r in search_results.results:
                url = r.url
                title = getattr(r, 'title', '')

                # Skip duplicate URLs across buckets
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Relevance filter: is this result actually about our defendant?
                if not _is_relevant(title, url, defendant):
                    continue

                snippet = (getattr(r, 'text', '') or '')[:500]

                results[qtype].append({
                    "url": url,
                    "title": title,
                    "score": getattr(r, 'score', 0),
                    "snippet": snippet,
                    "query": query,
                })

            total_searches += 1
            time.sleep(0.3)

        except Exception as e:
            print(f"      Search error ({qtype}): {e}")

    # Reddit + PACER (kept lightweight — search only, no content fetch)
    if defendant or jurisdiction:
        reddit_results = search_reddit_cases(exa, defendant, jurisdiction)
        reddit_hits = reddit_results.get("discussions", [])
        # Relevance-filter Reddit too
        results["reddit"] = [r for r in reddit_hits
                             if _is_relevant(r.get("title", ""), r.get("url", ""), defendant)]

        pacer_results = search_pacer(exa, defendant, jurisdiction)
        pacer_hits = pacer_results.get("sources", [])
        results["pacer"] = [r for r in pacer_hits
                            if _is_relevant(r.get("title", ""), r.get("url", ""), defendant)]

    # Dedup within each bucket
    for key in results:
        results[key] = _dedup_results(results[key])

    # Log search efficiency
    total_relevant = sum(len(v) for v in results.values())
    print(f"    Searches: {total_searches} | Relevant results: {total_relevant} (of {len(seen_urls)} total)")

    return results


def _slim_results(results_list: List[Dict], max_per_bucket: int = 3) -> List[Dict]:
    """Trim results to the most relevant for LLM assessment.

    Keeps only url, title, and snippet (truncated). Removes query/score
    metadata that wastes tokens in the prompt.
    """
    slim = []
    for r in results_list[:max_per_bucket]:
        entry = {"url": r.get("url", ""), "title": r.get("title", "")}
        snippet = r.get("snippet", "")
        if snippet:
            entry["text"] = snippet[:300]
        slim.append(entry)
    return slim


def assess_artifacts(llm, case_info: Dict, search_results: Dict) -> Dict:
    """Use LLM to assess artifact availability with depth metrics.

    Now receives actual page text snippets (not just URLs) so the LLM
    can distinguish primary sources from news coverage.
    """
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

NEWS/PORTAL:
{json.dumps(_slim_results(search_results.get('portal', [])), indent=2)}

REDDIT:
{json.dumps(_slim_results(search_results.get('reddit', [])), indent=2)}

PACER/COURTLISTENER:
{json.dumps(_slim_results(search_results.get('pacer', [])), indent=2)}

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
        
        defendant = str(case.get("Defendant Name(s)", "")).strip()
        jurisdiction = str(case.get("Jurisdiction", "")).strip()
        intake_id = str(case.get("Intake_ID", "")).strip()
        
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
            # Existing columns G-K (cols 7-11)
            ws_anchor.update_cell(row_idx, 7, assessment.get("body_cam_exists", ""))
            ws_anchor.update_cell(row_idx, 8, assessment.get("interrogation_exists", ""))
            ws_anchor.update_cell(row_idx, 9, assessment.get("court_video_exists", ""))

            # Consolidated sources: bodycam + interrogation + court + docket + dispatch
            all_sources = (
                assessment.get("body_cam_sources", []) +
                assessment.get("interrogation_sources", []) +
                assessment.get("court_sources", []) +
                assessment.get("docket_sources", []) +
                assessment.get("dispatch_sources", [])
            )
            ws_anchor.update_cell(row_idx, 10, "\n".join(all_sources[:8]))

            overall = assessment.get("overall_assessment", "INSUFFICIENT")
            ws_anchor.update_cell(row_idx, 11, overall)

            # New columns L-P (cols 12-16) — appended, never shift existing
            ws_anchor.update_cell(row_idx, 12, assessment.get("docket_exists", ""))
            ws_anchor.update_cell(row_idx, 13, assessment.get("dispatch_911_exists", ""))
            ws_anchor.update_cell(row_idx, 14, str(assessment.get("primary_source_score", 0)))
            ws_anchor.update_cell(row_idx, 15, str(assessment.get("evidence_depth_score", 0)))
            ws_anchor.update_cell(row_idx, 16, assessment.get("notes", ""))

            depth = assessment.get("evidence_depth_score", 0)
            primary = assessment.get("primary_source_score", 0)
            types_found = assessment.get("artifact_types_found", 0)

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
