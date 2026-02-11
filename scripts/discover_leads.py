#!/usr/bin/env python3
"""Stage 1 — DISCOVER: RSS + scraped pages → case_leads.

Reads RSS entries and scraped press pages, extracts entities and incident
type, computes hook_score, and writes to the case_leads table.

Usage:
    python -m scripts.discover_leads                    # all enabled RSS + pages
    python -m scripts.discover_leads --days 3           # last 3 days
    python -m scripts.discover_leads --limit 50         # first 50 sources
    python -m scripts.discover_leads --dry-run          # preview, don't write
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

from scripts.config_loader import (
    get_enabled_sources,
    get_openrouter_client,
    get_policy,
    setup_logging,
)
from scripts.db import get_connection, init_db, insert_lead

logger = setup_logging("discover_leads")

# Keywords for incident detection
INCIDENT_KEYWORDS = re.compile(
    r"(bodycam|body[\s-]?cam|dashcam|dash[\s-]?cam|officer[\s-]?involved|shooting|"
    r"pursuit|chase|critical[\s-]?incident|use[\s-]?of[\s-]?force|arrest|"
    r"homicide|domestic|dui|dwi|welfare[\s-]?check|missing|amber[\s-]?alert|"
    r"murder|manslaughter|assault|robbery|kidnap|abduct|standoff|barricade|"
    r"stabbing|fatal|killed|shot|weapon|gun|knife|hostage|fugitive|warrant|"
    r"indictment|charged|convicted|sentenced|arraign|verdict)",
    re.IGNORECASE,
)

INCIDENT_TYPE_PATTERNS = {
    "shooting": re.compile(r"shoot|shot|gunfire|firearm|officer.involved.shoot", re.I),
    "pursuit": re.compile(r"pursuit|chase|fleeing|elude|evade", re.I),
    "domestic": re.compile(r"domestic|family.violence", re.I),
    "dui": re.compile(r"\bdui\b|\bdwi\b|drunk.driv|impaired.driv", re.I),
    "assault": re.compile(r"assault|attack|battery|stab", re.I),
    "homicide": re.compile(r"homicide|murder|manslaughter|killed|fatal", re.I),
    "missing_person": re.compile(r"missing|abduct|kidnap|amber.alert", re.I),
    "theft": re.compile(r"robbery|burglary|theft|stolen|carjack", re.I),
    "use_of_force": re.compile(r"use.of.force|excessive.force|taser|tased", re.I),
    "welfare_check": re.compile(r"welfare.check|mental.health|crisis", re.I),
    "standoff": re.compile(r"standoff|barricade|hostage|swat", re.I),
}


def _clean_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_rss_date(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        parsed = feedparser.datetimes._parse_date(date_str)
        if parsed:
            from time import mktime
            dt = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            return dt.isoformat()
    except Exception:
        pass
    return date_str


def make_lead_id(source_id: str, url: str) -> str:
    return hashlib.sha256(f"{source_id}:{url}".encode()).hexdigest()[:16]


def classify_incident(text: str) -> str:
    """Classify incident type from text using keyword patterns."""
    for itype, pattern in INCIDENT_TYPE_PATTERNS.items():
        if pattern.search(text):
            return itype
    return "unknown"


def extract_location_heuristic(text: str) -> str | None:
    """Try to extract a location from text (simple heuristic)."""
    # Look for common patterns: "in City, State" or "City Police"
    m = re.search(r"(?:in|near)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),?\s*([A-Z]{2})?", text)
    if m:
        loc = m.group(1)
        state = m.group(2)
        return f"{loc}, {state}" if state else loc

    m = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:Police|Sheriff|PD|County)", text)
    if m:
        return m.group(1)

    return None


def compute_hook_score_heuristic(title: str, snippet: str) -> int:
    """Compute a rough hook score (0-100) based on keyword presence."""
    text = f"{title} {snippet}".lower()
    score = 0

    # Stakes (0-30)
    if any(w in text for w in ["shooting", "shot", "killed", "fatal", "weapon", "gun"]):
        score += 25
    elif any(w in text for w in ["chase", "pursuit", "assault", "stabbing"]):
        score += 20
    elif any(w in text for w in ["arrest", "charged", "indicted"]):
        score += 15

    # Specificity (0-25) — named entities suggest a real incident
    if re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", f"{title} {snippet}"):  # proper names
        score += 15
    if re.search(r"\d{1,2}/\d{1,2}|\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", text):
        score += 10

    # Video/artifact signal (0-25)
    if any(w in text for w in ["bodycam", "dashcam", "body cam", "dash cam", "footage", "video released"]):
        score += 25
    elif any(w in text for w in ["video", "surveillance", "camera"]):
        score += 15

    # Recency / freshness (0-20)
    if any(w in text for w in ["released", "new", "just", "breaking"]):
        score += 10
    if any(w in text for w in ["press conference", "briefing", "statement"]):
        score += 10

    return min(score, 100)


def compute_hook_score_llm(title: str, snippet: str, client) -> dict:
    """Use LLM to compute hook score and extract entities. Returns dict with score + entities."""
    prompt = f"""Analyze this news item for a law enforcement video content pipeline.

