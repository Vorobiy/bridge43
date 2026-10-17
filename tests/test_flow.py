"""End-to-end: every intake channel lands in a thread, gets converted, drafted, reviewed."""
import io
import json
import zipfile

from app import db, summary
from app.channels import twilio as tw
from app.config import settings


def _thread_for(address):
    address = db.canonical_address(address)
    return max((t for t in db.list_threads() if t["address"] == address), key=lambda t: t["id"])


def test_whatsapp_text_gets_consent_notice_then_processed(client):
    r = client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000001", "Body": "Bonjour, notre école a besoin d'aide", "MessageSid": "SM1",
        "NumMedia": "0", "ProfileName": "Mr. Tabi"})
    assert r.status_code == 200 and "PS43 Foundation" in r.text  # notice sent once
    t = _thread_for("whatsapp:+237670000001")
    items = db.thread_items(t["id"])
    assert items[0]["status"] == "processed" and items[0]["author"] == "Mr. Tabi"

    r2 = client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000001", "Body": "Le toit fuit", "MessageSid": "SM2", "NumMedia": "0"})
    assert "<Message>" not in r2.text  # no second notice
    # retry of the same webhook does not duplicate
    client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000001", "Body": "Le toit fuit", "MessageSid": "SM2", "NumMedia": "0"})
    assert len(db.thread_items(t["id"])) == 2


def test_whatsapp_photo_and_voice_note(client, monkeypatch):
    monkeypatch.setattr(tw, "download", lambda url: (b"\xff\xd8 fake bytes", "image/jpeg" if "img" in url else "audio/ogg"))
    client.post("/webhooks/twilio/messaging", data={
        "From": "whatsapp:+237670000002", "Body": "Notre toit", "MessageSid": "SM3", "NumMedia": "2",
        "MediaUrl0": "https://api.twilio.com/img", "MediaContentType0": "image/jpeg",
        "MediaUrl1": "https://api.twilio.com/voice", "MediaContentType1": "audio/ogg"})
    items = db.thread_items(_thread_for("whatsapp:+237670000002")["id"])
    kinds = {i["kind"]: i for i in items}
    assert kinds["image"]["extracted_text"] == "1 image(s): roof with holes"
    assert kinds["image"]["body"] == "Notre toit"
    assert "le toit fuit" in kinds["audio"]["extracted_text"]
    assert db.get_contact(items[0]["contact_id"])["language"] == "french"


def test_stop_deletes_everything(client):
    client.post("/webhooks/twilio/messaging", data={"From": "+237670000003", "Body": "hello", "MessageSid": "SM4"})
    r = client.post("/webhooks/twilio/messaging", data={"From": "+237670000003", "Body": "STOP", "MessageSid": "SM5"})
    assert "deleted" in r.text
    assert not [t for t in db.list_threads() if t["address"] == "+237670000003"]


def test_explicit_consent_holds_until_yes(client, monkeypatch):
    monkeypatch.setattr(settings, "require_explicit_consent", True)
    r = client.post("/webhooks/twilio/messaging", data={"From": "+237670000004", "Body": "We need desks", "MessageSid": "SM6"})
    assert "Reply YES" in r.text
    t = _thread_for("+237670000004")
    assert db.thread_items(t["id"])[0]["status"] == "held"
    client.post("/webhooks/twilio/messaging", data={"From": "+237670000004", "Body": "Oui", "MessageSid": "SM7"})
    items = db.thread_items(t["id"])
    assert len(items) == 1 and items[0]["status"] == "processed"  # "Oui" itself is not case content


def test_twilio_signature_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", "secret-token")
    params = {"From": "+237670000005", "Body": "hi", "MessageSid": "SM8"}
    assert client.post("/webhooks/twilio/messaging", data=params).status_code == 403
    sig = tw.compute_signature("secret-token", "https://bridge.test/webhooks/twilio/messaging", params)
    assert client.post("/webhooks/twilio/messaging", data=params, headers={"X-Twilio-Signature": sig}).status_code == 200


