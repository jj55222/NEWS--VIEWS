#!/usr/bin/env python3
"""Corroboration module: gather supporting sources for PASS candidates.

For each PASS candidate, automatically searches for 2-5 supporting sources
(DA/AG press pages, police press releases, court stream references,
reputable local news) and builds a Fact Pack.

Usage:
    python -m scripts.corroborate                       # all PASS candidates
    python -m scripts.corroborate --limit 10            # first 10
    python -m scripts.corroborate --dry-run             # preview, don't write
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

import requests
from bs4 import BeautifulSoup

from scripts.config_loader import (
    get_env,
    get_openrouter_client,
    get_policy,
    setup_logging,
)
from scripts.db import (
    get_candidates,
    get_connection,
    init_db,
    insert_corroboration,
    now_iso,
)

logger = setup_logging("corroborate")

# Search engines / news APIs to use for corroboration
GOOGLE_NEWS_SEARCH = "https://www.google.com/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Search helpers ─────────────────────────────────────────────────────────
def search_google_news(query: str, num_results: int = 5) -> list[dict]:
    """Perform a Google search and parse results (basic scraping fallback).

    Returns list of dicts with keys: url, title, snippet.
    """
    params = {
        "q": query,
        "num": num_results,
        "tbm": "nws",  # News tab
    }
    try:
        resp = requests.get(GOOGLE_NEWS_SEARCH, params=params, headers=HEADERS, timeout=15)
        if not resp.ok:
            return []
    except requests.RequestException as exc:
        logger.warning("Google search failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for g in soup.select("div.g, div.SoaBEf, div.MjjYud"):
        a = g.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if href.startswith("/url?q="):
            href = href.split("/url?q=")[1].split("&")[0]
        if not href.startswith("http"):
            continue

        title_el = g.find("h3") or g.find("div", {"role": "heading"})
        title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)

        snippet_el = g.find("div", class_=re.compile(r"VwiC3b|st|s3v9rd"))
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

        results.append({"url": href, "title": title[:300], "snippet": snippet[:500]})

    return results[:num_results]


# ── Brave Search ──────────────────────────────────────────────────────────
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds


def _fetch_json(url: str, headers: dict | None = None) -> dict | None:
    """Fetch JSON from URL with retry/backoff for 429 and 5xx errors."""
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


def search_brave(query: str, num_results: int = 5) -> list[dict]:
    """Search the web using Brave Search API."""
    api_key = get_env("BRAVE_API_KEY")
    if not api_key:
        return []

    params = urllib.parse.urlencode({
        "q": query,
        "count": min(num_results, 20),
    })
    url = f"{BRAVE_SEARCH_URL}?{params}"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }

    data = _fetch_json(url, headers=headers)
    if not data:
        return []

    results = []
    for item in data.get("web", {}).get("results", []):
        results.append({
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "snippet": item.get("description", "")[:500],
        })

    return results


def search_for_corroboration(query: str, num_results: int = 5) -> list[dict]:
    """Try Brave Search first, fall back to Google scraping."""
    results = search_brave(query, num_results)
    if not results:
        results = search_google_news(query, num_results)
    return results


# ── Fact pack builder ──────────────────────────────────────────────────────
FACT_PACK_SYSTEM_PROMPT = """\
You are a fact-checking analyst. Given a candidate story and supporting sources,
build a Fact Pack. Return ONLY valid JSON with this schema:

{
  "verified_facts": [
    {"fact": "<statement>", "source": "<url or source name>", "confidence": "high|medium|low"}
  ],
  "unverified_claims": ["<claim that couldn't be confirmed>"],
  "names_confirmed": ["<name>"],
  "charges_confirmed": ["<charge>"],
  "date_confirmed": "<date or null>",
  "location_confirmed": "<location or null>",
  "redaction_needed": ["<items that should be redacted>"],
  "summary": "<2-3 sentence factual summary>"
}

