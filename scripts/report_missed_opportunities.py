#!/usr/bin/env python3
"""Missed Opportunity Report — identify what the pipeline is failing to convert.

Outputs:
- Top leads by hook_score that ended in NO_ARTIFACT
- Breakdown by city/agency/incident_type
- Suggest registry expansions (new primary sources to add)
- Flag secondary video exists for leads where primary source is unknown

Usage:
    python -m scripts.report_missed_opportunities             # full report
    python -m scripts.report_missed_opportunities --top 50    # top 50 leads
    python -m scripts.report_missed_opportunities --json      # JSON output
"""

import argparse
import json
from collections import Counter

from scripts.config_loader import setup_logging
from scripts.db import get_connection, get_leads, get_artifacts, init_db

logger = setup_logging("missed_opportunities")


def generate_report(top_n: int = 50) -> dict:
    """Generate a missed opportunity report.

    Returns a dict with:
        top_missed: list of top NO_ARTIFACT leads
        by_incident_type: Counter
        by_location: Counter
        by_agency: Counter
        secondary_video_exists: leads where secondary artifacts were found
        registry_suggestions: recommended source additions
    """
    init_db()
    conn = get_connection()

    # Get all NO_ARTIFACT leads sorted by hook score
    missed = get_leads(conn, status="NO_ARTIFACT", limit=top_n * 2)
    # Also get high-hook leads still in NEW (never hunted yet)
    unhunted = get_leads(conn, status="NEW", min_hook_score=60, limit=top_n)

    # Analyze missed leads
    incident_counter = Counter()
    location_counter = Counter()
    agency_counter = Counter()
    secondary_exists = []

    top_missed = []
    for lead in missed[:top_n]:
        lead_id = lead["lead_id"]
        entities = lead.get("entities_json", "{}")
        if isinstance(entities, str):
            try:
                entities = json.loads(entities)
            except json.JSONDecodeError:
                entities = {}

        incident_type = lead.get("incident_type", "unknown")
        location = lead.get("location") or "unknown"
        agencies = entities.get("agencies", []) if isinstance(entities, dict) else []

        incident_counter[incident_type] += 1
        location_counter[location] += 1
        for agency in agencies:
            agency_counter[agency] += 1

        # Check if any secondary artifacts were found
        artifacts = get_artifacts(conn, lead_id)
        secondary = [a for a in artifacts if a.get("source_class") == "secondary"]
        primary = [a for a in artifacts if a.get("source_class") == "primary"]

        entry = {
            "lead_id": lead_id,
            "title": lead.get("title", "")[:100],
            "hook_score": lead.get("hook_score", 0),
            "incident_type": incident_type,
            "location": location,
            "agencies": agencies,
            "total_artifacts": len(artifacts),
            "secondary_artifacts": len(secondary),
            "primary_artifacts": len(primary),
            "url": lead.get("url", ""),
        }
        top_missed.append(entry)

        if secondary and not primary:
            secondary_exists.append({
                "lead_id": lead_id,
                "title": lead.get("title", "")[:100],
                "hook_score": lead.get("hook_score", 0),
                "location": location,
                "secondary_urls": [a.get("url", "") for a in secondary[:3]],
                "note": "Footage likely exists but primary source unknown",
            })

    # Build registry suggestions
    suggestions = []
    for agency, count in agency_counter.most_common(20):
        if count >= 2:
            suggestions.append({
                "agency": agency,
                "missed_count": count,
                "suggestion": f"Add official YouTube/press page for {agency}",
            })
    for location, count in location_counter.most_common(20):
        if count >= 3 and location != "unknown":
            suggestions.append({
                "location": location,
                "missed_count": count,
                "suggestion": f"Add local PD/sheriff transparency portal for {location}",
            })

    # Summary stats
    total_leads = conn.execute("SELECT COUNT(*) FROM case_leads").fetchone()[0]
    artifact_found = conn.execute(
        "SELECT COUNT(*) FROM case_leads WHERE status = 'ARTIFACT_FOUND'"
    ).fetchone()[0]
    no_artifact = conn.execute(
        "SELECT COUNT(*) FROM case_leads WHERE status = 'NO_ARTIFACT'"
    ).fetchone()[0]
    new_leads = conn.execute(
        "SELECT COUNT(*) FROM case_leads WHERE status = 'NEW'"
    ).fetchone()[0]

    conn.close()

    report = {
        "summary": {
            "total_leads": total_leads,
            "artifact_found": artifact_found,
            "no_artifact": no_artifact,
            "new_unhunted": new_leads,
            "conversion_rate": round(artifact_found / max(artifact_found + no_artifact, 1) * 100, 1),
        },
        "top_missed": top_missed,
        "by_incident_type": dict(incident_counter.most_common(20)),
        "by_location": dict(location_counter.most_common(20)),
        "by_agency": dict(agency_counter.most_common(20)),
        "secondary_video_exists": secondary_exists,
        "registry_suggestions": suggestions,
    }

    return report


def print_report(report: dict) -> None:
    """Print a human-readable report."""
    s = report["summary"]
    print("=" * 60)
    print("  MISSED OPPORTUNITY REPORT")
    print("=" * 60)
    print(f"  Total leads:      {s['total_leads']}")
    print(f"  Artifact found:   {s['artifact_found']}")
    print(f"  No artifact:      {s['no_artifact']}")
    print(f"  Unhunted (NEW):   {s['new_unhunted']}")
    print(f"  Conversion rate:  {s['conversion_rate']}%")
    print()

    print("─── Top Missed Leads ───")
    for i, lead in enumerate(report["top_missed"][:30], 1):
        print(f"  {i:2d}. [{lead['hook_score']:3d}] {lead['title']}")
        print(f"      Type: {lead['incident_type']} | Location: {lead['location']}")
        if lead["secondary_artifacts"] > 0:
            print(f"      ** Secondary video exists ({lead['secondary_artifacts']} clips)")
        print()

    print("─── By Incident Type ───")
    for itype, count in report["by_incident_type"].items():
        print(f"  {itype}: {count}")

    print()
    print("─── By Location ───")
    for loc, count in list(report["by_location"].items())[:15]:
        print(f"  {loc}: {count}")

    if report["secondary_video_exists"]:
        print()
        print("─── Footage Likely Exists (secondary found, no primary) ───")
        for item in report["secondary_video_exists"][:10]:
            print(f"  [{item['hook_score']}] {item['title']}")
            print(f"    Location: {item['location']}")
            for url in item["secondary_urls"]:
                print(f"    Secondary: {url[:80]}")
            print()

    if report["registry_suggestions"]:
        print()
        print("─── Registry Expansion Suggestions ───")
        for sug in report["registry_suggestions"][:15]:
            print(f"  - {sug['suggestion']} (missed {sug['missed_count']}x)")

    print()
    print("=" * 60)


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate missed opportunity report.")
    parser.add_argument("--top", type=int, default=50, help="Top N missed leads to show.")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of text.")
    args = parser.parse_args()

    report = generate_report(top_n=args.top)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
