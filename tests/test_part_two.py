"""Part two: priority matrix, tickets, partners & donors, matching, quotes, follow-ups, public sign-up."""
from datetime import datetime, timedelta, timezone

from app import db, gaps, matching, scoring, summary, workflow
from conftest import send_whatsapp


def _thread_for(address):
    address = db.canonical_address(address)
    return max((t for t in db.list_threads() if t["address"] == address), key=lambda t: t["id"])


def _school_draft(**over):
    d = {
        "relevance": "relevant", "case_type": "need", "title": "GBHS Test - laptops",
        "organization": {"name": "GBHS Test", "type": "school", "region": "Ouest", "town": "Bafoussam", "citations": [1]},
        "operating_status": {"status": "open", "safe_to_reach": "yes", "note": None, "citations": [1]},
        "profile": {"technology": {"status": "critical", "note": None, "citations": [1]},
                    "learning_space": {"status": "partial", "note": None, "citations": [1]},
                    "teachers": {"status": "partial", "note": None, "citations": [1]},
                    "materials_results": {"status": "partial", "note": None, "citations": [1]}},
        "readiness": {k: {"status": "yes", "note": None, "citations": [1]} for k in summary.READINESS_KEYS},
        "numbers": {"students": 820, "citations": [1]},
        "needs": [{"category": "devices", "description": "Laptops for a computer lab", "quantity": "40 laptops",
                   "urgency": "high", "citations": [1]}],
        "language": "English", "confidence": "high", "confirmation_message": {"language": "English", "text": "PS43: ok"},
    }
    d.update(over)
    return d


def _offer_draft(name, items, regions=("National",), **over):
    d = {
        "relevance": "relevant", "case_type": "offer", "title": f"{name} offer",
        "organization": {"name": name, "type": "donor", "region": None, "town": None, "citations": [1]},
        "donor": {"kind": "diaspora_group", "gives": ["goods"], "provides_summary": f"{name} gives laptops",
                  "amount": None, "regions": list(regions), "timeframe": "now", "conditions": None,
                  "registered": "unknown", "past_work_with_ps43": None, "citations": [1]},
        "offers": [{"category": c, "description": d, "quantity": q, "availability": "now", "conditions": None,
                    "location": loc, "abroad": abroad, "citations": [1]} for c, d, q, loc, abroad in items],
        "people": [{"name": "Contact", "role": None, "organization": name, "citations": [1]}],
        "language": "English", "confidence": "high", "confirmation_message": {"language": "English", "text": "PS43: ok"},
    }
    d.update(over)
    return d


def _intake_and_approve(client, fakes, phone, text, draft):
    send_whatsapp(client, phone, text)
    t = _thread_for(f"whatsapp:{phone}")
    fakes.model_output = draft
    summary.summarize_thread(t["id"], manual=True)
    s = db.latest_summary(t["id"])
    r = client.post(f"/summaries/{s['id']}/approve", data={"notes": ""}, follow_redirects=False)
    assert r.status_code == 303
    return t


# ------------------------------------------------------------------ scoring

def test_scores_and_quadrants():
    act = scoring.score_case(summary.normalize(_school_draft()))
    assert act["readiness"] == 100 and act["need"] >= 50 and act["quadrant"] == "act_now"
    low_ready = _school_draft(readiness={})
    assert scoring.score_case(summary.normalize(low_ready))["quadrant"] == "prepare_first"
    closed = _school_draft(operating_status={"status": "closed", "safe_to_reach": "no", "note": None, "citations": []})
    assert scoring.score_case(summary.normalize(closed))["quadrant"] == "safety_hold"
    # a listed critical need counts even when the profile area was left unknown
    d = summary.normalize(_school_draft(profile={}, needs=[{"category": "water", "description": "Borehole",
                                                            "quantity": "1", "urgency": "critical", "citations": []}]))
    assert scoring.area_levels(d)["water_toilets"] == 2


# ------------------------------------------------------------------ matching helpers

def test_regions_quantities_and_relations():
    assert matching.canon_region("Nord-Ouest") == "Northwest" and matching.canon_region("Bamenda") == "Northwest"
    assert matching.canon_region("Extrême-Nord") == "Far North" and matching.canon_region("anywhere in Cameroon") == "National"
    assert matching.canon_region("Northwest, Cameroon") == "Northwest"
    assert matching.parse_quantity("about 60 table-benches") == (60, "table-benches")
    assert matching.parse_quantity("3,000,000 FCFA") == (3_000_000, "fcfa")
    assert matching.region_fit("Far North", ["Northwest"]) is None  # "Northwest only" never matches the Far North
    assert matching.region_fit("West", ["National"])[0] == 20
    assert matching.relation("devices", "devices")[0] == 45
    assert matching.relation("furniture", "roofing")[0] == 25 and matching.relation("water", "devices") is None
    assert matching.relation("water", "funding")[0] == 20


