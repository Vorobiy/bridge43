"""Is a new WhatsApp conversation PS43 work? Asked before anything from a new chat is stored,
so personal chats on a staff laptop never reach the shared database."""
from __future__ import annotations

import logging

from . import ai
from .config import settings

log = logging.getLogger("bridge43.triage")

TRIAGE_TOOL = {
    "name": "record_triage",
    "description": "Record whether this conversation is PS43 Foundation work.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {"type": "boolean"},
            "reason": {"type": "string", "description": "One short sentence."},
        },
        "required": ["relevant", "reason"],
    },
}

SYSTEM = (
    "You screen WhatsApp conversations on a PS43 Foundation staff laptop. PS43 rebuilds and equips schools "
    "in Cameroon. A conversation is relevant if it is about a school (buildings, classrooms, roofs, latrines, "
    "water, power, furniture, books, computers, teachers, students), about rebuilding or repairing one, or "
    "about logistics for that work of any kind: transport, deliveries, materials, contractors, quotes, "
    "payments, permits, site visits, volunteers, partners or donations. It is also relevant if the person "
    "introduces themselves as a school, community, partner or supplier wanting to work with PS43. "
    "Personal chats, family, friends, social plans, spam and unrelated business are not relevant. "
    "Messages may be in English, French or Cameroonian Pidgin. When unsure, say relevant."
)


def is_relevant(conversation: str) -> tuple[bool, str]:
    if not conversation.strip():
        return False, "nothing to read yet"
    msg = ai.client().messages.create(
        model=settings.summary_model,
        max_tokens=200,
        system=SYSTEM,
        tools=[TRIAGE_TOOL],
        tool_choice={"type": "tool", "name": TRIAGE_TOOL["name"]},
        messages=[{"role": "user", "content": f"<conversation>\n{conversation[:8000]}\n</conversation>"}],
    )
    for block in msg.content:
        if getattr(block, "type", "") == "tool_use":
            return bool(block.input.get("relevant")), str(block.input.get("reason") or "")
    return True, "screening gave no answer, kept to be safe"
