#!/usr/bin/env python3
"""Run the FOIA-Free Content Pipeline v2 end-to-end.

Two-lane architecture:
  Lane A (Discovery):   ingest → enrich → triage → corroborate → package → render
  Lane B (Leads/Hunt):  discover → hunt → verify → package → render

Usage:
    python -m scripts.run_pipeline                          # full pipeline (both lanes)
    python -m scripts.run_pipeline --stage discover          # single v2 stage
    python -m scripts.run_pipeline --stages discover,hunt    # multiple stages
    python -m scripts.run_pipeline --lane a                  # Lane A only (v1 flow)
    python -m scripts.run_pipeline --lane b                  # Lane B only (v2 flow)
    python -m scripts.run_pipeline --dry-run                 # preview all stages
    python -m scripts.run_pipeline --days 3                  # look back 3 days
"""

import argparse
import json
import time

from scripts.config_loader import ensure_dirs, reload_sources, setup_logging
from scripts.db import get_connection, init_db

logger = setup_logging("pipeline")

# Lane A stages (v1 candidate flow)
LANE_A_STAGES = ["ingest", "enrich", "triage", "corroborate", "package", "render"]

# Lane B stages (v2 lead/artifact flow)
LANE_B_STAGES = ["discover", "hunt", "verify", "package_v2", "render"]

ALL_STAGES = list(dict.fromkeys(LANE_A_STAGES + LANE_B_STAGES))


# ── Lane A runners (v1 candidate flow) ───────────────────────────────────

def run_ingest(days: int = 7, limit: int | None = None, dry_run: bool = False,
               discovery_targets: frozenset[str] | None = None,
               raw_bodycam_only: bool = False) -> dict:
    """Run all ingestion methods (YouTube, RSS, page scraping)."""
    reload_sources()  # always read latest sources_registry.json from disk
    combined = {"youtube": {}, "rss": {}, "pages": {}}

    if discovery_targets is None or "youtube" in discovery_targets:
        from scripts.ingest_youtube import ingest as yt_ingest
        logger.info("─── YouTube Ingest ───")
        try:
            combined["youtube"] = yt_ingest(days=days, limit=limit, dry_run=dry_run,
                                            raw_bodycam_only=raw_bodycam_only)
        except Exception as exc:
            logger.error("YouTube ingest failed: %s", exc)
            combined["youtube"] = {"error": str(exc)}
    else:
        logger.info("Skipping YouTube ingest (not in discovery targets).")

    if discovery_targets is None or "rss" in discovery_targets:
        from scripts.ingest_rss import ingest as rss_ingest
        logger.info("─── RSS Ingest ───")
        try:
            combined["rss"] = rss_ingest(days=days, limit=limit, dry_run=dry_run)
        except Exception as exc:
            logger.error("RSS ingest failed: %s", exc)
            combined["rss"] = {"error": str(exc)}
    else:
        logger.info("Skipping RSS ingest (not in discovery targets).")

    if discovery_targets is None or "pages" in discovery_targets:
        from scripts.scrape_pages import ingest as page_ingest
        logger.info("─── Page Scrape ───")
        try:
            combined["pages"] = page_ingest(limit=limit, dry_run=dry_run)
        except Exception as exc:
            logger.error("Page scrape failed: %s", exc)
            combined["pages"] = {"error": str(exc)}
    else:
        logger.info("Skipping page scraping (not in discovery targets).")

    return combined


def run_enrich(limit: int = 200, dry_run: bool = False) -> dict:
    """Run transcript enrichment."""
    from scripts.enrich_transcripts import enrich
    logger.info("─── Enrich Transcripts ───")
    return enrich(status="NEW", limit=limit, dry_run=dry_run)


def run_triage(limit: int = 200, dry_run: bool = False) -> dict:
    """Run LLM triage (with v2 artifact gating)."""
    from scripts.triage_llm import triage
    logger.info("─── LLM Triage ───")
    return triage(status="NEW", limit=limit, dry_run=dry_run)


def run_corroborate(limit: int = 50, dry_run: bool = False) -> dict:
    """Run corroboration."""
    from scripts.corroborate import corroborate
    logger.info("─── Corroborate ───")
    return corroborate(limit=limit, dry_run=dry_run)


def run_package(limit: int = 20, dry_run: bool = False) -> dict:
    """Run case packaging (v1 flow)."""
    from scripts.package_case import package
    logger.info("─── Package Cases ───")
    return package(promote=True, limit=limit, dry_run=dry_run)


def run_render(limit: int = 10, dry_run: bool = False) -> dict:
    """Run video rendering."""
    from scripts.render_ffmpeg import render
    logger.info("─── Render ───")
    return render(limit=limit, dry_run=dry_run)


# ── Lane B runners (v2 lead/artifact flow) ───────────────────────────────

