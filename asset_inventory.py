#!/usr/bin/env python3
"""
NEWS -> VIEWS: Asset Inventory Builder

Takes a scored incident (video_id) and hunts down every available piece of
footage: bodycam angles, interrogation, court video, surveillance, 911 calls.
Organizes results into a structured inventory with links and timestamps.

This is the step AFTER incident scoring — it enriches scored videos with a
complete asset inventory for production.

Usage:
    python asset_inventory.py --video VIDEO_ID       # Build inventory for one video
    python asset_inventory.py --video VIDEO_ID --exa # Include Exa search (paid)
    python asset_inventory.py --list                 # List all inventories
    python asset_inventory.py --show VIDEO_ID        # Show inventory detail
    python asset_inventory.py --check                # Verify credentials
"""

import os
import json
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import asdict
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
EXA_API_KEY = os.getenv("EXA_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

INVENTORY_PATH = Path("asset_inventories.json")
SCORES_PATH = Path("incident_scores.json")


# =============================================================================
# CREDENTIAL CHECK
# =============================================================================

def check_credentials() -> bool:
    """Verify required API keys."""
    ok = True
    if not YOUTUBE_API_KEY:
        print("  YOUTUBE_API_KEY: NOT SET (required)")
        ok = False
    else:
        print("  YOUTUBE_API_KEY: set")

    if not EXA_API_KEY:
        print("  EXA_API_KEY: NOT SET (optional — needed for --exa)")
    else:
        print("  EXA_API_KEY: set")

    if not OPENROUTER_API_KEY:
        print("  OPENROUTER_API_KEY: NOT SET (optional — used for case detail extraction)")
    else:
        print("  OPENROUTER_API_KEY: set")

    return ok


# =============================================================================
# DATA LOADING
# =============================================================================

def load_inventories() -> dict:
    """Load existing inventories."""
    if INVENTORY_PATH.exists():
        with open(INVENTORY_PATH) as f:
            return json.load(f)
    return {}


def save_inventories(inventories: dict):
    """Save inventories to disk."""
    with open(INVENTORY_PATH, "w") as f:
        json.dump(inventories, f, indent=2)
    print(f"\nInventory saved to {INVENTORY_PATH}")


def load_scored_incident(video_id: str) -> Optional[dict]:
    """Load a previously scored incident if available."""
    if SCORES_PATH.exists():
        with open(SCORES_PATH) as f:
            scores = json.load(f)
        return scores.get(video_id)
    return None


# =============================================================================
# ASSET INVENTORY BUILDER
# =============================================================================

def build_inventory(video_id: str, use_exa: bool = False) -> dict:
    """
    Build a complete asset inventory for a video.

    Steps:
    1. Load scored incident data (or score it now)
    2. Extract transcript timestamps for artifact mentions
    3. Run comprehensive YouTube/Vimeo/portal search
    4. Optionally run Exa search for court/Reddit/PACER
    5. Organize into structured inventory
    """
    print("=" * 60)
    print(f"ASSET INVENTORY: {video_id}")
    print("=" * 60)

    # ── Step 1: Get case details ──────────────────────────────────
    print("\n[1/4] Loading case details...")
    scored = load_scored_incident(video_id)
    case_details = {}
    metadata = {}

    if scored:
        case_details = scored.get("case_details", {})
        metadata = scored.get("metadata", {})
        print(f"  Loaded from incident_scores.json")
        print(f"  Score: {scored.get('score', '?')}/100 ({scored.get('classification', '?')})")
    else:
        print(f"  Not yet scored — running incident scoring first...")
        try:
            from incident_score import score_incident, load_scores, save_scores
            entry = score_incident(video_id)
            if entry:
                case_details = entry.get("case_details", {})
                metadata = entry.get("metadata", {})
                # Save the score
                scores = load_scores()
                scores[video_id] = entry
                save_scores(scores)
                scored = entry
        except Exception as e:
            print(f"  Scoring error: {e}")

    defendant = ""
    if case_details.get("defendant_names"):
        defendant = case_details["defendant_names"][0]
    jurisdiction = case_details.get("jurisdiction", "")
    state = case_details.get("state", "")
    incident_year = case_details.get("incident_year", "")
    crime_type = case_details.get("crime_type", "")

    print(f"  Defendant: {defendant or 'Unknown'}")
    print(f"  Jurisdiction: {jurisdiction or 'Unknown'}")
    print(f"  State: {state or 'Unknown'}")
    print(f"  Crime: {crime_type or 'Unknown'}")

    # ── Step 2: Transcript artifact timestamps ────────────────────
    print("\n[2/4] Scanning transcript for artifact mentions...")
    transcript_artifacts = []
    try:
        from transcript_analyzer import get_transcript, detect_artifacts_in_transcript
        segments, full_text = get_transcript(video_id)
        if segments:
            mentions = detect_artifacts_in_transcript(segments, full_text)
            for m in mentions:
                transcript_artifacts.append({
                    "type": m.artifact_type if hasattr(m, 'artifact_type') else m.get("artifact_type", ""),
                    "timestamp": m.timestamp if hasattr(m, 'timestamp') else m.get("timestamp", ""),
                    "context": m.context if hasattr(m, 'context') else m.get("context", ""),
                    "confidence": m.confidence if hasattr(m, 'confidence') else m.get("confidence", ""),
                    "keyword": m.keyword_matched if hasattr(m, 'keyword_matched') else m.get("keyword_matched", ""),
                })
            print(f"  Found {len(transcript_artifacts)} artifact mentions in transcript")
            for ta in transcript_artifacts:
                print(f"    [{ta['timestamp']}] {ta['type']} — \"{ta['context'][:60]}...\"")
        else:
            print(f"  No transcript available")
    except Exception as e:
        print(f"  Transcript error: {e}")

    # ── Step 3: Comprehensive free search ─────────────────────────
    print("\n[3/4] Searching YouTube, Vimeo, official portals...")
    free_results = {
        "bodycam": [], "interrogation": [], "court": [],
        "911_calls": [], "surveillance": [], "portal_links": [],
    }

    if defendant or jurisdiction:
        try:
            from bodycam_sources import comprehensive_search
            findings = comprehensive_search(defendant, jurisdiction, state, incident_year)

            # Convert VideoResult objects to dicts
            for key in ["bodycam", "interrogation", "911_calls"]:
                for item in findings.get(key, []):
                    entry = _to_dict(item)
                    if entry not in free_results[key]:
                        free_results[key].append(entry)

            for item in findings.get("portal_links", []):
                free_results["portal_links"].append(
                    item if isinstance(item, dict) else _to_dict(item)
                )

        except Exception as e:
            print(f"  Search error: {e}")
            # Fallback: basic YouTube search
            try:
                from bodycam_sources import youtube_bodycam_search, youtube_interrogation_search
                if defendant:
                    bc, _ = youtube_bodycam_search(defendant, jurisdiction)
                    free_results["bodycam"] = [_to_dict(v) for v in bc]
                    intg, _ = youtube_interrogation_search(defendant)
                    free_results["interrogation"] = [_to_dict(v) for v in intg]
            except Exception as e2:
                print(f"  Fallback search error: {e2}")
    else:
        print("  No defendant/jurisdiction — skipping search")

    # ── Step 4: Exa search (optional, paid) ───────────────────────
    exa_results = {"court": [], "reddit": [], "pacer": []}

    if use_exa and EXA_API_KEY and (defendant or jurisdiction):
        print("\n[4/4] Running Exa search (court records, Reddit, PACER)...")
        try:
            from artifact_hunter import get_exa_client, search_artifacts
            exa = get_exa_client()
            # Determine region_id from jurisdiction_portals
            region_id = _guess_region_id(jurisdiction, state)
            results = search_artifacts(
                exa, defendant, jurisdiction, crime_type,
                region_id=region_id, incident_year=incident_year,
            )
            exa_results["court"] = results.get("court", [])
            exa_results["reddit"] = results.get("reddit", [])
            exa_results["pacer"] = results.get("pacer", [])

            # Also merge bodycam/interrogation from Exa into free_results
            for item in results.get("body_cam", []):
                if item not in free_results["bodycam"]:
                    free_results["bodycam"].append(item)
            for item in results.get("interrogation", []):
                if item not in free_results["interrogation"]:
                    free_results["interrogation"].append(item)

            total_exa = sum(len(v) for v in exa_results.values())
            print(f"  Exa found {total_exa} additional sources")
        except Exception as e:
            print(f"  Exa error: {e}")
    elif use_exa and not EXA_API_KEY:
        print("\n[4/4] Skipping Exa (EXA_API_KEY not set)")
    else:
        print("\n[4/4] Skipping Exa (use --exa to enable)")

    # ── Build final inventory ─────────────────────────────────────
    inventory = {
        "video_id": video_id,
        "video_title": scored.get("video_title", "") if scored else "",
        "channel_name": scored.get("channel_name", "") if scored else "",
        "built_at": datetime.now(timezone.utc).isoformat() + "Z",
        "case": {
            "defendant": defendant,
            "jurisdiction": jurisdiction,
            "state": state,
            "crime_type": crime_type,
            "incident_year": incident_year,
        },
        "incident_score": scored.get("score", 0) if scored else 0,
        "classification": scored.get("classification", "") if scored else "",
        "assets": {
            "bodycam": _dedup_assets(free_results["bodycam"]),
            "interrogation": _dedup_assets(free_results["interrogation"]),
            "court": _dedup_assets(free_results.get("court", []) + exa_results.get("court", [])),
            "911_calls": _dedup_assets(free_results["911_calls"]),
            "surveillance": _dedup_assets(free_results["surveillance"]),
        },
        "portals": free_results["portal_links"],
        "court_records": exa_results.get("pacer", []),
        "community": exa_results.get("reddit", []),
        "transcript_mentions": transcript_artifacts,
        "totals": {},
    }

    # Calculate totals
    totals = {}
    for asset_type, assets in inventory["assets"].items():
        totals[asset_type] = len(assets)
    totals["portals"] = len(inventory["portals"])
    totals["court_records"] = len(inventory["court_records"])
    totals["transcript_mentions"] = len(inventory["transcript_mentions"])
    totals["total_assets"] = sum(totals[k] for k in ["bodycam", "interrogation", "court", "911_calls", "surveillance"])
    inventory["totals"] = totals

    # ── Print summary ─────────────────────────────────────────────
    _print_inventory(inventory)

    return inventory


def _to_dict(item) -> dict:
    """Convert VideoResult or any object to dict."""
    if isinstance(item, dict):
        return item
    if hasattr(item, 'to_dict'):
        return item.to_dict()
    if hasattr(item, '__dict__'):
        try:
            return asdict(item)
        except Exception:
            return vars(item)
    return {"value": str(item)}


def _dedup_assets(assets: list) -> list:
    """Deduplicate assets by URL."""
    seen = set()
    result = []
    for a in assets:
        url = a.get("url", "") if isinstance(a, dict) else getattr(a, "url", "")
        if url and url not in seen:
            seen.add(url)
            result.append(a if isinstance(a, dict) else _to_dict(a))
        elif not url:
            result.append(a if isinstance(a, dict) else _to_dict(a))
    return result


def _guess_region_id(jurisdiction: str, state: str) -> str:
    """Try to match jurisdiction to a region_id from jurisdiction_portals."""
    try:
        from jurisdiction_portals import JURISDICTION_PORTALS
        jur_lower = jurisdiction.lower() if jurisdiction else ""
        for region_id, config in JURISDICTION_PORTALS.items():
            name = config.get("name", "").lower()
            if name and (name in jur_lower or jur_lower in name):
                return region_id
            for agency in config.get("agencies", []):
                aname = agency.get("name", "").lower()
                if aname and aname in jur_lower:
                    return region_id
    except ImportError:
        pass
    return ""


def _print_inventory(inv: dict):
    """Print a formatted asset inventory."""
    print(f"\n{'='*60}")
    print(f"ASSET INVENTORY COMPLETE")
    print(f"{'='*60}")
    print(f"Video: {inv['video_title']}")
    print(f"Case:  {inv['case']['defendant'] or 'Unknown'} — {inv['case']['crime_type'] or 'Unknown'}")
    print(f"Score: {inv['incident_score']}/100 ({inv['classification']})")
    print()

    totals = inv["totals"]
    print(f"  Bodycam videos:     {totals.get('bodycam', 0)}")
    print(f"  Interrogation:      {totals.get('interrogation', 0)}")
    print(f"  Court footage:      {totals.get('court', 0)}")
    print(f"  911 calls:          {totals.get('911_calls', 0)}")
    print(f"  Surveillance:       {totals.get('surveillance', 0)}")
    print(f"  Official portals:   {totals.get('portals', 0)}")
    print(f"  Court records:      {totals.get('court_records', 0)}")
    print(f"  Transcript mentions:{totals.get('transcript_mentions', 0)}")
    print(f"  ---")
    print(f"  TOTAL ASSETS:       {totals.get('total_assets', 0)}")

    # Print each category with links
    for asset_type in ["bodycam", "interrogation", "court", "911_calls", "surveillance"]:
        assets = inv["assets"].get(asset_type, [])
        if assets:
            print(f"\n  --- {asset_type.upper().replace('_', ' ')} ---")
            for a in assets[:10]:
                title = a.get("title", "")[:60]
                url = a.get("url", "")
                source = a.get("source_type", a.get("query", ""))
                if isinstance(source, str) and len(source) > 30:
                    source = source[:30] + "..."
                print(f"    {title}")
                if url:
                    print(f"      {url}")

    # Portals
    portals = inv.get("portals", [])
    if portals:
        print(f"\n  --- OFFICIAL PORTALS ---")
        for p in portals[:10]:
            name = p.get("name", p.get("type", "portal"))
            url = p.get("url", "")
            action = p.get("action", "")
            print(f"    {name}: {url}")
            if action:
                print(f"      Action: {action}")

    # Transcript mentions
    mentions = inv.get("transcript_mentions", [])
    if mentions:
        print(f"\n  --- TRANSCRIPT MENTIONS ---")
        for m in mentions[:15]:
            print(f"    [{m['timestamp']}] {m['type']} — \"{m['context'][:70]}\"")


# =============================================================================
# LIST / SHOW
# =============================================================================

def list_inventories():
    """List all asset inventories."""
    inventories = load_inventories()
    if not inventories:
        print("No inventories built yet. Run: python asset_inventory.py --video VIDEO_ID")
        return

    print(f"\n{'Assets':>6} {'Score':>5} {'Video'}")
    print("-" * 60)
    for vid, inv in sorted(inventories.items(),
                           key=lambda x: x[1].get("totals", {}).get("total_assets", 0),
                           reverse=True):
        total = inv.get("totals", {}).get("total_assets", 0)
        score = inv.get("incident_score", 0)
        title = inv.get("video_title", vid)[:45]
        print(f"{total:>6} {score:>5} {title}")

    print(f"\nTotal: {len(inventories)} inventories")


def show_inventory(video_id: str):
    """Show detailed inventory for a video."""
    inventories = load_inventories()
    inv = inventories.get(video_id)
    if not inv:
        print(f"No inventory for {video_id}. Run: python asset_inventory.py --video {video_id}")
        return
    _print_inventory(inv)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Build asset inventories for scored incidents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --video rykYVUNbAs0           # Build inventory (free sources only)
    %(prog)s --video rykYVUNbAs0 --exa     # Include Exa search (court/Reddit/PACER)
    %(prog)s --list                        # List all inventories
    %(prog)s --show rykYVUNbAs0            # Show detail for one video
    %(prog)s --check                       # Verify credentials
        """
    )

    parser.add_argument("--video", metavar="VIDEO_ID", help="Build inventory for a video")
    parser.add_argument("--exa", action="store_true", help="Include Exa search (paid)")
    parser.add_argument("--list", action="store_true", help="List all inventories")
    parser.add_argument("--show", metavar="VIDEO_ID", help="Show inventory detail")
    parser.add_argument("--check", action="store_true", help="Check credentials")

    args = parser.parse_args()

    if args.check:
        check_credentials()
    elif args.video:
        if not YOUTUBE_API_KEY:
            print("YOUTUBE_API_KEY required. Set it in .env or Colab Secrets.")
            return
        inventory = build_inventory(args.video, use_exa=args.exa)
        if inventory:
            inventories = load_inventories()
            inventories[args.video] = inventory
            save_inventories(inventories)
    elif args.list:
        list_inventories()
    elif args.show:
        show_inventory(args.show)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