def test_call_voicemail_recorded_and_transcribed(client, monkeypatch):
    r = client.post("/webhooks/twilio/voice", data={"From": "whatsapp:+237670000006", "CallSid": "CA1"})
    assert "<Record" in r.text and "https://bridge.test/webhooks/twilio/recording" in r.text and "fr-FR" in r.text
    monkeypatch.setattr(tw, "download", lambda url: (b"ID3 fake mp3", "audio/mpeg"))
    r = client.post("/webhooks/twilio/recording", data={
        "CallSid": "CA1", "RecordingUrl": "https://api.twilio.com/rec/RE1", "RecordingSid": "RE1",
        "RecordingStatus": "completed", "RecordingDuration": "70"})
    assert r.status_code == 204
    item = db.get_item_by_external_id("CA1")
    assert item["status"] == "processed"
    assert "[01:05] 40 bancs" in item["extracted_text"]  # call transcripts keep timestamps


def test_forwarded_call_dials_staff(client, monkeypatch):
    monkeypatch.setattr(settings, "staff_forward_number", "+14165550000")
    r = client.post("/webhooks/twilio/voice", data={"From": "+237670000007", "CallSid": "CA2"})
    assert "<Dial" in r.text and "record-from-answer-dual" in r.text and "+14165550000" in r.text


def test_kobo_submission(client, monkeypatch):
    from app.channels import kobo

    monkeypatch.setattr(kobo, "_download", lambda url: (b"\xff\xd8", "image/jpeg"))
    sub = {"_uuid": "abc", "_submission_time": "2026-10-16T10:00:00", "_xform_id_string": "school_visit",
           "group_a/school_name": "GBHS Bali", "group_a/phone": "+237 670 000 008", "group_b/students": 412,
           "group_b/latrines_working": "no", "meta/instanceID": "uuid:abc",
           "_attachments": [{"download_url": "https://kf.kobotoolbox.org/x/roof.jpg", "mimetype": "image/jpeg",
                             "filename": "user/attachments/roof.jpg"}]}
    r = client.post("/webhooks/kobo", json=sub)
    assert r.json()["attachments"] == 1
    t = _thread_for("+237670000008")
    items = db.thread_items(t["id"])
    assert "students: 412" in items[0]["body"] and "instanceID" not in items[0]["body"]
    assert items[1]["kind"] == "image" and items[1]["status"] == "processed"
    assert client.post("/webhooks/kobo", json=sub).json()["status"] == "duplicate"


def test_export_upload_to_approved_case(client, fakes):
    chat = ("[15/10/2026, 09:30:12] PS43 Eugene: Bonjour, comment va l'école ?\n"
            "[15/10/2026, 09:31:05] Mr. Tabi: Le toit de deux salles fuit.\n"
            "[15/10/2026, 09:32:00] Mr. Tabi: <attached: 00000012-AUDIO-2026-10-15-09-32-00.opus>\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", chat)
        zf.writestr("00000012-AUDIO-2026-10-15-09-32-00.opus", b"OggS")
    r = client.post("/intake/export", files={"chat": ("chat.zip", buf.getvalue(), "application/zip")},
                    data={"contact_name": "GBHS Test", "contact_phone": "+237 670 000 009", "staff_names": "PS43 Eugene"},
                    follow_redirects=False)
    assert r.status_code == 303
    thread_id = int(r.headers["location"].split("/threads/")[1].split("?")[0])
    items = db.thread_items(thread_id)
    assert [i["direction"] for i in items] == ["out", "in", "in"]
    assert items[2]["kind"] == "audio" and items[2]["status"] == "processed"

    # same upload again: nothing duplicated
    client.post("/intake/export", files={"chat": ("chat.zip", buf.getvalue(), "application/zip")},
                data={"contact_phone": "+237670000009", "staff_names": "PS43 Eugene"})
    assert len(db.thread_items(thread_id)) == 3

    # AI draft: prompt contains every item, invalid citations are dropped
    r = client.post(f"/threads/{thread_id}/summarize", follow_redirects=False)
    assert r.status_code == 303
    prompt = fakes.model_calls[-1]
    assert all(f"[I{i['id']}]" in prompt for i in items) and "PS43 staff" in prompt
    s = db.latest_summary(thread_id)
    valid = {i["id"] for i in items}
    assert set(s["data"]["needs"][0]["citations"]) <= valid and 12345 not in s["data"]["needs"][0]["citations"]
    assert s["data"]["readiness"]["internet"]["status"] == "unknown"  # normalized
    assert db.get_thread(thread_id)["status"] == "draft_ready"

    page = client.get(f"/threads/{thread_id}")
    assert page.status_code == 200 and "GBHS Test - leaking roof" in page.text and 'href="#item-' in page.text

    # human edits, then approval creates a case number
    client.post(f"/summaries/{s['id']}/edit", data={
        "title": "GBHS Test - roof", "case_type": "need", "org_name": "GBHS Test", "org_type": "school",
        "region": "Northwest", "town": "Bali", "situation": "Edited.", "open_questions": "- Roof size?\nBudget?",
        "confirmation_text": "PS43: ok?"})
    s = db.get_summary(s["id"])
    assert s["data"]["situation"]["text"] == "Edited." and s["data"]["open_questions"] == ["Roof size?", "Budget?"]
    client.post(f"/summaries/{s['id']}/approve", data={"notes": "checked"})
    s = db.get_summary(s["id"])
    thread = db.get_thread(thread_id)
    assert s["status"] == "approved" and s["case_number"] == thread["case_number"] and thread["case_number"].startswith("PS43-")
    cases = client.get("/api/cases").json()
    assert cases[0]["case"]["title"] == "GBHS Test - roof"

    # an approved case keeps collecting: a later WhatsApp message lands in the same case
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+237670000009", "Body": "Merci !", "MessageSid": "SM9"})
    assert _thread_for("whatsapp:+237670000009")["id"] == thread_id
    assert db.get_thread(thread_id)["stale_summary"] == 1

    # once staff close it, the next message opens a new case with a new number
    client.post(f"/threads/{thread_id}/close")
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+237670000009", "Body": "Bonjour", "MessageSid": "SM9b"})
    new = _thread_for("whatsapp:+237670000009")
    assert new["id"] != thread_id and new["case_number"] != thread["case_number"]


