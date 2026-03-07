# Codex Handoff

This rebuild branch intentionally favors a narrow, inspectable path over source breadth.

## Current scope
- Curated raw-source ingest only.
- Deterministic normalization into canonical incident schema.
- Lightweight enrichment hooks (query construction + capture of attempts/results).
- Separate story value and researchability scoring.
- Packet generation suitable for editor handoff.

## Deferred on purpose
- Broad autonomous source crawling.
- Heavy ML extraction stack.
- Automated publication decisions.

## Next safe expansions
1. Add new source adapters behind `src/ingest/` interface.
2. Replace heuristic normalization with specialized extractors while preserving schema.
3. Add real enrichment connectors (court dockets, FOIA portals, agency records).
4. Add packet QA gate before export.
