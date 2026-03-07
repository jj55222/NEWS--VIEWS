#!/usr/bin/env python3
"""
NEWS -> VIEWS: Bodycam Bot Pipeline Runner

Usage:
    python run_pipeline.py                     # Full run (requires API keys)
    python run_pipeline.py --check             # Validate config only
    python run_pipeline.py --dry-run           # Test with sample data (no API calls)
    python run_pipeline.py --region SF         # Single region
    python run_pipeline.py --limit 5           # Limit candidates
"""

import argparse
import json
import sys
from pathlib import Path

from src.common.config import Config
from src.common.schema import Incident, Evidence, EvidenceType
from src.common.logging import PacketLog
from src.ingest.prescore import compute_prescore
from src.ingest.router import route_candidate
from src.normalize.builder import normalize_incident
from src.enrich.evidence_search import enrich_incident
from src.score.scorer import score_incident
from src.packets.generator import generate_packet


def run_dry(config: Config):
    """
    Dry run with synthetic data. Tests the full pipeline without API calls.
    Validates that all stages connect properly.
    """
    print("=" * 60)
    print("NEWS -> VIEWS: Dry Run (no API calls)")
    print("=" * 60)

    config.ensure_dirs()

    # Synthetic article
    sample_text = """
    John Smith, 34, was sentenced to 25 years in prison for the murder of
    his neighbor, Maria Garcia, in Phoenix, Arizona. Body camera footage from
    the Phoenix Police Department showed officers arriving at the scene.
    The interrogation video, released by Maricopa County, shows Smith
    confessing after 8 hours. The trial was livestreamed on YouTube.
    Smith was convicted of first-degree murder and aggravated assault.
    Surveillance footage from a nearby convenience store captured Smith
    fleeing the scene. Officer James Wilson testified about the arrest.
    """

    # Stage 1: Prescore
    print("\n[1/5] PRESCORE")
    prescore = compute_prescore(sample_text, url="https://example.com/article", region_id="PPD")
    print(f"  Score: {prescore['score']}")
    print(f"  Matches: {prescore['matches']}")

    # Stage 2: Route
    print("\n[2/5] ROUTE")
    status, reason = route_candidate(prescore, config)
    print(f"  Status: {status}")
    print(f"  Reason: {reason}")

    if status != "candidate":
        print("  [STOP] Candidate rejected at routing")
        return

    # Build incident stub
    incident = Incident(
        source_type="manual",
        source_url="https://example.com/article",
        source_title="Phoenix man sentenced in neighbor's murder",
        publish_date="2024-03-15",
    )
    incident._raw_text = sample_text
    incident._raw_title = "Phoenix man sentenced in neighbor's murder"
    incident._region_id = "PPD"

    log = PacketLog(incident_id=incident.incident_id)
    log.add("ingest", "harvested", detail="dry-run sample")

    # Stage 3: Normalize (skip LLM, fill manually for dry run)
    print("\n[3/5] NORMALIZE (simulated)")
    incident.story_summary = "John Smith sentenced to 25 years for murdering neighbor Maria Garcia in Phoenix."
    incident.narrative_hook = "Body camera and interrogation footage reveal the full story of a neighborhood murder."
    incident.incident_type = "homicide"
    incident.incident_date = "2024-01-15"
    incident.agency = "Phoenix Police Department"
    incident.jurisdiction = {"city": "Phoenix", "county": "Maricopa", "state": "AZ"}
    incident.people = [
        {"name": "John Smith", "role": "defendant"},
        {"name": "Maria Garcia", "role": "victim"},
        {"name": "James Wilson", "role": "officer"},
    ]
    incident.allegations_or_charges = ["first-degree murder", "aggravated assault"]
    log.add("normalize", "extracted", detail="simulated (dry run)")
    print(f"  People: {len(incident.people)}")
    print(f"  Charges: {incident.allegations_or_charges}")

    # Stage 4: Enrich (simulated)
    print("\n[4/5] ENRICH (simulated)")
    incident.supporting_artifacts = [
        Evidence(evidence_type=EvidenceType.BODYCAM, url="https://youtube.com/watch?v=abc",
                 title="Phoenix PD bodycam - Smith arrest", source_tier="official", confidence=0.9),
        Evidence(evidence_type=EvidenceType.INTERROGATION, url="https://youtube.com/watch?v=def",
                 title="John Smith interrogation", source_tier="news", confidence=0.7),
        Evidence(evidence_type=EvidenceType.COURT_VIDEO, url="https://youtube.com/watch?v=ghi",
                 title="Smith trial day 3", source_tier="repost", confidence=0.5),
        Evidence(evidence_type=EvidenceType.SURVEILLANCE, url="https://youtube.com/watch?v=jkl",
                 title="Convenience store footage", source_tier="news", confidence=0.6),
    ]
    incident.missing_evidence = []
    incident.evidence_completeness_score = 85.0
    log.add("enrich", "complete", detail="simulated (dry run)")
    print(f"  Artifacts: {len(incident.supporting_artifacts)}")
    print(f"  Missing: {incident.missing_evidence}")

    # Stage 5: Score
    print("\n[5/5] SCORE")
    scoring = score_incident(incident, config, log)
    print(f"  Story value:     {scoring.story_value_score:.0f}")
    print(f"  Researchability: {scoring.researchability_score:.0f}")
    print(f"  Risk flags:      {scoring.risk_flags}")

    # Generate packet
    print("\n[PACKET]")
    packet = generate_packet(incident, scoring, log, output_dir=config.output_dir)
    print(f"  Recommendation: {packet.recommendation}")
    print(f"  Composite:      {packet.composite_score:.0f}")
    print(f"  Decision:       {packet.decision_reason}")

    # Save log
    log.finalize()
    log.save(config.log_dir)

    # Summary
    print("\n" + "=" * 60)
    print("DRY RUN COMPLETE")
    print("=" * 60)
    print(f"Output: {config.output_dir}/packets/")
    print(f"Logs:   {config.log_dir}/")

    # Print packet path
    packet_files = list(Path(config.output_dir).glob("packets/*.json"))
    for pf in packet_files:
        print(f"  -> {pf}")