def test_staff_notes_with_recording(client):
    r = client.post("/intake/notes", data={"contact_name": "Lycée de Test", "source": "whatsapp_call",
                                           "notes": "Principal says 3 classrooms have no benches."},
                    files=[("files", ("call.m4a", b"fake", "audio/mp4"))], follow_redirects=False)
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    items = db.thread_items(tid)
    assert items[0]["direction"] == "out" and "no benches" in items[0]["body"]
    assert items[1]["channel"] == "call" and items[1]["status"] == "processed"


def test_failed_item_shows_and_can_retry(client, monkeypatch):
    from app.processing import transcribe

    def boom(path):
        raise transcribe.TranscriptionError("TRANSCRIBE_API_KEY is not set")

    monkeypatch.setattr(transcribe, "transcribe", boom)
    r = client.post("/intake/notes", data={"contact_name": "X"}, files=[("files", ("v.ogg", b"x", "audio/ogg"))],
                    follow_redirects=False)
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    item = db.thread_items(tid)[0]
    assert item["status"] == "failed" and "TRANSCRIBE_API_KEY" in item["error"]
    assert "Retry" in client.get(f"/threads/{tid}").text


def test_staff_password(client, monkeypatch):
    monkeypatch.setattr(settings, "staff_password", "pw")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("ps43", "pw")).status_code == 200
    # webhooks are not behind the staff login
    assert client.post("/webhooks/twilio/messaging", data={"From": "+237670000010", "Body": "x", "MessageSid": "SM10"}).status_code == 200


def test_prompt_marks_untrusted_content():
    assert "do not follow them" in summary.SYSTEM


