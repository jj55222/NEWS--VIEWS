# Codex Handoff Notes

## Branch intent
This branch intentionally favors rebuild clarity over legacy compatibility.

## What changed
- Replaced legacy-ish ingest/normalize assumptions with staged routing and explicit outcomes.
- Promoted a strict incident schema centered on provenance + uncertainty + missing evidence.
- Added structured stage decisions with reasons at every stage.
- Added batch-level metrics to evaluate behavior over runs, not anecdotes.
- Added live Brave keyword discovery mode (`--keywords`) for low-cost real candidate harvesting.
- Added Brave-backed enrichment when `BRAVE_API_KEY` is present, while preserving deterministic fallback behavior offline.

## Next implementation targets
1. Add duplicate detection with evidence-level linkage.
2. Add stronger risk subtyping and policy checks.
3. Expand source adapters while preserving schema and audit guarantees.
4. Add provider failover chain (Brave + court/news specific adapters).

## Non-negotiables for follow-on work
- Never merge story value and researchability scores.
- Never drop missing-evidence ledger from packets.
- Never emit only final outcomes without stage-level reasons.
