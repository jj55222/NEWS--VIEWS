"""
Candidate routing: decides whether an article proceeds to normalization.

Routing is NOT editorial judgment — it's signal-based gating to avoid
wasting LLM credits on low-signal articles.
"""

from __future__ import annotations

from src.common.config import Config
from src.common.schema import RoutingStatus


def route_candidate(prescore: dict, config: Config) -> tuple[str, str]:
    """
    Decide routing status for a candidate based on prescore.

    Returns (status, reason) tuple.
    """
    score = prescore.get("score", 0)
    matches = prescore.get("matches", [])

    if score >= config.min_prescore:
        return (
            RoutingStatus.CANDIDATE.value,
            f"Prescore {score} >= threshold {config.min_prescore}, "
            f"signals: {', '.join(matches[:5])}"
        )

    return (
        RoutingStatus.REJECTED_LOW_SIGNAL.value,
        f"Prescore {score} < threshold {config.min_prescore}. "
        f"Matched: {', '.join(matches) if matches else 'none'}"
    )
