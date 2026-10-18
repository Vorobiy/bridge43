"""ui-redesign branch: live priority from drafts, matches on the case page, sending from staff phones, simpler inbox."""
import json

from app import db, summary, workflow


def _seed():
    from scripts import seed_demo
    seed_demo.main()


def test_schools_tab_ranks_unreviewed_and_updated_drafts(client, fakes):
    _seed()
    view = workflow.matrix_view()
    by_name = {c["score"]["name"]: c for c in view["cases"]}
    # a school whose draft is still waiting for review is already ranked
    assert any(c["pending"] == "new" for c in view["cases"])
    assert "not reviewed yet" in client.get("/dashboard").text
    # approved_cases() without drafts (used for tickets and impact) is unchanged
    assert all(c["pending"] is None for c in workflow.approved_cases())

    # new information after approval: the score moves before anyone re-approves
    case = next(c for c in view["cases"] if c["pending"] is None and c["score"]["quadrant"] != "safety_hold")
    tid = case["thread"]["id"]
    worse = json.loads(json.dumps(case["data"]))
    for k in summary.PROFILE_KEYS:
        worse["profile"][k]["status"] = "critical"
    db.add_summary(tid, worse, "test", [])
    after = next(c for c in workflow.matrix_view()["cases"] if c["thread"]["id"] == tid)
    assert after["pending"] == "update" and after["score"]["need"] > case["score"]["need"]
    assert "updated · check it" in client.get("/dashboard").text
    page = client.get(f"/threads/{tid}").text
    assert "from the draft" in page


