"""Local FastAPI server: SSE event stream, HTTP command intake and the LAN WebSocket fallback.

/events must never be compressed or buffered: no GZip middleware, and every frame is flushed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from command_queue import CommandRequest
from contracts import (
    ErrorMsg,
    ErrorPayload,
    PairDenied,
    PairDeniedPayload,
    PairGranted,
    PairGrantedPayload,
    PairRequest,
    QuickAction,
    dump_message,
    envelope,
    new_id,
    parse_message,
)
from event_bus import BusEvent
from guard import GuardMiddleware
from service import CommandService
from telemetry import Telemetry

log = logging.getLogger(__name__)

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# Maps a bearer/query token to a device id, or None when invalid.
TokenVerifier = Callable[[str], str | None]
# (room_secret, device_name) -> granted token, or None when the secret is wrong.
PairingFn = Callable[[str, str], PairGrantedPayload | None]
MAX_PAIR_FAILURES = 5


class ApiCommandBody(BaseModel):
    """Flat body for curl and the LAN PWA. Equivalent to a cmd.text / cmd.button envelope."""

    type: Literal["cmd.text", "cmd.button"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    text: str | None = Field(None, min_length=1, max_length=500)
    action: QuickAction | None = None
    command_id: str | None = None


def _bearer(request: Request, token: str | None = Query(None)) -> str:
    """FastAPI dependency (module level so annotations resolve): token -> device id."""
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else token
    authenticate: Callable[[str | None, str | None], str] = request.app.state.authenticate
    return authenticate(request.client.host if request.client else None, supplied)


DeviceId = Annotated[str, Depends(_bearer)]


def _require_local(request: Request) -> None:
    """Owner-only actions: the request must really come from this PC (tokens are not enough)."""
    host = request.client.host if request.client else None
    if host not in LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="only available on the PC itself")


Local = Annotated[None, Depends(_require_local)]  # module level so FastAPI can resolve it


def _revokes(event: BusEvent, device_id: str) -> bool:
    """True if this bus event is the owner revoking exactly this device."""
    if event.type != "device.revoke":
        return False
    try:
        return bool(json.loads(event.data)["payload"]["device_id"] == device_id)
    except (ValueError, KeyError, TypeError):
        return False


def create_app(
    service: CommandService,
    telemetry: Telemetry,
    *,
    token_verifier: TokenVerifier | None = None,
    pairing: PairingFn | None = None,
    trust_loopback: bool = True,
    allowed_origins: list[str] | None = None,
    keepalive_s: float = 15.0,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="CouchJarvis daemon", docs_url=None, redoc_url=None)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["authorization", "content-type", "last-event-id"],
        )
    # Outermost layer (added last): reject cross-site and DNS-rebinding traffic first.
    app.add_middleware(GuardMiddleware, allowed_origins=allowed_origins or [])
    bus = service.bus

    def authenticate(host: str | None, token: str | None) -> str:
        """Loopback is trusted; everyone else needs a verified device token."""
        if token and token_verifier is not None:
            device = token_verifier(token)
            if device is not None:
                return device
        if trust_loopback and host in LOOPBACK_HOSTS:
            return "local"
        raise HTTPException(status_code=401, detail="invalid or missing token")

    app.state.authenticate = authenticate

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "queue_depth": service.queue.depth, "rev": service.state.state.rev}

    @app.get("/api/devices")
    async def devices(_: Local) -> dict[str, object]:
        state = service.state.state
        return {
            "devices": [d.model_dump() for d in state.devices],
            "revoked": sorted(service.revoked),
        }

    @app.post("/api/devices/{device_id}/revoke")
    async def revoke(device_id: str, _: Local) -> dict[str, object]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id):
            raise HTTPException(status_code=422, detail="bad device id")
        service.revoke_device(device_id)
        return {"revoked": device_id}

    @app.get("/api/metrics")
    async def metrics(_: DeviceId) -> dict[str, object]:
        return telemetry.rollup()

    @app.post("/api/command", status_code=202)
    async def command(body: ApiCommandBody, device: DeviceId) -> dict[str, object]:
        command_id = body.command_id or new_id()
        if body.type == "cmd.text":
            if not body.text:
                raise HTTPException(422, "text is required for cmd.text")
            req = CommandRequest(
                command_id=command_id,
                idempotency_key=body.idempotency_key,
                device_id=device,
                kind="text",
                text=body.text,
            )
        else:
            if body.action is None:
                raise HTTPException(422, "action is required for cmd.button")
            req = CommandRequest(
                command_id=command_id,
                idempotency_key=body.idempotency_key,
                device_id=device,
                kind="button",
                action=body.action,
            )
        ack = service.submit(req)  # publishes cmd.ack so SSE/WS listeners see it too
        return {"command_id": command_id, "status": ack.status, "position": ack.position}

    @app.get("/events")
    async def events(request: Request, device: DeviceId) -> StreamingResponse:
        last_header = request.headers.get("last-event-id", "")
        resume_from = int(last_header) if last_header.isdigit() else None

        async def stream() -> AsyncIterator[str]:
            async with bus.subscribe() as queue:
                snapshot = service.state.snapshot_message()
                seen = bus.last_seq  # the snapshot already reflects everything up to here
                yield BusEvent(seen, "state.snapshot", dump_message(snapshot)).to_sse()
                if resume_from is not None:  # only a reconnecting client wants history
                    seen = resume_from
                    for old in bus.replay_since(resume_from):
                        seen = old.seq
                        yield old.to_sse()
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), keepalive_s)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if _revokes(event, device):
                        return  # this phone was removed: end its stream
                    if bus.consume_lag(queue):  # this client fell behind: events were dropped
                        fresh = service.state.snapshot_message()
                        yield BusEvent(bus.last_seq, "state.snapshot", dump_message(fresh)).to_sse()
                    if event.seq > seen:
                        seen = event.seq
                        yield event.to_sse()

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.websocket("/ws")
    async def lan_ws(ws: WebSocket) -> None:
        host = ws.client.host if ws.client else None
        device_id: str | None = None
        if pairing is not None and ws.query_params.get("pair") == "1":
            # An explicit ?pair=1 always runs the pairing flow, even from loopback: a browser on
            # the PC itself must be able to pair like any phone.
            pass
        else:
            try:
                device_id = authenticate(host, ws.query_params.get("token"))
            except HTTPException:
                await ws.close(code=4401)
                return
        await ws.accept()
        name = ws.query_params.get("name", "LAN phone")[:64]
        pump_task: asyncio.Task[None] | None = None
        failures = 0

        async def pump() -> None:
            async with bus.subscribe() as queue:
                await ws.send_text(dump_message(service.state.snapshot_message()))
                while True:
                    event = await queue.get()
                    if device_id is not None and _revokes(event, device_id):
                        await ws.close(code=4403)  # removed by the PC owner
                        return
                    if bus.consume_lag(queue):  # missed events: resend full state first
                        await ws.send_text(dump_message(service.state.snapshot_message()))
                    await ws.send_text(event.data)

        def start_session(did: str) -> None:
            nonlocal pump_task
            service.device_connected(did, name)
            pump_task = asyncio.create_task(pump())

        async def send_error(code: str, message: str, reply_to: str | None = None) -> None:
            payload = ErrorPayload(code=code, message=message)
            err = envelope(ErrorMsg, service.room_id, payload, reply_to=reply_to)
            await ws.send_text(dump_message(err))

        if device_id is not None:
            start_session(device_id)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = parse_message(raw)
                except ValidationError:
                    await send_error("bad_message", "Invalid envelope")
                    continue
                if device_id is None:
                    # An unpaired socket may only prove the QR secret; nothing else is served.
                    if not isinstance(msg, PairRequest) or pairing is None:
                        await send_error("unauthorized", "Pair this device first", msg.id)
                        continue
                    granted = pairing(msg.payload.room_secret, msg.payload.device_name)
                    if granted is None:
                        failures += 1
                        denied = PairDeniedPayload(reason="invalid_secret")
                        denial = envelope(PairDenied, service.room_id, denied, reply_to=msg.id)
                        await ws.send_text(dump_message(denial))
                        if failures >= MAX_PAIR_FAILURES:
                            await ws.close(code=4403)
                            return
                        continue
                    device_id, name = granted.device_id, msg.payload.device_name
                    ok = envelope(PairGranted, service.room_id, granted, reply_to=msg.id)
                    await ws.send_text(dump_message(ok))
                    start_session(device_id)
                    continue
                if device_id in service.revoked:
                    await ws.close(code=4403)
                    return
                for reply in await service.handle_envelope(msg, device_id):
                    await ws.send_text(dump_message(reply))
        except WebSocketDisconnect:
            pass
        finally:
            if pump_task is not None:
                pump_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pump_task
            if device_id is not None:
                service.device_disconnected(device_id)

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="pwa")
    return app
