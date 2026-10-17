"""Consent and opt-out handling for messaging channels.

Every new contact gets a short bilingual notice explaining that PS43 keeps notes of the
conversation with AI help. Replying STOP / ARRET deletes their data. With
REQUIRE_EXPLICIT_CONSENT=true, nothing is processed until they reply YES / OUI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import db, media
from .config import settings

OPT_OUT_WORDS = {"stop", "arret", "arrêt", "unsubscribe", "desabonner", "désabonner", "quit"}
OPT_IN_WORDS = {"yes", "oui", "ok", "okay", "d'accord", "daccord", "accept", "j'accepte"}

NOTICE = (
    "PS43 Foundation: thank you for contacting us. To help your school, our team keeps notes of this "
    "conversation (messages, voice notes, photos and calls), with help from an AI assistant. "
    "A PS43 staff member reviews every note. Reply STOP at any time to have your information deleted.\n\n"
    "Fondation PS43 : merci de nous avoir contactés. Pour aider votre école, notre équipe garde des notes "
    "de cette conversation (messages, notes vocales, photos et appels) avec l'aide d'un assistant IA. "
    "Un membre de l'équipe PS43 vérifie chaque note. Répondez ARRET à tout moment pour faire supprimer vos informations."
)
EXPLICIT_SUFFIX = "\n\nReply YES to continue. / Répondez OUI pour continuer."
OPTED_OUT_REPLY = (
    "Your information has been deleted and you will not receive further messages. "
    "/ Vos informations ont été supprimées et vous ne recevrez plus de messages."
)
CONSENT_THANKS = "Thank you. / Merci."


@dataclass
class ConsentDecision:
    reply: str | None       # text to send back now (None = nothing)
    process: bool           # process this item now
    hold: bool              # store the item but hold it until consent
    deleted: bool = False   # contact opted out and was erased
    released_item_ids: list[int] = field(default_factory=list)  # held items now cleared to process


def _word(text: str | None) -> str:
    return re.sub(r"[^\w'’]", "", (text or "").strip().lower()).replace("’", "'")


def decide(contact: dict, text: str | None) -> ConsentDecision:
    word = _word(text)
    status = contact["consent_status"]

    if word in OPT_OUT_WORDS:
        paths = db.delete_contact_data(contact["id"])
        media.delete_files(paths)
        db.set_consent(contact["id"], "opted_out")
        return ConsentDecision(reply=OPTED_OUT_REPLY, process=False, hold=False, deleted=True)

    if status == "opted_out":
        # They wrote again after opting out: treat it as a fresh start.
        status = "pending"

    if settings.require_explicit_consent:
        if status == "granted":
            return ConsentDecision(reply=None, process=True, hold=False)
        if word in OPT_IN_WORDS:
            db.set_consent(contact["id"], "granted")
            released = [i["id"] for i in db.release_held_items(contact["id"])]
            return ConsentDecision(reply=CONSENT_THANKS, process=False, hold=False, released_item_ids=released)
        db.set_consent(contact["id"], "notified")
        reply = NOTICE + EXPLICIT_SUFFIX if status != "notified" else None
        return ConsentDecision(reply=reply, process=False, hold=True)

    # Notice-based consent (default): the contact reached out to PS43 for help; we tell them how
    # their information is handled and process it unless they opt out.
    if status in ("pending",):
        db.set_consent(contact["id"], "notified")
        return ConsentDecision(reply=NOTICE, process=True, hold=False)
    return ConsentDecision(reply=None, process=True, hold=False)


def is_consent_keyword(text: str | None) -> bool:
    w = _word(text)
    return w in OPT_IN_WORDS or w in OPT_OUT_WORDS