def test_trust_levels():
    base = {"verified": 0, "registered": "unknown"}
    assert matching.donor_trust(base, [])["level"] == "New"
    assert matching.donor_trust({**base, "verified": 1}, [])["level"] == "Verified"
    done = [{"status": "completed"}, {"status": "completed"}]
    assert matching.donor_trust({**base, "verified": 1}, done)["level"] == "Trusted"
    assert matching.donor_trust({**base, "verified": 1}, [{"status": "cancelled"}])["score"] == 25


def test_price_check():
    q = lambda i, amount, qty, t=1: {"id": i, "amount": amount, "quantity": qty, "unit": "sheets", "category": "roofing", "ticket_id": t}
    quotes = [q(1, 1_800_000, 120), q(2, 2_640_000, 120), q(3, 1_920_000, 120)]
    assert matching.price_check(quotes[1], quotes)["status"] == "high"
    assert matching.price_check(quotes[0], quotes)["status"] == "fair"
    assert matching.price_check(quotes[0], quotes[:1])["status"] == "first"
    assert matching.price_check(q(4, 500_000, None), quotes)["status"] == "no_quantity"


# ------------------------------------------------------------------ donor intake

def test_donor_questions_and_schema():
    props = summary.CASE_TOOL["input_schema"]["properties"]
    assert "donor" in props and {"location", "abroad"} <= set(props["offers"]["items"]["properties"])
    assert "funding" in summary.NEED_CATEGORIES and "transport" in summary.NEED_CATEGORIES
    f = gaps.follow_up(summary.normalize(_offer_draft("Toronto Group", [("devices", "Laptops", None, None, False)])))
    assert f["bank"] == "donor" and f["asking"][0] == 101 and "how many" in f["message"]
    full = summary.normalize(_offer_draft("Toronto Group", [("devices", "Laptops", "25 laptops", "Toronto", True)]))
    assert 101 not in gaps.follow_up(full)["missing"] and 102 not in gaps.follow_up(full)["missing"]


# ------------------------------------------------------------------ end to end

