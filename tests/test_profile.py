"""School profile (8 areas), WhatsApp location, trust level and follow-up questions."""
from app import db, evidence, gaps, summary
from app.channels import twilio as tw


def _thread_for(address):
    address = db.canonical_address(address)
    return max((t for t in db.list_threads() if t["address"] == address), key=lambda t: t["id"])


def _draft(**over):
    """A French school draft that answers some, but not all, profile questions."""
    d = {
        "relevance": "relevant", "case_type": "need", "title": "EP Mokolo - no water",
        "organization": {"name": "EP Mokolo", "type": "school", "region": "Far North", "town": "Mokolo", "citations": [1]},
        "operating_status": {"status": "open", "safe_to_reach": "yes", "note": None, "citations": [1]},
        "situation": {"text": "Primary school with no water point.", "citations": [1]},
        "profile": {"water_toilets": {"status": "critical", "note": "No water on site", "citations": [1]}},
        "needs": [{"category": "water", "description": "Borehole", "quantity": "1", "urgency": "high", "citations": [1]}],
        "numbers": {"students": 640, "girls": 290, "citations": [1]},
        "language": "French", "confidence": "medium",
        "confirmation_message": {"language": "French", "text": "PS43: ..."},
    }
    d.update(over)
    return d


# ------------------------------------------------------------------ schema and defaults

def test_schema_has_profile_and_new_fields():
    props = summary.CASE_TOOL["input_schema"]["properties"]
    for key in ("profile", "location", "operating_status"):
        assert key in props and key in summary.CASE_TOOL["input_schema"]["required"]
    assert set(props["profile"]["required"]) == set(summary.PROFILE_KEYS) and len(summary.PROFILE_KEYS) == 8
    assert {"girls", "parent_paid_teachers", "without_birth_certificate"} <= set(props["numbers"]["required"])
    assert {"birth_registration", "school_meals_health"} <= set(summary.NEED_CATEGORIES)
    assert "parent_association" in summary.READINESS_KEYS


def test_normalize_fills_profile_defaults_and_fixes_bad_values():
    d = summary.normalize({"profile": {"teachers": {"status": "terrible"}}, "numbers": {"students": 10}})
    assert all(d["profile"][k]["status"] == "unknown" for k in summary.PROFILE_KEYS)
    assert d["operating_status"] == {"status": "unknown", "safe_to_reach": "unknown", "note": None, "citations": []}
    assert d["location"]["latitude"] is None
    assert d["numbers"]["students"] == 10 and d["numbers"]["girls"] is None
    assert d["readiness"]["parent_association"]["status"] == "unknown"


# ------------------------------------------------------------------ location

def test_location_text_and_parsing():
    line = tw.location_text({"Latitude": "10.7389", "Longitude": "13.8031", "Label": "EP Mokolo"})
    assert line.startswith("Shared location: 10.738900, 13.803100 (EP Mokolo)")
    assert tw.location_text({"Latitude": "abc", "Longitude": "1"}) is None
    assert evidence.find_coordinates(line) == (10.7389, 13.8031)
    assert evidence.find_coordinates("https://maps.google.com/?q=4.0511,9.7679") == (4.0511, 9.7679)
    assert evidence.find_coordinates("gps: 5.4778 10.4176 1400 5") == (5.4778, 10.4176)  # KoBo geopoint
    assert evidence.find_coordinates("we have 40 benches, 2.5 classrooms") is None


def test_whatsapp_location_pin_becomes_cited_map_location(client, fakes):
    client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000101", "Body": "Notre école n'a pas d'eau", "MessageSid": "SMa", "NumMedia": "0"})
    client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000101", "Body": "", "MessageSid": "SMb", "NumMedia": "0",
        "Latitude": "10.7389", "Longitude": "13.8031", "Label": "EP Mokolo"})
    t = _thread_for("whatsapp:+237670000101")
    items = db.thread_items(t["id"])
    pin = next(i for i in items if (i["body"] or "").startswith("Shared location"))
    assert pin["status"] == "processed"

    fakes.model_output = _draft()  # the model did not copy the coordinates
    summary.summarize_thread(t["id"], manual=True)
    loc = db.latest_summary(t["id"])["data"]["location"]
    assert (loc["latitude"], loc["longitude"]) == (10.7389, 13.8031) and loc["citations"] == [pin["id"]]
    page = client.get(f"/threads/{t['id']}").text
    assert "maps.google.com/?q=10.7389,13.8031" in page


# ------------------------------------------------------------------ trust level

def test_trust_level_rises_with_evidence():
    text = {"id": 1, "status": "processed", "direction": "in", "channel": "whatsapp", "kind": "text"}
    photo = {"id": 2, "status": "processed", "direction": "in", "channel": "whatsapp", "kind": "image"}
    staff_photo = {**photo, "id": 3, "direction": "out"}
    form = {"id": 4, "status": "processed", "direction": "in", "channel": "kobo", "kind": "form"}
    assert evidence.trust_level([text, staff_photo])["level"] == "self_reported"
    assert evidence.trust_level([text, photo]) == {"level": "photo_backed", "label": "Photo-backed",
                                                   "photos": 1, "visits": 0, "citations": [2]}
    assert evidence.trust_level([text, photo, form])["level"] == "visit_confirmed"


