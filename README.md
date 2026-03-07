# NEWS -> VIEWS: Bodycam Bot Pipeline

Automated pipeline for discovering true crime cases with recoverable video evidence and producing structured, evidence-backed case packets.

## Architecture

```
ingest/ -> normalize/ -> enrich/ -> score/ -> packets/
```

- **Ingest**: Exa search + deterministic prescore + routing (no LLM)
- **Normalize**: LLM extracts structured incident data
- **Enrich**: Multi-source evidence search with tier classification
- **Score**: Separate story-value and researchability scoring
- **Packets**: JSON case packets ready for human editorial review

## Quick Start

```bash
pip install -r requirements.txt

# Test the full pipeline with synthetic data (no API keys needed)
python run_pipeline.py --dry-run

# Run unit tests
python -m tests.test_schema
python -m tests.test_prescore
python -m tests.test_router
python -m tests.test_scorer

# Full run (requires API keys in .env)
python run_pipeline.py --check
python run_pipeline.py --region SF --limit 5
```

## Output

Case packets are written to `output/packets/` as JSON files containing:
- Incident data (people, charges, jurisdiction, agency)
- Supporting evidence with source tiers (official/news/repost)
- Dual scores: story value + researchability
- Recommendation: STRONG / MODERATE / WEAK / SKIP
- Decision reasoning and risk flags

## Documentation

- [Architecture & Rebuild Plan](docs/BODYCAM_BOT_REBUILD.md)
- [Scoring Rubric & Schema](docs/SYSTEM_RUBRIC.md)
- [Operations Guide](docs/OPERATIONS.md)
- [Handoff Notes](docs/CODEX_HANDOFF.md)
