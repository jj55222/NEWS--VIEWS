from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from src.common.io import read_json, write_json, write_jsonl
from src.common.models import AuditEvent, BatchMetrics
from src.ingest.curated_ingest import ingest_curated_sources
from src.ingest.brave_discovery import discover_candidates_from_brave
from src.normalize.normalizer import normalize_candidate
from src.enrich.enricher import run_lightweight_enrichment
from src.score.scorer import score_incident
from src.packet.generator import generate_case_packet


def _compute_batch_metrics(audits: List[Dict[str, Any]], packets: List[Dict[str, Any]]) -> BatchMetrics:
    metrics = BatchMetrics(total_candidates_harvested=len(audits))
    metrics.candidates_normalized = sum(
        1 for a in audits if any(d["stage"] == "normalize" and d["status"] != "NORMALIZATION_FAILED" for d in a["routing_decisions"])
    )
    metrics.candidates_enriched = sum(
        1 for a in audits if any(d["stage"] == "enrich" and d["status"] in {"ENRICHED_STRONG", "ENRICHED_PARTIAL"} for d in a["routing_decisions"])
    )
    metrics.usable_packets = sum(1 for p in packets if p["routing_status"] in {"PRIORITY_PACKET", "RESEARCH_PACKET", "WATCHLIST_PACKET"})

    corroboration_counts = [len(p["evidence_list"]) for p in packets]
    if packets:
        metrics.percent_with_1plus_corroborating_source = round(sum(1 for c in corroboration_counts if c >= 1) / len(packets) * 100, 2)
        metrics.percent_with_2plus_corroborating_sources = round(sum(1 for c in corroboration_counts if c >= 2) / len(packets) * 100, 2)
        metrics.average_missing_evidence_count = round(sum(len(p["open_questions"]) for p in packets) / len(packets), 2)
        metrics.average_story_value_score = round(sum(p["score_snapshot"]["story_value_score"] for p in packets) / len(packets), 2)
        metrics.average_researchability_score = round(sum(p["score_snapshot"]["researchability_score"] for p in packets) / len(packets), 2)
    return metrics


def run_pipeline(input_path: str, output_dir: str) -> None:
    raw_items = read_json(input_path)
    candidates = ingest_curated_sources(raw_items)

    incidents_out: List[Dict[str, Any]] = []
    audit_out: List[Dict[str, Any]] = []
    packets_out: List[Dict[str, Any]] = []
    output_root = Path(output_dir)
    packet_dir = output_root / "packets"

    for candidate, ingest_decision in candidates:
        audit = AuditEvent.from_candidate(candidate)
        audit.routing_decisions.append({"stage": ingest_decision.stage, "status": ingest_decision.status, "reason": ingest_decision.reason})

        incident, field_confidence, normalize_decision = normalize_candidate(candidate)
        audit.normalization_fields = {
            "agency": incident.agency,
            "incident_date": incident.incident_date,
            "location": incident.location,
            "people": incident.people,
            "incident_type": incident.incident_type,
            "allegations_or_charges": incident.allegations_or_charges,
            "narrative_hook": incident.narrative_hook,
            "missing_evidence": incident.missing_evidence,
        }
        audit.field_confidence = field_confidence
        audit.routing_decisions.append({"stage": normalize_decision.stage, "status": normalize_decision.status, "reason": normalize_decision.reason})

        if normalize_decision.status == "NORMALIZATION_FAILED":
            incident.routing_status = "MANUAL_REVIEW"
            incident.decision_reason = normalize_decision.reason
            audit.final_packet_status = "MANUAL_REVIEW"
            incidents_out.append(incident.asdict())
            audit_out.append(audit.asdict())
            continue

        enrich_data, enrich_decision = run_lightweight_enrichment(incident)
        audit.enrichment_queries_attempted = enrich_data["queries"]
        audit.enrichment_results = enrich_data["results"]
        audit.routing_decisions.append({"stage": enrich_decision.stage, "status": enrich_decision.status, "reason": enrich_decision.reason})

        score_breakdown, score_decision = score_incident(incident, field_confidence)
        audit.final_scores = score_breakdown
        audit.routing_decisions.append({"stage": score_decision.stage, "status": score_decision.status, "reason": score_decision.reason})

        packet = generate_case_packet(incident, score_breakdown)
        write_json(packet_dir / f"{packet['packet_id']}.json", packet)
        packets_out.append(packet)

        audit.final_packet_status = packet["routing_status"]
        incidents_out.append(incident.asdict())
        audit_out.append(audit.asdict())

    metrics = _compute_batch_metrics(audit_out, packets_out)
    write_jsonl(output_root / "incidents.jsonl", incidents_out)
    write_jsonl(output_root / "audit.jsonl", audit_out)
    write_json(output_root / "packets_index.json", packets_out)
    write_json(output_root / "batch_metrics.json", metrics.asdict())


def run_pipeline_from_keywords(keywords: list, output_dir: str, count_per_keyword: int = 5) -> None:
    raw_items = discover_candidates_from_brave(keywords, count_per_keyword=count_per_keyword)
    if not raw_items:
        print("No candidates discovered from Brave search.")
        return

    # Write discovered items to a temp file and run the standard pipeline
    import json
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(raw_items, tmp)
        tmp_path = tmp.name
    run_pipeline(tmp_path, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bodycam rebuild pipeline")
    parser.add_argument("--input", help="Path to curated JSON source input")
    parser.add_argument("--keywords", nargs="+", help="Keyword queries for Brave-based discovery")
    parser.add_argument("--count-per-keyword", type=int, default=5, help="Brave result count per keyword for discovery")
    parser.add_argument("--output", required=True, help="Output directory for run artifacts")
    args = parser.parse_args()

    if bool(args.input) == bool(args.keywords):
        parser.error("Provide exactly one of --input or --keywords")

    if args.input:
        run_pipeline(args.input, args.output)
    else:
        run_pipeline_from_keywords(args.keywords, args.output, count_per_keyword=args.count_per_keyword)


if __name__ == "__main__":
    main()
