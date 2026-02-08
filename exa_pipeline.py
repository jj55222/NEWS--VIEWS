#!/usr/bin/env python3
"""
NEWS → VIEWS: Exa-Powered News Intake Pipeline
Optimized for Claude Code autonomous execution

Usage:
    python exa_pipeline.py                    # Run full pipeline
    python exa_pipeline.py --test             # Test mode (3 regions)
    python exa_pipeline.py --region SF        # Single region
    python exa_pipeline.py --limit 10         # Max articles to triage
    python exa_pipeline.py --check            # Check credentials only
"""

import os
import re
import json
import time
import argparse
import datetime as dt
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# CONFIGURATION (from environment)
# =============================================================================

SHEET_ID = os.getenv("SHEET_ID")
EXA_API_KEY = os.getenv("EXA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Optional overrides
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v3.2")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json")

# Search defaults
DEFAULT_START_DATE = os.getenv("DEFAULT_START_DATE", "2018-01-01")
DEFAULT_END_DATE = os.getenv("DEFAULT_END_DATE", "2023-06-01")
MAX_RESULTS_PER_REGION = int(os.getenv("MAX_RESULTS_PER_REGION", "30"))
MIN_ARTICLE_LENGTH = int(os.getenv("MIN_ARTICLE_LENGTH", "500"))

# Test mode regions
TEST_REGIONS = ["SF", "MD", "PPD"]

# =============================================================================
# VALIDATION
# =============================================================================

def check_credentials() -> bool:
    """Validate all required credentials are present."""
    errors = []
    
    if not SHEET_ID:
        errors.append("SHEET_ID not set in .env")
    if not EXA_API_KEY:
        errors.append("EXA_API_KEY not set in .env")
    if not OPENROUTER_API_KEY:
        errors.append("OPENROUTER_API_KEY not set in .env")
    if not Path(SERVICE_ACCOUNT_PATH).exists():
        errors.append(f"Service account file not found: {SERVICE_ACCOUNT_PATH}")
    
    if errors:
        print("❌ Configuration errors:")
        for e in errors:
            print(f"   - {e}")
        print("\nCreate a .env file with:")
        print("   SHEET_ID=your-google-sheet-id")
        print("   EXA_API_KEY=your-exa-api-key")
        print("   OPENROUTER_API_KEY=your-openrouter-key")
        return False
    
    print("✅ All credentials found")
    return True

# =============================================================================
# LAZY IMPORTS (only import when needed, better error messages)
# =============================================================================

def get_gspread_client():
    """Initialize Google Sheets client."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("❌ Missing dependency. Run: pip install gspread google-auth")
        raise
    
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
    return gspread.authorize(creds)


def get_exa_client():
    """Initialize Exa client."""
    try:
        from exa_py import Exa
    except ImportError:
        print("❌ Missing dependency. Run: pip install exa-py")
        raise
    
    return Exa(api_key=EXA_API_KEY)


def get_llm_client():
    """Initialize OpenRouter client."""
    try:
        from openai import OpenAI
    except ImportError:
        print("❌ Missing dependency. Run: pip install openai")
        raise
    
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

# =============================================================================
# EXA SEARCH
# =============================================================================

def build_exa_query(metro_tokens: str) -> str:
    """Build semantic search query for Exa."""
    metros = [m.strip() for m in metro_tokens.split("|") if m.strip()]
    primary_metro = metros[0] if metros else "local"
    
    return f"""
    {primary_metro} criminal case court documents defendant sentenced prison 
    heinous crime murder abuse neglect charged convicted guilty plea sentencing
    """.strip()


def search_region(exa, region_id: str, metro_tokens: str, 
                  start_date: str, end_date: str, max_results: int) -> List[Dict]:
    """Search Exa for crime articles in a region."""
    query = build_exa_query(metro_tokens)
    
    print(f"\n[{region_id}] Searching...")
    print(f"   Query: {query[:60]}...")
    print(f"   Dates: {start_date} to {end_date}")
    
    try:
        results = exa.search_and_contents(
            query=query,
            type="auto",
            start_published_date=start_date,
            end_published_date=end_date,
            num_results=max_results,
            text={"max_characters": 15000},
        )
        
        articles = []
        for r in results.results:
            text = getattr(r, 'text', '') or ''
            if len(text) < MIN_ARTICLE_LENGTH:
                continue
            
            articles.append({
                "url": r.url,
                "title": getattr(r, 'title', ''),
                "text": text,
                "published_date": getattr(r, 'published_date', ''),
                "score": getattr(r, 'score', 0),
            })
        
        print(f"   Found {len(articles)} articles")
        return articles
        
    except Exception as e:
        print(f"   ❌ Exa error: {e}")
        return []

# =============================================================================
# LLM TRIAGE
# =============================================================================

ENHANCED_TRIAGE_SCHEMA = {
    "story_summary": "",
    "why_disturbing": "",
    "crime_type": "",
    "jurisdiction": {"city": "", "county": "", "state": ""},
    "incident_year": "",
    "defendant_names": [],
    "victim_names": [],
    "victim_roles": [],
    "case_identifiers": {
        "case_number": "",
        "incident_date": "",
        "arrest_date": "",
        "agency": "",
        "agency_abbrev": "",
    },
    "incident_location": "",
    "incident_type": "",
    "footage_indicators": {
        "bodycam_likely": False,
        "dashcam_likely": False,
        "surveillance_likely": False,
        "interrogation_mentioned": False,
        "trial_televised": False,
    },
    "viability_score": 0,
    "verdict": "KILL",
    "kill_reason": "",
    "artifact_queries": [
        "{defendant} {agency} bodycam {year}",
        "{case_number} court video",
        "{victim} {defendant} trial",
    ],
}

TRIAGE_SCHEMA = ENHANCED_TRIAGE_SCHEMA

TRIAGE_PROMPT = """You are triaging crime news for a true crime content creator.

