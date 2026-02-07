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

## Goals & Intentions (v3 — Feb 2026)

The pipeline is being evolved from a **news discovery tool** into a **primary-source artifact retrieval engine**. The goal is to find not just articles about crimes, but the actual recoverable evidence: bodycam footage, interrogation recordings, 911/dispatch audio, court filings, and docket documents — the raw material needed to produce EWU/Dr. Insanity-tier true crime content.

**Key shifts**:
- **From semantic search to multi-backend funnel**: Exa is great for finding articles but expensive and noisy for hunting specific artifacts. The new architecture uses YouTube/Vimeo for video, Google PSE for documents/records, and Exa only as a fallback.
- **From URL-only assessment to content-aware assessment**: The LLM now sees actual page snippets (not just URLs), reducing hallucinated confidence scores.
- **From single-model to model split**: Heavy model for triage (reasoning matters), light model (Gemini Flash) for artifact assessment (classification is simpler).
- **From blind search to cost-ordered funnel**: Free sources first (existing sheet data), then cheap APIs (YouTube/Vimeo), then moderate (PSE), then expensive (Exa). Skip LLM entirely when heuristics suffice.
- **From news-only to primary sources**: Docket filings, probable cause affidavits, 911 call audio, dispatch recordings — these are what differentiate surface-level coverage from deep investigative content.

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
| Exa API | Semantic web search for intake discovery | API key |
| Google PSE | Keyword web search for artifact hunting | API key + CX ID |
| YouTube Data API | Video search for bodycam/interrogation/court footage | API key |
| Vimeo API | Video search (supplement to YouTube) | Access token |
| OpenRouter | LLM access (model split: intake vs artifact) | API key |

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
| L | Docket/Court Records | artifact_hunter (col 12) |
| M | 911/Dispatch Audio | artifact_hunter (col 13) |
| N | Primary Source Score | artifact_hunter (col 14) |
| O | Evidence Depth Score | artifact_hunter (col 15) |
| P | Search Telemetry | artifact_hunter (col 16) |

### Environment Variables (required)

```
SHEET_ID               — Google Sheet ID (from URL)
EXA_API_KEY            — Exa API key (intake discovery)
OPENROUTER_API_KEY     — OpenRouter API key
SERVICE_ACCOUNT_PATH   — Path to Google service account JSON (default: ./service_account.json)
```

### Environment Variables (optional — artifact hunting backends)

