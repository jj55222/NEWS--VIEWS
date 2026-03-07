# NEWS--VIEWS Bodycam Bot (Rebuild Lane)

This repository is a clean rebuild of the bodycam bot. The pipeline is designed to transform raw footage candidates into structured, explainable, evidence-backed case packets.

## Operating posture
- Clean branch + narrow MVP.
- Strict canonical incident schema.
- Staged routing (not binary early pass/kill).
- Rich candidate-level audit logs.
- Editor-facing packet output.

## Pipeline stages
1. Harvest candidate source metadata and provenance.
2. Normalize into a canonical incident object with confidence and uncertainty notes.
3. Enrich with corroboration lookups, including explicit failure reasons.
4. Score story value, researchability, evidence completeness, and risk.
5. Packetize for editorial review, including a missing-evidence ledger.

## Run
```bash
python -m src.pipeline --input <path/to/candidates.json> --output runs/latest
```

## Batch outputs
- `audit.jsonl`: per-candidate auditable stage trail.
- `incidents.jsonl`: normalized and scored incidents.
- `packets/`: packet JSON files for editor use.
- `packets_index.json`: packet list.
- `batch_metrics.json`: batch success metrics.

See `docs/` for rebuild spec, rubric, and operations notes.
