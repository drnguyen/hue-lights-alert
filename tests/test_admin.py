import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.hue_client import HueError
from app.main import create_app
from app.tls import ensure_certificate
from tests.conftest import ADMIN_TOKEN


def test_discover_and_pair_flow(admin, pairing, store):
    res = admin.post("/admin/bridge/discover")
    assert res.status_code == 200
    bridges = res.json()["bridges"]
    assert bridges[0]["ip"] == "10.0.0.5"

    body = {"ip": bridges[0]["ip"], "id": bridges[0]["id"], "name": bridges[0]["name"]}
    first = admin.post("/admin/bridge/pair", json=body).json()
    assert first["status"] == "waiting"
    assert store.get_bridge() is None

    second = admin.post("/admin/bridge/pair", json=body).json()
    assert second["status"] == "paired"
    assert "username" not in second["bridge"]
    assert second["bridge"]["ip"] == "10.0.0.5"
    assert store.get_bridge()["username"] == "user-for-10.0.0.5"

    page = admin.get("/admin")
    assert "Paired with bridge" in page.text and "10.0.0.5" in page.text
    assert "Unpair / reset" in page.text


def test_pair_error_is_reported(admin, pairing):
    pairing.error = HueError("connection refused")
    res = admin.post("/admin/bridge/pair", json={"ip": "10.0.0.9"})
    assert res.status_code == 502 and "connection refused" in res.json()["detail"]


def test_unpair(admin, paired, store):
    assert admin.delete("/admin/bridge").json()["status"] == "unpaired"
    assert store.get_bridge() is None and store.get_selected_lights() == []


def test_lights_listing_and_selection(admin, paired, fake_hue, store):
    assert admin.get("/admin/lights").status_code == 200
    lights = admin.get("/admin/lights").json()["lights"]
    assert [l["id"] for l in lights] == ["1", "2", "3"]
    assert [l["selected"] for l in lights] == [True, True, False]
    assert lights[2]["reachable"] is False and lights[2]["supports_color"] is False

    res = admin.put("/admin/lights/selection", json={"light_ids": ["3", "1"]})
    assert res.json()["selected_lights"] == ["1", "3"]
    assert store.get_selected_lights() == ["1", "3"]

    fake_hue.fail = True
    assert admin.get("/admin/lights").status_code == 502


def test_lights_without_bridge(admin):
    assert admin.get("/admin/lights").status_code == 409


def test_settings_validation(admin, store):
    ok = admin.put("/admin/settings", json={"min_brightness": 10, "max_brightness": 200, "glow_period_seconds": 2})
    assert ok.status_code == 200 and store.get_settings()["max_brightness"] == 200
    bad = admin.put("/admin/settings", json={"min_brightness": 200, "max_brightness": 100, "glow_period_seconds": 2})
    assert bad.status_code == 422
    bad = admin.put("/admin/settings", json={"min_brightness": 0, "max_brightness": 100, "glow_period_seconds": 2})
    assert bad.status_code == 422


def test_test_alert_and_clear(admin, paired, fake_hue):
    res = admin.post("/admin/alert/test", json={"level": "amber", "mode": "dead", "duration_seconds": 30})
    assert res.status_code == 200
    assert res.json()["alert"]["source"] == "admin-test"
    assert admin.get("/admin/alert").json()["active"] is True
    assert fake_hue.calls_for("1")[0]["on"] is True
    res = admin.delete("/admin/alert")
    assert res.json()["status"] == "cleared" and admin.get("/admin/alert").json()["active"] is False


def test_test_alert_without_lights(admin, store):
    store.set_bridge(ip="10.0.0.5", username="u")
    assert admin.post("/admin/alert/test", json={"level": "amber", "mode": "glow"}).status_code == 409


@pytest.fixture
def tls_admin(tmp_path, store, fake_hue):
    settings = Settings(admin_token=ADMIN_TOKEN, session_secret="s", data_dir=str(tmp_path),
                        tls_enabled=True, tls_hostnames=("localhost", "10.1.2.3"), tls_cert_days=42)
    ensure_certificate(settings)
    app = create_app(settings=settings, store=store, client_factory=lambda b: fake_hue)
    with TestClient(app, follow_redirects=False) as client:
        client.post("/admin/login", data={"token": ADMIN_TOKEN})
        yield client, settings


def test_tls_endpoints_require_admin(client):
    assert client.get("/admin/tls").status_code == 401
    assert client.get("/admin/tls/certificate").status_code == 401


def test_tls_disabled_reports_and_hides_card(admin):
    assert admin.get("/admin/tls").json() == {"enabled": False}
    assert admin.get("/admin/tls/certificate").status_code == 404
    assert "HTTPS certificate" not in admin.get("/admin").text


def test_tls_info_and_download(tls_admin):
    client, settings = tls_admin
    info = client.get("/admin/tls").json()
    assert info["enabled"] is True and info["custom"] is False and info["self_signed"] is True
    assert set(info["sans"]) == {"localhost", "10.1.2.3"}
    assert 40 <= info["days_remaining"] <= 42
    assert len(info["fingerprint_sha256"]) == 95

    res = client.get("/admin/tls/certificate")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/x-pem-file")
    assert 'filename="hue-lights.crt"' in res.headers["content-disposition"]
    assert res.text.startswith("-----BEGIN CERTIFICATE-----")
    assert res.text == open(settings.tls_cert_file).read()

    page = client.get("/admin").text
    assert "HTTPS certificate" in page and info["fingerprint_sha256"] in page
    assert "Download certificate" in page and "--cacert" in page