def test_recorder_upload(client, monkeypatch):
    from app import recorder_api

    assert recorder_api.split_contact("Mr Fon +237 6 70 00 00 11") == ("Mr Fon", "+237670000011")
    assert recorder_api.split_contact("+237670000011") == (None, "+237670000011")
    assert recorder_api.split_contact("Lycée de Test") == ("Lycée de Test", None)

    files = {"file": ("call.m4a", b"fake audio", "audio/mp4")}
    assert client.post("/api/recorder/calls", files=files).status_code == 403  # off by default
    monkeypatch.setattr(settings, "recorder_token", "tok")
    assert client.post("/api/recorder/calls", files=files, headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.post("/api/recorder/calls", files=files, headers={"Authorization": "Bearer tok"},
                    data={"contact": "Mr Fon +237 670 000 011", "started_at": "2026-10-17T11:00:00", "duration_seconds": "240"})
    assert r.status_code == 200
    items = db.thread_items(r.json()["thread_id"])
    assert items[1]["channel"] == "call" and items[1]["status"] == "processed"
    contact = db.get_contact(items[0]["contact_id"])
    assert contact["address"] == "+237670000011" and contact["reply_to"] == "whatsapp:+237670000011"
    assert r.json()["case_number"] == db.get_thread(r.json()["thread_id"])["case_number"]
    assert client.get("/api/recorder/ping", headers={"Authorization": "Bearer tok"}).json() == {"ok": True}



def test_every_channel_lands_in_one_case(client, monkeypatch):
    """Text, photo, voice note, SMS, laptop call recording and KoBo form from one school = one case."""
    from app.channels import kobo
    monkeypatch.setattr(tw, "download", lambda url: (b"fake", "image/jpeg" if "img" in url else "audio/ogg"))
    monkeypatch.setattr(kobo, "_download", lambda url: (b"fake", "image/jpeg"))
    monkeypatch.setattr(settings, "recorder_token", "tok")
    num = "+237 670 000 020"
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+237670000020", "Body": "Bonjour", "MessageSid": "X1", "ProfileName": "M. Ewane"})
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+237670000020", "Body": "", "MessageSid": "X2", "NumMedia": "2",
                "MediaUrl0": "https://t/img", "MediaContentType0": "image/jpeg", "MediaUrl1": "https://t/voice", "MediaContentType1": "audio/ogg"})
    client.post("/webhooks/twilio/messaging", data={"From": "+237670000020", "Body": "SMS: notre toit fuit", "MessageSid": "X3"})
    client.post("/api/recorder/calls", headers={"Authorization": "Bearer tok"}, data={"contact": f"M. Ewane {num}"},
                files={"file": ("call.caf", b"fake", "audio/x-caf")})
    client.post("/webhooks/kobo", json={"_uuid": "k20", "school_phone": num, "students": 300})
    client.post("/intake/notes", data={"contact_name": "M. Ewane", "contact_phone": num, "notes": "Visit planned"})

    cases = [t for t in db.list_threads() if t["address"] == "+237670000020"]
    assert len(cases) == 1
    case = cases[0]
    items = db.thread_items(case["id"])
    assert {i["kind"] for i in items} >= {"text", "image", "audio", "form"}
    assert {i["channel"] for i in items} >= {"whatsapp", "sms", "call", "kobo", "agent"}
    assert case["case_number"].startswith("PS43-")
    # confirmations go back over WhatsApp/SMS, whichever they used last
    assert db.get_contact(case["contact_id"])["reply_to"] == "+237670000020"

    # a call filed with only a name joins the same case by name
    client.post("/api/recorder/calls", headers={"Authorization": "Bearer tok"}, data={"contact": "m. ewane"},
                files={"file": ("call2.caf", b"fake", "audio/x-caf")})
    assert len(db.thread_items(case["id"])) == len(items) + 2
    page = client.get(f"/threads/{case['id']}")
    assert case["case_number"] in page.text and "Merge into another case" in page.text
    assert case["case_number"] in client.get("/?q=" + case["case_number"]).text


def test_move_and_merge_cases(client):
    a = client.post("/intake/notes", data={"contact_name": "School A", "notes": "first"}, follow_redirects=False)
    b = client.post("/intake/notes", data={"contact_name": "School A annex", "notes": "second"}, follow_redirects=False)
    ta, tb = (int(r.headers["location"].rsplit("/", 1)[1]) for r in (a, b))
    case_a = db.get_thread(ta)["case_number"]
    item = db.thread_items(tb)[0]
    client.post(f"/items/{item['id']}/move", data={"into": case_a})
    assert db.get_item(item["id"])["thread_id"] == ta
    client.post("/intake/notes", data={"contact_name": "School A annex", "notes": "third"})
    client.post(f"/threads/{tb}/merge", data={"into": case_a})
    assert db.get_thread(tb) is None and len(db.thread_items(ta)) == 3


