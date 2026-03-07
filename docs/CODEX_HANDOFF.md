# Codex Handoff Notes

## Branch intent
This branch intentionally favors rebuild clarity over legacy compatibility.

## What changed
- Replaced legacy-ish ingest/normalize assumptions with staged routing and explicit outcomes.
- Promoted a strict incident schema centered on provenance + uncertainty + missing evidence.
- Added structured stage decisions with reasons at every stage.
- Added batch-level metrics to evaluate behavior over runs, not anecdotes.
- Kept enrichment offline-friendly with deterministic placeholders and explicit failure reasons.

## Next implementation targets
1. Replace enrichment stubs with real search providers.
2. Add duplicate detection with evidence-level linkage.
3. Add stronger risk subtyping and policy checks.
4. Expand source adapters while preserving schema and audit guarantees.

## Non-negotiables for follow-on work
- Never merge story value and researchability scores.
- Never drop missing-evidence ledger from packets.
- Never emit only final outcomes without stage-level reasons.
