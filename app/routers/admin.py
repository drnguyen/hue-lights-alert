"""Admin page and its JSON endpoints. Auth: signed session cookie issued after ADMIN_TOKEN login."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app import hue_client
from app.alerts import AlertError
from app.auth import (SESSION_COOKIE, check_admin_token, current_admin, issue_session,
                      require_admin, session_max_age)
from app.hue_client import HueError, LinkButtonNotPressed
from app.tls import certificate_info
from app.schemas import (AlertResponse, AlertState, LightSelection, PairRequest, SettingsUpdate,
                         TestAlertRequest, TokenCreate, TokenCreated)

router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)


def _templates(request: Request):
    return request.app.state.templates


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ---- pages ----------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_admin(request):
        return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    return _templates(request).TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, token: str = Form(default="")):
    if not check_admin_token(request, token):
        return _templates(request).TemplateResponse(
            request, "login.html", {"error": "Invalid admin token."}, status_code=status.HTTP_401_UNAUTHORIZED)
    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE, issue_session(request), max_age=session_max_age(request),
        httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request):
    if not current_admin(request):
        return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    store = request.app.state.store
    return _templates(request).TemplateResponse(request, "admin.html", {
        "bridge": store.get_bridge(),
        "selected_lights": store.get_selected_lights(),
        "settings": store.get_settings(),
        "tokens": store.list_tokens(),
        "alert": request.app.state.engine.status().model_dump(),
        "base_url": _base_url(request),
        "tls": _tls_info(request),
    })


# ---- bridge -----------------------------------------------------------------------

@router.post("/bridge/discover", dependencies=[Depends(require_admin)])
async def discover(request: Request) -> dict[str, Any]:
    timeout = request.app.state.settings.discovery_timeout_seconds
    bridges = await asyncio.to_thread(request.app.state.discover_bridges, timeout)
    return {"bridges": bridges}


@router.post("/bridge/pair", dependencies=[Depends(require_admin)])
async def pair(body: PairRequest, request: Request) -> dict[str, Any]:
    """One pairing attempt. The browser polls this until the link button has been pressed."""
    try:
        username = await asyncio.to_thread(request.app.state.pair_bridge, body.ip)
    except LinkButtonNotPressed:
        return {"status": "waiting", "message": "Press the link button on the Hue Bridge."}
    except HueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    bridge = request.app.state.store.set_bridge(ip=body.ip, username=username, bridge_id=body.id, name=body.name)
    return {"status": "paired", "bridge": _public_bridge(bridge)}


@router.delete("/bridge", dependencies=[Depends(require_admin)])
async def unpair(request: Request) -> dict[str, Any]:
    await request.app.state.engine.clear()
    request.app.state.store.clear_bridge()
    return {"status": "unpaired"}


# ---- lights & settings ------------------------------------------------------------

@router.get("/lights", dependencies=[Depends(require_admin)])
async def lights(request: Request) -> dict[str, Any]:
    store = request.app.state.store
    bridge = store.get_bridge()
    if not bridge:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No bridge paired")
    client = request.app.state.client_factory(bridge)
    try:
        found = await asyncio.to_thread(client.list_lights)
    except HueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    selected = set(store.get_selected_lights())
    for light in found:
        light["selected"] = light["id"] in selected
    return {"lights": found}


@router.put("/lights/selection", dependencies=[Depends(require_admin)])
def save_selection(body: LightSelection, request: Request) -> dict[str, Any]:
    return {"selected_lights": request.app.state.store.set_selected_lights(body.light_ids)}


@router.put("/settings", dependencies=[Depends(require_admin)])
def save_settings(body: SettingsUpdate, request: Request) -> dict[str, Any]:
    return {"settings": request.app.state.store.update_settings(**body.model_dump())}


# ---- test alert -------------------------------------------------------------------

@router.get("/alert", dependencies=[Depends(require_admin)], response_model=AlertState)
def alert_status(request: Request) -> AlertState:
    return request.app.state.engine.status()


@router.post("/alert/test", dependencies=[Depends(require_admin)], response_model=AlertResponse)
async def test_alert(body: TestAlertRequest, request: Request) -> AlertResponse:
    try:
        state = await request.app.state.engine.start(body.level, body.mode, body.duration_seconds, source="admin-test")
    except AlertError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AlertResponse(status="started", alert=state)


@router.delete("/alert", dependencies=[Depends(require_admin)], response_model=AlertResponse)
async def clear_alert(request: Request) -> AlertResponse:
    cleared = await request.app.state.engine.clear()
    return AlertResponse(status="cleared" if cleared else "no_alert", alert=request.app.state.engine.status())


# ---- API tokens -------------------------------------------------------------------

@router.get("/tokens", dependencies=[Depends(require_admin)])
def list_tokens(request: Request) -> dict[str, Any]:
    return {"tokens": request.app.state.store.list_tokens()}


@router.post("/tokens", dependencies=[Depends(require_admin)], response_model=TokenCreated,
             status_code=status.HTTP_201_CREATED)
def create_token(body: TokenCreate, request: Request) -> TokenCreated:
    plaintext, record = request.app.state.store.create_token(body.name)
    return TokenCreated(token=plaintext, record=record)


@router.delete("/tokens/{token_id}", dependencies=[Depends(require_admin)])
def revoke_token(token_id: str, request: Request) -> dict[str, Any]:
    if not request.app.state.store.revoke_token(token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return {"status": "revoked"}


# ---- TLS certificate ----------------------------------------------------------------

def _tls_info(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.tls_enabled:
        return {"enabled": False}
    try:
        info = certificate_info(settings.tls_cert_file)
    except (OSError, ValueError) as exc:
        return {"enabled": True, "error": f"Certificate could not be read: {exc}"}
    return {"enabled": True, "custom": settings.tls_custom_paths, **info}


@router.get("/tls", dependencies=[Depends(require_admin)])
def tls_info(request: Request) -> dict[str, Any]:
    return _tls_info(request)


@router.get("/tls/certificate", dependencies=[Depends(require_admin)])
def tls_certificate(request: Request):
    """Download the server certificate (PEM) so clients can trust it."""
    settings = request.app.state.settings
    if not settings.tls_enabled or not os.path.isfile(settings.tls_cert_file):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TLS is disabled or no certificate exists")
    return FileResponse(settings.tls_cert_file, media_type="application/x-pem-file", filename="hue-lights.crt")


def _public_bridge(bridge: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in bridge.items() if k != "username"}
