import time

from app.hue_client import color_xy
from tests.conftest import bearer


def test_health(client, paired):
    body = client.get("/api/v1/health").json()
    assert body == {"status": "ok", "bridge_paired": True, "selected_lights": 2, "alert_active": False}


def test_alert_requires_bridge_and_lights(admin, store):
    token = admin.post("/admin/tokens", json={"name": "t"}).json()["token"]
    res = admin.post("/api/v1/alert", json={"level": "red", "mode": "dead"}, headers=bearer(token))
    assert res.status_code == 409 and "No Hue Bridge" in res.json()["detail"]

    store.set_bridge(ip="10.0.0.5", username="u")
    res = admin.post("/api/v1/alert", json={"level": "red", "mode": "dead"}, headers=bearer(token))
    assert res.status_code == 409 and "No lights selected" in res.json()["detail"]


def test_invalid_body(client, api_token):
    h = bearer(api_token)
    assert client.post("/api/v1/alert", json={"level": "blue", "mode": "dead"}, headers=h).status_code == 422
    assert client.post("/api/v1/alert", json={"level": "red", "mode": "blink"}, headers=h).status_code == 422
    assert client.post("/api/v1/alert", json={"level": "red", "mode": "dead", "duration_seconds": 0}, headers=h).status_code == 422


def test_dead_alert_sets_static_colour_and_restores(client, api_token, fake_hue, store):
    h = bearer(api_token)
    res = client.post("/api/v1/alert", json={"level": "red", "mode": "dead"}, headers=h)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "started"
    assert body["alert"]["active"] and body["alert"]["level"] == "red" and body["alert"]["mode"] == "dead"
    assert body["alert"]["light_ids"] == ["1", "2"] and body["alert"]["expires_at"] is None
    assert body["alert"]["source"] == "api:monitoring"

    expected = {"on": True, "xy": color_xy("#FF0000"), "bri": 254, "transitiontime": 0}
    assert fake_hue.calls_for("1") == [expected]
    assert fake_hue.calls_for("2") == [expected]
    assert fake_hue.calls_for("3") == []  # not selected

    time.sleep(0.3)
    assert len(fake_hue.calls) == 2  # dead mode: no further updates

    status = client.get("/api/v1/alert", headers=h).json()
    assert status["active"] is True
    assert client.get("/api/v1/health").json()["alert_active"] is True

    res = client.delete("/api/v1/alert", headers=h)
    assert res.json()["status"] == "cleared" and res.json()["alert"]["active"] is False
    # light 1 was on with xy colour, light 2 was off -> restored
    assert fake_hue.calls_for("1")[-1] == {"on": True, "bri": 100, "xy": [0.3, 0.3], "transitiontime": 4}
    assert fake_hue.calls_for("2")[-1] == {"on": False, "transitiontime": 4}

    assert client.delete("/api/v1/alert", headers=h).json()["status"] == "no_alert"


def test_glow_alert_pulses_between_min_and_max(client, api_token, fake_hue, store):
    store.update_settings(min_brightness=40, max_brightness=200, glow_period_seconds=0.4)
    h = bearer(api_token)
    res = client.post("/api/v1/alert", json={"level": "amber", "mode": "glow"}, headers=h)
    assert res.status_code == 200
    time.sleep(0.9)
    calls = fake_hue.calls_for("1")
    assert calls[0] == {"on": True, "xy": color_xy("#FFBF00"), "bri": 200, "transitiontime": 0}
    bris = [c["bri"] for c in calls[1:]]
    assert len(bris) >= 3
    assert set(bris) <= {40, 200}
    assert all(a != b for a, b in zip(bris, bris[1:]))  # alternates
    client.delete("/api/v1/alert", headers=h)
    n = len(fake_hue.calls)
    time.sleep(0.5)
    assert len(fake_hue.calls) == n  # loop stopped


def test_new_alert_replaces_running_one_and_keeps_original_snapshot(client, api_token, fake_hue):
    h = bearer(api_token)
    client.post("/api/v1/alert", json={"level": "amber", "mode": "glow"}, headers=h)
    time.sleep(0.2)
    res = client.post("/api/v1/alert", json={"level": "red", "mode": "dead"}, headers=h)
    assert res.json()["alert"]["level"] == "red"
    res = client.delete("/api/v1/alert", headers=h)
    assert res.json()["status"] == "cleared"
    # restored to the state before the *first* alert, not the amber state
    assert fake_hue.calls_for("1")[-1] == {"on": True, "bri": 100, "xy": [0.3, 0.3], "transitiontime": 4}


def test_duration_auto_clears(client, api_token, fake_hue):
    h = bearer(api_token)
    res = client.post("/api/v1/alert", json={"level": "red", "mode": "dead", "duration_seconds": 1}, headers=h)
    assert res.json()["alert"]["expires_at"] is not None
    time.sleep(1.4)
    assert client.get("/api/v1/alert", headers=h).json()["active"] is False
    assert fake_hue.calls_for("2")[-1] == {"on": False, "transitiontime": 4}


def test_bridge_failure_does_not_crash_alert(client, api_token, fake_hue):
    fake_hue.fail = True
    res = client.post("/api/v1/alert", json={"level": "red", "mode": "dead"}, headers=bearer(api_token))
    assert res.status_code == 200 and res.json()["alert"]["active"]
    assert client.delete("/api/v1/alert", headers=bearer(api_token)).json()["status"] == "cleared"


def test_openapi_documents_alert_endpoints(client):
    spec = client.get("/openapi.json").json()
    assert "/api/v1/alert" in spec["paths"]
    assert set(spec["paths"]["/api/v1/alert"]) == {"get", "post", "delete"}
    assert "/admin/tokens" not in spec["paths"]
    assert client.get("/docs").status_code == 200
