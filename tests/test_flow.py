"""End-to-end: every intake channel lands in a thread, gets converted, drafted, reviewed."""
import io
import json
import zipfile

from app import db, summary
from app.channels import twilio as tw
from app.config import settings


def _thread_for(address):
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
    assert s["status"] == "approved" and s["case_number"].startswith("PS43-") and s["case_number"].endswith("0001")
    cases = client.get("/api/cases").json()
    assert cases[0]["case"]["title"] == "GBHS Test - roof"

    # a new message after approval starts a fresh thread
    client.post("/webhooks/twilio/messaging", data={"From": "whatsapp:+237670000009", "Body": "Merci !", "MessageSid": "SM9"})
    assert _thread_for("whatsapp:+237670000009")["id"] != thread_id


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
    assert db.get_contact(items[0]["contact_id"])["address"] == "whatsapp:+237670000011"
    assert client.get("/api/recorder/ping", headers={"Authorization": "Bearer tok"}).json() == {"ok": True}
