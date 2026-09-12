"""Alert engine: drives the selected lights in glow (pulsing) or dead (static) mode."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.hue_client import ClientFactory, HueClient, HueError, color_xy, restore_body
from app.schemas import AlertLevel, AlertMode, AlertState
from app.storage import Store

log = logging.getLogger(__name__)

LEVEL_COLORS = {
    AlertLevel.amber: "#FFBF00",
    AlertLevel.red: "#FF0000",
}


class AlertError(Exception):
    """Alert could not be started because of configuration (maps to HTTP 409)."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class AlertEngine:
    def __init__(self, store: Store, client_factory: ClientFactory):
        self._store = store
        self._client_factory = client_factory
        self._lock = asyncio.Lock()
        self._current: Optional[dict[str, Any]] = None
        self._snapshot: Optional[dict[str, dict[str, Any]]] = None
        self._glow_task: Optional[asyncio.Task] = None
        self._timer_task: Optional[asyncio.Task] = None

    # ---- public API ------------------------------------------------------
    def status(self) -> AlertState:
        if self._current is None:
            return AlertState(active=False)
        return AlertState(active=True, **self._current)

    async def start(self, level: AlertLevel, mode: AlertMode,
                    duration_seconds: Optional[int] = None, source: str = "api") -> AlertState:
        bridge = self._store.get_bridge()
        if not bridge:
            raise AlertError("No Hue Bridge is paired. Pair one in the admin page first.")
        light_ids = self._store.get_selected_lights()
        if not light_ids:
            raise AlertError("No lights selected for alerts. Select lights in the admin page first.")

        async with self._lock:
            client = self._client_factory(bridge)
            await self._cancel_tasks()

            if self._snapshot is None:
                try:
                    self._snapshot = await asyncio.to_thread(client.get_light_states, light_ids)
                except HueError as exc:
                    log.warning("Could not snapshot light state: %s", exc)
                    self._snapshot = {}

            settings = self._store.get_settings()
            xy = color_xy(LEVEL_COLORS[level])
            max_bri = int(settings["max_brightness"])
            min_bri = int(settings["min_brightness"])
            period = float(settings["glow_period_seconds"])

            await self._apply(client, light_ids, {"on": True, "xy": xy, "bri": max_bri, "transitiontime": 0})

            started = _now()
            expires = started + timedelta(seconds=duration_seconds) if duration_seconds else None
            self._current = {
                "level": level, "mode": mode, "source": source, "light_ids": list(light_ids),
                "started_at": started.isoformat(),
                "expires_at": expires.isoformat() if expires else None,
            }

            if mode == AlertMode.glow:
                self._glow_task = asyncio.create_task(
                    self._glow_loop(client, light_ids, min_bri, max_bri, period), name="hue-glow")
            if duration_seconds:
                self._timer_task = asyncio.create_task(self._auto_clear(duration_seconds), name="hue-alert-timer")

            return self.status()

    async def clear(self) -> bool:
        async with self._lock:
            return await self._clear_locked()

    async def shutdown(self) -> None:
        try:
            await asyncio.wait_for(self.clear(), timeout=5)
        except Exception as exc:  # pragma: no cover - best effort on shutdown
            log.warning("Alert clear on shutdown failed: %s", exc)

    # ---- internals ---------------------------------------------------------
    async def _clear_locked(self) -> bool:
        if self._current is None:
            return False
        await self._cancel_tasks()
        light_ids = self._current["light_ids"]
        snapshot = self._snapshot or {}
        self._current = None
        self._snapshot = None

        bridge = self._store.get_bridge()
        if bridge:
            client = self._client_factory(bridge)
            for lid in light_ids:
                body = restore_body(snapshot[lid]) if lid in snapshot else {"on": False, "transitiontime": 4}
                await self._safe_put(client, lid, body)
        return True

    async def _cancel_tasks(self) -> None:
        for task in (self._glow_task, self._timer_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._glow_task = None
        self._timer_task = None

    async def _apply(self, client: HueClient, light_ids: list[str], body: dict[str, Any]) -> None:
        for lid in light_ids:
            await self._safe_put(client, lid, body)

    @staticmethod
    async def _safe_put(client: HueClient, light_id: str, body: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(client.set_state, light_id, body)
        except HueError as exc:
            log.warning("Light %s update failed: %s", light_id, exc)

    async def _glow_loop(self, client: HueClient, light_ids: list[str],
                         min_bri: int, max_bri: int, period: float) -> None:
        half = max(period / 2, 0.2)
        transition = max(int(half * 10) - 1, 0)  # deciseconds, finish slightly before the next step
        target = min_bri
        while True:
            await self._apply(client, light_ids, {"bri": target, "transitiontime": transition})
            target = max_bri if target == min_bri else min_bri
            await asyncio.sleep(half)

    async def _auto_clear(self, seconds: int) -> None:
        await asyncio.sleep(seconds)
        async with self._lock:
            self._timer_task = None
            await self._clear_locked()
