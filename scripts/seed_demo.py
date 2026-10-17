"""Load fictional demo data (one school need, one partner offer) so the screens can be shown
without any API keys. Run:  python -m scripts.seed_demo

All schools, people and numbers here are invented for the demo.
"""
from __future__ import annotations

from app import db

SCHOOL = [
    ("in", "text", "Mr. Fon (Principal)", "2026-10-14T08:12:00+00:00",
     "Bonjour PS43. Je suis M. Fon, proviseur du Lycée de Bambui-Colline (fictif). Notre école a besoin d'aide."),
    ("out", "text", "Eugene (PS43)", "2026-10-14T08:20:00+00:00",
     "Bonjour M. Fon, merci. Pouvez-vous nous dire combien d'élèves et quels sont les problèmes principaux ?"),
    ("in", "audio", "Mr. Fon (Principal)", "2026-10-14T09:02:00+00:00", None,
     "Nous avons 540 élèves et 14 enseignants. Nous avons 8 salles de classe mais le toit de 3 salles fuit. "
     "Quand il pleut, les élèves de ces salles rentrent à la maison. Il nous manque aussi environ 60 table-bancs. "
     "Les latrines des filles sont cassées depuis l'année passée."),
    ("in", "image", "Mr. Fon (Principal)", "2026-10-14T09:05:00+00:00", "Salle de 4e, après la pluie",
     "Classroom interior with daylight visible through about a dozen holes in the corrugated metal roof. "
     "Water stains on the floor and the back wall. About 20 wooden benches, 5 of them broken. A blackboard is "
     "mounted at the front. No light fixtures or sockets are visible."),
    ("in", "text", "Mr. Fon (Principal)", "2026-10-14T09:10:00+00:00",
     "Nous n'avons pas l'électricité à l'école. Le réseau téléphonique passe bien. "
     "Les parents (APEE) peuvent aider avec la main d'oeuvre pour la toiture."),
    ("out", "text", "Eugene (PS43)", "2026-10-14T09:30:00+00:00",
     "Merci. Nous allons étudier votre dossier et vous rappeler la semaine prochaine pour une visite."),
]

SCHOOL_DRAFT = {
    "case_type": "need",
    "title": "Lycée de Bambui-Colline — 3 leaking classrooms, 60 desks, girls' latrines",
    "organization": {"name": "Lycée de Bambui-Colline (fictional)", "type": "school", "region": "Northwest",
                     "town": "Bambui", "citations": [1]},
    "situation": {"text": "Secondary school with 540 students and 14 teachers in 8 classrooms. The roof of 3 classrooms "
                          "leaks, so students in those rooms go home when it rains. The girls' latrines have been broken "
                          "for a year.", "citations": [1, 3, 4]},
    "location": {"latitude": None, "longitude": None, "description": None, "citations": []},
    "operating_status": {"status": "open", "safe_to_reach": "unknown", "note": "Open; safety of the area not discussed",
                         "citations": [3]},
    "profile": {
        "safety_operation": {"status": "partial", "note": "Pupils in 3 classrooms go home when it rains", "citations": [3]},
        "learning_space": {"status": "critical", "note": "3 of 8 classroom roofs leak; about 60 table-benches missing", "citations": [3, 4]},
        "water_toilets": {"status": "critical", "note": "Girls' latrines broken for a year; water not discussed", "citations": [3]},
        "teachers": {"status": "unknown", "note": "14 teachers; enough or not not discussed", "citations": [3]},
        "materials_results": {"status": "unknown", "note": None, "citations": []},
        "barriers": {"status": "unknown", "note": None, "citations": []},
        "health_wellbeing": {"status": "unknown", "note": None, "citations": []},
        "technology": {"status": "unknown", "note": "No electricity, so no devices in use", "citations": [5]},
    },
    "needs": [
        {"category": "roofing", "description": "Repair or replace roof sheets on 3 classrooms; daylight visible through holes",
         "quantity": "3 classrooms", "urgency": "critical", "citations": [3, 4]},
        {"category": "furniture", "description": "Table-benches (table-bancs) missing; several existing benches broken",
         "quantity": "about 60", "urgency": "high", "citations": [3, 4]},
        {"category": "sanitation_latrines", "description": "Girls' latrines broken since last school year",
         "quantity": None, "urgency": "high", "citations": [3]},
    ],
    "offers": [],
    "assets": [{"description": "Parents' association (APEE) can provide labour for the roofing", "citations": [5]},
               {"description": "Good mobile network coverage at the school", "citations": [5]}],
    "readiness": {
        "electricity": {"status": "no", "note": "No electricity at the school; no sockets visible in photo", "citations": [4, 5]},
        "internet": {"status": "partial", "note": "Mobile network works; no fixed internet mentioned", "citations": [5]},
        "secure_storage": {"status": "unknown", "note": None, "citations": []},
        "road_access": {"status": "unknown", "note": None, "citations": []},
        "trained_staff": {"status": "unknown", "note": None, "citations": []},
        "space": {"status": "yes", "note": "8 classrooms; 3 unusable in rain", "citations": [3]},
        "parent_association": {"status": "yes", "note": "APEE can provide labour for the roofing", "citations": [5]},
    },
    "numbers": {"students": 540, "teachers": 14, "classrooms": 8, "citations": [3]},
    "people": [{"name": "M. Fon", "role": "Principal (proviseur)", "organization": "Lycée de Bambui-Colline", "citations": [1]}],
    "plan": [
        {"action": "Call the principal back next week to arrange a site visit", "owner": "ps43", "due": "week of 19 Oct",
         "agreed": True, "citations": [6]},
        {"action": "Get roof dimensions and a local contractor quote during the visit", "owner": "ps43", "due": None,
         "agreed": False, "citations": []},
    ],
    "open_questions": ["Roof area and number of sheets needed for the 3 classrooms", "How many girls use the latrines; is there water on site?",
                       "Road access for delivering materials in the rainy season", "Is the region currently safe for a site visit?"],
    "safety_flags": [],
    "language": "French",
    "confidence": "medium",
    "confirmation_message": {"language": "French", "text": "PS43 : merci M. Fon. Nous avons compris : 540 élèves, 8 salles dont 3 avec un toit qui fuit, "
                                                         "environ 60 table-bancs manquants et les latrines des filles cassées. Pas d'électricité. "
                                                         "Les parents peuvent aider pour la toiture. Est-ce correct ? Répondez OUI ou corrigez-nous."},
}

