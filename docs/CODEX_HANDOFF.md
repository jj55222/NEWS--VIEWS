# Codex Handoff

## What Was Built

Clean-slate rebuild of the NEWS -> VIEWS bodycam bot pipeline.

### Architecture: ingest -> normalize -> enrich -> score -> packets

1. **Ingest** (`src/ingest/`): Exa search + deterministic prescore + routing
2. **Normalize** (`src/normalize/`): LLM extracts structured Incident objects
3. **Enrich** (`src/enrich/`): Multi-source evidence search with tier classification
4. **Score** (`src/score/`): Separate story-value and researchability scoring
5. **Packets** (`src/packets/`): Structured JSON case packet generation

### Key Design Decisions

- **Prescore before LLM**: Keyword-based gating saves 30-50% of LLM calls
- **Separate scores**: Story value != researchability. A great story with no evidence is still weak.
- **Explicit missing evidence**: We track what we searched for and didn't find
- **Auditable decisions**: Every routing/scoring decision has a logged reason
- **No editorial judgment in ingest**: Ingest routes, it doesn't decide

### What's Working

- Full pipeline dry-run (`python run_pipeline.py --dry-run`)
- All unit tests passing (schema, prescore, router, scorer)
- Canonical Incident schema with Evidence attachments
- Packet output to `output/packets/` with audit logs

### What's Next (Expansion Hooks)

- Google Sheets integration (config is there, just needs wiring)
- YouTube Data API connector for official agency channels
- CourtListener API for court records
- Case deduplication via case_key generation
- Caching layer (SQLite) for repeated runs
- KPI instrumentation (run_log.json per execution)

### How to Validate

```bash
python run_pipeline.py --dry-run     # Full pipeline, no API calls
python -m tests.test_schema          # Data model tests
python -m tests.test_prescore        # Prescore tests
python -m tests.test_router          # Routing tests
python -m tests.test_scorer          # Scoring tests
```
