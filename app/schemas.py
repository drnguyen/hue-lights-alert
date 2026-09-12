"""Pydantic models shared by the public API and the admin endpoints."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class AlertLevel(str, Enum):
    amber = "amber"
    red = "red"


class AlertMode(str, Enum):
    glow = "glow"
    dead = "dead"


class AlertRequest(BaseModel):
    level: AlertLevel = Field(description="Alert severity: amber or red")
    mode: AlertMode = Field(description="glow = pulsing brightness, dead = static colour")
    duration_seconds: Optional[int] = Field(
        default=None, ge=1, le=86400,
        description="Auto-clear after this many seconds. Omit to keep the alert until DELETE.",
    )

    model_config = {"json_schema_extra": {"examples": [{"level": "red", "mode": "glow", "duration_seconds": 60}]}}


class AlertState(BaseModel):
    active: bool
    level: Optional[AlertLevel] = None
    mode: Optional[AlertMode] = None
    started_at: Optional[str] = None
    expires_at: Optional[str] = None
    light_ids: list[str] = []
    source: Optional[str] = None


class AlertResponse(BaseModel):
    status: str
    alert: AlertState


class HealthResponse(BaseModel):
    status: str
    bridge_paired: bool
    selected_lights: int
    alert_active: bool


class ErrorResponse(BaseModel):
    detail: str


# ---- admin models ---------------------------------------------------------------

class PairRequest(BaseModel):
    ip: str = Field(min_length=1)
    id: Optional[str] = None
    name: Optional[str] = None


class LightSelection(BaseModel):
    light_ids: list[str]


class SettingsUpdate(BaseModel):
    min_brightness: int = Field(ge=1, le=254)
    max_brightness: int = Field(ge=1, le=254)
    glow_period_seconds: float = Field(ge=0.4, le=30)

    @model_validator(mode="after")
    def _check_range(self) -> "SettingsUpdate":
        if self.min_brightness >= self.max_brightness:
            raise ValueError("min_brightness must be lower than max_brightness")
        return self


class TestAlertRequest(BaseModel):
    level: AlertLevel
    mode: AlertMode
    duration_seconds: int = Field(default=10, ge=1, le=600)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class TokenCreated(BaseModel):
    token: str
    record: dict[str, Any]