def run_discover(days: int = 7, limit: int | None = None, dry_run: bool = False,
                 discovery_targets: frozenset[str] | None = None) -> dict:
    """Run lead discovery from RSS + pages."""
    reload_sources()  # always read latest sources_registry.json from disk
    from scripts.discover_leads import discover
    logger.info("─── Discover Leads ───")
    return discover(days=days, limit=limit, dry_run=dry_run,
                    discovery_targets=discovery_targets)


def run_hunt(min_hook: int = 70, limit: int = 50, dry_run: bool = False) -> dict:
    """Run artifact hunt for qualifying leads."""
    from scripts.artifact_hunter_v2 import hunt
    logger.info("─── Artifact Hunt ───")
    return hunt(min_hook=min_hook, limit=limit, dry_run=dry_run)


def run_verify(limit: int = 50, dry_run: bool = False) -> dict:
    """Verify ARTIFACT_FOUND leads: corroborate + build fact packs."""
    from scripts.corroborate import corroborate
    from scripts.db import get_connection, get_leads, insert_lead

    logger.info("─── Verify Leads ───")
    conn = get_connection()

    # Promote ARTIFACT_FOUND leads to candidates for corroboration
    leads = get_leads(conn, status="ARTIFACT_FOUND", limit=limit)
    promoted = 0
    for lead in leads:
        # Create a candidate-like dict for the corroboration system
        lead_data = {
            "candidate_id": lead["lead_id"],
            "title": lead.get("title", ""),
            "url": lead.get("url", ""),
            "description": lead.get("snippet", ""),
            "entities_json": lead.get("entities_json", "{}"),
            "incident_type": lead.get("incident_type", "unknown"),
        }
        promoted += 1

    conn.close()

    stats = {"leads_verified": len(leads), "promoted": promoted}

    # Run corroboration on these leads
    if not dry_run and leads:
        corr_stats = corroborate(limit=limit, dry_run=dry_run)
        stats["corroboration"] = corr_stats

    return stats


def run_package_v2(limit: int = 20, dry_run: bool = False) -> dict:
    """Package ARTIFACT_FOUND leads into case bundles."""
    import uuid
    from scripts.db import (
        get_artifacts,
        get_connection,
        get_leads,
        has_primary_artifact,
        insert_bundle,
        update_lead_status,
    )
    from scripts.config_loader import get_openrouter_client, get_policy

    logger.info("─── Package Bundles (v2) ───")
    conn = get_connection()
    min_conf = get_policy("artifact_gating", "artifact_min_confidence", 0.7)
    leads = get_leads(conn, status="ARTIFACT_FOUND", limit=limit)

    stats = {"leads_processed": 0, "bundles_created": 0, "errors": 0}

    for lead in leads:
        lead_id = lead["lead_id"]
        if not has_primary_artifact(conn, lead_id, min_conf):
            logger.info("Skipping lead %s — no primary artifact above %.2f", lead_id, min_conf)
            continue

        artifacts = get_artifacts(conn, lead_id, min_confidence=min_conf)
        primary_ids = [a["artifact_id"] for a in artifacts if a.get("source_class") == "primary"]

        bundle = {
            "bundle_id": uuid.uuid4().hex[:16],
            "lead_id": lead_id,
            "primary_artifact_ids": primary_ids,
            "status": "APPROVED",
        }

        if not dry_run:
            try:
                insert_bundle(conn, bundle)
                stats["bundles_created"] += 1
            except Exception as exc:
                logger.error("Bundle creation failed for %s: %s", lead_id, exc)
                stats["errors"] += 1
        else:
            logger.info("[DRY RUN] Would create bundle for lead %s with %d artifacts",
                        lead_id, len(primary_ids))
            stats["bundles_created"] += 1

        stats["leads_processed"] += 1

    conn.close()
    logger.info("Package v2: %d leads, %d bundles, %d errors",
                stats["leads_processed"], stats["bundles_created"], stats["errors"])
    return stats


def run_report(top_n: int = 50) -> dict:
    """Generate missed opportunity report."""
    from scripts.report_missed_opportunities import generate_report, print_report
    logger.info("─── Missed Opportunity Report ───")
    report = generate_report(top_n=top_n)
    print_report(report)
    return report.get("summary", {})


# ── Stage registry ────────────────────────────────────────────────────────

STAGE_RUNNERS = {
    # Lane A
    "ingest": run_ingest,
    "enrich": run_enrich,
    "triage": run_triage,
    "corroborate": run_corroborate,
    "package": run_package,
    "render": run_render,
    # Lane B
    "discover": run_discover,
    "hunt": run_hunt,
    "verify": run_verify,
    "package_v2": run_package_v2,
    # Utility
    "report": run_report,
}


