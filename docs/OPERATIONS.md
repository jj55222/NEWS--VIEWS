# Operations

## Input contract
Pipeline expects a JSON array with fields:
- `source_url`
- `title`
- `description`
- `publisher`
- `published_date`
- `media_type`

Optional:
- `transcript_available`
- `raw_footage_likelihood`
- `watermark_likelihood`
- `raw_text`
- `hints`

## Run command
```bash
python -m src.pipeline --input <input.json> --output <run_dir>
```

## Artifact contract
- `audit.jsonl`: stage trail and rationale per candidate.
- `incidents.jsonl`: normalized incident objects.
- `packets/*.json`: editor-facing packet documents.
- `packets_index.json`: all packets in one file.
- `batch_metrics.json`: run-level metrics.

## Batch metrics tracked
- total harvested,
- normalized,
- enriched,
- usable packets,
- % with >=1 corroborating source,
- % with >=2 corroborating sources,
- % potentially premature ingest kills,
- average missing-evidence count,
- average story value score,
- average researchability score.

## Manual review guidance
Use manual review for:
- strong incidents with weak metadata,
- contradictory evidence,
- high-risk but high-value cases,
- unresolved ambiguity not tractable via autonomous search.
