# NEWS--VIEWS Bodycam Bot Rebuild

This branch contains a clean-slate, auditable research pipeline for converting raw body-worn camera leads into structured case packets.

## Quick start

```bash
python -m src.pipeline --input config/curated_sources.sample.json --output runs/latest
```

## What this does

1. Harvests curated raw-source candidates.
2. Normalizes each candidate into a canonical incident object.
3. Runs lightweight enrichment hooks and logs all attempts.
4. Scores story value and researchability separately.
5. Produces packet JSON files plus JSONL audit logs.

See:
- `docs/BODYCAM_BOT_REBUILD.md`
- `docs/SYSTEM_RUBRIC.md`
- `docs/OPERATIONS.md`
- `docs/CODEX_HANDOFF.md`