```
GOOGLE_PSE_API_KEY     — Google Programmable Search Engine API key
GOOGLE_PSE_CX          — Google PSE search engine ID
YOUTUBE_API_KEY        — YouTube Data API v3 key
VIMEO_ACCESS_TOKEN     — Vimeo API access token
OPENROUTER_MODEL_INTAKE  — LLM for triage (default: OPENROUTER_MODEL or deepseek/deepseek-v3.2)
OPENROUTER_MODEL_ARTIFACT — LLM for artifact assessment (default: google/gemini-2.0-flash-001)
MIN_PRESCORE           — Minimum pre-score for LLM triage (default: 20)
ALLOW_EXA_FALLBACK     — Use Exa as fallback in artifact_hunter (default: true)
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
- **Flow**: Load regions → Exa search → Pre-score → LLM triage → Write to NEWS INTAKE → Promote PASS cases to CASE ANCHOR
- **Key functions**:
  - `search_region()` — Exa semantic search with date/content filtering
  - `triage_article()` — LLM structured JSON triage (PASS/KILL)
  - `append_intake_row()` — Write to NEWS INTAKE (includes prescore cols O-P)
  - `promote_to_anchor()` — Copy PASS case to CASE ANCHOR
- **Config knobs**: `MAX_RESULTS_PER_REGION`, `MIN_ARTICLE_LENGTH`, `DEFAULT_START_DATE`, `DEFAULT_END_DATE`, `MIN_PRESCORE`
- **LLM model**: Set via `OPENROUTER_MODEL_INTAKE` (falls back to `OPENROUTER_MODEL`, default: `deepseek/deepseek-v3.2`)

### `artifact_hunter.py` (Pass 2 — Artifact Discovery, v3)

- **Entry**: `run_artifact_hunter(limit, dry_run)`
- **Flow**: Read CASE ANCHOR → For each unassessed case → Multi-step search funnel → Heuristic or LLM assessment → Write results back
- **Search funnel** (cost-ordered):
  1. **Step 0**: Parse existing sources from sheet (free)
  2. **Step 1**: YouTube + Vimeo API search (video-specific)
  3. **Step 2**: Google PSE web search (keyword, bucketized)
  4. **Step 3**: Exa fallback (only if <3 results found, capped at 2 queries)
  5. **Step 4**: Heuristic skip or LLM assessment
- **Key functions**:
  - `parse_existing_sources()` — Extract URLs already in sheet
  - `search_videos()` — YouTube + Vimeo via `search_backends.py`
  - `search_web()` — Google PSE with query buckets (bodycam, interrogation, court, docket, dispatch)
  - `search_exa_fallback()` — Optional Exa when other backends return sparse results
  - `heuristic_assess()` — Skip LLM when evidence is obviously ENOUGH or INSUFFICIENT
  - `assess_artifacts()` — LLM assessment with expanded schema (primary_source_score, evidence_depth_score)
- **Caps**: `MAX_RESULTS_PER_BUCKET = 6`, `MAX_TOTAL_RESULTS_FOR_LLM = 25`
- **Writes to**: CASE ANCHOR columns G-P (by cell index)
- **LLM model**: Set via `OPENROUTER_MODEL_ARTIFACT` (default: `google/gemini-2.0-flash-001`)
- **CLI flags**: `--limit N`, `--dry-run`, `--check`
- **Telemetry**: Per-case dict tracking youtube_hits, vimeo_hits, pse_hits, exa_fallback_used, llm_used

### `search_backends.py` (Search API Clients)

- **Purpose**: Unified interface for Google PSE, YouTube Data API v3, and Vimeo API
- **Key functions**:
  - `web_search_pse(query, num)` — Google Programmable Search Engine
  - `youtube_search(defendant, jurisdiction, incident_year, hints)` — Multi-query YouTube search with dedup
  - `vimeo_search(defendant, jurisdiction, incident_year, hints)` — Multi-query Vimeo search with dedup
  - `check_search_credentials()` — Returns dict of which backends are configured
- **Shared**: All functions return consistent `{"url", "title", "snippet", "source"}` schema
- **Retry**: Exponential backoff for 429/5xx errors, 3 attempts max

### `evidence_prescore.py` (Pre-LLM Gating)

- **Purpose**: Score articles for artifact likelihood *before* LLM triage to reduce token spend
- **Key function**: `evidence_prescore(article_text, article_url, region_id) -> Dict`
- **Scoring**: keyword hits (+15), video URLs (+20), agency match (+10), lifecycle indicators (+5), sunshine state bonus (+10), court video bonus (+10)
- **Sunshine states**: FL, TX, AZ, WA, OH, GA, UT — states with loosest public records access

### `jurisdiction_portals.py` (Knowledge Layer)

- **Purpose**: Static registry of 20 regions across 6 states with agency details, YouTube channels, transparency portals, court info, news domains
- **Key data**: `JURISDICTION_PORTALS` dict, `TRUE_CRIME_CHANNELS` list, `SUNSHINE_STATES` set, `RECORDS_DOMAINS`, `DISPATCH_DOMAINS`
- **Helper functions**: `build_jurisdiction_queries()` (6 buckets: bodycam, interrogation, court, news, docket, dispatch), `is_sunshine_state()`, `get_agency_youtube_channels()`, `get_transparency_portals()`, `get_search_domains_for_region()`
- **States covered**: CA (5 regions), FL (4), AZ (3), WA (2), CO (3), TX (3)

### File Dependencies

```
exa_pipeline.py
  └── evidence_prescore.py
        └── jurisdiction_portals.py (is_sunshine_state, has_court_video)

