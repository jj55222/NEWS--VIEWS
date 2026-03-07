# NEWS--VIEWS Bodycam Bot (Rebuild V1)

This repository implements a routing-first rebuild of the bodycam bot. The pipeline transforms raw footage candidates into structured incidents, enriches them, scores value vs researchability, and emits editor-ready case packets.

## Pipeline stages
1. Ingest and route candidates from trusted source lanes.
2. Normalize each candidate into the canonical incident schema.
3. Enrich with corroborating artifacts and explicit misses.
4. Score story value, researchability, evidence completeness, and risk.
5. Generate case packets for editorial triage.

## Run
```bash
python -m src.pipeline --input <path/to/candidates.json> --output runs/latest
```

## Outputs
- `audit.jsonl`
- `incidents.jsonl`
- `packets/`
- `packets_index.json`
- `batch_metrics.json`

See `docs/BODYCAM_BOT_REBUILD.md` for the output contract and system posture.