def test_full_loop_school_to_follow_up(client, fakes, monkeypatch):
    school = _intake_and_approve(client, fakes, "+237670001001", "We need a computer lab", _school_draft())
    tickets = db.list_tickets(thread_id=school["id"])
    assert len(tickets) == 1 and tickets[0]["quantity_total"] == 40 and tickets[0]["status"] == "new"
    assert any(s["title"].startswith("School confirms") for s in db.list_steps(tickets[0]["id"]))
    ticket = tickets[0]

    toronto_t = _intake_and_approve(client, fakes, "+16475550001", "We have laptops",
                                    _offer_draft("Toronto Group", [("devices", "Refurbished laptops", "25 laptops",
                                                                    "Toronto, Canada", True)]))
    yaounde_t = _intake_and_approve(client, fakes, "+237670001002", "Nous avons des ordinateurs",
                                    _offer_draft("Yaoundé NGO", [("devices", "Laptops", "20 laptops", "Yaoundé", False),
                                                                 ("transport", "Delivery from Douala port", None, "Douala", False)],
                                                 organization={"name": "Yaoundé NGO", "type": "ngo", "region": "Centre",
                                                               "town": "Yaoundé", "citations": [1]}))
    donors = {d["name"]: d for d in db.list_donors()}
    assert {"Toronto Group", "Yaoundé NGO"} <= set(donors)
    assert "shipper" in donors["Yaoundé NGO"]["roles"] and donors["Toronto Group"]["thread_id"] == toronto_t["id"]
    items = db.list_donor_items(donors["Toronto Group"]["id"])
    assert items[0]["abroad"] == 1 and items[0]["quantity_total"] == 25

    # a donor abroad who only serves other regions is never suggested
    _intake_and_approve(client, fakes, "+33600000001", "Paris laptops",
                        _offer_draft("Paris Group", [("devices", "Laptops", "15 laptops", "Paris", True)],
                                     regions=("Southwest",)))
    assert "Paris Group" not in {c["donor_name"] for c in workflow.ticket_matches(ticket)["candidates"]}

    # 40 laptops: no single donor has enough, so two are combined (25 + 15)
    m = workflow.ticket_matches(ticket)
    plan = m["plan"]
    assert plan["type"] == "combined" and sorted(p["covers"] for p in plan["picks"]) == [15, 25]
    assert plan["needs_shipping"] and plan["shippers"][0]["donor_name"] == "Yaoundé NGO"
    assert client.get(f"/tickets/{ticket['id']}").status_code == 200

    client.post(f"/tickets/{ticket['id']}/assign-plan")
    ticket = db.get_ticket(ticket["id"])
    assert ticket["status"] == "matched"
    titles = [s["title"] for s in db.list_steps(ticket["id"])]
    assert titles.index("Pack and ship from abroad") < titles.index("Deliver the equipment")  # shipping first
    assert workflow.ticket_matches(ticket)["plan"]["type"] == "covered"

    # staff message the partner and the school from their own phone; Bridge logs both
    r = client.post(f"/tickets/{ticket['id']}/message", data={"to": str(donors["Toronto Group"]["id"]),
                                                             "text": "PS43: can you ship?", "manual": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    client.post(f"/tickets/{ticket['id']}/message", data={"to": "school", "text": "PS43: good news", "manual": "1"})
    kinds = [e["kind"] for e in db.list_events(ticket["id"])]
    assert kinds.count("message") == 2
    assert any("[Message to partner]" in (i["body"] or "") for i in db.thread_items(toronto_t["id"]))

    # work steps -> delivered (follow-ups scheduled) -> school confirms -> partners' trust rises
    steps = db.list_steps(ticket["id"])
    for s in steps:
        if not s["title"].startswith("School confirms"):
            client.post(f"/tickets/{ticket['id']}/steps/{s['id']}/done")
    assert db.get_ticket(ticket["id"])["status"] == "delivered"
    assert [f["days"] for f in db.list_followups(ticket_id=ticket["id"])] == [30, 90, 180]
    before = workflow.trust_map()[donors["Toronto Group"]["id"]]["score"]
    confirm = next(s for s in steps if s["title"].startswith("School confirms"))
    client.post(f"/tickets/{ticket['id']}/steps/{confirm['id']}/done")
    assert db.get_ticket(ticket["id"])["status"] == "checked"
    assert {l["status"] for l in db.list_links(ticket_id=ticket["id"])} == {"completed"}
    assert workflow.trust_map()[donors["Toronto Group"]["id"]]["score"] == before + 15 - 5  # completed replaces delivered

    # a delivered need no longer counts in the school's need score
    case = next(c for c in workflow.approved_cases() if c["thread"]["id"] == school["id"])
    assert case["helped"] == ["Laptops for a computer lab"]

    # follow-up due 30 days later goes to the school and lands in its case
    f = db.list_followups(ticket_id=ticket["id"])[0]
    later = (datetime.now(timezone.utc) + timedelta(days=31)).date().isoformat()
    due = workflow.followups_due(later)
    assert due and due[0]["id"] == f["id"] and "1 month ago" in due[0]["message"]
    r = client.post(f"/followups/{f['id']}/send", data={"text": due[0]["message"]}, follow_redirects=False)
    assert r.status_code == 303 and db.get_followup(f["id"])["sent_at"]
    assert any("30-day follow-up" in (i["body"] or "") for i in db.thread_items(school["id"]))

    # every page and API renders
    for url in ("/dashboard", "/tickets", "/partners", f"/partners/{donors['Toronto Group']['id']}", "/matches",
                "/api/matrix", "/api/partners", "/api/matches", f"/threads/{school['id']}"):
        assert client.get(url).status_code == 200, url
    # its only need is confirmed: the case is resolved, leaves the active ranking and shows on Impact
    assert db.get_thread(school["id"])["status"] == "resolved"
    assert all(c["name"] != "GBHS Test" for c in client.get("/api/matrix").json())
    assert any(r["school"] == "GBHS Test" for r in client.get("/api/impact").json()["resolved"])


def test_quotes_and_cancelled_commitment(client, fakes):
    school = _intake_and_approve(client, fakes, "+237670001003", "roof", _school_draft(needs=[
        {"category": "roofing", "description": "Roof sheets", "quantity": "120 sheets", "urgency": "critical", "citations": [1]}]))
    k = db.list_tickets(thread_id=school["id"])[0]
    for amount in ("1 800 000", "1,920,000"):
        client.post(f"/tickets/{k['id']}/quotes", data={"amount": amount, "supplier_name": "A", "quantity": "120", "unit": "sheets"})
    r = client.post(f"/tickets/{k['id']}/quotes", data={"amount": "2640000", "supplier_name": "Inflated", "quantity": "120",
                                                       "unit": "sheets"}, follow_redirects=False)
    assert "above+other+quotes" in r.headers["location"]
    checks = {q["who"]: q["check"]["status"] for q in workflow.quotes_view(k["id"])}
    assert checks["Inflated"] == "high"

    _intake_and_approve(client, fakes, "+237670001004", "sheets", _offer_draft(
        "Roof Co", [("roofing", "Aluminium sheets", "400 sheets", "Bafoussam", False)], regions=("West",)))
    donor = db.list_donors()[0]
    client.post(f"/tickets/{k['id']}/assign-plan")
    link = db.list_links(ticket_id=k["id"])[0]
    assert link["quantity"] == 120 and db.get_ticket(k["id"])["status"] == "matched"
    client.post(f"/tickets/{k['id']}/links/{link['id']}/cancel")
    assert db.get_ticket(k["id"])["status"] == "new"
    assert workflow.trust_map()[donor["id"]]["score"] == 0  # 10 - 15, floored at 0


def test_manual_partner_and_item(client):
    r = client.post("/partners", data={"name": "Douala Freight", "phone": "+237 699 000 111", "kind": "company",
                                       "roles": ["shipper"], "regions": "National"}, follow_redirects=False)
    pid = int(r.headers["location"].split("/partners/")[1].split("?")[0])
    client.post(f"/partners/{pid}/items", data={"category": "transport", "description": "Port clearance",
                                                "quantity": "", "location": "Douala"})
    client.post(f"/partners/{pid}/verify", data={"verified": "1"})
    p = db.get_donor(pid)
    assert p["roles"] == ["shipper"] and p["verified"] == 1 and db.list_donor_items(pid)[0]["category"] == "transport"
    assert client.get(f"/partners/{pid}").status_code == 200


# ------------------------------------------------------------------ public sign-up

def test_public_forms_land_in_the_inbox(client):
    assert client.get("/join").status_code == 200 and "Mon école a besoin" in client.get("/join").text
    assert "My school needs help" in client.get("/join?lang=en").text
    r = client.post("/join/school", data={"lang": "fr", "school_name": "EP Test", "town": "Mokolo", "region": "Far North",
                                          "phone": "+237 670 009 999", "pupils": "300", "needs": ["water", "devices"],
                                          "details": "Pas d'eau", "consent": "1"})
    assert r.status_code == 200 and "Merci" in r.text
    t = _thread_for("whatsapp:+237670009999")
    item = db.thread_items(t["id"])[0]
    assert item["channel"] == "web" and "Drinking water" in item["body"] and item["status"] == "processed"
    assert db.get_contact(t["contact_id"])["consent_status"] == "granted"

    r = client.post("/join/partner", data={"lang": "en", "org_name": "Friends in Paris", "email": "f@example.org",
                                           "gives": ["goods"], "offer_details": "15 laptops", "regions": ["Southwest"],
                                           "abroad": "1", "country": "France", "consent": "1"})
    assert r.status_code == 200
    t = _thread_for("web:f@example.org")
    assert "need shipping" in db.thread_items(t["id"])[0]["body"]

    # no consent -> refused; hidden bot field -> silently dropped
    n = len(db.list_threads())
    assert client.post("/join/school", data={"school_name": "X", "phone": "+237670000000"}).status_code == 400
    client.post("/join/school", data={"school_name": "Bot", "phone": "+237670000001", "consent": "1", "website": "spam"})
    assert len(db.list_threads()) == n


def test_demo_data_loads_and_renders(client):
    from scripts import seed_demo
    seed_demo.main()
    cov = workflow.coverage_view()
    kinds = {r["plan"]["type"] for r in cov["rows"]}
    assert {"none", "related", "single", "combined", "covered"} <= kinds
    assert workflow.followups_due()  # the Garoua borehole 30-day check
    trust = {d["name"]: v["level"] for d in db.list_donors() for k, v in workflow.trust_map().items() if k == d["id"]}
    assert trust["Aqua Nord Forages"] == "Trusted"
    for url in ("/dashboard", "/tickets", "/partners", "/matches"):
        assert client.get(url).status_code == 200
    for k in db.list_tickets():
        assert client.get(f"/tickets/{k['id']}").status_code == 200
    for d in db.list_donors():
        assert client.get(f"/partners/{d['id']}").status_code == 200
