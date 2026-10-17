"""Load fictional demo data (one school need, one partner offer) so the screens can be shown
without any API keys. Run:  python -m scripts.seed_demo

All schools, people and numbers here are invented for the demo (the app shows a "Demo data" banner).
"""
from __future__ import annotations

from app import db, matching

SCHOOL = [
    ("in", "text", "Mr. Fon (Principal)", "2026-10-14T08:12:00+00:00",
     "Bonjour PS43. Je suis M. Fon, proviseur du Lycée des Hauts-Plateaux. Notre école a besoin d'aide."),
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
    "title": "Lycée des Hauts-Plateaux — 3 leaking classrooms, 60 desks, girls' latrines",
    "organization": {"name": "Lycée des Hauts-Plateaux", "type": "school", "region": "Northwest",
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
    "people": [{"name": "M. Fon", "role": "Principal (proviseur)", "organization": "Lycée des Hauts-Plateaux", "citations": [1]}],
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
     "Hello PS43, this is Ngwa from BuildRight Bamenda, a roofing contractor. We have 400 aluminium "
     "roofing sheets left from a project and a team free from mid-November. We work in the Northwest only."),
    ("in", "audio", "Ngwa (BuildRight Bamenda)", "2026-10-15T14:03:00+00:00", None,
     "We can give the sheets at cost price, and our team can do about two classrooms per week if the community provides labourers."),
]

