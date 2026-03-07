# System Rubric

## 1) Story Value Score (0-100)
Measures editorial potential without considering ease of reporting.

Signals:
- Narrative hook clarity.
- Incident severity.
- Public-interest angle (authority conduct, misconduct claims, procedural failures).
- Human stakes and consequence clarity.

## 2) Researchability Score (0-100)
Measures whether a human producer can quickly build a verified episode.

Signals:
- Availability of primary artifacts.
- Traceable agency/jurisdiction context.
- People/incident/date/location completeness.
- Corroboration opportunities.

## 3) Evidence Completeness Score (0-100)
Measures current state of documented evidence, not final truth.

Signals:
- Count/quality of supporting artifacts.
- Key missing evidence list length.
- Confidence in extracted normalized fields.

## Routing guidance
- `ROUTE_ENRICH`: low researchability but plausible lead.
- `ROUTE_PACKET`: high enough completeness + researchability.
- `ROUTE_HOLD`: critical identity/date/jurisdiction ambiguity.
- `ROUTE_REJECT`: non-incident or invalid source.

Every route must include `decision_reason` with concise, inspectable logic.
