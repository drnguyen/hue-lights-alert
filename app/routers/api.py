"""Public alert API used by other applications. Auth: Authorization: Bearer <token>."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.alerts import AlertError
from app.auth import require_api_token
from app.schemas import AlertRequest, AlertResponse, AlertState, ErrorResponse, HealthResponse

router = APIRouter(prefix="/api/v1", tags=["alerts"])

_AUTH_RESPONSES = {401: {"model": ErrorResponse, "description": "Invalid or missing API token"}}


@router.get("/health", response_model=HealthResponse, summary="Liveness and pairing status (no auth)")
def health(request: Request) -> HealthResponse:
    store = request.app.state.store
    return HealthResponse(
        status="ok",
        bridge_paired=store.get_bridge() is not None,
        selected_lights=len(store.get_selected_lights()),
        alert_active=request.app.state.engine.status().active,
    )


@router.get("/alert", response_model=AlertState, responses=_AUTH_RESPONSES,
            summary="Get the current alert state")
def get_alert(request: Request, _token=Depends(require_api_token)) -> AlertState:
    return request.app.state.engine.status()


@router.post("/alert", response_model=AlertResponse,
             responses={**_AUTH_RESPONSES, 409: {"model": ErrorResponse, "description": "No bridge paired or no lights selected"}},
             summary="Raise an alert on the selected lights")
async def post_alert(body: AlertRequest, request: Request, token=Depends(require_api_token)) -> AlertResponse:
    """Start an alert. A new alert replaces any running one.

    - **level**: `amber` or `red`
    - **mode**: `glow` (brightness pulses between the configured min and max) or `dead` (static colour)
    - **duration_seconds**: optional auto-clear; omit to keep the alert until `DELETE /api/v1/alert`
    """
    try:
        state = await request.app.state.engine.start(
            body.level, body.mode, body.duration_seconds, source=f"api:{token['name']}")
    except AlertError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AlertResponse(status="started", alert=state)


@router.delete("/alert", response_model=AlertResponse, responses=_AUTH_RESPONSES,
               summary="Clear the current alert and restore the lights")
async def delete_alert(request: Request, _token=Depends(require_api_token)) -> AlertResponse:
    cleared = await request.app.state.engine.clear()
    return AlertResponse(status="cleared" if cleared else "no_alert", alert=request.app.state.engine.status())