TITLE: {title}
SNIPPET: {snippet[:1000]}

Return ONLY JSON:
{{
  "hook_score": <0-100, how interesting/dramatic is this for video content>,
  "incident_type": "<shooting|pursuit|domestic|dui|assault|homicide|missing_person|theft|use_of_force|welfare_check|standoff|fraud|unknown>",
  "entities": {{
    "names": ["<person names>"],
    "agencies": ["<law enforcement agencies>"],
    "locations": ["<cities/counties>"]
  }},
  "location": "<city, state if identifiable>",
  "date_of_incident": "<date if mentioned, else null>",
  "risk_flags": ["<minors_sensitive|sexual_violence|extreme_gore if applicable>"],
  "why_interesting": "<1 sentence>"
}}

Score high (70+) if: bodycam/dashcam mentioned, officer-involved incident, dramatic escalation, clear resolution.
Score low (<50) if: routine crime report, no video likely, no drama."""

    try:
        model = get_policy("llm", "corroboration_model", "openai/gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model, temperature=0.2, max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```json")[-1].split("```")[0].strip() if "```json" in raw else raw.split("```")[1].split("```")[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.debug("LLM hook score failed: %s", exc)
        return {}


# ── RSS discovery ─────────────────────────────────────────────────────────
def discover_from_rss(days: int = 7, limit: int | None = None,
                      use_llm: bool = True, dry_run: bool = False) -> dict:
    """Discover leads from RSS feeds."""
    sources = get_enabled_sources(source_type="rss")
    if limit:
        sources = sources[:limit]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats = {"feeds_processed": 0, "entries_found": 0, "leads_created": 0, "errors": 0}

    conn = None
    client = None
    if not dry_run:
        init_db()
        conn = get_connection()
    if use_llm:
        try:
            client = get_openrouter_client()
        except Exception:
            logger.warning("LLM client not available; using heuristic scoring.")

    for src in sources:
        source_id = src["source_id"]
        feed_url = src["url"]
        logger.info("Processing RSS: %s (%s)", src["name"], source_id)

        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:
            logger.error("RSS parse error for %s: %s", source_id, exc)
            stats["errors"] += 1
            continue

        stats["feeds_processed"] += 1

        for entry in feed.entries:
            link = entry.get("link", "")
            if not link:
                continue

            title = entry.get("title", "")
            summary = _clean_html(entry.get("summary") or entry.get("description", ""))
            combined = f"{title} {summary}"

            # Filter: only incident-related entries
            if not INCIDENT_KEYWORDS.search(combined):
                continue

            pub_date = _parse_rss_date(entry.get("published") or entry.get("updated"))
            if pub_date:
                try:
                    entry_dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                    if entry_dt < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            stats["entries_found"] += 1
            lead_id = make_lead_id(source_id, link)

            # Score and extract
            if client:
                llm_result = compute_hook_score_llm(title, summary, client)
                hook_score = llm_result.get("hook_score", 0)
                entities = llm_result.get("entities", {})
                incident_type = llm_result.get("incident_type", classify_incident(combined))
                location = llm_result.get("location") or extract_location_heuristic(combined)
                date_of_incident = llm_result.get("date_of_incident")
                risk_flags = llm_result.get("risk_flags", [])
            else:
                hook_score = compute_hook_score_heuristic(title, summary)
                entities = {}
                incident_type = classify_incident(combined)
                location = extract_location_heuristic(combined)
                date_of_incident = None
                risk_flags = []

            lead = {
                "lead_id": lead_id,
                "source_id": source_id,
                "title": title[:500],
                "url": link,
                "published_at": pub_date,
                "snippet": summary[:1000],
                "entities_json": entities,
                "incident_type": incident_type,
                "location": location,
                "date_of_incident": date_of_incident,
                "hook_score": hook_score,
                "risk_flags_json": risk_flags,
            }

            if dry_run:
                logger.info("[DRY RUN] Lead: [%d] %s — %s", hook_score, title[:60], link)
                stats["leads_created"] += 1
            else:
                if insert_lead(conn, lead):
                    stats["leads_created"] += 1

    if conn:
        conn.close()

    logger.info(
        "RSS discovery: %d feeds, %d entries, %d leads created, %d errors",
        stats["feeds_processed"], stats["entries_found"],
        stats["leads_created"], stats["errors"],
    )
    return stats


# ── Page discovery ────────────────────────────────────────────────────────
def discover_from_pages(limit: int | None = None, use_llm: bool = True,
                        dry_run: bool = False) -> dict:
    """Discover leads from press/transparency pages."""
    sources = get_enabled_sources(source_type="webpage")
    if limit:
        sources = sources[:limit]

    stats = {"pages_processed": 0, "links_found": 0, "leads_created": 0, "errors": 0}

    conn = None
    client = None
    if not dry_run:
        init_db()
        conn = get_connection()
    if use_llm:
        try:
            client = get_openrouter_client()
        except Exception:
            pass

    headers = {"User-Agent": "Mozilla/5.0 (compatible; FOIAFreePipeline/2.0)"}

    for src in sources:
        source_id = src["source_id"]
        page_url = src["url"]
        logger.info("Scraping page: %s (%s)", src["name"], source_id)

        try:
            resp = requests.get(page_url, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Page fetch failed %s: %s", source_id, exc)
            stats["errors"] += 1
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        stats["pages_processed"] += 1

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)
            parent_text = a_tag.parent.get_text(strip=True) if a_tag.parent else ""
            combined = f"{text} {parent_text}"

            if not INCIDENT_KEYWORDS.search(combined):
                continue

            from urllib.parse import urljoin, urlparse
            full_url = urljoin(f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}", href)
            if not full_url.startswith("http"):
                continue

            stats["links_found"] += 1
            lead_id = make_lead_id(source_id, full_url)
            title = text[:500] or "Untitled"
            snippet = parent_text[:1000]

            if client:
                llm_result = compute_hook_score_llm(title, snippet, client)
                hook_score = llm_result.get("hook_score", 0)
                entities = llm_result.get("entities", {})
                incident_type = llm_result.get("incident_type", classify_incident(combined))
                location = llm_result.get("location")
                risk_flags = llm_result.get("risk_flags", [])
            else:
                hook_score = compute_hook_score_heuristic(title, snippet)
                entities = {}
                incident_type = classify_incident(combined)
                location = extract_location_heuristic(combined)
                risk_flags = []

            lead = {
                "lead_id": lead_id,
                "source_id": source_id,
                "title": title,
                "url": full_url,
                "snippet": snippet,
                "entities_json": entities,
                "incident_type": incident_type,
                "location": location,
                "hook_score": hook_score,
                "risk_flags_json": risk_flags,
            }

            if dry_run:
                logger.info("[DRY RUN] Lead: [%d] %s", hook_score, title[:60])
                stats["leads_created"] += 1
            else:
                if insert_lead(conn, lead):
                    stats["leads_created"] += 1

    if conn:
        conn.close()

    logger.info(
        "Page discovery: %d pages, %d links, %d leads, %d errors",
        stats["pages_processed"], stats["links_found"],
        stats["leads_created"], stats["errors"],
    )
    return stats


# ── Main ──────────────────────────────────────────────────────────────────
def discover(days: int = 7, limit: int | None = None, use_llm: bool = True,
             dry_run: bool = False) -> dict:
    """Run full discovery (RSS + pages) → case_leads."""
    rss_stats = discover_from_rss(days=days, limit=limit, use_llm=use_llm, dry_run=dry_run)
    page_stats = discover_from_pages(limit=limit, use_llm=use_llm, dry_run=dry_run)
    return {"rss": rss_stats, "pages": page_stats}


def main():
    parser = argparse.ArgumentParser(description="Discover case leads from RSS + pages.")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7).")
    parser.add_argument("--limit", type=int, default=None, help="Max sources to process.")
    parser.add_argument("--no-llm", action="store_true", help="Use heuristic scoring only.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = discover(days=args.days, limit=args.limit, use_llm=not args.no_llm, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
