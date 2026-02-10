#!/usr/bin/env python3
"""Run the full FOIA-Free Content Pipeline end-to-end.

Orchestrates: Ingest → Enrich → Triage → Corroborate → Package → Render

Usage:
    python -m scripts.run_pipeline                      # full pipeline
    python -m scripts.run_pipeline --stage ingest       # single stage
    python -m scripts.run_pipeline --stage triage       # single stage
    python -m scripts.run_pipeline --stages ingest,triage  # multiple stages
    python -m scripts.run_pipeline --dry-run            # preview all stages
    python -m scripts.run_pipeline --days 3             # look back 3 days
"""

import argparse
import json
import sys
import time

from scripts.config_loader import ensure_dirs, setup_logging
from scripts.db import get_connection, init_db

logger = setup_logging("pipeline")

ALL_STAGES = ["ingest", "enrich", "triage", "corroborate", "package", "render"]


def run_ingest(days: int = 7, limit: int | None = None, dry_run: bool = False) -> dict:
    """Run all ingestion methods (YouTube, RSS, page scraping)."""
    combined = {"youtube": {}, "rss": {}, "pages": {}}

    from scripts.ingest_youtube import ingest as yt_ingest
    logger.info("─── YouTube Ingest ───")
    try:
        combined["youtube"] = yt_ingest(days=days, limit=limit, dry_run=dry_run)
    except Exception as exc:
        logger.error("YouTube ingest failed: %s", exc)
        combined["youtube"] = {"error": str(exc)}

    from scripts.ingest_rss import ingest as rss_ingest
    logger.info("─── RSS Ingest ───")
    try:
        combined["rss"] = rss_ingest(days=days, limit=limit, dry_run=dry_run)
    except Exception as exc:
        logger.error("RSS ingest failed: %s", exc)
        combined["rss"] = {"error": str(exc)}

    from scripts.scrape_pages import ingest as page_ingest
    logger.info("─── Page Scrape ───")
    try:
        combined["pages"] = page_ingest(limit=limit, dry_run=dry_run)
    except Exception as exc:
        logger.error("Page scrape failed: %s", exc)
        combined["pages"] = {"error": str(exc)}

    return combined


def run_enrich(limit: int = 200, dry_run: bool = False) -> dict:
    """Run transcript enrichment."""
    from scripts.enrich_transcripts import enrich
    logger.info("─── Enrich Transcripts ───")
    return enrich(status="NEW", limit=limit, dry_run=dry_run)


def run_triage(limit: int = 200, dry_run: bool = False) -> dict:
    """Run LLM triage."""
    from scripts.triage_llm import triage
    logger.info("─── LLM Triage ───")
    return triage(status="NEW", limit=limit, dry_run=dry_run)


def run_corroborate(limit: int = 50, dry_run: bool = False) -> dict:
    """Run corroboration."""
    from scripts.corroborate import corroborate
    logger.info("─── Corroborate ───")
    return corroborate(limit=limit, dry_run=dry_run)


def run_package(limit: int = 20, dry_run: bool = False) -> dict:
    """Run case packaging."""
    from scripts.package_case import package
    logger.info("─── Package Cases ───")
    return package(promote=True, limit=limit, dry_run=dry_run)


def run_render(limit: int = 10, dry_run: bool = False) -> dict:
    """Run video rendering."""
    from scripts.render_ffmpeg import render
    logger.info("─── Render ───")
    return render(limit=limit, dry_run=dry_run)


STAGE_RUNNERS = {
    "ingest": run_ingest,
    "enrich": run_enrich,
    "triage": run_triage,
    "corroborate": run_corroborate,
    "package": run_package,
    "render": run_render,
}


def run_pipeline(stages: list[str] | None = None, days: int = 7,
                 limit: int | None = None, dry_run: bool = False) -> dict:
    """Run the pipeline for specified stages (or all stages).

    Returns:
        dict mapping stage name to its results.
    """
    if stages is None:
        stages = ALL_STAGES

    ensure_dirs()
    init_db()

    results = {}
    for stage in stages:
        if stage not in STAGE_RUNNERS:
            logger.warning("Unknown stage: %s (skipping)", stage)
            continue

        logger.info("══════════════════════════════════════════════")
        logger.info("  STAGE: %s", stage.upper())
        logger.info("══════════════════════════════════════════════")

        start = time.time()
        runner = STAGE_RUNNERS[stage]

        kwargs = {"dry_run": dry_run}
        if stage == "ingest":
            kwargs["days"] = days
            if limit:
                kwargs["limit"] = limit
        elif limit:
            kwargs["limit"] = limit

        try:
            results[stage] = runner(**kwargs)
        except Exception as exc:
            logger.error("Stage %s failed: %s", stage, exc)
            results[stage] = {"error": str(exc)}

        elapsed = time.time() - start
        logger.info("Stage %s completed in %.1f seconds", stage, elapsed)

    # Summary
    logger.info("══════════════════════════════════════════════")
    logger.info("  PIPELINE COMPLETE")
    logger.info("══════════════════════════════════════════════")

    # Print DB stats
    try:
        conn = get_connection()
        for table in ["candidates", "cases", "corroboration_sources"]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            logger.info("  %s: %d rows", table, count)

        # Triage distribution
        for status in ["NEW", "PASS", "MAYBE", "KILL"]:
            count = conn.execute(
                "SELECT COUNT(*) FROM candidates WHERE triage_status = ?", (status,)
            ).fetchone()[0]
            if count > 0:
                logger.info("  Candidates %s: %d", status, count)

        conn.close()
    except Exception:
        pass

    return results


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Run the FOIA-Free Content Pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Stages (run in order):
  ingest        Pull new candidates from YouTube, RSS, press pages
  enrich        Add transcripts, entities, quality signals
  triage        LLM scoring → PASS / MAYBE / KILL
  corroborate   Gather supporting sources for PASS candidates
  package       Build timeline, narration, shorts plan
  render        Download, cut, caption, export videos

Examples:
  python -m scripts.run_pipeline                      # full pipeline
  python -m scripts.run_pipeline --stage ingest       # just ingest
  python -m scripts.run_pipeline --stages ingest,triage
  python -m scripts.run_pipeline --dry-run            # preview all
        """,
    )
    parser.add_argument("--stage", type=str, help="Run a single stage.")
    parser.add_argument("--stages", type=str, help="Comma-separated stages to run.")
    parser.add_argument("--days", type=int, default=7, help="Look-back days for ingest (default: 7).")
    parser.add_argument("--limit", type=int, default=None, help="Limit items per stage.")
    parser.add_argument("--dry-run", action="store_true", help="Preview all stages without writing.")
    args = parser.parse_args()

    if args.stage:
        stages = [args.stage]
    elif args.stages:
        stages = [s.strip() for s in args.stages.split(",")]
    else:
        stages = None

    results = run_pipeline(stages=stages, days=args.days, limit=args.limit, dry_run=args.dry_run)
    print("\n" + json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
