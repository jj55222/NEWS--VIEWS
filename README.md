# NEWS → VIEWS: FOIA-Free Content Pipeline

Automated pipeline for discovering, triaging, and packaging high-retention
law enforcement incident stories using only publicly available sources.
No FOIA requests required.

## Pipeline Stages

```
Ingest ─→ Enrich ─→ Triage ─→ Corroborate ─→ Package ─→ Render
  │          │         │           │             │          │
  │          │         │           │             │          └─ ffmpeg export
  │          │         │           │             └─ timeline + narration + shorts
  │          │         │           └─ press releases, news, court records
  │          │         └─ LLM scoring (PASS/MAYBE/KILL)
  │          └─ transcripts, entities, quality signals
  └─ YouTube channels, RSS feeds, press pages
```

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
# Also needed: ffmpeg, yt-dlp (system packages)
```

### 2. Configure credentials
```bash
cp .env.template .env
# Edit .env with your API keys:
#   OPENROUTER_API_KEY  — required for LLM triage/narration
#   YOUTUBE_API_KEY     — required for YouTube ingest
#   EXA_API_KEY         — optional, improves corroboration search
```

### 3. Initialize the database
```bash
python -m scripts.db --init
```

### 4. Run the full pipeline
```bash
python -m scripts.run_pipeline                    # all stages
python -m scripts.run_pipeline --dry-run          # preview without writing
python -m scripts.run_pipeline --stage ingest     # single stage
python -m scripts.run_pipeline --stages ingest,triage  # multiple stages
```

## Individual Scripts

Each script has `--dry-run` mode and CLI args:

```bash
# Ingest
python -m scripts.ingest_youtube --days 7
python -m scripts.ingest_rss --days 3
python -m scripts.scrape_pages --limit 10

# Process
python -m scripts.enrich_transcripts --status NEW --limit 100
python -m scripts.triage_llm --status NEW --limit 200

# Output
python -m scripts.corroborate --limit 20
python -m scripts.package_case --promote --limit 10
python -m scripts.render_ffmpeg --case-id <id>
```

## Repo Structure

```
config/
  policy.yaml               # Redaction rules, thresholds, LLM config
  sources_registry.json      # 228 curated source feeds (YouTube, RSS, web)
data/
  pipeline.db                # SQLite database (auto-created)
  logs/                      # Pipeline logs
scripts/
  config_loader.py           # Central config + env loading
  db.py                      # Database schema + helpers
  ingest_youtube.py          # YouTube channel ingestion
  ingest_rss.py              # RSS feed ingestion
  scrape_pages.py            # Press/transparency page scraping
  enrich_transcripts.py      # Transcripts, entities, quality signals
  triage_llm.py              # LLM-based triage scoring
  corroborate.py             # Supporting source gathering
  package_case.py            # Timeline, narration, shorts planning
  render_ffmpeg.py           # Video download, cut, caption, export
  run_pipeline.py            # Full pipeline orchestrator
outputs/
  case_bundles/              # Per-case bundles (timeline, narration, facts)
  exports/
    longform/                # Final longform videos
    shorts/                  # Final short clips
    metadata/                # Export metadata JSON
notebooks/
  run_pipeline_colab.ipynb   # Google Colab runner
```

## Triage Scoring

Candidates are scored 0–100 across five dimensions:

| Dimension | Max | What it measures |
|-----------|-----|------------------|
| Hook clarity | 25 | Can you understand what's happening in 15s? |
| Escalation | 25 | Does it intensify from routine to wild? |
| Character | 15 | Memorable quote, decision, personality? |
| Resolution | 15 | Clear outcome (arrest, twist, reveal)? |
| Quality | 10 | Video/audio production quality |
| Uniqueness | 10 | Not already saturated on other channels |

**PASS**: score >= 70 — **MAYBE**: 50–69 — **KILL**: < 50

## Source Policy

**Tier A (preferred):** Official law enforcement channels, court livestream
archives, DA/AG press releases.

**Tier B (careful):** Local newsroom YouTube (discovery only), public scanner
archives (discovery only).

**Disallowed:** FOIA request portals, leaked private footage, unclear-license
reuploads.

## Configuration

Edit without code changes:

- `config/policy.yaml` — blur rules, risk categories, pass thresholds, LLM models
- `config/sources_registry.json` — enable/disable sources, add new feeds

## Colab

Open `notebooks/run_pipeline_colab.ipynb` in Google Colab, set your API keys,
and run each cell.

## Legacy Scripts

The original pipeline scripts are preserved:
- `exa_pipeline.py` — Exa-based news intake (still works independently)
- `artifact_hunter.py` — Video artifact discovery
- `jurisdiction_portals.py` — Regional portal configuration
