# Bodycam Bot Rebuild

## Core Objective

Rebuild the NEWS -> VIEWS system so it can take raw body-worn camera footage leads and produce **structured, evidence-backed case packets**.

This is NOT optimized for fully autonomous content generation on the first pass. It's optimized for a **reliable research pipeline**.

## Architecture

```
ingest/ -> normalize/ -> enrich/ -> score/ -> packets/
```

### 1. Ingest (Candidate Harvesting)
- Searches Exa for crime articles across configured regions
- Pre-scores articles using keyword/signal matching (no LLM)
- Routes candidates: articles below prescore threshold are rejected
- **Ingest routes; it does NOT perform final editorial judgment**

### 2. Normalize (Incident Builder)
- LLM extracts structured fields from raw article text
- Produces a canonical Incident object with: people, jurisdiction, charges, agency, narrative hook
- Every viable candidate gets a normalized Incident

### 3. Enrich (Evidence Search)
- Searches for supporting artifacts: bodycam, interrogation, court video, surveillance
- Classifies source tiers: official > news > repost > unknown
- **Tracks missing evidence explicitly** (what we searched for and didn't find)

### 4. Score (Dual Scoring)
- **Story value** and **researchability** are scored separately
- A case can be a great story but hard to research, or vice versa
- Risk flags identify problems a human editor should review

### 5. Packets (Output)
- Generates a structured JSON case packet per candidate
- Includes: incident data, evidence list, scores, recommendation, decision reasoning
- Recommendation: STRONG / MODERATE / WEAK / SKIP

## Non-Negotiable Rules

1. Ingest routes; it does not perform final editorial judgment
2. Every viable candidate gets a normalized incident object
3. Story value and researchability must be separate scores
4. Every decision must have an auditable reason
5. Missing evidence must be tracked explicitly
6. Output should be a case packet, not just a label

## File Structure

```
/docs/                    # This documentation
/src/
  ingest/                 # Candidate harvesting + routing
  normalize/              # LLM-powered incident builder
  enrich/                 # Evidence search + attachment
  score/                  # Dual scoring engine
  packets/                # Case packet generator
  common/                 # Schema, config, logging
/archive/                 # Legacy code preserved for reference
/tests/                   # Unit tests
/config/                  # Configuration templates
```
