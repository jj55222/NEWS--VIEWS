# NEWS → VIEWS (Claude Code Version)

Automated pipeline for discovering and triaging true crime stories.

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure credentials
```bash
cp .env.template .env
# Edit .env with your actual values
```

### 3. Add service account
Place your Google service account JSON as `service_account.json` in this directory.

## Usage

### Check everything is configured
```bash
python exa_pipeline.py --check
```

### Run news intake (test mode - 3 regions)
```bash
python exa_pipeline.py --test
```

### Run news intake (full - all 20 regions)
```bash
python exa_pipeline.py
```

### Run news intake (single region)
```bash
python exa_pipeline.py --region SF
```

### Hunt for artifacts
```bash
python artifact_hunter.py
```

### Hunt artifacts with limit
```bash
python artifact_hunter.py --limit 5
```

## With Claude Code

Just tell Claude:
- "Run the pipeline in test mode and fix any errors"
- "Process the SF region and show me what passed"
- "Hunt for artifacts for all unassessed cases"
- "Check why the pipeline is failing and fix it"

Claude Code will:
1. Run the command
2. See any errors
3. Automatically fix the code
4. Re-run until it works

## Pipeline Flow

```
Regions & Sources (your config)
        ↓
   exa_pipeline.py
        ↓
   NEWS INTAKE (auto-filled)
        ↓ (PASS cases only)
   CASE ANCHOR & FOOTAGE CHECK
        ↓
   artifact_hunter.py
        ↓
   Footage assessment added
        ↓
   GREENLIGHT → CASE BUNDLE (manual)
```

## Files

- `exa_pipeline.py` - Main news intake script
- `artifact_hunter.py` - Video artifact discovery
- `.env` - Your credentials (don't commit!)
- `.env.template` - Template for .env
- `requirements.txt` - Python dependencies
- `service_account.json` - Google auth (don't commit!)
