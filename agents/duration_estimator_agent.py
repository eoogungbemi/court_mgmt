"""
LLM-backed (Claude). Given structured case/hearing context, returns a
P25-P75 duration range and a plain-English rationale.

Falls back to a rule-based lookup when the LLM is unavailable or
returns invalid output, ensuring the system never blocks on LLM failure.
"""

import json
import logging
from datetime import datetime
from sqlalchemy.orm import Session

import anthropic
from anthropic import APIError

from config import ANTHROPIC_API_KEY, HEARING_DURATIONS, COMPLEXITY_MULTIPLIER
from db.models import Hearing, Case, ETAEstimate, AuditLog
from graph.state import CourtState, ETAEstimate as ETAEstimateState, AuditEvent

logger = logging.getLogger(__name__)
AGENT_NAME = "DurationEstimatorAgent"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = """You are an AI assistant supporting a US court scheduling system.
Your job is to estimate how long a court hearing will take based on structured inputs.

Rules:
- Output ONLY valid JSON, no markdown, no explanation outside the JSON.
- p25_mins must be less than p75_mins.
- Rationale must be 1-2 sentences, plain English, suitable for a court clerk.
- Base estimates on case type, hearing type, complexity, and any contextual signals.

Output format:
{"p25_mins": <int>, "p75_mins": <int>, "rationale": "<string>"}"""


def _llm_estimate(case_type: str, hearing_type: str,
                  complexity: str, context: dict) -> dict | None:
    prompt = (
        f"Case type: {case_type}\n"
        f"Hearing type: {hearing_type}\n"
        f"Complexity: {complexity}\n"
        f"Additional context: {json.dumps(context)}\n\n"
        "Provide a duration estimate."
    )
    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        result = json.loads(raw)
        if (
            isinstance(result.get("p25_mins"), int)
            and isinstance(result.get("p75_mins"), int)
            and result["p25_mins"] < result["p75_mins"]
            and isinstance(result.get("rationale"), str)
        ):
            return result
    except (APIError, json.JSONDecodeError, KeyError) as exc:
        logger.warning("LLM estimation failed: %s — falling back to rules", exc)
    return None


def _rule_estimate(case_type: str, hearing_type: str, complexity: str) -> dict:
    lo, hi = HEARING_DURATIONS.get(case_type, {}).get(hearing_type, (20, 40))
    mult = COMPLEXITY_MULTIPLIER[complexity]
    p25 = max(5, int(lo * mult))
    p75 = max(p25 + 5, int(hi * mult))
    return {
        "p25_mins": p25,
        "p75_mins": p75,
        "rationale": (
            f"Rule-based estimate for {complexity}-complexity {hearing_type} "
            f"({case_type} case). LLM unavailable."
        ),
    }


def run(state: CourtState, db: Session) -> CourtState:
    payload = state["trigger_payload"]
    hearing_id: int = payload.get("hearing_id")

    hearing = db.get(Hearing, hearing_id)
    if hearing is None:
        return state

    case: Case = hearing.case

    # Build context for the LLM
    context = {
        "courtroom": state["courtroom_name"],
        "queue_position": next(
            (i for i, h in enumerate(state["queue"])
             if h["hearing_id"] == hearing_id), 0
        ),
        "all_parties_present": (
            hearing.lawyer_checked_in and hearing.accused_checked_in
        ),
    }

    result = _llm_estimate(case.case_type, hearing.hearing_type,
                           case.complexity, context)
    agent_used = AGENT_NAME
    if result is None:
        result = _rule_estimate(case.case_type, hearing.hearing_type,
                                case.complexity)
        agent_used = f"{AGENT_NAME}(fallback)"

    # Persist to DB
    eta_row = ETAEstimate(
        hearing_id=hearing_id,
        estimated_start=hearing.scheduled_start,
        p25_mins=result["p25_mins"],
        p75_mins=result["p75_mins"],
        rationale=result["rationale"],
        agent_name=agent_used,
    )
    db.add(eta_row)
    db.commit()

    # Update in-memory state
    eta_state = ETAEstimateState(
        hearing_id=hearing_id,
        estimated_start=hearing.scheduled_start.isoformat(),
        p25_mins=result["p25_mins"],
        p75_mins=result["p75_mins"],
        rationale=result["rationale"],
        agent_name=agent_used,
        generated_at=datetime.now().isoformat(),
    )

    event = AuditEvent(
        event_type="ETA_ESTIMATED",
        agent_name=agent_used,
        entity_type="hearing",
        entity_id=hearing_id,
        payload=result,
        created_at=datetime.now().isoformat(),
    )
    db.add(AuditLog(
        event_type=event["event_type"],
        agent_name=event["agent_name"],
        entity_type=event["entity_type"],
        entity_id=event["entity_id"],
        payload=json.dumps(event["payload"]),
    ))
    db.commit()

    return {
        **state,
        "eta_estimates": {**state["eta_estimates"], hearing_id: eta_state},
        "trigger": "eta_ready",
        "audit_events": [event],
    }