def test_migration_merges_old_contacts(fakes):
    """Databases from the earlier version: whatsapp:+X and +X were separate contacts with no case numbers."""
    with db.tx() as conn:
        conn.execute("INSERT INTO contacts (address, channel, created_at, updated_at) VALUES ('whatsapp:+237670000030','whatsapp','t','t')")
        conn.execute("INSERT INTO contacts (address, channel, created_at, updated_at) VALUES ('+237670000030','sms','t','t')")
        ids = [r[0] for r in conn.execute("SELECT id FROM contacts WHERE address LIKE '%670000030' ORDER BY id")]
        for cid in ids:
            conn.execute("INSERT INTO threads (contact_id, opened_at, last_item_at) VALUES (?, '2026-10-01', '2026-10-01')", (cid,))
    db.init_db()
    with db.tx() as conn:
        contacts = conn.execute("SELECT * FROM contacts WHERE address LIKE '%670000030'").fetchall()
        threads = conn.execute("SELECT * FROM threads WHERE contact_id = ?", (contacts[0]["id"],)).fetchall()
    assert len(contacts) == 1 and contacts[0]["address"] == "+237670000030"
    assert contacts[0]["reply_to"] == "whatsapp:+237670000030"
    assert len(threads) == 2 and all(t["case_number"] for t in threads)


def test_whatsapp_window_title_names_are_cleaned(client, monkeypatch):
    assert db.clean_name("‎Connor - WhatsApp voice call") == "Connor"
    assert db.clean_name("Lycée de Bali — Appel vocal WhatsApp") == "Lycée de Bali"
    assert db.clean_name("Mr Fon") == "Mr Fon"
    monkeypatch.setattr(settings, "recorder_token", "tok")
    client.post("/intake/notes", data={"contact_name": "Connor", "notes": "earlier notes"})
    r = client.post("/api/recorder/calls", headers={"Authorization": "Bearer tok"},
                    data={"contact": "‎Connor - WhatsApp voice call"}, files={"file": ("c.caf", b"x", "audio/x-caf")})
    items = db.thread_items(r.json()["thread_id"])
    assert len(items) == 3  # notes + recorder note + recording, all in Connor's case


def test_text_after_name_only_call_joins_the_same_case(client, monkeypatch):
    """Connor called on WhatsApp Desktop (name only), then texts the WhatsApp number: same case."""
    monkeypatch.setattr(settings, "recorder_token", "tok")
    r = client.post("/api/recorder/calls", headers={"Authorization": "Bearer tok"},
                    data={"contact": "‎Connor - WhatsApp voice call"}, files={"file": ("c.caf", b"x", "audio/x-caf")})
    case = db.get_thread(r.json()["thread_id"])
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+16475550123", "Body": "Hi, following up on our call",
                                                    "MessageSid": "SMconn", "ProfileName": "Connor"})
    items = db.thread_items(case["id"])
    assert items[-1]["body"] == "Hi, following up on our call"
    contact = db.get_contact(case["contact_id"])
    assert contact["address"] == "+16475550123" and contact["reply_to"] == "whatsapp:+16475550123"
    # and a later call with only the name still lands there
    client.post("/api/recorder/calls", headers={"Authorization": "Bearer tok"},
                data={"contact": "Connor"}, files={"file": ("c2.caf", b"x", "audio/x-caf")})
    assert len([t for t in db.list_threads() if t["contact_id"] == contact["id"]]) == 1


