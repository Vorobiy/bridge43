"""Today page, priority reasons and adjustable weights, Impact report, map, WhatsApp on the sign-up page."""
from app import db, geo, scoring, summary, workflow
from app.config import settings


def _draft(**over):
    d = {"organization": {"name": "EP Test", "type": "school", "region": "Far North", "town": "Mokolo", "citations": []},
         "profile": {"water_toilets": {"status": "critical"}, "learning_space": {"status": "partial"},
                     "teachers": {"status": "partial"}, "materials_results": {"status": "partial"}},
         "readiness": {k: {"status": "yes"} for k in summary.READINESS_KEYS},
         "numbers": {"students": 640}, "operating_status": {"status": "open", "safe_to_reach": "yes"}}
    d.update(over)
    return summary.normalize(d)


def test_why_explains_the_box(fakes):
    s = scoring.score_case(_draft())
    assert s["quadrant"] == "act_now"
    assert s["why"].startswith("Act now because:") and "water and toilets critical" in s["why"] and "640 pupils" in s["why"]
    hold = scoring.score_case(_draft(operating_status={"status": "closed", "safe_to_reach": "no"}))
    assert "closed" in hold["why"] and hold["quadrant"] == "safety_hold"
    not_ready = scoring.score_case(_draft(readiness={}))
    assert "not ready yet: no power" in not_ready["why"]


def test_settings_change_the_weights(client):
    before = scoring.score_case(_draft())["need"]
    r = client.post("/settings", data={"w_water_toilets": "0", "w_learning_space": "0", "split": "60"},
                    follow_redirects=False)
    assert r.status_code == 303
    cfg = scoring.config()
    assert cfg["need_weights"]["water_toilets"] == 0 and cfg["split"] == 60
    assert scoring.score_case(_draft())["need"] < before
    assert client.get("/settings").status_code == 200
    client.post("/settings", data={"reset": "1"})
    assert scoring.config() == scoring.defaults()


def test_map_places_schools_and_finds_blind_spots(fakes):
    pinned = _draft(location={"latitude": 10.74, "longitude": 13.80, "description": None, "citations": []})
    assert geo.place(pinned)[2] == "shared location"
    assert geo.place(_draft())[2] == "town centre"
    assert geo.place(_draft(organization={"name": "X", "type": "school", "region": "Est", "town": None}))[2] == "region (approximate)"
    cases = [{"data": _draft(), "thread": {"id": 1}, "score": scoring.score_case(_draft())}]
    m = geo.map_view(cases)
    assert len(m["dots"]) == 1 and "Far North" not in m["blind_spots"] and "Littoral" in m["blind_spots"]
    x, y = m["dots"][0]["x"], m["dots"][0]["y"]
    assert 0 < x < geo.WIDTH and 0 < y < geo.HEIGHT and m["path"].startswith("M")


def test_today_impact_and_demo(client):
    from scripts import seed_demo
    seed_demo.main()
    v = workflow.today_view()
    assert v["followups"] and v["ready"] and v["flagged"] and v["count"] >= 3
    assert v["flagged"][0]["who"].startswith("Quincaillerie")
    page = client.get("/today").text
    assert "Check new requests" in page and "Confirm a partner" in page and "Demo data" in page

    # confirming a partner from Today stays on Today
    t = v["ready"][0]["ticket"]
    r = client.post(f"/tickets/{t['id']}/assign-plan", data={"back": "/today"}, follow_redirects=False)
    assert r.headers["location"].startswith("/today") and db.get_ticket(t["id"])["status"] == "matched"

    imp = workflow.impact_view()
    assert imp["confirmed"] == 2 and imp["pupils"] == 520 and imp["confirmed_rate"] == 100
    assert imp["questioned"] > 0 and imp["stories"][0]["partners"] == ["Aqua Nord Forages"]
    assert imp["cost"] is None
    db.set_setting("costs", {"per_message": 0.01, "monthly_hosting": 5, "currency": "USD"})
    cost = workflow.impact_view()["cost"]
    assert cost["total"] > 0 and cost["per_case"] > 0
    for url in ("/impact", "/api/impact", "/dashboard", "/today", "/settings"):
        assert client.get(url).status_code == 200, url
    dash = client.get("/dashboard").text
    assert "Blind spots" in dash and "because:" in dash
    case = next(c for c in workflow.approved_cases() if c["score"]["quadrant"] == "act_now")
    assert "Why:" in client.get(f"/threads/{case['thread']['id']}").text


def test_whatsapp_button_on_signup(client, monkeypatch):
    assert "wa.me/16479017011" in client.get("/join").text  # PS43 number by default
    monkeypatch.setattr(settings, "public_whatsapp", "+237 6 99 00 11 22")
    page = client.get("/join").text
    assert "https://wa.me/237699001122?text=Bonjour%20PS43" in page
    assert "wa.me/237699001122" in client.get("/join/school?lang=en").text


def test_vulnerability_raises_need_and_is_explained(fakes):
    base = _draft(organization={"name": "EP X", "type": "school", "region": "Centre", "town": "Mbalmayo", "citations": []})
    plain = scoring.score_case(base)
    assert plain["vulnerability"] == 0 and "vulnerable" not in plain["why"]
    vul = _draft(organization={"name": "EP X", "type": "school", "region": "Centre", "town": "Mbalmayo", "citations": []},
                 vulnerability={"conflict_affected": "yes", "displaced_pupils": 85, "pupils_with_disability": None,
                                "orphans": 20, "note": None, "citations": []},
                 numbers={"students": 640, "girls": 200})
    s = scoring.score_case(vul)
    assert s["vulnerability"] == 13 and s["need"] == min(100, plain["need"] + 13)
    assert "affected by conflict" in s["why"] and "85 displaced or refugee pupils" in s["why"] and "few girls enrolled (31%)" in s["why"]
    # crisis region counts even when the school did not say "conflict"
    assert "crisis-affected region (Far North)" in scoring.score_case(_draft())["why"]
    # staff can switch it off
    db.set_setting("scoring", {"vulnerability_max": 0})
    assert scoring.score_case(vul)["vulnerability"] == 0
    db.set_setting("scoring", {})
    from app import gaps
    assert 15 in gaps.follow_up(summary.normalize(base))["missing"]