PASS if the story is:
- Genuinely heinous or disturbing
- Has clear wrongdoing and moral abnormality
- Features betrayal, authority abuse, or prolonged suffering

KILL if the story is:
- Routine crime (random robbery, accidents)
- Too recent (ongoing investigation)
- Not morally compelling

TITLE: {title}
TEXT: {text}

Return JSON matching this schema:
{schema}

Be conservative. When in doubt, KILL.
JSON only:"""


def triage_article(llm, title: str, text: str) -> Dict:
    """Run LLM triage on article."""
    prompt = TRIAGE_PROMPT.format(
        title=title,
        text=text[:12000],
        schema=json.dumps(TRIAGE_SCHEMA, indent=2)
    )
    
    try:
        response = llm.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            extra_headers={
                "HTTP-Referer": "https://newstoviews.app",
                "X-Title": "NewsToViews-Pipeline",
            }
        )
        
        content = response.choices[0].message.content.strip()
        
        # Extract JSON from possible markdown wrapper
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        return json.loads(content)
        
    except json.JSONDecodeError as e:
        print(f"      JSON parse error: {e}")
        return {}
    except Exception as e:
        print(f"      LLM error: {e}")
        return {}

# =============================================================================
# SHEETS OPERATIONS
# =============================================================================

def get_existing_urls(ws_intake) -> set:
    """Get URLs already in NEWS INTAKE."""
    try:
        records = ws_intake.get_all_records()
        return {r.get("Article URL", "").strip() for r in records if r.get("Article URL")}
    except Exception:
        return set()


def append_intake_row(ws_intake, region_id: str, article: Dict, triage: Dict) -> bool:
    """Append row to NEWS INTAKE."""
    try:
        pub_year = ""
        if article.get("published_date"):
            match = re.search(r"(20\d{2})", article["published_date"])
            if match:
                pub_year = match.group(1)
        
        url = article.get("url", "")
        outlet = ""
        if url:
            match = re.search(r"https?://(?:www\.)?([^/]+)", url)
            if match:
                outlet = match.group(1)
        
        row = [
            region_id,
            outlet,
            article.get("title", "")[:200],
            url,
            pub_year,
            json.dumps(triage) if triage else "",
            triage.get("story_summary", ""),
            triage.get("why_disturbing", ""),
            triage.get("crime_type", ""),
            triage.get("verdict", "KILL"),
            "",
            triage.get("verdict", "KILL"),
            str(triage.get("viability_score", 0)),
            "|".join(triage.get("artifact_queries", [])),
        ]
        
        ws_intake.append_row(row, value_input_option="RAW")
        return True
        
    except Exception as e:
        print(f"      Sheet error: {e}")
        return False


def promote_to_anchor(ws_anchor, region_id: str, article: Dict, 
                      triage: Dict, intake_row: int) -> bool:
    """Copy PASS case to CASE ANCHOR."""
    try:
        jurisdiction = triage.get("jurisdiction", {})
        
        row = [
            "",
            str(intake_row),
            ", ".join(triage.get("defendant_names", [])),
            ", ".join(triage.get("victim_roles", [])),
            "",
            f"{jurisdiction.get('city', '')}, {jurisdiction.get('county', '')}, {jurisdiction.get('state', '')}",
            "", "", "", "", "",
        ]
        
        ws_anchor.append_row(row, value_input_option="RAW")
        return True
        
    except Exception as e:
        print(f"      Anchor error: {e}")
        return False

# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_pipeline(test_mode: bool = False, single_region: str = None, limit: int = None):
    """Main pipeline execution."""
    print("=" * 60)
    print("NEWS → VIEWS: Exa Pipeline")
    print("=" * 60)
    
    # Check credentials
    if not check_credentials():
        return {"error": "Invalid credentials"}
    
    # Initialize clients
    print("\n[INIT] Connecting to APIs...")
    try:
        gc = get_gspread_client()
        exa = get_exa_client()
        llm = get_llm_client()
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        return {"error": str(e)}
    
    # Open spreadsheet
    print(f"[INIT] Opening sheet: {SHEET_ID}")
    try:
        sh = gc.open_by_key(SHEET_ID)
        print(f"   Title: {sh.title}")
    except Exception as e:
        print(f"❌ Sheet error: {e}")
        print("   Check that the service account has Editor access to the sheet")
        return {"error": str(e)}
    
    ws_regions = sh.worksheet("Regions & Sources")
    ws_intake = sh.worksheet("NEWS INTAKE")
    ws_anchor = sh.worksheet("CASE ANCHOR & FOOTAGE CHECK")
    
    # Get existing URLs
    existing_urls = get_existing_urls(ws_intake)
    print(f"[INIT] {len(existing_urls)} existing articles")
    
    # Load regions
    regions = ws_regions.get_all_records()
    print(f"[INIT] {len(regions)} regions configured")
    
    # Filter regions
    if single_region:
        regions = [r for r in regions if r.get("Region_ID") == single_region]
        print(f"[FILTER] Single region: {single_region}")
    elif test_mode:
        regions = [r for r in regions if r.get("Region_ID") in TEST_REGIONS]
        print(f"[TEST] Processing: {TEST_REGIONS}")
    
    if not regions:
        print("❌ No regions to process")
        return {"error": "No regions found"}
    
    # Stats
    stats = {
        "regions": 0, "articles": 0, "triaged": 0,
        "passed": 0, "killed": 0, "skipped": 0, "errors": 0
    }
    
    current_row = len(ws_intake.get_all_values())
    
    # Process regions
    for region in regions:
        region_id = region.get("Region_ID", "").strip()
        if not region_id:
            continue
        
        metro_tokens = region.get("Metro_Tokens", "").strip()
        start_date = region.get("Start_Date") or DEFAULT_START_DATE
        end_date = region.get("End_Date") or DEFAULT_END_DATE
        
        # Normalize dates
        start_date = str(start_date)[:10] if start_date else DEFAULT_START_DATE
        end_date = str(end_date)[:10] if end_date else DEFAULT_END_DATE
        
        # Search
        articles = search_region(exa, region_id, metro_tokens, 
                                 start_date, end_date, MAX_RESULTS_PER_REGION)
        stats["articles"] += len(articles)
        
        # Process articles
        for i, article in enumerate(articles):
            url = article.get("url", "")
            title = article.get("title", "")[:50]
            
            print(f"   [{i+1}/{len(articles)}] {title}...")
            
            if url in existing_urls:
                print(f"      SKIP: duplicate")
                stats["skipped"] += 1
                continue

            if limit and stats["triaged"] >= limit:
                print(f"\n[LIMIT] Reached {limit} articles triaged")
                break

            # Triage
            triage = triage_article(llm, article.get("title", ""), article.get("text", ""))
            
            if not triage:
                stats["errors"] += 1
                continue
            
            stats["triaged"] += 1
            verdict = triage.get("verdict", "KILL")
            score = triage.get("viability_score", 0)
            
            # Write to sheet
            current_row += 1
            if append_intake_row(ws_intake, region_id, article, triage):
                existing_urls.add(url)
                
                if verdict == "PASS":
                    stats["passed"] += 1
                    print(f"      ✅ PASS (score={score})")
                    promote_to_anchor(ws_anchor, region_id, article, triage, current_row)
                else:
                    stats["killed"] += 1
                    print(f"      ❌ KILL: {triage.get('kill_reason', '')[:40]}")
            
            time.sleep(0.5)
        
        stats["regions"] += 1

        if limit and stats["triaged"] >= limit:
            break

        time.sleep(1)
    
    # Report
    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)
    print(f"Regions:   {stats['regions']}")
    print(f"Articles:  {stats['articles']}")
    print(f"Triaged:   {stats['triaged']}")
    print(f"  PASS:    {stats['passed']}")
    print(f"  KILL:    {stats['killed']}")
    print(f"Skipped:   {stats['skipped']}")
    print(f"Errors:    {stats['errors']}")
    
    return stats

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="NEWS → VIEWS Pipeline")
    parser.add_argument("--test", action="store_true", help="Test mode (3 regions)")
    parser.add_argument("--region", type=str, help="Process single region")
    parser.add_argument("--limit", type=int, help="Max articles to triage")
    parser.add_argument("--check", action="store_true", help="Check credentials only")

    args = parser.parse_args()

    if args.check:
        check_credentials()
        return

    run_pipeline(test_mode=args.test, single_region=args.region, limit=args.limit)


if __name__ == "__main__":
    main()