PARTNER = [
    ("in", "text", "Ngwa (BuildRight Bamenda)", "2026-10-15T14:00:00+00:00",
     "Hello PS43, this is Ngwa from BuildRight Bamenda (fictional), a roofing contractor. We have 400 aluminium "
     "roofing sheets left from a project and a team free from mid-November. We work in the Northwest only."),
    ("in", "audio", "Ngwa (BuildRight Bamenda)", "2026-10-15T14:03:00+00:00", None,
     "We can give the sheets at cost price, and our team can do about two classrooms per week if the community provides labourers."),
]

PARTNER_DRAFT = {
    "case_type": "offer",
    "title": "BuildRight Bamenda — 400 roofing sheets + roofing team from mid-November",
    "organization": {"name": "BuildRight Bamenda (fictional)", "type": "contractor", "region": "Northwest", "town": "Bamenda", "citations": [7]},
    "situation": {"text": "Roofing contractor with surplus aluminium sheets and a team available from mid-November, Northwest region only.", "citations": [7, 8]},
    "needs": [],
    "offers": [
        {"category": "roofing", "description": "Aluminium roofing sheets at cost price", "quantity": "400 sheets",
         "availability": "Now", "conditions": "Northwest region only", "citations": [7, 8]},
        {"category": "roofing", "description": "Roofing team, about 2 classrooms per week", "quantity": None,
         "availability": "From mid-November", "conditions": "Community provides labourers", "citations": [7, 8]},
    ],
    "assets": [], "readiness": {}, "numbers": {},
    "people": [{"name": "Ngwa", "role": None, "organization": "BuildRight Bamenda", "citations": [7]}],
    "plan": [], "open_questions": ["Cost price per sheet", "Is the company registered and insured?"],
    "safety_flags": [], "language": "English", "confidence": "high",
    "confirmation_message": {"language": "English", "text": "PS43: thank you Ngwa. We understood: 400 aluminium roofing sheets at cost, and a team from mid-November doing about 2 classrooms a week with community labour, Northwest only. Is that right?"},
}


def load(address: str, name: str, channel: str, messages: list, draft: dict) -> int:
    from app.summary import enrich, normalize

    contact = db.get_or_create_contact(address, channel, name)
    db.set_consent(contact["id"], "notified")
    thread = db.open_thread_for(contact["id"], messages[0][3])
    ids = []
    for direction, kind, author, ts, body, *extracted in messages:
        item = db.add_item(contact_id=contact["id"], channel="whatsapp", kind=kind, body=body, direction=direction,
                           author=author, sent_at=ts, thread_id=thread["id"], status="processed",
                           external_id=f"demo:{address}:{ts}")
        if item is None:
            return thread["id"]  # already loaded
        meta = ({"language": "fr" if "Fon" in author else "en", "model": "demo"} if kind == "audio" else {"model": "demo"} if kind != "text" else {})
        db.update_item(item["id"], extracted_text=extracted[0] if extracted else body, extraction_meta=meta)
        ids.append(item["id"])
    # remap the draft's citations (written as 1..n) to the real item ids
    remap = {i + 1: real for i, real in enumerate(ids)}
    remap.update({len(SCHOOL) + i + 1: real for i, real in enumerate(ids)})  # partner draft numbering

    def fix(o):
        if isinstance(o, dict):
            return {k: ([remap.get(c, c) for c in v] if k == "citations" else fix(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [fix(v) for v in o]
        return o

    data = enrich(normalize(fix(draft)), db.thread_items(thread["id"]))
    db.add_summary(thread["id"], data, "demo (hand-written)", ids)
    db.set_thread(thread["id"], title=data["title"])
    return thread["id"]


def main() -> None:
    db.init_db()
    a = load("whatsapp:+237600000001", "Mr. Fon (Principal)", "whatsapp", SCHOOL, SCHOOL_DRAFT)
    b = load("whatsapp:+237600000002", "Ngwa (BuildRight Bamenda)", "whatsapp", PARTNER, PARTNER_DRAFT)
    print(f"Demo threads ready: /threads/{a} (school need) and /threads/{b} (partner offer)")


if __name__ == "__main__":
    main()