PARTNER_DRAFT = {
    "case_type": "offer",
    "title": "BuildRight Bamenda — 400 roofing sheets + roofing team from mid-November",
    "organization": {"name": "BuildRight Bamenda", "type": "contractor", "region": "Northwest", "town": "Bamenda", "citations": [7]},
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


# ---------------------------------------------------------------- part two demo (all fictional)

def _school(name, town, region, pupils, girls, profile, readiness, needs, status="open", safe="yes", lang="French",
            situation="", vulnerable=None):
    lvl = {"ok", "partial", "critical"}
    v = {"conflict_affected": "unknown", "displaced_pupils": None, "pupils_with_disability": None, "orphans": None,
         "note": None, "citations": [1]}
    v.update(vulnerable or {})
    return {
        "vulnerability": v,
        "relevance": "relevant", "case_type": "need",
        "title": f"{name} - " + ", ".join(matching.CATEGORY_LABELS.get(n[0], n[0]) for n in needs),
        "organization": {"name": name, "type": "school", "region": region, "town": town, "citations": [1]},
        "operating_status": {"status": status, "safe_to_reach": safe, "note": None, "citations": [1]},
        "situation": {"text": situation or f"{name} in {town} has {pupils} pupils.", "citations": [1]},
        "profile": {k: {"status": v, "note": None, "citations": [1] if v in lvl else []} for k, v in profile.items()},
        "readiness": {k: {"status": v, "note": None, "citations": [1]} for k, v in readiness.items()},
        "numbers": {"students": pupils, "girls": girls, "citations": [1]},
        "needs": [{"category": c, "description": d, "quantity": q, "urgency": u, "citations": [1]} for c, d, q, u in needs],
        "language": lang, "confidence": "high",
        "confirmation_message": {"language": lang, "text": (
            f"PS43 : merci. Nous avons compris : {name}, {pupils} élèves. Besoins : "
            + "; ".join(f"{d} ({q})" for _, d, q, _ in needs) + ". Est-ce correct ? Répondez OUI ou corrigez-nous."
            if lang == "French" else
            f"PS43: thank you. We understood: {name}, {pupils} pupils. Needs: "
            + "; ".join(f"{d} ({q})" for _, d, q, _ in needs) + ". Is that right? Reply YES or correct us.")},
    }


def _partner(name, kind, gives, regions, offers, summary, org_type="donor", registered="unknown", lang="English",
             past=None, amount=None, timeframe=None, conditions=None):
    return {
        "relevance": "relevant", "case_type": "offer", "title": f"{name} - offer",
        "organization": {"name": name, "type": org_type, "region": None, "town": None, "citations": [1]},
        "situation": {"text": summary, "citations": [1]},
        "donor": {"kind": kind, "gives": gives, "provides_summary": summary, "amount": amount, "regions": regions,
                  "timeframe": timeframe, "conditions": conditions, "registered": registered,
                  "past_work_with_ps43": past, "citations": [1]},
        "offers": [{"category": c, "description": d, "quantity": q, "availability": timeframe, "conditions": cond,
                    "location": loc, "abroad": abroad, "citations": [1]} for c, d, q, cond, loc, abroad in offers],
        "people": [{"name": name, "role": "contact", "organization": name, "citations": [1]}],
        "language": lang, "confidence": "high",
        "confirmation_message": {"language": lang, "text": (
            f"PS43 : merci {name}. Nous avons compris : {summary} Est-ce correct ? Répondez OUI ou corrigez-nous."
            if lang == "French" else f"PS43: thank you {name}. We understood: {summary} Is that right? Reply YES or correct us.")},
    }


def quick_case(address: str, name: str, message: str, draft: dict, when: str, approve: bool = True) -> int:
    from app import workflow
    from app.summary import enrich, normalize

    contact = db.get_or_create_contact(address, "whatsapp", name)
    db.set_consent(contact["id"], "notified")
    thread = db.open_thread_for(contact["id"], when)
    item = db.add_item(contact_id=contact["id"], channel="whatsapp", kind="text", body=message, author=name,
                       sent_at=when, thread_id=thread["id"], status="processed", external_id=f"demo:{address}")
    if item is None:
        return thread["id"]  # already loaded
    db.update_item(item["id"], extracted_text=message, extraction_meta={})

    def fix(o):
        if isinstance(o, dict):
            return {k: ([item["id"] for _ in v][:1] if k == "citations" and v else fix(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [fix(v) for v in o]
        return o

    data = enrich(normalize(fix(draft)), db.thread_items(thread["id"]))
    summary = db.add_summary(thread["id"], data, "demo (hand-written)", [item["id"]])
    db.set_thread(thread["id"], title=data["title"])
    if approve:
        approve_case(thread["id"], summary["id"])
    return thread["id"]


def approve_case(thread_id: int, summary_id: int) -> None:
    from app import workflow
    number = db.get_thread(thread_id)["case_number"]
    db.update_summary(summary_id, status="approved", reviewer="demo", reviewed_at=db.now(), case_number=number)
    db.set_thread(thread_id, status="approved", stale_summary=0)
    workflow.on_case_approved(thread_id, "demo")


def load_part_two(bambui_thread: int, buildright_thread: int) -> None:
    from datetime import datetime, timedelta, timezone

    from app import workflow

    if db.list_donors() and len(db.list_tickets()) > 3:
        return  # already loaded
    # approve the two original demo cases too
    for tid in (bambui_thread, buildright_thread):
        s = db.latest_summary(tid)
        if s and s["status"] == "draft":
            approve_case(tid, s["id"])

    full = dict(electricity="yes", internet="yes", secure_storage="yes", trained_staff="yes", road_access="yes",
                parent_association="yes", space="yes")
    S = [
        ("whatsapp:+237600000011", "M. Abba (CES Soleil de Mokolo)", "Bonjour, le CES Soleil de Mokolo n'a pas d'eau. Les élèves marchent 3 km.",
         _school("CES Soleil de Mokolo", "Mokolo", "Far North", 640, 290,
                 dict(learning_space="partial", water_toilets="critical", teachers="partial", materials_results="partial",
                      barriers="partial", health_wellbeing="partial", technology="unknown", safety_operation="ok"),
                 dict(electricity="no", internet="partial", secure_storage="no", trained_staff="unknown",
                      road_access="partial", parent_association="yes"),
                 [("water", "Borehole with hand pump", "1 borehole", "critical"),
                  ("sanitation_latrines", "Separate latrines for girls", "6 latrines", "high")],
                 vulnerable={"conflict_affected": "yes", "displaced_pupils": 85, "orphans": 20})),
        ("whatsapp:+237600000012", "Mrs Tchoupo (Collège Lumière)", "We want a computer lab for our 820 students. We have power and a room with a lock.",
         _school("Collège Lumière Bafoussam", "Bafoussam", "West", 820, 410,
                 dict(learning_space="partial", water_toilets="ok", teachers="partial", materials_results="partial",
                      barriers="ok", health_wellbeing="ok", technology="critical", safety_operation="ok"),
                 full, [("devices", "Laptops for a computer lab", "40 laptops", "high"),
                        ("teacher_training", "ICT training for teachers", "6 teachers", "medium")], lang="English")),
        ("whatsapp:+237600000013", "M. Mvondo (EP Les Manguiers)", "Il nous manque 30 table-bancs.",
         _school("EP Les Manguiers", "Ebolowa", "South", 180, 95,
                 dict(learning_space="partial", water_toilets="ok", teachers="ok", materials_results="partial",
                      barriers="ok", health_wellbeing="ok", technology="unknown", safety_operation="ok"),
                 dict(full, internet="partial", secure_storage="partial"),
                 [("furniture", "Table-benches", "30 table-benches", "medium")])),
        ("whatsapp:+237600000014", "M. Oumarou (EP Espoir)", "L'école est fermée depuis les attaques.",
         _school("EP Espoir de Kousseri", "Kousseri", "Far North", 400, 150,
                 dict(learning_space="critical", water_toilets="critical", teachers="critical", materials_results="critical",
                      barriers="critical", health_wellbeing="partial", technology="unknown", safety_operation="critical"),
                 dict(electricity="no", internet="no", secure_storage="no", trained_staff="no", road_access="no",
                      parent_association="partial"),
                 [("classrooms_building", "Repair 4 classrooms", "4 classrooms", "high")], status="closed", safe="no",
                 vulnerable={"conflict_affected": "yes"})),
        ("whatsapp:+237600000015", "Mr Ebai (Horizon Tech College)", "Our ICT room has solar but only 5 old computers for 450 students.",
         _school("Horizon Tech College Buea", "Buea", "Southwest", 450, 200,
                 dict(learning_space="ok", water_toilets="partial", teachers="partial", materials_results="partial",
                      barriers="partial", health_wellbeing="ok", technology="critical", safety_operation="ok"),
                 dict(electricity="partial", internet="partial", secure_storage="yes", trained_staff="yes",
                      road_access="yes", parent_association="yes"),
                 [("devices", "Laptops for the ICT room", "20 laptops", "high")], lang="English")),
        ("whatsapp:+237600000016", "Mme Ngono (EP Arc-en-Ciel Bertoua)", "Nous avons besoin de livres. 45 élèves n'ont pas d'acte de naissance.",
         _school("EP Arc-en-Ciel Bertoua", "Bertoua", "East", 300, 140,
                 dict(learning_space="partial", water_toilets="partial", teachers="partial", materials_results="critical",
                      barriers="critical", health_wellbeing="partial", technology="unknown", safety_operation="ok"),
                 dict(electricity="partial", internet="partial", secure_storage="no", trained_staff="no",
                      road_access="partial", parent_association="no"),
                 [("learning_materials", "Reading and maths textbooks", "300 textbooks", "high"),
                  ("birth_registration", "Birth certificates for pupils", "45 pupils", "high")],
                 vulnerable={"displaced_pupils": 40, "pupils_with_disability": 6, "note": "Refugee families from the CAR border area"})),
    ]
    ids = {}
    for n, (addr, name, msg, draft) in enumerate(S):
        ids[addr] = quick_case(addr, name, msg, draft, f"2026-10-1{n % 7}T09:00:00+00:00")

    P = [
        ("whatsapp:+16475550101", "Maple Laptop Circle Toronto",
         "We collected 25 refurbished laptops in Toronto for a school in Cameroon.",
         _partner("Maple Laptop Circle Toronto", "diaspora_group", ["goods"], ["National"],
                  [("devices", "Refurbished laptops", "25 laptops", "Photos of the lab when installed", "Toronto, Canada", True)],
                  "25 refurbished laptops collected in Toronto, for any school with power and a secure room.")),
        ("whatsapp:+237600000021", "Atelier Numérique Yaoundé", "Nous avons 20 ordinateurs portables reconditionnés à Yaoundé.",
         _partner("Atelier Numérique Yaoundé", "ngo", ["goods", "expertise"], ["National"],
                  [("devices", "Reconditioned laptops", "20 laptops", None, "Yaoundé", False),
                   ("teacher_training", "2-day ICT training for teachers", "1 session", None, "Yaoundé", False)],
                  "20 reconditioned laptops and ICT training for teachers, anywhere in Cameroon.", org_type="ngo",
                  registered="yes", lang="French")),
        ("whatsapp:+237600000022", "Wouri Freight & Transit", "We clear and deliver shipments from Douala port.",
         _partner("Wouri Freight & Transit", "company", ["transport"], ["National"],
                  [("transport", "Customs clearance and delivery from Douala port", None, None, "Douala", False)],
                  "Clears shipments at Douala port and delivers to any region.", org_type="supplier", registered="yes")),
        ("whatsapp:+237600000023", "Menuiserie Bois d'Or", "Nous fabriquons des table-bancs. Capacité 40 par mois.",
         _partner("Menuiserie Bois d'Or", "company", ["goods", "services"], ["South", "Centre"],
                  [("furniture", "Table-benches made to order", "40 table-benches", "South and Centre only", "Ebolowa", False)],
                  "Makes table-benches, about 40 a month, for the South and Centre regions.", org_type="supplier",
                  lang="French")),
        ("whatsapp:+237600000024", "Club Solidarité Bafoussam", "Our club can give 3,000,000 FCFA for a school project in the West.",
         _partner("Club Solidarité Bafoussam", "ngo", ["money"], ["West"],
                  [("funding", "Grant for a school project", "3,000,000 FCFA", "Report with photos", "Bafoussam", False)],
                  "A 3,000,000 FCFA grant for one school project in the West region.", registered="yes",
                  amount="3,000,000 FCFA", conditions="Report with photos")),
        ("whatsapp:+33600000026", "Amis de Buea à Paris",
         "Notre association à Paris a 15 ordinateurs portables pour une école anglophone.",
         _partner("Amis de Buea à Paris", "diaspora_group", ["goods"], ["Southwest", "Northwest"],
                  [("devices", "Donated laptops", "15 laptops", "Southwest or Northwest only", "Paris, France", True)],
                  "15 laptops collected in Paris for a school in the Southwest or Northwest.", lang="French")),
        ("whatsapp:+237600000025", "Aqua Nord Forages", "Nous faisons des forages dans le Nord et l'Extrême-Nord.",
         _partner("Aqua Nord Forages", "company", ["services"], ["North", "Far North"],
                  [("water", "Borehole drilling with hand pump", "2 boreholes", None, "Garoua", False)],
                  "Drills boreholes with hand pumps in the North and Far North.", org_type="contractor",
                  registered="yes", lang="French")),
    ]
    partner_threads = {}
    for n, (addr, name, msg, draft) in enumerate(P):
        partner_threads[name] = quick_case(addr, name, msg, draft, f"2026-10-0{n + 1}T10:00:00+00:00")
    donors = {d["name"]: d for d in db.list_donors()}
    for name in ("Wouri Freight & Transit", "Aqua Nord Forages", "Atelier Numérique Yaoundé"):
        db.set_donor(donors[name]["id"], verified=1, verified_by="demo")

    # A new request waiting for review: a head teacher's voice note (transcribed), not approved yet.
    quick_case("whatsapp:+237600000018", "Mme Bih (EP Source de Vie)",
               "Note vocale (transcription) : Bonjour PS43, je suis Mme Bih, directrice de l'EP Source de Vie à Kumbo. "
               "Nous avons 380 élèves dont 190 filles et 6 enseignants, dont 2 payés par les parents. Il n'y a pas d'eau "
               "à l'école, les enfants vont à la rivière. Le toit d'une salle est tombé pendant la pluie. "
               "Nous avons l'électricité ENEO mais avec des coupures. La route est mauvaise en saison des pluies.",
               _school("EP Source de Vie", "Kumbo", "Northwest", 380, 190,
                       dict(learning_space="critical", water_toilets="critical", teachers="partial",
                            materials_results="unknown", barriers="unknown", health_wellbeing="unknown",
                            technology="unknown", safety_operation="partial"),
                       dict(electricity="partial", internet="unknown", secure_storage="unknown",
                            trained_staff="unknown", road_access="partial", parent_association="unknown"),
                       [("water", "Water point at the school (children fetch water from the river)", "1 water point", "critical"),
                        ("roofing", "Rebuild the roof of one classroom that collapsed in the rain", "1 classroom", "critical")]),
               "2026-10-17T08:30:00+00:00", approve=False)

    # A past project: Aqua Nord drilled a borehole and built latrines at a school in Garoua (both confirmed).
    past = quick_case("whatsapp:+237600000017", "M. Bello (EP Baobab de Garoua)", "Merci, le forage fonctionne.",
                      _school("EP Baobab de Garoua", "Garoua", "North", 520, 250,
                              dict(learning_space="partial", water_toilets="ok", teachers="partial",
                                   materials_results="partial", barriers="ok", health_wellbeing="ok",
                                   technology="unknown", safety_operation="ok"),
                              dict(full, internet="partial"),
                              [("water", "Borehole with hand pump", "1 borehole", "critical"),
                               ("sanitation_latrines", "Latrines for girls and boys", "4 latrines", "high")]),
                      "2026-07-01T09:00:00+00:00")
    aqua = donors["Aqua Nord Forages"]["id"]
    long_ago = datetime.now(timezone.utc) - timedelta(days=35)
    for k in db.list_tickets(thread_id=past):
        workflow.assign(k["id"], aqua, None, None, "demo")
        db.add_followups(k["id"], long_ago)  # delivered 35 days ago: the 30-day follow-up is due
        for st in db.list_steps(k["id"]):
            workflow.step_done(k["id"], st["id"], "demo")
    db.add_quote(db.list_tickets(thread_id=past)[0]["id"], 6_500_000, donor_id=aqua, quantity=1, unit="borehole",
                 note="Drilling 60 m, hand pump, water test", added_by="demo")

    # The computer lab: 40 laptops for Bafoussam = 25 from Toronto (shipped) + 20 from Yaoundé.
    lab = next(k for k in db.list_tickets(thread_id=ids["whatsapp:+237600000012"]) if k["category"] == "devices")
    for p in workflow.ticket_matches(lab)["plan"]["picks"]:
        workflow.assign(lab["id"], p["donor_id"], p["item_id"], p["covers"], "demo")
    steps = db.list_steps(lab["id"])
    workflow.step_done(lab["id"], steps[0]["id"], "demo")
    db.add_event(lab["id"], "message", "Message to partner to Maple Laptop Circle Toronto (WhatsApp): PS43: Hello, a school "
                 "needs your laptops: Collège Lumière Bafoussam (West) - 40 laptops. Could you confirm when you can ship?", "demo")
    db.add_event(lab["id"], "note", "Call with the principal: room has bars on windows and a steel door. Photos received.", "demo")

    # Roofing at Bambui: BuildRight is the partner; two quotes, one inflated.
    roof = next((k for k in db.list_tickets(thread_id=bambui_thread) if k["category"] == "roofing"), None)
    if roof:
        build = next(d for d in db.list_donors() if d["name"].startswith("BuildRight"))
        sheets = next(i for i in db.list_donor_items(build["id"]) if "sheets" in i["description"].lower())
        workflow.assign(roof["id"], build["id"], sheets["id"], 120, "demo")
        workflow.add_quote(roof["id"], 1_800_000, "demo", donor_id=build["id"], quantity=120, unit="sheets",
                           note="Aluminium sheets, nails and labour")
        workflow.add_quote(roof["id"], 2_640_000, "demo", supplier_name="Quincaillerie du Marché",
                           quantity=120, unit="sheets", note="Same sheets, transport included")
        workflow.add_quote(roof["id"], 1_920_000, "demo", supplier_name="Bamenda Hardware",
                           quantity=120, unit="sheets", note="Sheets and nails, no labour")


def main() -> None:
    db.init_db()
    db.set_setting("demo_data", True)  # shows the "Demo data: all made up" banner on staff screens
    a = load("whatsapp:+237600000001", "Mr. Fon (Principal)", "whatsapp", SCHOOL, SCHOOL_DRAFT)
    b = load("whatsapp:+237600000002", "Ngwa (BuildRight Bamenda)", "whatsapp", PARTNER, PARTNER_DRAFT)
    print(f"Demo threads ready: /threads/{a} (school need) and /threads/{b} (partner offer)")
    if "--intake-only" not in __import__("sys").argv:
        load_part_two(a, b)
        print("Part two demo ready: /dashboard, /tickets, /partners, /matches")


if __name__ == "__main__":
    main()
