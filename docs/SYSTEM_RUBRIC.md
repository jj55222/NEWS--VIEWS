# System Rubric

## Incident Schema

Every candidate gets a canonical Incident with these fields:

| Field | Type | Description |
|-------|------|-------------|
| incident_id | string | Auto-generated unique ID |
| source_type | string | rss, exa, manual, youtube, courtlistener |
| source_url | string | Original article/video URL |
| source_title | string | Article headline |
| channel_or_publisher | string | News outlet or channel |
| publish_date | string | Publication date |
| raw_footage_flag | bool | Whether raw footage is referenced |
| watermark_flag | bool | Whether watermarked content detected |
| agency | string | Law enforcement agency involved |
| incident_date | string | When the incident occurred |
| incident_type | string | shooting, use_of_force, pursuit, etc. |
| people | list | Named people with roles |
| allegations_or_charges | list | Specific charges |
| jurisdiction | dict | {city, county, state} |
| narrative_hook | string | One-sentence moral weight |
| story_summary | string | 2-3 sentence factual summary |
| story_value_score | float | 0-100 story compelling-ness |
| researchability_score | float | 0-100 evidence findability |
| supporting_artifacts | list | Evidence objects found |
| evidence_completeness_score | float | 0-100 evidence coverage |
| routing_status | string | candidate, duplicate, rejected_* |
| risk_flags | list | Problems for human review |
| missing_evidence | list | What we searched for but didn't find |
| decision_reason | string | Why this decision was made |

## Evidence Model

Each piece of supporting evidence:

| Field | Type | Description |
|-------|------|-------------|
| evidence_type | enum | bodycam, dashcam, interrogation, surveillance, court_video, news_clip |
| url | string | Direct URL to evidence |
| title | string | Title/description |
| source_tier | string | official, news, repost, unknown |
| confidence | float | 0-1 confidence score |
| found_via | string | Which search query found this |

## Scoring Rubric

### Story Value (0-100)
- Narrative hook quality: 0-20 pts
- Incident severity: 0-20 pts
- Named people: 0-20 pts (5 per person)
- Charges specificity: 0-15 pts
- Summary quality: 0-10 pts
- Agency identified: 0-10 pts

### Researchability (0-100)
- Evidence completeness: 0-40 pts
- Artifact count: 0-25 pts (3 per artifact)
- Source tier quality: 0-20 pts
- Jurisdiction known: 0-10 pts
- Missing evidence penalty: -5 per missing type

### Recommendations
- **STRONG**: Composite >= 55, <= 1 risk flag
- **MODERATE**: Composite >= 40
- **WEAK**: Composite >= 25
- **SKIP**: Composite < 25

## Pre-Score Rubric (Ingest Gate)

No LLM required. Pure keyword/signal matching:

- Artifact keywords (bodycam, interrogation, etc.): +15 each, cap 45
- Video platform URLs in text: +15-20 each, cap 20
- Lifecycle keywords (sentenced, convicted, etc.): +3-5 each, cap 15
- Jurisdiction bonuses (Florida Sunshine Law, court video): +10 each, cap 20

Default threshold: score >= 20 to proceed to normalization.
