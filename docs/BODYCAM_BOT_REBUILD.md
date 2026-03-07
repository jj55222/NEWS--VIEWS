# Bodycam Bot Rebuild

## Goal
Build a reliable research pipeline that transforms raw body-worn camera footage candidates into evidence-backed case packets.

## Non-negotiables
1. Ingest routes only; no final editorial PASS/KILL in ingest.
2. Every viable candidate gets a normalized incident object.
3. Story value and researchability are independently scored.
4. Every decision emits auditable reasons.
5. Missing evidence is explicitly tracked.
6. Output artifact is a case packet, not a single label.

## Pipeline stages
1. `src/ingest/`: candidate harvesting from curated raw sources.
2. `src/normalize/`: canonical incident normalization + confidence map.
3. `src/enrich/`: enrichment attempts and result capture.
4. `src/score/`: separate scoring for story value and researchability.
5. `src/packet/`: packet assembly for editor handoff.

## Logging posture
Each candidate keeps:
- provenance,
- extracted fields,
- field confidence,
- routing decisions and reasons,
- enrichment queries attempted + found/not-found,
- score breakdown,
- packet status.

All logs are JSONL for future tuning and replay.
