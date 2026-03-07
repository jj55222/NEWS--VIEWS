from __future__ import annotations

import argparse
from pathlib import Path

from src.common.io import read_json, write_json, write_jsonl
from src.common.models import AuditEvent
from src.ingest.curated_ingest import ingest_curated_sources
from src.normalize.normalizer import normalize_candidate
from src.enrich.enricher import run_lightweight_enrichment
from src.score.scorer import score_incident
from src.packet.generator import generate_case_packet


def run_pipeline(input_path: str, output_dir: str) -> None:
    raw_items = read_json(input_path)
    candidates = ingest_curated_sources(raw_items)

    incidents_out = []
    audit_out = []
    packet_dir = Path(output_dir) / "packets"

    for candidate in candidates:
        audit = AuditEvent.from_candidate(candidate.source_url)
        audit.provenance = {
            "source_type": candidate.source_type,
            "source_url": candidate.source_url,
            "source_title": candidate.source_title,
            "channel_or_publisher": candidate.channel_or_publisher,
            "publish_date": candidate.publish_date,
        }

        incident, field_confidence = normalize_candidate(candidate)
        audit.extracted_fields = incident.asdict()
        audit.field_confidence = field_confidence

        enrich_data = run_lightweight_enrichment(incident)
        audit.enrichment_queries_attempted = enrich_data["queries"]
        audit.enrichment_results = enrich_data["results"]

        score_breakdown = score_incident(incident, field_confidence)
        audit.score_breakdown = score_breakdown
        audit.routing_decisions.append(
            {"status": incident.routing_status, "reason": incident.decision_reason}
        )

        packet = generate_case_packet(incident)
        audit.final_packet_status = "GENERATED" if incident.routing_status == "ROUTE_PACKET" else "DRAFT"

        incidents_out.append(incident.asdict())
        audit_out.append(audit.asdict())
        write_json(packet_dir / f"{packet['packet_id']}.json", packet)

    write_jsonl(Path(output_dir) / "incidents.jsonl", incidents_out)
    write_jsonl(Path(output_dir) / "audit.jsonl", audit_out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bodycam rebuild pipeline")
    parser.add_argument("--input", required=True, help="Path to curated JSON source input")
    parser.add_argument("--output", required=True, help="Output directory for run artifacts")
    args = parser.parse_args()
    run_pipeline(args.input, args.output)


if __name__ == "__main__":
    main()