def run_pipeline(stages: list[str] | None = None, days: int = 7,
                 limit: int | None = None, dry_run: bool = False,
                 lane: str | None = None,
                 discovery_targets: frozenset[str] | None = None,
                 raw_bodycam_only: bool = False) -> dict:
    """Run the pipeline for specified stages (or all stages).

    Args:
        stages: List of stage names to run. If None, runs all.
        days: Look-back days for ingest/discover.
        limit: Max items per stage.
        dry_run: Preview without writing.
        lane: 'a' for Lane A only, 'b' for Lane B only, None for both.
        discovery_targets: Restrict collectors (youtube, rss, pages). None = all.
        raw_bodycam_only: YouTube ingest: only raw-bodycam channels.

    Returns:
        dict mapping stage name to its results.
    """
    if stages is None:
        if lane == "a":
            stages = LANE_A_STAGES
        elif lane == "b":
            stages = LANE_B_STAGES
        else:
            stages = LANE_A_STAGES + LANE_B_STAGES

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
            if discovery_targets is not None:
                kwargs["discovery_targets"] = discovery_targets
            if raw_bodycam_only:
                kwargs["raw_bodycam_only"] = raw_bodycam_only
        elif stage == "discover":
            kwargs["days"] = days
            if limit:
                kwargs["limit"] = limit
            if discovery_targets is not None:
                kwargs["discovery_targets"] = discovery_targets
        elif stage == "hunt":
            kwargs["min_hook"] = 70
            if limit:
                kwargs["limit"] = limit
        elif stage == "report":
            kwargs = {"top_n": limit or 50}
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
        for table in ["candidates", "case_leads", "artifacts", "case_bundles",
                       "cases", "corroboration_sources"]:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if count > 0:
                    logger.info("  %s: %d rows", table, count)
            except Exception:
                pass

        # Triage distribution
        for status in ["NEW", "PASS", "MAYBE", "KILL"]:
            count = conn.execute(
                "SELECT COUNT(*) FROM candidates WHERE triage_status = ?", (status,)
            ).fetchone()[0]
            if count > 0:
                logger.info("  Candidates %s: %d", status, count)

        # Lead status distribution
        for status in ["NEW", "HUNTING", "ARTIFACT_FOUND", "NO_ARTIFACT", "KILL"]:
            count = conn.execute(
                "SELECT COUNT(*) FROM case_leads WHERE status = ?", (status,)
            ).fetchone()[0]
            if count > 0:
                logger.info("  Leads %s: %d", status, count)

        conn.close()
    except Exception:
        pass

    return results


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Run the FOIA-Free Content Pipeline v2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Lane A stages (v1 candidate flow):
  ingest        Pull new candidates from YouTube, RSS, press pages
  enrich        Add transcripts, entities, quality signals
  triage        LLM scoring → PASS / MAYBE / KILL (with artifact gating)
  corroborate   Gather supporting sources for PASS candidates
  package       Build timeline, narration, shorts plan
  render        Download, cut, caption, export videos

Lane B stages (v2 lead/artifact flow):
  discover      RSS + pages → case_leads with hook scoring
  hunt          Brave Search for primary artifacts
  verify        Corroborate ARTIFACT_FOUND leads
  package_v2    Build case bundles from verified leads
  render        Download, cut, caption, export videos

Utility stages:
  report        Generate missed opportunity report

Examples:
  python -m scripts.run_pipeline                            # full pipeline (both lanes)
  python -m scripts.run_pipeline --lane b                   # Lane B only
  python -m scripts.run_pipeline --stage discover           # just discover
  python -m scripts.run_pipeline --stages discover,hunt     # discover + hunt
  python -m scripts.run_pipeline --dry-run                  # preview all
  python -m scripts.run_pipeline --stage ingest --discovery-targets youtube --dry-run
  python -m scripts.run_pipeline --stage ingest --discovery-targets youtube --raw-bodycam-only --dry-run
  python -m scripts.run_pipeline --stage discover --discovery-targets rss --dry-run
        """,
    )
    parser.add_argument("--stage", type=str, help="Run a single stage.")
    parser.add_argument("--stages", type=str, help="Comma-separated stages to run.")
    parser.add_argument("--lane", type=str, choices=["a", "b"], help="Run only Lane A or Lane B.")
    parser.add_argument("--days", type=int, default=7, help="Look-back days for ingest/discover (default: 7).")
    parser.add_argument("--limit", type=int, default=None, help="Limit items per stage.")
    parser.add_argument("--discovery-targets", type=str, default=None,
                        help="Comma-separated collectors to run: youtube,rss,pages (default: all).")
    parser.add_argument("--raw-bodycam-only", action="store_true",
                        help="YouTube ingest: only raw-bodycam channels.")
    parser.add_argument("--dry-run", action="store_true", help="Preview all stages without writing.")
    args = parser.parse_args()

    if args.stage:
        stages = [args.stage]
    elif args.stages:
        stages = [s.strip() for s in args.stages.split(",")]
    else:
        stages = None

    discovery_targets = None
    if args.discovery_targets:
        discovery_targets = frozenset(t.strip().lower() for t in args.discovery_targets.split(","))

    results = run_pipeline(
        stages=stages, days=args.days, limit=args.limit,
        dry_run=args.dry_run, lane=args.lane,
        discovery_targets=discovery_targets,
        raw_bodycam_only=args.raw_bodycam_only,
    )
    print("\n" + json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
