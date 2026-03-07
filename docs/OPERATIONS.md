# Operations

## Input contract
Pipeline input is a JSON array with:
- `source_url`
- `title`
- `publisher`
- `published_date`
- `media_type`

Optional:
- `description`
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
- `audit.jsonl`: full routing and enrichment decision log.
- `incidents.jsonl`: required normalized incident records.
- `packets/*.json`: editor-ready case packets.
- `packets_index.json`: packet index.
- `batch_metrics.json`: run-level metrics snapshot.

## Routing policy
Ingest is routing-first. Candidates are routed to enrichment/review/archive lanes and still leave an auditable trace; no early binary pass/kill gate is used.
