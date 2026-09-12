from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.hue_client import HueError, LinkButtonNotPressed
from app.main import create_app
from app.storage import Store

ADMIN_TOKEN = "test-admin-token"


class FakeHueClient:
    """In-memory stand-in for the bridge. Records every state PUT."""

    def __init__(self):
        self.lights: dict[str, dict[str, Any]] = {
            "1": {"name": "Desk", "type": "Extended color light",
                  "state": {"on": True, "bri": 100, "xy": [0.3, 0.3], "colormode": "xy", "reachable": True}},
            "2": {"name": "Shelf", "type": "Extended color light",
                  "state": {"on": False, "bri": 254, "ct": 300, "colormode": "ct", "reachable": True}},
            "3": {"name": "Hall", "type": "Dimmable light",
                  "state": {"on": True, "bri": 50, "reachable": False}},
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    def list_lights(self):
        if self.fail:
            raise HueError("bridge unreachable")
        out = []
        for lid, info in self.lights.items():
            s = info["state"]
            out.append({"id": lid, "name": info["name"], "type": info["type"], "on": s["on"],
                        "reachable": s.get("reachable", True), "supports_color": "xy" in s or "hue" in s})
        return out

    def get_light_states(self, light_ids):
        if self.fail:
            raise HueError("bridge unreachable")
        return {lid: copy.deepcopy(self.lights[lid]["state"]) for lid in light_ids if lid in self.lights}

    def set_state(self, light_id, body):
        if self.fail:
            raise HueError("bridge unreachable")
        self.calls.append((light_id, dict(body)))
        self.lights[light_id]["state"].update({k: v for k, v in body.items() if k != "transitiontime"})

    def calls_for(self, light_id):
        return [b for lid, b in self.calls if lid == light_id]


class FakePairing:
    def __init__(self):
        self.attempts = 0
        self.succeed_after = 2
        self.error: Exception | None = None

    def __call__(self, ip):
        self.attempts += 1
        if self.error:
            raise self.error
        if self.attempts < self.succeed_after:
            raise LinkButtonNotPressed("link button not pressed")
        return f"user-for-{ip}"


@pytest.fixture
def settings(tmp_path):
    return Settings(admin_token=ADMIN_TOKEN, session_secret="unit-test-secret", session_ttl_hours=1,
                    data_dir=str(tmp_path), discovery_timeout_seconds=1, tls_enabled=False)


@pytest.fixture
def store(settings):
    return Store(settings.config_path)


@pytest.fixture
def fake_hue():
    return FakeHueClient()


@pytest.fixture
def pairing():
    return FakePairing()


@pytest.fixture
def app(settings, store, fake_hue, pairing):
    return create_app(
        settings=settings, store=store,
        client_factory=lambda bridge: fake_hue,
        discover_bridges=lambda timeout: [{"ip": "10.0.0.5", "id": "001788fffe123456", "name": "Philips hue", "source": "mdns"}],
        pair_bridge=pairing,
    )


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def admin(client):
    """Client logged in to the admin page."""
    res = client.post("/admin/login", data={"token": ADMIN_TOKEN})
    assert res.status_code == 303
    return client


@pytest.fixture
def paired(store):
    store.set_bridge(ip="10.0.0.5", username="hue-user", bridge_id="001788fffe123456", name="Philips hue")
    store.set_selected_lights(["1", "2"])
    return store


@pytest.fixture
def api_token(admin, paired):
    res = admin.post("/admin/tokens", json={"name": "monitoring"})
    assert res.status_code == 201
    return res.json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}