def run_full(config: Config, regions: list[dict], limit: int = None):
    """Full pipeline run with live API calls."""
    from src.ingest.harvester import harvest_candidates

    print("=" * 60)
    print("NEWS -> VIEWS: Full Pipeline Run")
    print("=" * 60)

    config.ensure_dirs()

    # Stage 1+2: Ingest (harvest + prescore + route)
    print("\n[INGEST] Harvesting candidates...")
    candidates = harvest_candidates(config, regions)

    if limit:
        candidates = candidates[:limit]
        print(f"[LIMIT] Processing {limit} candidates")

    packets = []
    for i, (incident, log) in enumerate(candidates):
        print(f"\n--- Candidate {i+1}/{len(candidates)} ---")

        # Stage 3: Normalize
        print("[NORMALIZE]")
        incident = normalize_incident(incident, config, log)

        # Stage 4: Enrich
        print("[ENRICH]")
        incident = enrich_incident(incident, config, log)

        # Stage 5: Score
        print("[SCORE]")
        scoring = score_incident(incident, config, log)
        print(f"  Story: {scoring.story_value_score:.0f}  Research: {scoring.researchability_score:.0f}")

        # Generate packet
        print("[PACKET]")
        packet = generate_packet(incident, scoring, log, output_dir=config.output_dir)
        print(f"  -> {packet.recommendation} (composite={packet.composite_score:.0f})")

        log.finalize()
        log.save(config.log_dir)
        packets.append(packet)

    # Summary
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Candidates: {len(candidates)}")
    print(f"Packets:    {len(packets)}")

    recs = {}
    for p in packets:
        recs[p.recommendation] = recs.get(p.recommendation, 0) + 1
    for rec, count in sorted(recs.items()):
        print(f"  {rec}: {count}")


def main():
    parser = argparse.ArgumentParser(description="NEWS -> VIEWS Pipeline")
    parser.add_argument("--check", action="store_true", help="Validate config")
    parser.add_argument("--dry-run", action="store_true", help="Test with sample data")
    parser.add_argument("--region", type=str, help="Single region ID")
    parser.add_argument("--limit", type=int, help="Max candidates to process")
    args = parser.parse_args()

    config = Config.from_env()

    if args.check:
        errors = config.validate()
        if errors:
            print("Configuration errors:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("Configuration OK")
            sys.exit(0)

    if args.dry_run:
        run_dry(config)
        return

    # Full run requires valid config
    errors = config.validate()
    if errors:
        print("Cannot run - missing config:")
        for e in errors:
            print(f"  - {e}")
        print("\nUse --dry-run to test without API keys")
        sys.exit(1)

    # Load regions (for now, hardcoded test regions)
    # TODO: load from Google Sheet when sheet_id is configured
    test_regions = [
        {"Region_ID": args.region or "SF", "Metro_Tokens": args.region or "San Francisco"},
    ]

    run_full(config, test_regions, limit=args.limit)


if __name__ == "__main__":
    main()
