"""
Incident normalization: LLM extracts structured fields from raw article text.

Every viable candidate gets a normalized Incident object.
The LLM fills in: people, jurisdiction, incident_type, allegations,
narrative_hook, and summary.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from src.common.config import Config
from src.common.schema import Incident
from src.common.logging import PacketLog


EXTRACT_SCHEMA = {
    "story_summary": "",
    "narrative_hook": "",
    "incident_type": "",
    "incident_date": "",
    "agency": "",
    "jurisdiction": {"city": "", "county": "", "state": ""},
    "people": [{"name": "", "role": "defendant/victim/officer/witness"}],
    "allegations_or_charges": [],
    "footage_indicators": {
        "bodycam_mentioned": False,
        "dashcam_mentioned": False,
        "surveillance_mentioned": False,
        "interrogation_mentioned": False,
        "court_video_mentioned": False,
    },
    "case_identifiers": {
        "case_number": "",
        "arrest_date": "",
        "agency_abbrev": "",
    },
}


NORMALIZE_PROMPT = """Extract structured case information from this article.

TITLE: {title}
TEXT: {text}

Return JSON matching this schema exactly:
{schema}

Rules:
- people: list every named person with their role (defendant, victim, officer, witness)
- jurisdiction: city/county/state where the incident occurred
- incident_type: one of: shooting, use_of_force, pursuit, in_custody_death, domestic, homicide, child_abuse, other
- narrative_hook: one sentence capturing why this case is compelling (the moral weight)
- story_summary: 2-3 sentence factual summary
- allegations_or_charges: list of specific charges or allegations
- footage_indicators: set true ONLY if the article explicitly mentions that type of footage

JSON only, no commentary:"""


def _init_llm(config: Config):
    from openai import OpenAI
    return OpenAI(
        api_key=config.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )


def _parse_llm_json(content: str) -> Optional[dict]:
    """Extract JSON from LLM response, handling markdown wrappers."""
    content = content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        return None


def normalize_incident(
    incident: Incident,
    config: Config,
    log: PacketLog,
) -> Incident:
    """
    Normalize a raw incident by extracting structured fields via LLM.

    Mutates and returns the incident with filled fields.
    """
    raw_text = getattr(incident, "_raw_text", "")
    raw_title = getattr(incident, "_raw_title", incident.source_title)

    if not raw_text:
        log.add("normalize", "skip", detail="No raw text available")
        return incident

    llm = _init_llm(config)

    prompt = NORMALIZE_PROMPT.format(
        title=raw_title,
        text=raw_text[:12000],
        schema=json.dumps(EXTRACT_SCHEMA, indent=2),
    )

    try:
        response = llm.chat.completions.create(
            model=config.openrouter_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            extra_headers={
                "HTTP-Referer": "https://newstoviews.app",
                "X-Title": "NewsToViews-Normalize",
            },
        )
        content = response.choices[0].message.content
        extracted = _parse_llm_json(content)
    except Exception as e:
        log.add("normalize", "error", detail=str(e))
        return incident

    if not extracted:
        log.add("normalize", "error", detail="Failed to parse LLM JSON")
        return incident

    # Apply extracted fields to incident
    incident.story_summary = extracted.get("story_summary", "")
    incident.narrative_hook = extracted.get("narrative_hook", "")
    incident.incident_type = extracted.get("incident_type", "")
    incident.incident_date = extracted.get("incident_date", "")
    incident.agency = extracted.get("agency", "")
    incident.jurisdiction = extracted.get("jurisdiction", {})
    incident.people = extracted.get("people", [])
    incident.allegations_or_charges = extracted.get("allegations_or_charges", [])

    # Track what the LLM found for evidence hints
    footage = extracted.get("footage_indicators", {})
    footage_flags = [k for k, v in footage.items() if v]

    case_ids = extracted.get("case_identifiers", {})
    if case_ids.get("agency_abbrev"):
        incident.agency = incident.agency or case_ids["agency_abbrev"]

    log.add(
        "normalize", "extracted",
        detail=f"people={len(incident.people)} charges={len(incident.allegations_or_charges)}",
        fields_extracted={
            "footage_indicators": footage_flags,
            "case_ids": case_ids,
            "incident_type": incident.incident_type,
        },
    )

    # Stash footage indicators for enrich stage
    incident._footage_indicators = footage
    incident._case_identifiers = case_ids

    time.sleep(config.llm_sleep)
    return incident