artifact_hunter.py
  └── search_backends.py (web_search_pse, youtube_search, vimeo_search)
  └── jurisdiction_portals.py (build_jurisdiction_queries, get_agency_youtube_channels, ...)
```

---

## 4. Architecture Decisions

### Why Google Sheets (not a database)?

Sheets is the "source of truth" because the operator is non-technical and needs visual control. Every pipeline output is immediately visible. Manual overrides (Human Override column) coexist naturally with automated writes.

### Why OpenRouter (not direct API)?

Single billing endpoint, model flexibility — can switch between deepseek, gpt-4o-mini, claude models without code changes. Just change `OPENROUTER_MODEL` in `.env`.

### Why Exa for intake (not Google/Bing search)?

Exa's semantic search returns higher-quality crime article matches than keyword search. Supports `include_domains`, `start_published_date`, `end_published_date` filters that are critical for targeted discovery. Also returns full text content in a single call.

### Why multi-backend search for artifacts (not Exa-only)?

Exa is great for semantic article discovery but expensive and noisy for artifact hunting. Replacing it with Google PSE + YouTube + Vimeo for Pass 2:
- **YouTube/Vimeo APIs** are purpose-built for video discovery — the exact artifact type we need
- **Google PSE** is cheaper ($5/1k queries or free Vertex tier) and better for keyword-specific document hunting (dockets, 911 audio, court filings)
- **Exa stays as optional fallback** — only triggered when primary backends return <3 results, capped at 2 queries per case
- **Cost reduction**: PSE + YouTube + Vimeo queries are cheaper per-call than Exa semantic search

### Why model split (intake vs artifact)?

Triage (PASS/KILL decision on article quality) benefits from a reasoning-capable model. Artifact assessment is simpler classification — just evaluating whether search results contain what we need. Using a lighter/cheaper model (Gemini Flash) for artifact assessment cuts LLM costs ~70% on Pass 2 without quality loss.

### Why heuristic assessment skip?

Two obvious cases don't need LLM evaluation: (1) Zero search results → auto INSUFFICIENT; (2) Primary source domain + 2+ video artifact types → auto ENOUGH. Skipping the LLM call in these cases saves tokens and latency.

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

### Phase 1: Evidence-First Pre-Score Gating ✅ COMPLETE

**Goal**: Score cases for artifact likelihood *before* LLM triage to avoid wasting credits on low-artifact cases.

**Delivered**:
- `evidence_prescore.py` — Pre-scores articles (0-100) based on keyword hits, video URLs, agency matches, lifecycle indicators, sunshine state bonus, court video bonus
- Integrated into `exa_pipeline.py` — articles below `MIN_PRESCORE` (default 20) get auto-KILL
- Sunshine state bonus applies to FL, TX, AZ, WA, OH, GA, UT (states with loosest public records access)
- Prescore and matched keywords written to NEWS INTAKE cols O-P

---

### Phase 1.5: Multi-Backend Artifact Search ✅ COMPLETE

**Goal**: Replace Exa-only artifact hunting with a cost-ordered, multi-backend search funnel that prioritizes free and cheap sources before expensive ones.

**Delivered**:
- `search_backends.py` — Unified clients for Google PSE, YouTube Data API, Vimeo API (stdlib urllib, no heavy deps)
- `artifact_hunter.py` v3 — Complete rewrite with 5-step search funnel:
  1. Parse existing sources (free)
  2. YouTube + Vimeo video search
  3. Google PSE web search (bucketized: bodycam, interrogation, court, docket, dispatch)
  4. Exa fallback (optional, <3 results trigger, capped at 2 queries)
  5. Heuristic or LLM assessment
- Heuristic skip: 0 results → auto INSUFFICIENT; primary source + 2+ video types → auto ENOUGH
- Model split: `OPENROUTER_MODEL_ARTIFACT` (Gemini Flash) for cheap artifact assessment
- Dry-run mode (`--dry-run`) for testing without sheet writes
- Per-case telemetry (youtube_hits, vimeo_hits, pse_hits, exa_fallback_used, llm_used)
- Expanded CASE ANCHOR schema: cols L-P for docket, dispatch, primary_source_score, evidence_depth_score, telemetry
- `jurisdiction_portals.py` updated with docket/dispatch query buckets, `RECORDS_DOMAINS`, `DISPATCH_DOMAINS`

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

### Phase 4: Deterministic Connectors (Future — partially delivered)

**Goal**: Supplement probabilistic search with reliable API-based discovery.

**Delivered in Phase 1.5**:
- ✅ YouTube Data API connector (via `search_backends.py`)
- ✅ Vimeo API connector (via `search_backends.py`)
- ✅ Google PSE for docket/court record keyword search

**Remaining**:

1. **CourtListener API connector** — Structured query against CourtListener for case records, transcripts, audio.
   - Needs: Free API access (no key required for basic)
   - Output: Case metadata, docket entries, linked documents

2. **Agency channel enumeration** — Use YouTube Data API `playlistItems` to enumerate uploads from official agency channels in `jurisdiction_portals.py`, matching against defendant names/case numbers (source_tier="official").

3. **Caching layer** — SQLite database persisted to Google Drive (Colab-friendly). Store discovered artifacts so repeated runs don't re-search.
   - Tables: `cases`, `artifacts`, `search_runs`
   - On each run: check cache first, only search for cases with no recent results

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
- **Budget caps partial**: Pre-score gating limits LLM triage calls; artifact_hunter caps Exa fallback at 2 queries and total results at 25 per case. But no global credit budget exists yet. → Phase 5 instrumentation.
- **PSE requires setup**: Google PSE needs a Custom Search Engine created at https://programmablesearchengine.google.com/ and both `GOOGLE_PSE_API_KEY` + `GOOGLE_PSE_CX` configured. Without it, artifact_hunter falls through to Exa-only.

### Low Priority

- **`TRUE_CRIME_CHANNELS` in jurisdiction_portals.py not used**: The list exists but no code searches these channels. → Phase 4 agency enumeration would use this.
- **Some YouTube channel URLs in jurisdiction_portals.py appear to be placeholders** (e.g., `@ABORINGDYSTOPIA` appears multiple times for different agencies — likely copy-paste errors). Audit and fix.

---

## 8. Changelog

*Format: `[DATE] WHAT — WHY — HOW VALIDATED — WHAT IT AFFECTS`*

```
[2025-XX-XX] Initial advanced-knowledge.md created
  WHY: Enable Claude Code to iterate safely with full project context
  VALIDATED: Manual review of codebase against documented invariants
  AFFECTS: No code changes — documentation only

