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
    assert "Suggested matches" in page and "Use this match" in page
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
