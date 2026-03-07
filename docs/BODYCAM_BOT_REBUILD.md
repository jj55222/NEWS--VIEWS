# Bodycam Bot Rebuild Plan

## Mission
Convert raw incident footage into structured, explainable, evidence-backed case opportunities.

## MVP foundation
- Canonical incident schema first.
- Routing-first ingest (`ROUTE_ENRICH`, `ROUTE_REVIEW`, `ARCHIVE`, `KILL`).
- Explicit normalization outcomes (`NORMALIZED_STRONG`, `NORMALIZED_PARTIAL`, `NORMALIZATION_FAILED`).
- Explicit enrichment outcomes (`ENRICHED_STRONG`, `ENRICHED_PARTIAL`, `NEEDS_MANUAL_RESEARCH`, `ENRICHMENT_STALLED`).
- Distinct scoring for story value and researchability.
- Required missing-evidence ledger in every non-killed case.

## Canonical incident schema (minimum)
- Provenance: source URL, title, description, publisher, date, media type.
- Candidate quality hints: raw-footage likelihood, watermark likelihood, transcript availability.
- Normalized incident: agency, date/date-range, location, people/entities, incident category, allegations/events.
- Research hooks: transcript-derived search anchors, uncertainty notes, unresolved questions.
- Enrichment: attempted queries, sources found/not found, probable matches, supporting documents.
- Scoring: story value, researchability, evidence completeness, risk flags.
- Final recommendation: `PRIORITY_PACKET`, `RESEARCH_PACKET`, `WATCHLIST_PACKET`, `MANUAL_REVIEW`, `ARCHIVE`, `KILL`.

## Hard constraints
- Do not collapse to final editorial judgments in ingest.
- Do not merge story value + researchability into one score.
- Do not hide ambiguity; record uncertainty and failure reasons.
- Do not suppress incomplete but promising candidates.

## Required auditable trail
Per candidate, persist:
- source provenance,
- normalization fields + confidence,
- stage routing decisions + reasons,
- enrichment queries + outcomes,
- score breakdown + recommendation,
- packet status.
