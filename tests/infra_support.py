"""Shared infra-test helpers (importable as ``infra_support``; tests/ is on pythonpath)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from common import auth
from common.ddb import Store

ROOM = "room-abcdef123"
PC_KEY = "pc-key-secret-value"
ROOM_SECRET = "room-secret-in-qr"
PC_HEADERS = {
    "Authorization": f"Bearer {PC_KEY}",
    "X-Room-Secret-Hash": auth.sha256_hex(ROOM_SECRET),
}


class FakeApiClient:
    """Stands in for the apigatewaymanagementapi client."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.gone: set[str] = set()
        self.closed: list[str] = []

    def post_to_connection(self, ConnectionId: str, Data: bytes) -> None:  # noqa: N803
        if ConnectionId in self.gone:
            raise ClientError({"Error": {"Code": "GoneException"}}, "PostToConnection")
        self.sent.append((ConnectionId, json.loads(Data.decode())))

    def delete_connection(self, ConnectionId: str) -> None:  # noqa: N803
        self.closed.append(ConnectionId)

    def to(self, connection_id: str, type_: str | None = None) -> list[dict[str, Any]]:
        return [
            m for c, m in self.sent if c == connection_id and (type_ is None or m["type"] == type_)
        ]


@dataclass
class World:
    store: Store
    api: FakeApiClient
    clock: list[float] = field(default_factory=lambda: [time.time()])

    # -------- event builders
    @staticmethod
    def event(connection_id: str, **extra: Any) -> dict[str, Any]:
        return {
            "requestContext": {
                "connectionId": connection_id,
                "domainName": "x.test",
                "stage": "prod",
            },
            **extra,
        }

    def connect_pc(
        self, cid: str = "pc-1", headers: dict[str, str] | None = None, room: str = ROOM
    ) -> dict[str, Any]:
        import connect_handler

        ev = self.event(
            cid,
            queryStringParameters={"room": room, "role": "pc"},
            headers=PC_HEADERS if headers is None else headers,
        )
        return connect_handler.handler(ev, None)

    def connect_phone(self, cid: str, **query: str) -> dict[str, Any]:
        import connect_handler

        ev = self.event(cid, queryStringParameters={"room": ROOM, **query}, headers={})
        return connect_handler.handler(ev, None)

    def send(self, cid: str, message: dict[str, Any] | str) -> dict[str, Any]:
        import relay_router

        body = message if isinstance(message, str) else json.dumps(message)
        return relay_router.handler(self.event(cid, body=body), None)

    def disconnect(self, cid: str) -> dict[str, Any]:
        import disconnect_handler

        return disconnect_handler.handler(self.event(cid), None)

    # -------- scenario helpers
    def pair_phone(self, cid: str, name: str = "Pixel") -> dict[str, Any]:
        """Connect with pair=1, send pair.request, return the pair.granted payload."""
        assert self.connect_phone(cid, pair="1")["statusCode"] == 200
        self.send(cid, msg("pair.request", {"room_secret": ROOM_SECRET, "device_name": name}))
        granted = self.api.to(cid, "pair.granted")
        assert granted, "pairing failed"
        payload: dict[str, Any] = granted[-1]["payload"]
        return payload

    def room_key(self) -> str:
        return auth.derive_room_key(self.store.master_key(), ROOM)


def msg(type_: str, payload: dict[str, Any], id_: str = "m1", **extra: Any) -> dict[str, Any]:
    return {
        "v": 1,
        "type": type_,
        "id": id_,
        "ts": 1.0,
        "room_id": ROOM,
        "from": "client",
        "reply_to": None,
        "payload": payload,
        **extra,
    }


def button(key: str = "k1", cmd: str = "c1", action: str = "next") -> dict[str, Any]:
    return msg("cmd.button", {"command_id": cmd, "idempotency_key": key, "action": action})
