# Bodycam Bot Rebuild Plan

## Mission
Convert raw body-worn camera footage into structured, evidence-backed case packets that editors can quickly evaluate.

## MVP foundation
- Routing-first ingest (`ROUTE_ENRICH`, `ROUTE_REVIEW`, `ROUTE_ARCHIVE`) with no hard story kill at intake.
- Strict normalized incident schema before any editorial judgment.
- Explicit enrichment traces (queries attempted + outcomes).
- Separate scoring for story value and researchability.
- Case packet output as the primary deliverable.
- Candidate-level audit trail for every stage decision.

## Required output contract
Every normalized incident must include:
- `incident_id`
- `source_type`
- `source_url`
- `source_title`
- `channel_or_publisher`
- `publish_date`
- `raw_footage_flag`
- `watermark_flag`
- `agency`
- `incident_date`
- `location`
- `people`
- `incident_type`
- `allegations_or_charges`
- `supporting_artifacts`
- `narrative_hook`
- `story_value_score`
- `researchability_score`
- `evidence_completeness_score`
- `risk_flags`
- `missing_evidence`
- `routing_status`
- `decision_reason`

## Stage posture
1. Harvest: capture candidate + provenance without final judgment.
2. Normalize: build incident object and track uncertainty.
3. Enrich: attach corroboration and record misses.
4. Score: evaluate value, researchability, completeness, and risk.
5. Packetize: produce editor-ready case brief with next steps.