Rules:
- Only state what is directly sourced
- Mark anything unconfirmed as "unverified_claims"
- Flag anything that needs redaction (addresses, minor names, medical info)
- Be conservative: if in doubt, mark as unverified
"""


def build_fact_pack(candidate: dict, sources: list[dict], client) -> dict:
    """Use LLM to build a fact pack from candidate + corroboration sources."""
    user_parts = [
        f"CANDIDATE TITLE: {candidate.get('title', 'N/A')}",
        f"CANDIDATE URL: {candidate.get('url', 'N/A')}",
        f"DESCRIPTION: {(candidate.get('description') or '')[:1500]}",
    ]

    transcript = candidate.get("transcript_text") or ""
    if transcript:
        user_parts.append(f"TRANSCRIPT EXCERPT: {transcript[:2000]}")

    entities = candidate.get("entities_json", "[]")
    if isinstance(entities, str):
        try:
            entities = json.loads(entities)
        except json.JSONDecodeError:
            entities = []
    if entities:
        user_parts.append(f"ENTITIES: {json.dumps(entities)}")

    user_parts.append("\nSUPPORTING SOURCES:")
    for i, src in enumerate(sources, 1):
        user_parts.append(f"{i}. [{src.get('title', 'Untitled')}]({src['url']})")
        if src.get("snippet"):
            user_parts.append(f"   Snippet: {src['snippet'][:300]}")

    user_prompt = "\n".join(user_parts)

    try:
        model = get_policy("llm", "corroboration_model", "openai/gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            temperature=0.1,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": FACT_PACK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```json")[-1].split("```")[0].strip() if "```json" in raw else raw.split("```")[1].split("```")[0].strip()
        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Fact pack LLM failed: %s", exc)
        return {
            "verified_facts": [],
            "unverified_claims": [],
            "names_confirmed": [],
            "charges_confirmed": [],
            "date_confirmed": None,
            "location_confirmed": None,
            "redaction_needed": [],
            "summary": "Fact pack generation failed.",
        }


# ── Build search queries ──────────────────────────────────────────────────
def build_corroboration_queries(candidate: dict) -> list[str]:
    """Generate search queries from candidate metadata."""
    queries = []
    title = candidate.get("title", "")
    entities = candidate.get("entities_json", "[]")
    if isinstance(entities, str):
        try:
            entities = json.loads(entities)
        except json.JSONDecodeError:
            entities = {}

    # Query from title
    if title:
        # Clean title of common YouTube prefixes
        clean_title = re.sub(
            r"^(bodycam|dashcam|body cam|dash cam|full video|raw footage)[:\s-]*",
            "", title, flags=re.IGNORECASE,
        ).strip()
        if clean_title:
            queries.append(f"{clean_title} press release OR charges OR arrest")

    # Query from entities
    names = entities.get("names", []) if isinstance(entities, dict) else []
    places = entities.get("places", []) if isinstance(entities, dict) else []
    agencies = entities.get("agencies", []) if isinstance(entities, dict) else []

    if names and places:
        queries.append(f"{names[0]} {places[0] if places else ''} arrest OR charges OR incident")
    if agencies and names:
        queries.append(f"{agencies[0]} {names[0]} press release")

    # Generic incident query
    incident_type = candidate.get("incident_type", "")
    if incident_type and incident_type != "unknown" and places:
        queries.append(f"{places[0]} {incident_type} police")

    return queries[:4]  # Cap at 4 queries


# ── Main corroboration ────────────────────────────────────────────────────
def corroborate(limit: int = 50, dry_run: bool = False) -> dict:
    """Run corroboration for all PASS candidates.

    Returns:
        dict with keys: processed, sources_found, fact_packs_built, errors
    """
    init_db()
    conn = get_connection()
    candidates = get_candidates(conn, status="PASS", limit=limit)

    if not candidates:
        logger.info("No PASS candidates to corroborate.")
        conn.close()
        return {"processed": 0, "sources_found": 0, "fact_packs_built": 0, "errors": 0}

    client = get_openrouter_client()
    stats = {"processed": 0, "sources_found": 0, "fact_packs_built": 0, "errors": 0}

    for cand in candidates:
        cid = cand["candidate_id"]
        title = (cand.get("title") or "")[:60]
        logger.info("Corroborating: %s — %s", cid, title)

        try:
            # Build search queries
            queries = build_corroboration_queries(cand)
            if not queries:
                queries = [f"{cand.get('title', '')} police arrest charges"]

            # Search for supporting sources
            all_sources = []
            seen_urls = set()
            for q in queries:
                results = search_for_corroboration(q, num_results=3)
                for r in results:
                    if r["url"] not in seen_urls:
                        seen_urls.add(r["url"])
                        all_sources.append(r)

            stats["sources_found"] += len(all_sources)
            logger.info("  Found %d supporting sources for %s", len(all_sources), cid)

            if dry_run:
                for src in all_sources:
                    logger.info("  [DRY RUN] Source: %s — %s", src["title"][:60], src["url"])
                stats["processed"] += 1
                continue

            # Save corroboration sources to DB
            for src in all_sources:
                src_type = "news_article"
                url_lower = src["url"].lower()
                if "press-release" in url_lower or "pressrelease" in url_lower:
                    src_type = "press_release"
                elif "court" in url_lower or "docket" in url_lower:
                    src_type = "court_record"
                elif "da.gov" in url_lower or "ag.gov" in url_lower or "attorney" in url_lower:
                    src_type = "da_statement"

                insert_corroboration(conn, {
                    "id": uuid.uuid4().hex[:16],
                    "case_id": cid,  # Temporarily using candidate_id
                    "url": src["url"],
                    "source_type": src_type,
                    "title": src["title"],
                    "snippet": src.get("snippet", ""),
                })

            # Build fact pack via LLM
            if all_sources:
                fact_pack = build_fact_pack(cand, all_sources, client)
                # Store fact pack in candidate's facts_to_verify field
                conn.execute(
                    "UPDATE candidates SET facts_to_verify_json = ?, updated_at = ? WHERE candidate_id = ?",
                    (json.dumps(fact_pack), now_iso(), cid),
                )
                conn.commit()
                stats["fact_packs_built"] += 1

            stats["processed"] += 1

        except Exception as exc:
            logger.error("Corroboration error for %s: %s", cid, exc)
            stats["errors"] += 1

    conn.close()
    logger.info(
        "Corroboration complete: %d processed, %d sources, %d fact packs, %d errors",
        stats["processed"], stats["sources_found"],
        stats["fact_packs_built"], stats["errors"],
    )
    return stats


# ── CLI ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Corroborate PASS candidates with supporting sources.")
    parser.add_argument("--limit", type=int, default=50, help="Max candidates to corroborate.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB.")
    args = parser.parse_args()

    stats = corroborate(limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
