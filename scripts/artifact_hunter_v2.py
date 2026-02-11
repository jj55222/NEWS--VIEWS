#!/usr/bin/env python3
"""Stage 2 — HUNT: case_leads → artifacts via Brave Search.

For each high-hook lead, searches for primary artifacts (official bodycam,
dashcam, court streams, press videos, charging documents) using Brave Search
and the primary_source_registry.

Usage:
    python -m scripts.artifact_hunter_v2                     # hunt all NEW leads
    python -m scripts.artifact_hunter_v2 --min-hook 70       # only high-hook leads
    python -m scripts.artifact_hunter_v2 --limit 20          # cap at 20 leads
    python -m scripts.artifact_hunter_v2 --dry-run           # preview, don't write
"""

import argparse
import gzip
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from scripts.config_loader import (
    get_enabled_sources,
    get_env,
    get_policy,
    setup_logging,
)
from scripts.db import (
    get_connection,
    get_leads,
    has_primary_artifact,
    init_db,
    insert_artifact,
    update_lead_status,
)

logger = setup_logging("artifact_hunter")

# ── Brave Search ──────────────────────────────────────────────────────────
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0


def _fetch_json(url: str, headers: dict | None = None) -> dict | None:
    """Fetch JSON with retry/backoff."""
    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url)
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                if raw[:2] == b'\x1f\x8b':
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            logger.warning("Brave HTTP %d: %s", e.code, e.reason)
            return None
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            logger.warning("Brave fetch error: %s", e)
            return None
    return None


def brave_search(query: str, num: int = 10) -> list[dict]:
    """Search via Brave API."""
    api_key = get_env("BRAVE_API_KEY")
    if not api_key:
        logger.warning("BRAVE_API_KEY not set.")
        return []

    params = urllib.parse.urlencode({"q": query, "count": min(num, 20)})
    url = f"{BRAVE_SEARCH_URL}?{params}"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }

    data = _fetch_json(url, headers=headers)
    if not data:
        return []

    return [
        {
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "snippet": item.get("description", "")[:500],
        }
        for item in data.get("web", {}).get("results", [])
    ]


# ── Query generation ──────────────────────────────────────────────────────
def build_hunt_queries(lead: dict) -> list[dict]:
    """Generate search queries for a lead. Returns list of {query, category}."""
    queries = []
    entities = lead.get("entities_json", "{}")
    if isinstance(entities, str):
        try:
            entities = json.loads(entities)
        except json.JSONDecodeError:
            entities = {}

    title = lead.get("title", "")
    location = lead.get("location", "")
    incident = lead.get("incident_type", "")
    names = entities.get("names", []) if isinstance(entities, dict) else []
    agencies = entities.get("agencies", []) if isinstance(entities, dict) else []
    locations = entities.get("locations", []) if isinstance(entities, dict) else []

    city = location or (locations[0] if locations else "")
    agency = agencies[0] if agencies else ""
    name = names[0] if names else ""

    # Official source queries (3)
    if agency and city:
        queries.append({"query": f'"body worn camera" "{agency}" {city}', "category": "official"})
        queries.append({"query": f'"critical incident briefing" "{agency}"', "category": "official"})
    if city:
        queries.append({"query": f'site:youtube.com "bodycam" "{city}" {incident}', "category": "official"})

    # Broad queries (3)
    clean_title = re.sub(
        r"^(bodycam|dashcam|body cam|dash cam|full video|raw footage)[:\s-]*",
        "", title, flags=re.IGNORECASE,
    ).strip()
    if clean_title:
        queries.append({"query": f"{clean_title} bodycam OR dashcam video", "category": "broad"})
    if name and city:
        queries.append({"query": f"{name} {city} arrest bodycam", "category": "broad"})
    if agency:
        queries.append({"query": f"{agency} officer involved {incident} video released", "category": "broad"})

    # Court-specific queries (2)
    if name:
        queries.append({"query": f"{name} court hearing trial livestream", "category": "court"})
    if city and incident:
        queries.append({"query": f"{city} {incident} charges complaint affidavit", "category": "court"})

    return queries[:8]  # Cap at 8 per spec


