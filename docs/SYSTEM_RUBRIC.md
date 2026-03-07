# System Rubric

## Stage-by-stage intent
1. Ingest: route without hard blocking.
2. Normalize: enforce canonical incident fields.
3. Enrich: collect corroboration + preserve failed attempts.
4. Score: keep story value and researchability independent.
5. Packetize: produce actionable editor packet.

## Scoring rubrics

### Story value (0-25)
- stakes
- clarity
- tension
- novelty
- follow-on potential

### Researchability (0-25)
- source provenance
- search anchors
- artifact availability
- cross-source consistency
- gap tractability

### Evidence completeness (checklist)
- primary footage flag
- agency/date/location identified
- one+ corroborating source
- two+ corroborating sources
- provenance-linked artifacts

## Recommendation matrix
- `PRIORITY_PACKET`: high value + high researchability.
- `RESEARCH_PACKET`: strong enough for focused next-pass enrichment.
- `WATCHLIST_PACKET`: interesting but still evidence-thin.
- `MANUAL_REVIEW`: unresolved risk/ambiguity.
- `ARCHIVE`: preserve with rationale for possible revisit.
