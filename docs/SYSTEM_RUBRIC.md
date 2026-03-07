# System Rubric

## Stage-by-stage intent
1. Ingest: determine if candidate is incident-like and route appropriately.
2. Normalize: extract coherent incident object while preserving uncertainty.
3. Enrich: attach corroborating artifacts and explain lookup failures.
4. Score: evaluate value, tractability, completeness, and risk separately.
5. Packetize: produce editor-usable case packets with explicit next actions.

## Scoring rubrics

### Story value (0-25)
Dimensions (0-5 each):
- stakes,
- clarity of narrative,
- emotional/dramatic tension,
- novelty/distinctiveness,
- follow-on potential.

### Researchability (0-25)
Dimensions (0-5 each):
- source provenance,
- search anchors,
- supporting artifact availability,
- cross-source consistency,
- gap tractability.

### Evidence completeness (0-10)
1 point each for:
- primary footage,
- agency identified,
- location identified,
- date narrowed,
- one corroborating source,
- two+ corroborating sources,
- legal/case context,
- post-incident outcome context,
- ambiguities explicitly listed,
- source provenance linked.

### Risk flags (non-fatal)
Common flags:
- juvenile involvement,
- identity mismatch,
- weak provenance,
- contradictory context,
- unclear authenticity,
- confusing timeline.

Risk should route toward manual review/caveats, not automatic kill.

## Decision matrix
- `PRIORITY_PACKET`: high story value + high researchability.
- `RESEARCH_PACKET`: workable editorial value + tractable research.
- `WATCHLIST_PACKET`: compelling but incomplete evidence.
- `MANUAL_REVIEW`: promising but ambiguous/risky.
- `ARCHIVE`: low current value.
- `KILL`: non-incident junk or unusable duplicate.
