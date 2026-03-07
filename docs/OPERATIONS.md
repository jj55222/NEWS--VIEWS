# Operations Guide

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Test the pipeline with synthetic data (no API keys needed)
python run_pipeline.py --dry-run

# Validate configuration
python run_pipeline.py --check

# Run unit tests
python -m tests.test_schema
python -m tests.test_prescore
python -m tests.test_router
python -m tests.test_scorer
```

## Configuration

Copy `.env.template` to `.env` and fill in your API keys:

```
EXA_API_KEY=your-exa-key
OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_MODEL=deepseek/deepseek-v3.2
```

Optional (for Google Sheets integration):
```
SHEET_ID=your-google-sheet-id
SERVICE_ACCOUNT_PATH=./service_account.json
```

## Pipeline Stages

### 1. Dry Run (no API calls)
```bash
python run_pipeline.py --dry-run
```
Tests the full pipeline with synthetic data. Validates all stages connect.

### 2. Full Run
```bash
python run_pipeline.py                  # Default region
python run_pipeline.py --region SF      # Single region
python run_pipeline.py --limit 5        # Cap candidates
```

## Output

All output goes to `./output/`:
- `output/packets/*.json` — Case packets (the final deliverable)
- `output/logs/*.json` — Audit logs per incident

Each packet JSON contains:
- Incident data (people, charges, jurisdiction)
- Evidence list with source tiers
- Story value + researchability scores
- Recommendation (STRONG/MODERATE/WEAK/SKIP)
- Decision reasoning

## Logging

Every pipeline decision is logged with:
- Stage (ingest/normalize/enrich/score/packet)
- Action taken
- Decision and reason
- Confidence level
- Fields extracted

Logs are written per-incident to `output/logs/{incident_id}.json`.

## Legacy Code

Original pipeline code is preserved in `/archive/`:
- `exa_pipeline.py` — Original Pass 1 news intake
- `artifact_hunter.py` — Original Pass 2 artifact search
- `jurisdiction_portals.py` — Region/agency knowledge base (still used by prescore)