def test_photo_makes_case_photo_backed_and_drops_photo_question(client, fakes, monkeypatch):
    monkeypatch.setattr(tw, "download", lambda url: (b"\xff\xd8 fake", "image/jpeg"))
    client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000102", "Body": "Les toilettes", "MessageSid": "SMc", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/img", "MediaContentType0": "image/jpeg"})
    t = _thread_for("whatsapp:+237670000102")
    fakes.model_output = _draft()
    summary.summarize_thread(t["id"], manual=True)
    data = db.latest_summary(t["id"])["data"]
    assert data["_trust"]["level"] == "photo_backed"
    assert 14 not in data["_follow_up"]["missing"]


# ------------------------------------------------------------------ follow-up questions

def test_follow_up_asks_only_whats_missing_in_french():
    d = summary.normalize(_draft())
    f = gaps.follow_up(d, "self_reported")
    # answered: identity (1), open and safe (3), pupils and girls (4), water/toilets (7, 8), biggest need (13)
    assert not {1, 3, 4, 7, 8, 13} & set(f["missing"])
    assert f["asking"] == [5, 6, 14] and f["language"] == "fr"
    assert f["message"].startswith("PS43 : Merci") and "ces 3 questions" in f["message"]
    assert "salles de classe" in f["message"] and "eau potable" not in f["message"]
    assert f["complete"] == round(100 * 6 / 15)
    assert len(f["message"]) < 600


def test_follow_up_english_single_question_and_complete_profile():
    d = summary.normalize(_draft(language="English", confirmation_message={"language": "English", "text": ""}))
    d["numbers"].update(classrooms=6, teachers=8, parent_paid_teachers=3, without_birth_certificate=40)
    d["profile"]["learning_space"]["status"] = "partial"
    d["location"].update(latitude=10.7, longitude=13.8)
    d["vulnerability"]["orphans"] = 0
    for k in ("electricity", "internet", "road_access"):
        d["readiness"][k]["status"] = "no"
    f = gaps.follow_up(d, "self_reported")
    assert f["asking"] == [14] and "this question" in f["message"] and "No children's faces" in f["message"]
    done = gaps.follow_up(d, "visit_confirmed")
    assert done["missing"] == [] and done["message"] == "" and done["complete"] == 100


def test_no_profile_questions_for_partners_or_unrelated_chats():
    offer = gaps.follow_up(summary.normalize(_draft(case_type="offer")))
    assert offer["bank"] == "donor" and "salles de classe" not in offer["message"]  # donors get donor questions
    assert gaps.follow_up(summary.normalize(_draft(organization={"type": "contractor"})))["applies"] is False
    assert gaps.follow_up(summary.normalize(_draft(relevance="not_relevant")))["missing"] == []


def test_follow_up_panel_send_and_api(client, fakes, monkeypatch):
    sent = []
    monkeypatch.setattr(tw, "send_message", lambda to, text: sent.append((to, text)) or "SMout1")
    client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000103", "Body": "Pas d'eau à l'école", "MessageSid": "SMd", "NumMedia": "0"})
    t = _thread_for("whatsapp:+237670000103")
    fakes.model_output = _draft()
    summary.summarize_thread(t["id"], manual=True)
    s = db.latest_summary(t["id"])
    page = client.get(f"/threads/{t['id']}").text
    assert "Ask for missing information" in page and "School profile" in page and "Self-reported" in page

    r = client.post(f"/summaries/{s['id']}/send-follow-up", data={"text": s["data"]["_follow_up"]["message"]},
                    follow_redirects=False)
    assert r.status_code == 303 and sent and sent[0][1].startswith("PS43 : Merci")
    out = [i for i in db.thread_items(t["id"]) if i["direction"] == "out"]
    assert out[-1]["body"].startswith("[Follow-up questions sent] PS43 : Merci")

    # a staff edit that fills a gap updates the questions
    d = dict(s["data"])
    d["numbers"] = {**d["numbers"], "classrooms": 6}
    d["profile"] = {**d["profile"], "learning_space": {"status": "critical", "note": None, "citations": []}}
    import json
    client.post(f"/summaries/{s['id']}/edit-json", data={"data": json.dumps(d)})
    edited = db.latest_summary(t["id"])["data"]
    assert 5 not in edited["_follow_up"]["missing"]

    client.post(f"/summaries/{s['id']}/approve", data={"notes": ""})
    case = client.get("/api/cases").json()[0]
    assert case["trust"] == "self_reported" and case["profile_complete"] > 0
    assert "classrooms" not in case["missing_topics"] and "teachers" in case["missing_topics"]
    assert case["case"]["profile"]["water_toilets"]["status"] == "critical"
