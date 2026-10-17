"""Follow-up questions: after each draft, ask the contact only what is still missing from the school profile.

14 short questions (French and English) cover what the priority matrix needs. Each question knows how to tell
from the case draft whether it is already answered, so a school that already explained its water problem is
never asked about water again. At most 3 questions go out at a time, most important first, in the contact's
language. Staff review the message before it is sent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

MAX_PER_MESSAGE = 3


def _known(block: dict | None, key: str) -> bool:
    return ((block or {}).get(key) or {}).get("status", "unknown") != "unknown"


def _num(d: dict, key: str) -> bool:
    return (d.get("numbers") or {}).get(key) is not None


@dataclass(frozen=True)
class Question:
    id: int
    topic: str
    en: str
    fr: str
    answered: Callable[[dict, str], bool]   # (case data, trust level) -> already known?


QUESTIONS = [
    Question(1, "identity",
             "What is the school's full name, and which town and region is it in?",
             "Quel est le nom complet de l'école, et dans quelle ville et quelle région se trouve-t-elle ?",
             lambda d, t: bool((d.get("organization") or {}).get("name"))
             and bool((d.get("organization") or {}).get("town") or (d.get("organization") or {}).get("region"))),
    Question(2, "location",
             "When you are at the school, please share its location (tap the paperclip, then Location).",
             "Quand vous êtes à l'école, merci de partager sa position (trombone, puis Position).",
             lambda d, t: (d.get("location") or {}).get("latitude") is not None),
    Question(3, "operating",
             "Is the school open now, and is it safe for pupils to get there? Reply 1 = open and safe, "
             "2 = open but the road is sometimes unsafe, 3 = closed.",
             "L'école est-elle ouverte en ce moment, et les élèves peuvent-ils y aller en sécurité ? Répondez "
             "1 = ouverte et sûre, 2 = ouverte mais la route est parfois dangereuse, 3 = fermée.",
             lambda d, t: (d.get("operating_status") or {}).get("status", "unknown") != "unknown"
             and (d.get("operating_status") or {}).get("safe_to_reach", "unknown") != "unknown"),
    Question(4, "pupils",
             "How many pupils are enrolled, and how many of them are girls?",
             "Combien d'élèves sont inscrits, et combien sont des filles ?",
             lambda d, t: _num(d, "students") and _num(d, "girls")),
    Question(5, "classrooms",
             "How many classrooms do you use, and how many need repair?",
             "Combien de salles de classe utilisez-vous, et combien ont besoin de réparation ?",
             lambda d, t: _num(d, "classrooms") and _known(d.get("profile"), "learning_space")),
    Question(6, "teachers",
             "How many teachers are there, and how many are paid by the parents?",
             "Combien d'enseignants avez-vous, et combien sont payés par les parents ?",
             lambda d, t: _num(d, "teachers") and _num(d, "parent_paid_teachers")),
    Question(7, "water",
             "Is there drinking water at the school? 1 = every day, 2 = sometimes or far away, 3 = no.",
             "Y a-t-il de l'eau potable à l'école ? 1 = tous les jours, 2 = parfois ou loin, 3 = non.",
             lambda d, t: _known(d.get("profile"), "water_toilets")),
    Question(8, "toilets",
             "Are there working toilets? 1 = yes, separate for girls, 2 = shared or broken, 3 = none.",
             "Y a-t-il des toilettes qui fonctionnent ? 1 = oui, séparées pour les filles, 2 = partagées ou cassées, 3 = aucune.",
             lambda d, t: _known(d.get("profile"), "water_toilets")),
    Question(9, "electricity",
             "Does the school have electricity? 1 = grid power, 2 = solar or frequent cuts, 3 = none.",
             "L'école a-t-elle l'électricité ? 1 = réseau ENEO, 2 = solaire ou coupures fréquentes, 3 = aucune.",
             lambda d, t: _known(d.get("readiness"), "electricity")),
    Question(10, "signal",
             "How is the phone signal at the school? 1 = good, with internet, 2 = calls only, 3 = no signal.",
             "Comment est le réseau téléphonique à l'école ? 1 = bon, avec internet, 2 = appels seulement, 3 = pas de réseau.",
             lambda d, t: _known(d.get("readiness"), "internet")),
    Question(11, "road",
             "Can a truck reach the school? 1 = all year, 2 = not in the rainy season, 3 = no.",
             "Un camion peut-il arriver jusqu'à l'école ? 1 = toute l'année, 2 = pas en saison des pluies, 3 = non.",
             lambda d, t: _known(d.get("readiness"), "road_access")),
    Question(12, "birth_certificates",
             "About how many pupils have no birth certificate?",
             "Environ combien d'élèves n'ont pas d'acte de naissance ?",
             lambda d, t: _num(d, "without_birth_certificate")),
    Question(13, "biggest_need",
             "What is the school's biggest need right now? A voice note is fine.",
             "Quel est le plus grand besoin de l'école en ce moment ? Une note vocale suffit.",
             lambda d, t: bool(d.get("needs"))),
    Question(14, "photos",
             "Please send 2 photos: one classroom, and the toilets or water point. No children's faces, please.",
             "Merci d'envoyer 2 photos : une salle de classe, et les toilettes ou le point d'eau. Pas de visages d'enfants, s'il vous plaît.",
             lambda d, t: t in ("photo_backed", "visit_confirmed")),
]
BY_ID = {q.id: q for q in QUESTIONS}
# Safety first, then what decides priority; location, photos and details last.
ASK_ORDER = [3, 1, 13, 4, 7, 8, 5, 6, 14, 2, 9, 10, 11, 12]

INTRO = {
    "fr": "PS43 : Merci ! Pour compléter le dossier de votre école, pouvez-vous répondre à {n} :",
    "en": "PS43: Thank you! To complete your school's file, could you answer {n}:",
}
OUTRO = {
    "fr": "Vous pouvez répondre par message ou par note vocale.",
    "en": "You can reply by text or voice note.",
}
COUNT = {"fr": ("cette question", "ces {n} questions"), "en": ("this question", "these {n} questions")}


def message_language(data: dict) -> str:
    lang = f"{data.get('language') or ''} {(data.get('confirmation_message') or {}).get('language') or ''}".lower()
    return "fr" if ("fr" in lang or "fran" in lang) else "en"


def applies(data: dict) -> bool:
    """Only school / community needs get profile questions, not partners, donors or personal chats."""
    if data.get("relevance") == "not_relevant":
        return False
    if data.get("case_type") == "offer":
        return False
    return (data.get("organization") or {}).get("type", "unknown") in ("school", "community", "unknown")


def follow_up(data: dict, trust: str = "self_reported", limit: int = MAX_PER_MESSAGE) -> dict:
    lang = message_language(data)
    if not applies(data):
        return {"applies": False, "language": lang, "missing": [], "asking": [],
                "missing_topics": [], "complete": None, "message": ""}
    missing = [qid for qid in ASK_ORDER if not BY_ID[qid].answered(data, trust)]
    asking = missing[:limit]
    complete = round(100 * (len(QUESTIONS) - len(missing)) / len(QUESTIONS))
    message = ""
    if asking:
        one, many = COUNT[lang]
        lines = [INTRO[lang].format(n=one if len(asking) == 1 else many.format(n=len(asking)))]
        lines += [f"{n}. {getattr(BY_ID[qid], lang)}" for n, qid in enumerate(asking, 1)]
        lines.append(OUTRO[lang])
        message = "\n".join(lines)
    return {"applies": True, "language": lang, "missing": missing, "asking": asking,
            "missing_topics": [BY_ID[q].topic for q in missing], "complete": complete, "message": message}


def refresh(data: dict) -> dict:
    """Recompute after a staff edit (the trust level comes from the items and is kept)."""
    data["_follow_up"] = follow_up(data, (data.get("_trust") or {}).get("level", "self_reported"))
    return data