def test_new_message_updates_an_approved_case(client, fakes):
    """A new WhatsApp text on an approved case produces an updated draft built on the approved record."""
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+237670000040", "Body": "Notre toit fuit", "MessageSid": "U1"})
    case = _thread_for("whatsapp:+237670000040")
    summary.summarize_thread(case["id"])
    s1 = db.latest_summary(case["id"])
    edited = dict(s1["data"], situation={"text": "Staff-corrected situation.", "citations": []})
    db.update_summary(s1["id"], data=edited)
    client.post(f"/summaries/{s1['id']}/approve", data={"notes": "ok"})
    assert db.get_thread(case["id"])["status"] == "approved"

    # nothing new -> automatic update does nothing
    assert summary.summarize_thread(case["id"]) is None

    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+237670000040", "Body": "Aussi 30 bancs cassés", "MessageSid": "U2"})
    fakes.model_output = dict(s1["data"], what_changed="Adds 30 broken benches [I2].")
    s2 = summary.summarize_thread(case["id"])           # what the pipeline runs after new items settle
    assert s2 is not None and s2["version"] == 2
    prompt = fakes.model_calls[-1]
    assert "<current_case_record>" in prompt and "Staff-corrected situation." in prompt
    assert "reviewed and approved by PS43 staff" in prompt and "Aussi 30 bancs" in prompt
    assert db.get_thread(case["id"])["status"] == "draft_ready"   # back to a person for review
    assert db.get_thread(case["id"])["case_number"] == case["case_number"]
    page = client.get(f"/threads/{case['id']}").text
    assert "Updated from new messages" in page and "Adds 30 broken benches" in page


def test_hosted_requires_password_and_uses_railway_domain(client, monkeypatch):
    monkeypatch.setattr(settings, "hosted", True)
    assert client.get("/").status_code == 503
    assert client.post("/webhooks/twilio/messaging", data={"From": "+237670000050", "Body": "x", "MessageSid": "H1"}).status_code == 200
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "bridge43-production.up.railway.app")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    from app.config import Settings
    assert Settings().public_base_url == "https://bridge43-production.up.railway.app"


def test_railway_overrides_bad_values(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "bridge43-production.up.railway.app")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.setenv("DATA_DIR", "data")
    s = Settings()
    assert s.public_base_url == "https://bridge43-production.up.railway.app"
    assert str(s.data_dir) == "/data"
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://custom.example.org/")
    assert Settings().public_base_url == "https://custom.example.org"


def test_unrelated_conversations_are_set_aside(client, fakes, monkeypatch):
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+16470000060", "Body": "yo are we still on for basketball", "MessageSid": "R1"})
    case = _thread_for("whatsapp:+16470000060")
    base = summary.normalize({"title": "Personal chat", "case_type": "unclear"})
    fakes.model_output = dict(base, relevance="not_relevant", relevance_reason="Personal plans, not school work.")
    summary.summarize_thread(case["id"])
    assert db.get_thread(case["id"])["status"] == "not_relevant"
    assert case["case_number"] not in client.get("/").text            # hidden from the inbox
    assert case["case_number"] in client.get("/?status=not_relevant").text
    monkeypatch.setattr(settings, "recorder_token", "tok")
    recent = client.get("/api/recorder/recent", headers={"Authorization": "Bearer tok"}).json()
    assert all(r["case_number"] != case["case_number"] for r in recent)

    # the same contact later asks about a school: the case comes back automatically
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+16470000060", "Body": "also my cousin's school in Kumba lost its roof", "MessageSid": "R2"})
    fakes.model_output = dict(base, relevance="relevant", relevance_reason="School roof in Kumba.")
    summary.summarize_thread(case["id"])
    assert db.get_thread(case["id"])["status"] == "draft_ready"
    assert case["case_number"] in client.get("/").text

    # staff can override, and the AI then leaves it alone
    client.post(f"/threads/{case['id']}/relevance", data={"relevant": "no"})
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+16470000060", "Body": "ok", "MessageSid": "R3"})
    fakes.model_output = dict(base, relevance="relevant")
    summary.summarize_thread(case["id"])
    assert db.get_thread(case["id"])["status"] == "not_relevant"     # staff said not relevant: AI can't undo it
    client.post(f"/threads/{case['id']}/relevance", data={"relevant": "yes"})
    fakes.model_output = dict(base, relevance="not_relevant")
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+16470000060", "Body": "lol", "MessageSid": "R4"})
    summary.summarize_thread(case["id"])
    assert db.get_thread(case["id"])["status"] != "not_relevant"     # staff said it's a case


def test_recent_cases_api(client, monkeypatch):
    monkeypatch.setattr(settings, "recorder_token", "tok")
    assert client.get("/api/recorder/recent").status_code == 401
    client.post("/intake/notes", data={"contact_name": "Lycée Recent", "notes": "roof"})
    r = client.get("/api/recorder/recent?limit=3", headers={"Authorization": "Bearer tok"}).json()
    assert r[0]["name"] == "Lycée Recent" and r[0]["case_number"].startswith("PS43-") and "/threads/" in r[0]["url"]