# ── Artifact classification ──────────────────────────────────────────────
def _get_primary_domains() -> set[str]:
    """Build set of known primary source domains from registry."""
    domains = set()
    for src in get_enabled_sources():
        if src.get("source_class") == "primary" or src.get("tier") == "A":
            url = src.get("url", "")
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lower()
                if domain:
                    # Strip www.
                    domain = domain.replace("www.", "")
                    domains.add(domain)
            except Exception:
                pass
    # Add known official domains
    domains.update([
        "youtube.com", "facebook.com",  # will be checked against channel names
    ])
    return domains


# Official publisher keywords in URL or title
PRIMARY_SIGNALS = re.compile(
    r"(police|sheriff|pd\b|\.gov|department|agency|county|district.attorney|"
    r"da\.org|court|dps\.|highway.patrol|state.patrol|prosecutor|"
    r"critical.incident|officer.involved|press.release|media.release|"
    r"bodycam|body.worn.camera|dashcam|dash.camera|"
    r"Law.Crime|Court.TV|Police.Activity|Real.World.Police|BodyCam.Central)",
    re.IGNORECASE,
)

SECONDARY_SIGNALS = re.compile(
    r"(daily.mail|tmz|insider|buzzfeed|reddit|tiktok|instagram|twitter\.com/|"
    r"compilation|top.10|worst|best.of|react|commentary)",
    re.IGNORECASE,
)

ARTIFACT_TYPE_PATTERNS = {
    "bodycam": re.compile(r"bodycam|body.worn|bwc|body.cam", re.I),
    "dashcam": re.compile(r"dashcam|dash.cam|in.car|cruiser", re.I),
    "court": re.compile(r"court|trial|hearing|sentencing|arraign|livestream", re.I),
    "interview": re.compile(r"interrogat|interview|confession", re.I),
    "press_video": re.compile(r"press.conference|briefing|press.release.*video", re.I),
    "document": re.compile(r"affidavit|complaint|indictment|warrant|docket|\.pdf", re.I),
    "audio": re.compile(r"911.call|dispatch|scanner|radio|audio", re.I),
}


def classify_result(url: str, title: str, snippet: str) -> dict:
    """Classify a search result as primary/secondary + artifact type + confidence."""
    combined = f"{url} {title} {snippet}"

    # Determine source class
    if PRIMARY_SIGNALS.search(combined) and not SECONDARY_SIGNALS.search(combined):
        source_class = "primary"
        base_confidence = 0.8
    elif SECONDARY_SIGNALS.search(combined):
        source_class = "secondary"
        base_confidence = 0.3
    else:
        source_class = "secondary"
        base_confidence = 0.5

    # Boost for .gov domains
    if ".gov" in url.lower():
        source_class = "primary"
        base_confidence = max(base_confidence, 0.9)

    # Determine artifact type
    artifact_type = "unknown"
    for atype, pattern in ARTIFACT_TYPE_PATTERNS.items():
        if pattern.search(combined):
            artifact_type = atype
            break

    # Confidence adjustments
    confidence = base_confidence
    if artifact_type != "unknown":
        confidence += 0.1
    if "youtube.com" in url and source_class == "primary":
        confidence += 0.05
    if artifact_type == "document" and ".pdf" in url.lower():
        confidence = max(confidence, 0.85)

    confidence = min(confidence, 1.0)

    # Extract publisher name
    publisher = None
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower().replace("www.", "")
    if ".gov" in domain:
        publisher = domain
    elif "youtube.com" in domain:
        # Try to extract channel from title context
        m = re.search(r"by\s+([^|]+)|[-–]\s+([^|]+?)$", title)
        if m:
            publisher = (m.group(1) or m.group(2) or "").strip()

    return {
        "source_class": source_class,
        "artifact_type": artifact_type,
        "confidence": round(confidence, 2),
        "publisher": publisher or domain,
    }


