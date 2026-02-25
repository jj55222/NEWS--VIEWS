#!/usr/bin/env python3
"""
NEWS → VIEWS: KPI Tracker

Logs per-run metrics, generates weekly summaries, and syncs to Google Sheets.
Uses append-only JSONL format for run_log.json.

Usage:
    python kpi_tracker.py --dashboard          # Print KPI dashboard
    python kpi_tracker.py --weekly             # Weekly summary report
    python kpi_tracker.py --sync               # Sync KPIs to Google Sheets
    python kpi_tracker.py --check              # Verify setup
"""

import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv
import os

load_dotenv()

from pipeline_ops import (
    KPI_DEFINITIONS,
    KPI_DASHBOARD_COLUMNS,
    RUN_LOG_SCHEMA,
    SHEETS_TABS,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

RUN_LOG_PATH = Path("run_log.json")
SHEET_ID = os.getenv("SHEET_ID")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json")


# =============================================================================
# RUN LOGGING
# =============================================================================

def log_run(run_type: str, metrics: dict, errors: list = None, notes: str = ""):
    """
    Append a run entry to run_log.json (JSONL format).

    Args:
        run_type: "channel_qualify" | "incident_score" | "artifact_hunt" | "full_pipeline"
        metrics: Dict of metric values (keys should match KPI_DEFINITIONS keys)
        errors: List of error strings encountered during run
        notes: Optional notes about the run
    """
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "run_type": run_type,
        "metrics": metrics,
        "errors": errors or [],
        "notes": notes,
    }

    # Append to JSONL file
    with open(RUN_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")

    print(f"Run logged: {run_type} ({len(metrics)} metrics)")


def get_runs(since_days: int = 7) -> List[dict]:
    """Read run entries from the past N days."""
    if not RUN_LOG_PATH.exists():
        return []

    cutoff = datetime.utcnow() - timedelta(days=since_days)
    runs = []

    with open(RUN_LOG_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", "")
                if ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt.replace(tzinfo=None) >= cutoff:
                        runs.append(entry)
            except (json.JSONDecodeError, ValueError):
                continue

    return runs


# =============================================================================
# WEEKLY SUMMARY
# =============================================================================

def get_weekly_summary(weeks_back: int = 1) -> dict:
    """
    Aggregate metrics from runs over the past week.

    Returns dict mapping KPI name -> aggregated value.
    """
    runs = get_runs(since_days=7 * weeks_back)

    if not runs:
        return {
            "week_start": (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d"),
            "week_end": datetime.utcnow().strftime("%Y-%m-%d"),
            "run_count": 0,
            "kpis": {},
        }

    # Aggregate metrics across runs
    aggregated = {}
    for run in runs:
        metrics = run.get("metrics", {})
        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                if key not in aggregated:
                    aggregated[key] = 0
                aggregated[key] += value

    # Calculate ratios
    ratios = {}
    for kpi_name, kpi_def in KPI_DEFINITIONS.items():
        if kpi_def["type"] == "ratio":
            formula = kpi_def.get("formula", "")
            if "/" in formula:
                numerator_key, denominator_key = [s.strip() for s in formula.split("/")]
                numerator = aggregated.get(numerator_key, 0)
                denominator = aggregated.get(denominator_key, 0)
                if denominator > 0:
                    ratios[kpi_name] = numerator / denominator
                else:
                    ratios[kpi_name] = 0.0

    # Merge counts and ratios
    kpis = {}
    for kpi_name in KPI_DEFINITIONS:
        kpi_def = KPI_DEFINITIONS[kpi_name]
        if kpi_def["type"] == "ratio":
            kpis[kpi_name] = ratios.get(kpi_name, 0.0)
        else:
            kpis[kpi_name] = aggregated.get(kpi_name, 0)

    return {
        "week_start": (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d"),
        "week_end": datetime.utcnow().strftime("%Y-%m-%d"),
        "run_count": len(runs),
        "kpis": kpis,
    }


# =============================================================================
# KPI HEALTH CHECK
# =============================================================================

def check_kpi_health(summary: dict) -> dict:
    """
    Compare weekly KPI values against targets.

    Returns dict: kpi_name -> {value, target, status: "green"|"yellow"|"red"}
    """
    health = {}
    kpis = summary.get("kpis", {})

    for kpi_name, kpi_def in KPI_DEFINITIONS.items():
        value = kpis.get(kpi_name, 0)
        target_label = kpi_def.get("target_label", "")
        status = "green"

        if kpi_def["type"] in ("count", "ratio"):
            # Check if this KPI has a minimum target
            target_min = kpi_def.get("target_min")
            red_below = kpi_def.get("red_below")

            # Or a maximum target (resource usage)
            target_max = kpi_def.get("target_max")
            red_above = kpi_def.get("red_above")

            if target_min is not None:
                if red_below is not None and value < red_below:
                    status = "red"
                elif value < target_min:
                    status = "yellow"

            if target_max is not None:
                if red_above is not None and value > red_above:
                    status = "red"
                elif value > target_max:
                    status = "yellow"

        health[kpi_name] = {
            "value": value,
            "target": target_label,
            "status": status,
            "description": kpi_def.get("description", ""),
        }

    return health


# =============================================================================
# DASHBOARD
# =============================================================================

def print_dashboard(summary: dict = None):
    """Print formatted KPI dashboard to terminal."""
    if summary is None:
        summary = get_weekly_summary()

    health = check_kpi_health(summary)

    print(f"\n{'='*70}")
    print(f"  KPI DASHBOARD  |  {summary.get('week_start', '')} to {summary.get('week_end', '')}")
    print(f"  Runs this period: {summary.get('run_count', 0)}")
    print(f"{'='*70}")

    # Status indicators
    status_icons = {"green": "[OK]", "yellow": "[!!]", "red": "[XX]"}

    # Group KPIs by category
    categories = {
        "Channel Qualification": ["channels_evaluated", "channels_qualified", "channel_qualification_rate"],
        "Incident Selection": ["incidents_scanned", "incidents_selected", "incident_conversion_rate"],
        "Footage & Enrichment": ["footage_hit_rate", "enrichment_completion_rate"],
        "Resource Usage": ["exa_credits_used", "youtube_api_units_used", "llm_calls_used"],
        "Output": ["content_pieces_produced"],
    }

    for category, kpi_names in categories.items():
        print(f"\n  --- {category} ---")
        for kpi_name in kpi_names:
            if kpi_name not in health:
                continue
            h = health[kpi_name]
            icon = status_icons.get(h["status"], "[??]")

            # Format value
            value = h["value"]
            kpi_def = KPI_DEFINITIONS.get(kpi_name, {})
            if kpi_def.get("type") == "ratio":
                value_str = f"{value:.0%}"
            else:
                value_str = f"{value:,.0f}" if isinstance(value, float) else str(value)

            # Truncate description
            desc = h.get("description", kpi_name)[:40]
            print(f"  {icon} {desc:<42} {value_str:>8}  (target: {h['target']})")

    # Overall health
    statuses = [h["status"] for h in health.values()]
    red_count = statuses.count("red")
    yellow_count = statuses.count("yellow")

    print(f"\n{'='*70}")
    if red_count > 0:
        print(f"  OVERALL: {red_count} RED, {yellow_count} YELLOW -- action needed")
    elif yellow_count > 0:
        print(f"  OVERALL: {yellow_count} YELLOW -- monitor closely")
    else:
        print(f"  OVERALL: ALL GREEN")
    print(f"{'='*70}")


# =============================================================================
# SHEETS SYNC
# =============================================================================

def sync_to_sheets(summary: dict = None):
    """Push weekly KPI summary to Google Sheets 'KPI DASHBOARD' tab."""
    if not SHEET_ID:
        print("SHEET_ID not set, skipping Sheets sync")
        return

    if summary is None:
        summary = get_weekly_summary()

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SHEET_ID)

        tab_name = SHEETS_TABS["kpi_dashboard"]

        try:
            ws = sheet.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet(title=tab_name, rows=100, cols=len(KPI_DASHBOARD_COLUMNS))
            ws.update(range_name="A1", values=[KPI_DASHBOARD_COLUMNS])

        health = check_kpi_health(summary)
        kpis = summary.get("kpis", {})

        # Format ratio values
        def fmt(key):
            val = kpis.get(key, 0)
            kpi_def = KPI_DEFINITIONS.get(key, {})
            if kpi_def.get("type") == "ratio":
                return f"{val:.0%}"
            return str(val)

        # Overall health
        statuses = [h["status"] for h in health.values()]
        overall = "RED" if "red" in statuses else ("YELLOW" if "yellow" in statuses else "GREEN")

        row = [
            f"{summary.get('week_start', '')} - {summary.get('week_end', '')}",
            fmt("channels_evaluated"),
            fmt("channels_qualified"),
            fmt("channel_qualification_rate"),
            fmt("incidents_scanned"),
            fmt("incidents_selected"),
            fmt("incident_conversion_rate"),
            fmt("footage_hit_rate"),
            fmt("enrichment_completion_rate"),
            fmt("exa_credits_used"),
            fmt("youtube_api_units_used"),
            fmt("llm_calls_used"),
            fmt("content_pieces_produced"),
            overall,
        ]

        # Append row (find next empty row)
        existing = ws.get_all_values()
        next_row = len(existing) + 1
        ws.update(range_name=f"A{next_row}", values=[row])

        print(f"Synced weekly KPIs to '{tab_name}' row {next_row}")

    except ImportError:
        print("gspread not installed. Run: pip install gspread google-auth")
    except Exception as e:
        print(f"Sheets sync error: {e}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="KPI Tracker - Pipeline Performance Monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --dashboard     # Print KPI dashboard
    %(prog)s --weekly        # Weekly summary
    %(prog)s --sync          # Sync to Google Sheets
    %(prog)s --check         # Verify setup
        """
    )

    parser.add_argument("--dashboard", action="store_true", help="Print KPI dashboard")
    parser.add_argument("--weekly", action="store_true", help="Print weekly summary")
    parser.add_argument("--sync", action="store_true", help="Sync KPIs to Google Sheets")
    parser.add_argument("--check", action="store_true", help="Check setup")
    parser.add_argument("--weeks-back", type=int, default=1, help="Weeks to look back")

    args = parser.parse_args()

    if args.check:
        print("KPI Tracker check:")
        print(f"  Run log: {RUN_LOG_PATH} ({'exists' if RUN_LOG_PATH.exists() else 'not created yet'})")
        print(f"  KPIs defined: {len(KPI_DEFINITIONS)}")
        print(f"  Sheet ID: {'set' if SHEET_ID else 'not set'}")
        runs = get_runs(since_days=7)
        print(f"  Runs in last 7 days: {len(runs)}")
    elif args.dashboard or args.weekly:
        summary = get_weekly_summary(weeks_back=args.weeks_back)
        print_dashboard(summary)
    elif args.sync:
        summary = get_weekly_summary(weeks_back=args.weeks_back)
        sync_to_sheets(summary)
        print_dashboard(summary)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
