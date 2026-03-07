# Operations

## Run pipeline
```bash
python -m src.pipeline --input config/curated_sources.sample.json --output runs/latest
```

## Outputs
- `runs/<run>/audit.jsonl`: candidate-by-candidate audit trail.
- `runs/<run>/incidents.jsonl`: normalized incidents.
- `runs/<run>/packets/*.json`: final case packets.

## Input format
Curated source input is a JSON array where each item includes at minimum:
- `source_url`
- `source_title`
- `source_type`
- `channel_or_publisher`
- `publish_date`

Optional raw hints can include:
- `agency`, `incident_date`, `location`, `people`, `incident_type`, `narrative_hook`.

## Tuning workflow
1. Run pipeline on known-good sample set.
2. Review `audit.jsonl` for:
   - low-confidence fields,
   - false holds/rejects,
   - enrichment misses.
3. Adjust extractors/scoring weights.
4. Re-run and compare route distribution.