# ── Main hunt ─────────────────────────────────────────────────────────────
def hunt(min_hook: int = 70, limit: int = 50, dry_run: bool = False) -> dict:
    """Run artifact hunt for qualifying leads.

    Returns:
        dict with keys: leads_hunted, artifacts_found, primary_found, leads_upgraded, errors
    """
    init_db()
    conn = get_connection()

    leads = get_leads(conn, status="NEW", min_hook_score=min_hook, limit=limit)
    if not leads:
        logger.info("No NEW leads with hook_score >= %d to hunt.", min_hook)
        conn.close()
        return {"leads_hunted": 0, "artifacts_found": 0, "primary_found": 0,
                "leads_upgraded": 0, "errors": 0}

    max_queries = get_policy("artifact_gating", "hunt_max_queries", 8)
    max_urls = get_policy("artifact_gating", "hunt_max_urls", 25)
    min_confidence = get_policy("artifact_gating", "artifact_min_confidence", 0.7)

    stats = {"leads_hunted": 0, "artifacts_found": 0, "primary_found": 0,
             "leads_upgraded": 0, "errors": 0}

    for lead in leads:
        lead_id = lead["lead_id"]
        title = (lead.get("title") or "")[:60]
        logger.info("Hunting: %s — %s (hook=%d)", lead_id, title, lead.get("hook_score", 0))

        if not dry_run:
            update_lead_status(conn, lead_id, "HUNTING")

        try:
            queries = build_hunt_queries(lead)
            if not queries:
                logger.warning("No queries generated for lead %s", lead_id)
                if not dry_run:
                    update_lead_status(conn, lead_id, "NO_ARTIFACT")
                continue

            all_results = []
            seen_urls = set()
            for q in queries[:max_queries]:
                results = brave_search(q["query"], num=5)
                for r in results:
                    if r["url"] not in seen_urls and len(all_results) < max_urls:
                        seen_urls.add(r["url"])
                        r["category"] = q["category"]
                        all_results.append(r)

            logger.info("  %d unique results from %d queries", len(all_results), len(queries))

            # Classify and store artifacts
            found_primary = False
            for result in all_results:
                classification = classify_result(result["url"], result["title"], result["snippet"])

                artifact = {
                    "artifact_id": hashlib.sha256(f"{lead_id}:{result['url']}".encode()).hexdigest()[:16],
                    "lead_id": lead_id,
                    "artifact_type": classification["artifact_type"],
                    "url": result["url"],
                    "publisher": classification["publisher"],
                    "source_class": classification["source_class"],
                    "confidence": classification["confidence"],
                    "notes": f"Query category: {result.get('category', 'unknown')}. {result['snippet'][:200]}",
                }

                if dry_run:
                    logger.info(
                        "  [DRY RUN] %s artifact: [%.2f] %s — %s",
                        classification["source_class"],
                        classification["confidence"],
                        classification["artifact_type"],
                        result["url"][:80],
                    )
                else:
                    if insert_artifact(conn, artifact):
                        stats["artifacts_found"] += 1
                        if classification["source_class"] == "primary" and classification["confidence"] >= min_confidence:
                            found_primary = True
                            stats["primary_found"] += 1

            # Update lead status
            if dry_run:
                if any(
                    classify_result(r["url"], r["title"], r["snippet"])["source_class"] == "primary"
                    and classify_result(r["url"], r["title"], r["snippet"])["confidence"] >= min_confidence
                    for r in all_results
                ):
                    stats["leads_upgraded"] += 1
            else:
                if found_primary or has_primary_artifact(conn, lead_id, min_confidence):
                    update_lead_status(conn, lead_id, "ARTIFACT_FOUND")
                    stats["leads_upgraded"] += 1
                else:
                    update_lead_status(conn, lead_id, "NO_ARTIFACT")

            stats["leads_hunted"] += 1

        except Exception as exc:
            logger.error("Hunt error for lead %s: %s", lead_id, exc)
            stats["errors"] += 1
            if not dry_run:
                update_lead_status(conn, lead_id, "NO_ARTIFACT")

    conn.close()
    logger.info(
        "Hunt complete: %d leads hunted, %d artifacts found (%d primary), %d upgraded, %d errors",
        stats["leads_hunted"], stats["artifacts_found"], stats["primary_found"],
        stats["leads_upgraded"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Hunt for primary artifacts for case leads.")
    parser.add_argument("--min-hook", type=int, default=70, help="Minimum hook score (default: 70).")
    parser.add_argument("--limit", type=int, default=50, help="Max leads to hunt.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = hunt(min_hook=args.min_hook, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
