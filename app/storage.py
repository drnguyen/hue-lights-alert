"""Single-file JSON store for bridge pairing, light selection, levels and API tokens."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "min_brightness": 30,
    "max_brightness": 254,
    "glow_period_seconds": 1.5,
}

_EMPTY: dict[str, Any] = {
    "bridge": None,
    "selected_lights": [],
    "settings": dict(DEFAULT_SETTINGS),
    "tokens": [],
}

TOKEN_PREFIX = "hue_"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = deepcopy(_EMPTY)
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        data = deepcopy(_EMPTY)
        data.update({k: v for k, v in raw.items() if k in data})
        merged_settings = dict(DEFAULT_SETTINGS)
        merged_settings.update(data.get("settings") or {})
        data["settings"] = merged_settings
        self._data = data

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)

    # ---- bridge --------------------------------------------------------
    def get_bridge(self) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._data["bridge"])

    def set_bridge(self, ip: str, username: str, bridge_id: str | None = None,
                   name: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._data["bridge"] = {
                "ip": ip,
                "id": bridge_id,
                "name": name,
                "username": username,
                "paired_at": _now(),
            }
            self._data["selected_lights"] = []
            self._save()
            return deepcopy(self._data["bridge"])

    def clear_bridge(self) -> None:
        with self._lock:
            self._data["bridge"] = None
            self._data["selected_lights"] = []
            self._save()

    # ---- lights ----------------------------------------------------------
    def get_selected_lights(self) -> list[str]:
        with self._lock:
            return list(self._data["selected_lights"])

    def set_selected_lights(self, light_ids: list[str]) -> list[str]:
        with self._lock:
            self._data["selected_lights"] = sorted(set(str(i) for i in light_ids), key=_sort_key)
            self._save()
            return list(self._data["selected_lights"])

    # ---- settings --------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data["settings"])

    def update_settings(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            for key, value in values.items():
                if key in DEFAULT_SETTINGS and value is not None:
                    self._data["settings"][key] = value
            self._save()
            return deepcopy(self._data["settings"])

    # ---- API tokens ------------------------------------------------------
    def list_tokens(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_token(t) for t in self._data["tokens"]]

    def create_token(self, name: str) -> tuple[str, dict[str, Any]]:
        """Create a token. Returns (plaintext, public record). Plaintext is never stored."""
        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = {
            "id": secrets.token_hex(8),
            "name": name.strip() or "unnamed",
            "prefix": plaintext[: len(TOKEN_PREFIX) + 6],
            "hash": _hash_token(plaintext),
            "created_at": _now(),
            "last_used_at": None,
        }
        with self._lock:
            self._data["tokens"].append(record)
            self._save()
        return plaintext, self._public_token(record)

    def revoke_token(self, token_id: str) -> bool:
        with self._lock:
            before = len(self._data["tokens"])
            self._data["tokens"] = [t for t in self._data["tokens"] if t["id"] != token_id]
            changed = len(self._data["tokens"]) != before
            if changed:
                self._save()
            return changed

    def verify_token(self, plaintext: str) -> dict[str, Any] | None:
        """Return the public token record if plaintext matches a stored token."""
        if not plaintext:
            return None
        digest = _hash_token(plaintext)
        with self._lock:
            for record in self._data["tokens"]:
                if secrets.compare_digest(record["hash"], digest):
                    record["last_used_at"] = _now()
                    self._save()
                    return self._public_token(record)
        return None

    @staticmethod
    def _public_token(record: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in record.items() if k != "hash"}


def _sort_key(value: str):
    return (0, int(value)) if value.isdigit() else (1, value)