def test_case_page_suggests_matches_and_uses_them(client, fakes):
    _seed()
    rows = None
    for c in workflow.approved_cases():
        rows = workflow.case_matches(c["thread"]["id"])
        if any(r["exact"] and r["picks"] for r in rows):
            tid = c["thread"]["id"]
            break
    assert rows, "the demo has a school with a ready match"
    page = client.get(f"/threads/{tid}").text
    assert "pop-matches" in page and "Use this match" in page  # the matches popup
    r = next(r for r in rows if r["exact"] and r["picks"])
    resp = client.post(f"/tickets/{r['ticket']['id']}/assign-plan", data={"back": f"/threads/{tid}"}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith(f"/threads/{tid}")
    links = [l for l in db.list_links() if l["ticket_id"] == r["ticket"]["id"] and l["status"] != "cancelled"]
    assert {l["donor_id"] for l in links} == {p["donor_id"] for p in r["picks"]}


def test_send_from_own_phone_is_logged_without_twilio(client, fakes):
    _seed()
    t = next(t for t in db.list_threads() if db.latest_summary(t["id"]))
    s = db.latest_summary(t["id"])
    page = client.get(f"/threads/{t['id']}").text
    assert "Open in WhatsApp" in page and "I sent it" in page and "Copy" in page
    r = client.post(f"/summaries/{s['id']}/send-confirmation", data={"text": "PS43: merci", "manual": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    last = db.thread_items(t["id"])[-1]
    assert last["direction"] == "out" and last["body"].startswith("[Confirmation sent from staff phone] PS43: merci")


def test_inbox_groups_and_plain_words(client, fakes):
    _seed()
    page = client.get("/").text
    assert "Needs you" in page and "Schools" in page and "Partners" in page and "Review" in page
    assert "To review" in page and ("ago" in page or "Oct" in page)


def test_case_page_survives_old_drafts_missing_fields(client, fakes):
    """Drafts written before readiness/profile/number fields existed must still render."""
    contact = db.get_or_create_contact("whatsapp:+237699123456", "whatsapp", "Old Draft School")
    thread = db.open_thread_for(contact["id"])
    db.add_item(contact_id=contact["id"], channel="whatsapp", kind="text", body="hello", thread_id=thread["id"])
    old = {"case_type": "need", "title": "Old school - roof", "organization": {"name": "Old school", "type": "school"},
           "situation": {"text": "Roof leaks.", "citations": []},
           "needs": [{"category": "roofing", "description": "Roof", "quantity": None, "urgency": "high", "citations": []}],
           "readiness": {"electricity": {"status": "no", "note": None, "citations": []}},
           "profile": {"water_toilets": {"status": "critical", "note": None, "citations": []}},
           "numbers": {"students": 100}, "confirmation_message": {"language": "French", "text": "PS43: ..."}}
    db.add_summary(thread["id"], old, "old", [])
    r = client.get(f"/threads/{thread['id']}")
    assert r.status_code == 200 and "Old school" in r.text
    assert client.get("/dashboard").status_code == 200 and client.get("/").status_code == 200


def test_case_lifecycle_updates_every_tab(client, fakes):
    """Approve -> match -> steps from the case page -> school confirms -> case resolved -> Impact, Today, Inbox, Schools."""
    _seed()
    draft = next(t for t in db.list_threads() if t["status"] == "draft_ready")
    s = db.latest_summary(draft["id"])
    client.post(f"/summaries/{s['id']}/approve", data={"notes": ""})
    assert db.get_thread(draft["id"])["status"] == "approved" and db.list_tickets(thread_id=draft["id"])
    assert "not reviewed yet" not in client.get("/dashboard").text.split(draft["case_number"])[0][-400:]

    # a school with a ready match: accept it from the case page, then work every step from the case page
    tid = next(c["thread"]["id"] for c in workflow.approved_cases()
               if any(r["exact"] and r["picks"] for r in workflow.case_matches(c["thread"]["id"])))
    for r in workflow.case_matches(tid):
        if r["exact"] and r["picks"]:
            client.post(f"/tickets/{r['ticket']['id']}/assign-plan", data={"back": f"/threads/{tid}"})
    for k in db.list_tickets(thread_id=tid):
        if k["status"] == "new":
            client.post(f"/tickets/{k['id']}/status", data={"status": "cancelled", "back": f"/threads/{tid}"})
    page = client.get(f"/threads/{tid}").text
    assert 'id="work"' in page and "steps" in page
    for k in db.list_tickets(thread_id=tid):
        if k["status"] == "matched":  # "Tell the partner" first (sent from a staff phone, logged)
            donor = next(l for l in db.list_links(ticket_id=k["id"]) if l["status"] != "cancelled")
            client.post(f"/tickets/{k['id']}/message", data={"to": str(donor["donor_id"]), "text": "Bonjour", "manual": "1"})
    for _ in range(40):
        rows = [r for r in workflow.case_needs(tid) if r["next"] and r["next"].get("action")]
        if not rows:
            break
        resp = client.post(rows[0]["next"]["action"], data={"back": f"/threads/{tid}"}, follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"].startswith(f"/threads/{tid}")
    t = db.get_thread(tid)
    assert t["status"] == "resolved" and t["resolved_at"]

    # every tab reflects it
    impact = client.get("/api/impact").json()
    assert any(r["case_number"] == t["case_number"] for r in impact["resolved"])
    assert t["case_number"] in client.get("/?status=resolved").text
    assert all(c["thread_id"] != tid for c in client.get("/api/matrix").json())
    assert "Recently resolved" in client.get("/today").text
    history = workflow.case_history(tid)
    assert history[0]["kind"] == "resolved" or any(e["kind"] == "resolved" for e in history)

    # a later message (a follow-up answer) keeps the case resolved and flags it for review
    db.add_summary(tid, db.latest_summary(tid)["data"], "test", [])
    t = db.get_thread(tid)
    assert t["status"] == "resolved" and t["stale_summary"] == 1
    # reopening a need puts the case back in progress
    k = next(k for k in db.list_tickets(thread_id=tid) if k["status"] == "checked")
    client.post(f"/tickets/{k['id']}/status", data={"status": "in_progress"})
    assert db.get_thread(tid)["status"] == "approved"


def test_partner_page_assign_and_today_confirm(client, fakes):
    _seed()
    p = next(x for x in workflow.partners_view() if workflow.partner_can_help(x["id"]))
    r = next((r for r in workflow.partner_can_help(p["id"]) if r["best"]["exact"]), None)
    if r:
        resp = client.post(f"/tickets/{r['ticket']['id']}/assign", data={
            "donor_id": p["id"], "item_id": r["best"]["item_id"], "back": f"/partners/{p['id']}"}, follow_redirects=False)
        assert resp.headers["location"].startswith(f"/partners/{p['id']}")
        assert db.get_ticket(r["ticket"]["id"])["status"] == "matched"
    # a delivered need shows on Today with a one-click confirmation
    k = next(k for k in db.list_tickets() if k["status"] in ("matched", "in_progress"))
    workflow.set_status(k["id"], "delivered", "test")
    v = workflow.today_view()
    item = next(c for c in v["confirm"] if c["id"] == k["id"])
    resp = client.post(f"/tickets/{k['id']}/steps/{item['step_id']}/done", data={"back": "/today"}, follow_redirects=False)
    assert resp.headers["location"].startswith("/today") and db.get_ticket(k["id"])["status"] == "checked"


def test_startup_resolves_cases_confirmed_before_the_update(client, fakes):
    """Databases from before case resolution: a case whose needs are all confirmed becomes resolved at startup."""
    _seed()
    t = next(t for t in db.list_threads() if t["status"] == "resolved")
    db.set_thread(t["id"], status="approved", resolved_at=None)  # as an older version left it
    assert workflow.refresh_all_cases() >= 1
    t2 = db.get_thread(t["id"])
    delivered = max(k["delivered_at"] for k in db.list_tickets(thread_id=t["id"]))
    assert t2["status"] == "resolved" and t2["resolved_at"] == delivered
    assert workflow.refresh_all_cases() == 0  # nothing left to change


def test_approving_opens_the_matches_popup(client, fakes):
    _seed()
    draft = next(t for t in db.list_threads() if t["status"] == "draft_ready")
    s = db.latest_summary(draft["id"])
    r = client.post(f"/summaries/{s['id']}/approve", data={"notes": ""}, follow_redirects=False)
    assert "approved=1" in r.headers["location"]
    page = client.get(r.headers["location"]).text
    if any(m["exact"] and m["picks"] for m in workflow.case_matches(draft["id"])):
        assert "Approved. These partners can help" in page and "pm.showModal()" in page
    # using a match from the popup comes back to the case with the popup open for the rest
    m = next((m for m in workflow.case_matches(draft["id"]) if m["exact"] and m["picks"]), None)
    if m:
        r = client.post(f"/tickets/{m['ticket']['id']}/assign-plan", data={"back": f"/threads/{draft['id']}?matches=1"},
                        follow_redirects=False)
        assert r.headers["location"].startswith(f"/threads/{draft['id']}?matches=1&msg=")
