"""Thin wrapper around huesdk.

huesdk's Light.on()/off() helpers are gated by cached state, so this module talks
to the bridge through Hue.get/Hue.put with explicit state bodies.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Protocol

import urllib3
from huesdk import Discover, Hue
from huesdk.generics import hexa_to_xy

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger(__name__)


class HueError(Exception):
    """Generic bridge error."""


class LinkButtonNotPressed(HueError):
    """Pairing attempted before the physical link button was pressed."""


class HueClient(Protocol):
    def list_lights(self) -> list[dict[str, Any]]: ...
    def get_light_states(self, light_ids: list[str]) -> dict[str, dict[str, Any]]: ...
    def set_state(self, light_id: str, body: dict[str, Any]) -> None: ...


ClientFactory = Callable[[dict[str, Any]], HueClient]


def color_xy(hexa: str) -> list[float]:
    x, y = hexa_to_xy(hexa)
    return [round(x, 4), round(y, 4)]


# ---- discovery & pairing ----------------------------------------------------

def discover_bridges(mdns_timeout: int = 5) -> list[dict[str, Any]]:
    """Find bridges via mDNS and Hue's cloud discovery. Merged and de-duplicated by id/ip."""
    found: dict[str, dict[str, Any]] = {}
    discover = Discover()

    try:
        for item in json.loads(discover.find_hue_bridge_mdns(timeout=mdns_timeout)):
            ip = item.get("internalipaddress")
            if ip:
                found[ip] = {"ip": ip, "id": item.get("id"), "name": item.get("name"), "source": "mdns"}
    except Exception as exc:  # zeroconf / socket errors
        log.warning("mDNS discovery failed: %s", exc)

    try:
        for item in json.loads(discover.find_hue_bridge()):
            ip = item.get("internalipaddress")
            if ip and ip not in found:
                found[ip] = {"ip": ip, "id": item.get("id"), "name": item.get("name"), "source": "cloud"}
    except Exception as exc:  # network / rate-limit errors
        log.warning("Cloud discovery failed: %s", exc)

    return list(found.values())


def pair_bridge(ip: str) -> str:
    """Attempt one pairing round. Returns the Hue username on success."""
    try:
        username = Hue.connect(ip)
    except Exception as exc:
        message = str(exc)
        if "link button" in message.lower():
            raise LinkButtonNotPressed(message) from exc
        raise HueError(message) from exc
    if not username:
        raise HueError("Bridge returned no username")
    return username


# ---- real client -------------------------------------------------------------

class HuesdkClient:
    def __init__(self, ip: str, username: str):
        self._hue = Hue(bridge_ip=ip, username=username)
        self._username = username

    def _raw_lights(self) -> dict[str, Any]:
        try:
            result = self._hue.get(f"/{self._username}/lights")
        except Exception as exc:
            raise HueError(f"Could not read lights from bridge: {exc}") from exc
        if not isinstance(result, dict):
            raise HueError(f"Unexpected response from bridge: {result!r}")
        return result

    def list_lights(self) -> list[dict[str, Any]]:
        lights = []
        for light_id, info in self._raw_lights().items():
            state = info.get("state", {})
            lights.append({
                "id": str(light_id),
                "name": info.get("name", f"Light {light_id}"),
                "type": info.get("type"),
                "on": bool(state.get("on", False)),
                "reachable": bool(state.get("reachable", True)),
                "supports_color": "xy" in state or "hue" in state,
            })
        lights.sort(key=lambda l: (0, int(l["id"])) if l["id"].isdigit() else (1, l["id"]))
        return lights

    def get_light_states(self, light_ids: list[str]) -> dict[str, dict[str, Any]]:
        raw = self._raw_lights()
        return {lid: dict(raw[lid].get("state", {})) for lid in light_ids if lid in raw}

    def set_state(self, light_id: str, body: dict[str, Any]) -> None:
        try:
            response = self._hue.put(f"/{self._username}/lights/{light_id}/state", json.dumps(body))
            payload = response.json()
        except Exception as exc:
            raise HueError(f"Could not update light {light_id}: {exc}") from exc
        errors = [item["error"] for item in payload if isinstance(item, dict) and "error" in item]
        if errors:
            raise HueError(f"Bridge rejected update for light {light_id}: {errors[0].get('description', errors[0])}")


def default_client_factory(bridge: dict[str, Any]) -> HueClient:
    return HuesdkClient(ip=bridge["ip"], username=bridge["username"])


# ---- state helpers -------------------------------------------------------------

def restore_body(state: dict[str, Any]) -> dict[str, Any]:
    """Build a PUT body that returns a light to a previously captured state."""
    if not state.get("on", False):
        return {"on": False, "transitiontime": 4}
    body: dict[str, Any] = {"on": True, "transitiontime": 4}
    if "bri" in state:
        body["bri"] = state["bri"]
    mode = state.get("colormode")
    if mode == "xy" and "xy" in state:
        body["xy"] = state["xy"]
    elif mode == "ct" and "ct" in state:
        body["ct"] = state["ct"]
    elif mode == "hs" and "hue" in state and "sat" in state:
        body["hue"] = state["hue"]
        body["sat"] = state["sat"]
    elif "xy" in state:
        body["xy"] = state["xy"]
    return body