# ------------------------------------------------- WhatsApp Desktop messages from the Mac app

def _post_chat(client, chat, files=None, token="tok"):
    import json as _json
    return client.post("/api/recorder/messages", data={"chat": _json.dumps(chat)},
                       files=files or [], headers={"Authorization": f"Bearer {token}"})


def test_desktop_messages_update_existing_case_and_skip_personal_chats(client, fakes, monkeypatch):
    from app import db
    from app.config import settings
    monkeypatch.setattr(settings, "recorder_token", "tok")

    # a WhatsApp call from this number already made a case
    r = client.post("/api/recorder/calls", data={"contact": "Mr Fon +237 670 000 001", "duration_seconds": "60"},
                    files={"file": ("call.m4a", b"audio-bytes", "audio/mp4")}, headers={"Authorization": "Bearer tok"})
    case = r.json()["case_number"]

    # 1. a personal chat is screened out and nothing is stored
    r = _post_chat(client, {"chat_id": "16470000000@s.whatsapp.net", "phone": "+16470000000", "name": "Mom",
                            "messages": [{"id": 1, "at": "2026-10-17T18:00:00Z", "from_me": False, "kind": "text",
                                          "text": "Dinner at 7?"}]})
    assert r.status_code == 200 and r.json()["filed"] is False
    with db.tx() as conn:
        assert conn.execute("SELECT 1 FROM contacts WHERE address = '+16470000000'").fetchone() is None
    assert all("Mom" != (t["display_name"] or "") for t in db.list_threads())

    # 2. a text from the caller joins the existing case without screening, even if it is small talk
    calls_before = len(fakes.triage_calls)
    r = _post_chat(client, {"chat_id": "237670000001@s.whatsapp.net", "phone": "237670000001", "name": "Mr Fon",
                            "messages": [{"id": 7, "at": "2026-10-17T18:05:00Z", "from_me": False, "kind": "text",
                                          "text": "Thanks for calling"},
                                         {"id": 8, "at": "2026-10-17T18:06:00Z", "from_me": False, "kind": "audio",
                                          "file": "8.opus"}]},
                   files=[("files", ("8.opus", b"opus-bytes", "audio/ogg"))])
    body = r.json()
    assert body["filed"] is True and body["case_number"] == case and body["created"] == 2
    assert len(fakes.triage_calls) == calls_before
    thread = db.get_thread_by_case(case)
    items = db.thread_items(thread["id"])
    assert {i["kind"] for i in items} >= {"text", "audio"}
    assert any("le toit fuit" in (i["extracted_text"] or "") for i in items if i["kind"] == "audio")

    # the same messages sent again are not duplicated
    _post_chat(client, {"chat_id": "237670000001@s.whatsapp.net", "phone": "237670000001", "name": "Mr Fon",
                        "messages": [{"id": 7, "at": "2026-10-17T18:05:00Z", "kind": "text", "text": "Thanks for calling"}]})
    assert len(db.thread_items(thread["id"])) == len(items)


def test_desktop_messages_new_school_chat_becomes_case_with_context(client, fakes, monkeypatch):
    from app import db
    from app.config import settings
    monkeypatch.setattr(settings, "recorder_token", "tok")
    r = _post_chat(client, {"chat_id": "237699000002@s.whatsapp.net", "phone": "+237699000002", "name": "GBHS Bali",
                            "context": [{"id": 1, "at": "2026-10-17T17:00:00Z", "kind": "text", "text": "Good evening"}],
                            "messages": [{"id": 2, "at": "2026-10-17T17:01:00Z", "kind": "text",
                                          "text": "Our school roof is leaking, can PS43 help?"}]})
    body = r.json()
    assert body["filed"] is True and body["case_number"].startswith("PS43-")
    items = db.thread_items(db.get_thread_by_case(body["case_number"])["id"])
    assert [i["body"] for i in items] == ["Good evening", "Our school roof is leaking, can PS43 help?"]
    assert _post_chat(client, {"chat_id": "x", "messages": [{"id": 1, "text": "hi"}]}, token="bad").status_code == 401