[2026-02-07] Phase 1: evidence_prescore.py + exa_pipeline integration
  WHY: Reduce LLM triage costs by gating articles with deterministic pre-score
  VALIDATED: Python compile clean, integrated into exa_pipeline main loop
  AFFECTS: exa_pipeline.py, evidence_prescore.py (new), jurisdiction_portals.py (sunshine states)

[2026-02-07] Phase 1.5: Multi-backend artifact search (v3 rewrite)
  WHY: Replace expensive Exa-only artifact hunting with cost-ordered funnel (YouTube → Vimeo → PSE → Exa fallback)
  VALIDATED: All 3 files compile clean (search_backends.py, artifact_hunter.py, exa_pipeline.py)
  AFFECTS: artifact_hunter.py (complete rewrite), search_backends.py (new), exa_pipeline.py (model split),
           jurisdiction_portals.py (docket/dispatch queries, RECORDS_DOMAINS, DISPATCH_DOMAINS),
           CASE ANCHOR schema expanded to cols L-P
```

<!-- 
TEMPLATE FOR FUTURE ENTRIES:
[YYYY-MM-DD] Brief description of change
  WHY: Motivation
  VALIDATED: How you confirmed it works (e.g., "ran --test, 3 regions, 0 errors")
  AFFECTS: Which files/sheets changed
-->
