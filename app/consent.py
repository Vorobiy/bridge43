"""Opt-out: anyone who writes STOP / ARRET has all their data deleted.

Messages reach Bridge43 through the PS43 laptop (WhatsApp Desktop), uploads and forms. Staff send the
NOTICE below from their own phone the first time they reply to a new school.
"""
from __future__ import annotations

import re

from . import db, media

OPT_OUT_WORDS = {"stop", "arret", "arrêt", "unsubscribe", "desabonner", "désabonner", "quit"}

NOTICE = (
    "PS43 Foundation: thank you for contacting us. To help your school, our team keeps notes of this "
    "conversation (messages, voice notes, photos and calls), with help from an AI assistant. "
    "A PS43 staff member reviews every note. Reply STOP at any time to have your information deleted.\n\n"
    "Fondation PS43 : merci de nous avoir contactés. Pour aider votre école, notre équipe garde des notes "
    "de cette conversation (messages, notes vocales, photos et appels) avec l'aide d'un assistant IA. "
    "Un membre de l'équipe PS43 vérifie chaque note. Répondez ARRET à tout moment pour faire supprimer vos informations."
)


def _word(text: str | None) -> str:
    return re.sub(r"[^\w'’]", "", (text or "").strip().lower()).replace("’", "'")


def is_opt_out(text: str | None) -> bool:
    return _word(text) in OPT_OUT_WORDS


def opt_out(contact_id: int) -> None:
    """Delete everything about this contact and remember that they opted out."""
    media.delete_files(db.delete_contact_data(contact_id))
    db.set_consent(contact_id, "opted_out")
