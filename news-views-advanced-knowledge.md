# NEWS → VIEWS: Advanced Knowledge

> **Purpose**: This is the master context document for Claude Code. Read this file first before making ANY changes to the codebase. It contains architecture decisions, invariants you must not break, the implementation roadmap, and operational knowledge accumulated through iteration.
>
> **Usage**: `Read @news-views-advanced-knowledge.md and understand.`

---

## INDEX

1. [System Overview](#1-system-overview) — What this project does and how it works
2. [Pipeline Invariants](#2-pipeline-invariants) — Things you MUST NOT break
3. [Codebase Map](#3-codebase-map) — What each file does and how they connect
4. [Architecture Decisions](#4-architecture-decisions) — Why things are built this way
5. [Implementation Roadmap](#5-implementation-roadmap) — Phased plan with priorities
6. [Colab Runbook](#6-colab-runbook) — How to run and test
7. [Known Issues & Tech Debt](#7-known-issues--tech-debt) — Current problems to be aware of
8. [Changelog](#8-changelog) — What changed, why, how validated

---

## 1. System Overview

NEWS → VIEWS is a discovery pipeline for a true crime content creator. It finds criminal cases with recoverable video artifacts (bodycam, interrogation, surveillance, court footage), triages them for narrative quality, and tracks them through a Google Sheets workflow toward production.

### Core Flow

```
Regions & Sources (Google Sheet config)
        │
        ▼
   exa_pipeline.py          ← Pass 1: Exa search + LLM triage
        │
        ▼
   NEWS INTAKE (sheet)       ← All articles land here with verdict
        │
        ▼ (PASS cases only)
   CASE ANCHOR & FOOTAGE CHECK (sheet)
        │
        ▼
   artifact_hunter.py        ← Pass 2: Deep artifact search + LLM assessment
        │
        ▼
   Footage columns populated
        │
        ▼
   GREENLIGHT → CASE BUNDLE  ← Manual editorial decision
```

### Key Concepts

- **Region**: A metro area or jurisdiction (e.g., "SF" = San Francisco). Defined in the "Regions & Sources" sheet tab with metro tokens, date windows, and source config.
- **Triage**: LLM evaluates whether an article describes a case that is heinous, morally compelling, and viable for content. Verdict is PASS or KILL.
- **Artifact**: A recoverable video source — bodycam/BWC, custodial interrogation, surveillance footage, court/trial video, or press conference footage.
- **Assessment**: LLM evaluates search results to determine whether artifacts likely exist. Current categories: ENOUGH / BORDERLINE / INSUFFICIENT.

### External Dependencies

| Service | Purpose | Auth |
|---------|---------|------|
| Google Sheets | Workflow orchestration, status tracking | Service account JSON |
| Exa API | Semantic web search for articles and artifacts | API key |
| OpenRouter | LLM access (currently deepseek/deepseek-v3.2) | API key |

---

## 2. Pipeline Invariants

**These are hard constraints. Do NOT violate them when making changes.**

### Sheet Schema (NEWS INTAKE)

The following columns MUST exist in this exact order. Scripts write by column index.

| Col | Header | Written By |
|-----|--------|-----------|
| A | Region_ID | exa_pipeline |
| B | Outlet | exa_pipeline |
| C | Headline | exa_pipeline |
| D | Article URL | exa_pipeline |
| E | Pub Year | exa_pipeline |
| F | Triage JSON | exa_pipeline |
| G | Story Summary | exa_pipeline |
| H | Why Disturbing | exa_pipeline |
| I | Crime Type | exa_pipeline |
| J | LLM Verdict | exa_pipeline |
| K | Human Override | manual |
| L | Final Verdict | exa_pipeline |
| M | Viability Score | exa_pipeline |
| N | Artifact Queries | exa_pipeline |

### Sheet Schema (CASE ANCHOR & FOOTAGE CHECK)

| Col | Header | Written By |
|-----|--------|-----------|
| A | Case_Key | (planned — not yet implemented) |
| B | Intake_ID | exa_pipeline (row number reference) |
| C | Defendant Name(s) | exa_pipeline |
| D | Victim Role(s) | exa_pipeline |
| E | (reserved) | — |
| F | Jurisdiction | exa_pipeline |
| G | Body Cam | artifact_hunter (col 7) |
| H | Interrogation | artifact_hunter (col 8) |
| I | Court Video | artifact_hunter (col 9) |
| J | Source URLs | artifact_hunter (col 10) |
| K | Footage Assessment | artifact_hunter (col 11) |

### Environment Variables (required)

```
SHEET_ID          — Google Sheet ID (from URL)
EXA_API_KEY       — Exa API key
OPENROUTER_API_KEY — OpenRouter API key
SERVICE_ACCOUNT_PATH — Path to Google service account JSON (default: ./service_account.json)
```

### Rules

1. **Never change column order** in sheet writes without updating ALL scripts that reference column indices.
2. **Never commit secrets** — `.env`, `service_account.json`, and all `*.json` are gitignored.
3. **Article URL is the dedup key** in NEWS INTAKE — `get_existing_urls()` depends on this.
4. **artifact_hunter writes by cell index** (row_idx, col_number) — if CASE ANCHOR columns shift, those hardcoded indices break.
5. **OpenRouter requires extra headers** — `HTTP-Referer` and `X-Title` must be present on all LLM calls.
6. **Rate limiting**: Exa calls have 0.3s sleep, LLM calls have 0.5s sleep, region transitions have 1s sleep. Do not remove these.
7. **All scripts must work from CLI** with `--check`, `--test`, and `--limit` flags for safe iteration.

---

## 3. Codebase Map

### `exa_pipeline.py` (Pass 1 — News Intake)

- **Entry**: `run_pipeline(test_mode, single_region)`
- **Flow**: Load regions from sheet → Exa search per region → LLM triage per article → Write to NEWS INTAKE → Promote PASS cases to CASE ANCHOR
- **Key functions**:
  - `search_region()` — Exa semantic search with date/content filtering
  - `triage_article()` — LLM structured JSON triage (PASS/KILL)
  - `append_intake_row()` — Write to NEWS INTAKE
  - `promote_to_anchor()` — Copy PASS case to CASE ANCHOR
- **Config knobs**: `MAX_RESULTS_PER_REGION`, `MIN_ARTICLE_LENGTH`, `DEFAULT_START_DATE`, `DEFAULT_END_DATE`
- **LLM model**: Set via `OPENROUTER_MODEL` env var (default: `deepseek/deepseek-v3.2`)

### `artifact_hunter.py` (Pass 2 — Footage Discovery)

- **Entry**: `run_artifact_hunter(limit)`
- **Flow**: Read CASE ANCHOR → For each unassessed case → Search for artifacts → LLM assessment → Write results back to CASE ANCHOR
- **Key functions**:
  - `search_artifacts()` — Multi-source artifact search (video platforms, Reddit, PACER/CourtListener, jurisdiction portals)
  - `assess_artifacts()` — LLM assessment of artifact availability
  - `search_reddit_cases()` — Reddit-specific case discussion search
  - `search_pacer()` — CourtListener/PACER record search
- **Writes to**: CASE ANCHOR columns G-K (by cell index)

### `jurisdiction_portals.py` (Knowledge Layer)

- **Purpose**: Static registry of 20 regions across 6 states with agency details, YouTube channels, transparency portals, court info, news domains
- **Key data**: `JURISDICTION_PORTALS` dict, `TRUE_CRIME_CHANNELS` list
- **Helper functions**: `build_jurisdiction_queries()`, `get_agency_youtube_channels()`, `get_transparency_portals()`, `get_search_domains_for_region()`
- **States covered**: CA (5 regions), FL (4), AZ (3), WA (2), CO (3), TX (3)

### File Dependencies

```
exa_pipeline.py
  └── (no local imports — standalone)

artifact_hunter.py
  └── jurisdiction_portals.py
        └── build_jurisdiction_queries()
        └── get_agency_youtube_channels()
        └── get_transparency_portals()
        └── get_search_domains_for_region()
        └── extract_domain()
```

---

## 4. Architecture Decisions

### Why Google Sheets (not a database)?

Sheets is the "source of truth" because the operator is non-technical and needs visual control. Every pipeline output is immediately visible. Manual overrides (Human Override column) coexist naturally with automated writes.

### Why OpenRouter (not direct API)?

Single billing endpoint, model flexibility — can switch between deepseek, gpt-4o-mini, claude models without code changes. Just change `OPENROUTER_MODEL` in `.env`.

### Why Exa (not Google/Bing search)?

Exa's semantic search returns higher-quality crime article matches than keyword search. Supports `include_domains`, `start_published_date`, `end_published_date` filters that are critical for targeted discovery. Also returns full text content in a single call.

### Why two-pass (not single-pass)?

Pass 1 (exa_pipeline) is **breadth** — find articles, triage for narrative quality. Pass 2 (artifact_hunter) is **depth** — expensive per-case artifact search. Separating them means you only spend artifact-hunting credits on cases that passed triage.

---

## 5. Implementation Roadmap

### Phase 0: Project Memory & Guardrails (DO THIS FIRST)

**Goal**: Make Claude Code iteration safe and self-documenting.

- [ ] Add this file (`news-views-advanced-knowledge.md`) to repo root
- [ ] Add `CLAUDE.md` to repo root with: `Read @news-views-advanced-knowledge.md before making any changes.`
- [ ] Ensure all scripts pass `--check` cleanly before any changes

**Validation**: `python exa_pipeline.py --check && python artifact_hunter.py --check`

---

### Phase 1: Evidence-First Pre-Score Gating (HIGHEST ROI)

**Goal**: Score cases for artifact likelihood *before* LLM triage to avoid wasting credits on low-artifact cases.

**What to build**:

1. **`evidence_prescore.py`** — New module. Takes article text + URL + metadata, returns `artifact_pre_score` (0-100) based on:
   - Keyword hits: "bodycam", "BWC", "body-worn camera", "custodial interview", "interrogation video", "surveillance footage", "trial livestream", "dashcam" → +15 each
   - Video platform URL presence in article text (youtube.com, vimeo.com) → +20
   - Jurisdiction/agency token matches from `jurisdiction_portals.py` → +10
   - Lifecycle indicators: "sentenced", "convicted", "plea", "trial", "verdict" → +5 each (case is far enough along that artifacts are likely released)
   - Florida region bonus → +10 (Sunshine Law = better records access)
   - Court has video capability (`has_court_video()`) → +10

2. **Integration into `exa_pipeline.py`**:
   - Call `evidence_prescore()` BEFORE `triage_article()`
   - Add `Artifact_Pre_Score` column to NEWS INTAKE (col O — appended, does not shift existing columns)
   - Add `Evidence_Intent_Matches` column (col P — pipe-delimited matched keywords)
   - Only send articles with `artifact_pre_score >= 20` to LLM triage (configurable threshold via `MIN_PRESCORE` env var, default 20)
   - Articles below threshold get auto-KILL with `kill_reason: "Low artifact likelihood (pre-score: {score})"`

3. **New .env vars**:
   ```
   MIN_PRESCORE=20           # Minimum pre-score to proceed to LLM triage
   ```

**Expected impact**: 30-50% reduction in LLM triage calls. Higher artifact yield on PASS cases.

**Validation**: Run `--test` mode, compare pre-score distribution. Cases that score <20 should be ones you wouldn't have wanted to triage anyway.

**Invariant check**: NEWS INTAKE columns A-N unchanged. New columns appended only.

---

### Phase 2: Case Key Deduplication

**Goal**: Stop rediscovering the same case through different articles.

**What to build**:

1. **`case_identity.py`** — New module. Generates a `case_key` from triage output:
   ```
   normalize(defendant_last_name) + "_" + normalize(county) + "_" + incident_year
   ```
   - Normalization: lowercase, strip punctuation, normalize whitespace, common abbreviation expansion ("co." → "county", "st." → "saint")
   - If `case_number` exists in triage, use it as primary key instead
   - Collision handling: if a case_key matches an existing row, flag it as `DUPLICATE_OF:{row_number}` rather than auto-merging

2. **Integration**:
   - Call after triage in `exa_pipeline.py`
   - Write `Case_Key` to CASE ANCHOR col A (currently empty)
   - Before promoting to CASE ANCHOR, check if case_key already exists → skip promotion, note duplicate in NEWS INTAKE
   - Add `Case_Key` to NEWS INTAKE (col Q — appended)

3. **Dedup lookup**: Load existing case keys from CASE ANCHOR at pipeline start, check before promoting.

**Validation**: Run on a region known to have repeat cases. Duplicates should be caught and logged.

---

### Phase 3: Per-Artifact Confidence Engine

**Goal**: Replace coarse ENOUGH/BORDERLINE/INSUFFICIENT with explainable per-artifact scores.

**What to change**:

1. **Update `assess_artifacts()` prompt in `artifact_hunter.py`**:
   - New output schema:
     ```json
     {
       "body_cam": {"confidence": 0-100, "source_tier": "official|news|repost|none", "best_url": "", "notes": ""},
       "interrogation": {"confidence": 0-100, "source_tier": "...", "best_url": "", "notes": ""},
       "court_video": {"confidence": 0-100, "source_tier": "...", "best_url": "", "notes": ""},
       "surveillance": {"confidence": 0-100, "source_tier": "...", "best_url": "", "notes": ""},
       "composite_score": 0-100,
       "artifact_types_found": 2,
       "recommendation": "STRONG|MODERATE|WEAK|SKIP",
       "reasoning": ""
     }
     ```
   - Confidence decomposition factors: source trust tier, entity match quality, temporal alignment, artifact specificity

2. **Sheet changes**: CASE ANCHOR gets richer data — but maintain backward compatibility by writing the `recommendation` to the existing Footage Assessment column (col K).

3. **Store full assessment JSON** in a new column or in artifact_candidates_json for reproducibility.

**Validation**: Run `artifact_hunter.py --limit 3`, manually verify that confidence scores align with your intuition about artifact quality.

---

### Phase 4: Deterministic Connectors (Future)

**Goal**: Supplement probabilistic Exa search with reliable API-based discovery.

**Connectors to build (in priority order)**:

1. **YouTube Data API connector** — Enumerate uploads from official agency channels in `jurisdiction_portals.py`. Match against defendant names/case numbers. Highest ROI because agencies frequently publish critical incident videos.
   - Needs: YouTube Data API key, quota management (10,000 units/day)
   - Output: `ArtifactRecord` with source_tier="official"

2. **CourtListener API connector** — Structured query against CourtListener for case records, transcripts, audio.
   - Needs: Free API access (no key required for basic)
   - Output: Case metadata, docket entries, linked documents

3. **Caching layer** — SQLite database persisted to Google Drive (Colab-friendly). Store discovered artifacts so repeated runs don't re-search.
   - Tables: `cases`, `artifacts`, `search_runs`
   - On each run: check cache first, only search for cases with no recent results

**These are Phase 4 because they require new API integrations and more complex error handling. Do not attempt until Phases 1-3 are stable.**

---

### Phase 5: KPI Instrumentation (Future)

**Goal**: Make improvement measurable.

Track per run:
- Regions processed, candidates found, candidates triaged, PASS count
- Exa credits used (estimate from call count), LLM tokens used
- Pre-score distribution (histogram)
- Duplicate cases caught
- Artifact yield: % of PASS cases with ≥2 artifact types

Store as a `run_log.json` appended after each execution.

---

## 6. Colab Runbook

### Setup Cell

```python
# Cell 1: Setup
!pip install gspread google-auth exa-py openai python-dotenv -q

import os
os.environ['SHEET_ID'] = 'your-sheet-id'
os.environ['EXA_API_KEY'] = 'your-exa-key'
os.environ['OPENROUTER_API_KEY'] = 'your-openrouter-key'
os.environ['SERVICE_ACCOUNT_PATH'] = '/content/service_account.json'

# Upload service_account.json via Colab file upload
from google.colab import files
uploaded = files.upload()  # Upload service_account.json
```

### Run Cell

```python
# Cell 2: Run pipeline
!python exa_pipeline.py --test
```

### Artifact Hunt Cell

```python
# Cell 3: Hunt artifacts
!python artifact_hunter.py --limit 5
```

### Tips

- If runtime restarts, re-run Cell 1 (env vars are lost)
- Service account JSON persists in `/content/` until runtime recycles
- For Drive persistence: mount Drive and set `SERVICE_ACCOUNT_PATH` to a Drive path
- Always run `--check` first after a restart to verify credentials

---

## 7. Known Issues & Tech Debt

### Critical

- **Hardcoded column indices in `artifact_hunter.py`**: Lines writing to CASE ANCHOR use `update_cell(row_idx, 7, ...)` through `update_cell(row_idx, 11, ...)`. If CASE ANCHOR columns change, these silently write to wrong columns. → **Phase 2 should add column lookup by header name.**
- **No idempotency**: Re-running the pipeline on the same region can produce duplicate triage calls if the article URL check fails (e.g., trailing slash differences). → **Phase 2 case_key dedup partially addresses this.**

### Moderate

- **JSON parsing is fragile**: Both triage and assessment strip markdown code fences but don't handle all LLM output variations. Consider a retry-with-repair pattern.
- **No budget caps**: Nothing stops the pipeline from burning through Exa/OpenRouter credits if pointed at many regions. → **Phase 1 pre-score gating helps; Phase 5 instrumentation makes it visible.**
- **`assess_artifacts()` gets messy search results**: Reddit and PACER searches often return irrelevant results that confuse the LLM assessment. Consider filtering by relevance score before sending to LLM.

### Low Priority

- **`TRUE_CRIME_CHANNELS` in jurisdiction_portals.py not used**: The list exists but no code searches these channels. → Phase 4 YouTube connector would use this.
- **Some YouTube channel URLs in jurisdiction_portals.py appear to be placeholders** (e.g., `@ABORINGDYSTOPIA` appears multiple times for different agencies — likely copy-paste errors). Audit and fix.

---

## 8. Changelog

*Format: `[DATE] WHAT — WHY — HOW VALIDATED — WHAT IT AFFECTS`*

```
[2025-XX-XX] Initial advanced-knowledge.md created
  WHY: Enable Claude Code to iterate safely with full project context
  VALIDATED: Manual review of codebase against documented invariants
  AFFECTS: No code changes — documentation only
```

<!-- 
TEMPLATE FOR FUTURE ENTRIES:
[YYYY-MM-DD] Brief description of change
  WHY: Motivation
  VALIDATED: How you confirmed it works (e.g., "ran --test, 3 regions, 0 errors")
  AFFECTS: Which files/sheets changed
-->
