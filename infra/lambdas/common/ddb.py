"""DynamoDB access for rooms, connections, devices, idempotency keys and the master secret."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

CONNECTION_TTL_S = 3 * 3600
DEVICE_TTL_S = 200 * 24 * 3600
IDEMPOTENCY_TTL_S = 15 * 60


@dataclass(frozen=True)
class TableNames:
    rooms: str
    connections: str
    devices: str
    idempotency: str
    config: str


def _is(err: ClientError, code: str) -> bool:
    return bool(err.response.get("Error", {}).get("Code") == code)


class Store:
    def __init__(
        self,
        resource: Any,
        tables: TableNames,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._rooms = resource.Table(tables.rooms)
        self._connections = resource.Table(tables.connections)
        self._devices = resource.Table(tables.devices)
        self._idem = resource.Table(tables.idempotency)
        self._config = resource.Table(tables.config)
        self._clock = clock
        self._master_cache: str | None = None

    # ------------------------------------------------------------------ master key

    def master_key(self) -> str:
        """Per-deployment random secret, created race-safely on first use."""
        if self._master_cache:
            return self._master_cache
        item = self._config.get_item(Key={"pk": "master"}, ConsistentRead=True).get("Item")
        if item is None:
            try:
                self._config.put_item(
                    Item={"pk": "master", "value": secrets.token_hex(32)},
                    ConditionExpression="attribute_not_exists(pk)",
                )
            except ClientError as err:
                if not _is(err, "ConditionalCheckFailedException"):
                    raise
            item = self._config.get_item(Key={"pk": "master"}, ConsistentRead=True)["Item"]
        self._master_cache = str(item["value"])
        return self._master_cache

    # ------------------------------------------------------------------ rooms

    def get_room(self, room_id: str) -> dict[str, Any] | None:
        item: dict[str, Any] | None = self._rooms.get_item(
            Key={"room_id": room_id}, ConsistentRead=True
        ).get("Item")
        return item

    def register_room(self, room_id: str, pc_key_hash: str, room_secret_hash: str) -> bool:
        """Trust-on-first-use. False if the room already exists."""
        try:
            self._rooms.put_item(
                Item={
                    "room_id": room_id,
                    "pc_key_hash": pc_key_hash,
                    "room_secret_hash": room_secret_hash,
                    "created_at": int(self._clock()),
                },
                ConditionExpression="attribute_not_exists(room_id)",
            )
            return True
        except ClientError as err:
            if _is(err, "ConditionalCheckFailedException"):
                return False
            raise

    def set_room_secret_hash(self, room_id: str, room_secret_hash: str) -> None:
        self._rooms.update_item(
            Key={"room_id": room_id},
            UpdateExpression="SET room_secret_hash = :h",
            ExpressionAttributeValues={":h": room_secret_hash},
        )

    def swap_pc_connection(self, room_id: str, connection_id: str) -> str | None:
        """Make connection_id the room's PC; returns the previous PC connection id, if any."""
        old = self._rooms.update_item(
            Key={"room_id": room_id},
            UpdateExpression="SET pc_connection_id = :c",
            ExpressionAttributeValues={":c": connection_id},
            ReturnValues="UPDATED_OLD",
        )
        prev = old.get("Attributes", {}).get("pc_connection_id")
        return str(prev) if prev else None

    def clear_pc_connection(self, room_id: str, connection_id: str) -> None:
        try:
            self._rooms.update_item(
                Key={"room_id": room_id},
                UpdateExpression="REMOVE pc_connection_id",
                ConditionExpression="pc_connection_id = :c",
                ExpressionAttributeValues={":c": connection_id},
            )
        except ClientError as err:
            if not _is(err, "ConditionalCheckFailedException"):
                raise  # a newer PC connection already replaced this one

    # ------------------------------------------------------------------ connections

    def put_connection(
        self,
        connection_id: str,
        room_id: str,
        role: str,
        device_id: str | None,
        authed: bool,
        token_exp: float | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "connection_id": connection_id,
            "room_id": room_id,
            "role": role,
            "authed": authed,
            "connected_at": int(self._clock()),
            "pair_failures": 0,
            "ttl": int(self._clock()) + CONNECTION_TTL_S,
        }
        if device_id:
            item["device_id"] = device_id
        if token_exp is not None:
            item["token_exp"] = int(token_exp)
        self._connections.put_item(Item=item)

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        item: dict[str, Any] | None = self._connections.get_item(
            Key={"connection_id": connection_id}, ConsistentRead=True
        ).get("Item")
        return item

    def authenticate_connection(self, connection_id: str, device_id: str, token_exp: float) -> None:
        self._connections.update_item(
            Key={"connection_id": connection_id},
            UpdateExpression="SET authed = :t, device_id = :d, token_exp = :e",
            ExpressionAttributeValues={":t": True, ":d": device_id, ":e": int(token_exp)},
        )

    def record_pair_failure(self, connection_id: str) -> int:
        resp = self._connections.update_item(
            Key={"connection_id": connection_id},
            UpdateExpression="ADD pair_failures :one",
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["pair_failures"])

    def delete_connection(self, connection_id: str) -> dict[str, Any] | None:
        old: dict[str, Any] | None = self._connections.delete_item(
            Key={"connection_id": connection_id}, ReturnValues="ALL_OLD"
        ).get("Attributes")
        return old

    def list_connections(self, room_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "IndexName": "room-index",
            "KeyConditionExpression": "room_id = :r",
            "ExpressionAttributeValues": {":r": room_id},
        }
        while True:
            resp = self._connections.query(**kwargs)
            out += resp.get("Items", [])
            if "LastEvaluatedKey" not in resp:
                return out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # ------------------------------------------------------------------ devices

    def put_device(self, room_id: str, device_id: str, name: str) -> None:
        now = int(self._clock())
        self._devices.put_item(
            Item={
                "room_id": room_id,
                "device_id": device_id,
                "name": name,
                "revoked": False,
                "created_at": now,
                "last_seen": now,
                "ttl": now + DEVICE_TTL_S,
            }
        )

    def get_device(self, room_id: str, device_id: str) -> dict[str, Any] | None:
        item: dict[str, Any] | None = self._devices.get_item(
            Key={"room_id": room_id, "device_id": device_id}, ConsistentRead=True
        ).get("Item")
        return item

    def touch_device(self, room_id: str, device_id: str) -> None:
        now = int(self._clock())
        try:
            self._devices.update_item(
                Key={"room_id": room_id, "device_id": device_id},
                UpdateExpression="SET last_seen = :n, #t = :t",
                ExpressionAttributeNames={"#t": "ttl"},
                ExpressionAttributeValues={":n": now, ":t": now + DEVICE_TTL_S},
                ConditionExpression="attribute_exists(device_id)",
            )
        except ClientError as err:
            if not _is(err, "ConditionalCheckFailedException"):
                raise

    def revoke_device(self, room_id: str, device_id: str) -> None:
        self._devices.update_item(
            Key={"room_id": room_id, "device_id": device_id},
            UpdateExpression="SET revoked = :t",
            ExpressionAttributeValues={":t": True},
        )

    def list_devices(self, room_id: str) -> list[dict[str, Any]]:
        resp = self._devices.query(
            KeyConditionExpression="room_id = :r", ExpressionAttributeValues={":r": room_id}
        )
        items: list[dict[str, Any]] = resp.get("Items", [])
        return items

    # ------------------------------------------------------------------ idempotency

    def claim_idempotency(self, room_id: str, key: str) -> bool:
        """True the first time a key is seen in the window; False for duplicates."""
        now = int(self._clock())
        try:
            self._idem.put_item(
                Item={"pk": f"{room_id}#{key}", "first_seen": now, "ttl": now + IDEMPOTENCY_TTL_S},
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as err:
            if _is(err, "ConditionalCheckFailedException"):
                return False
            raise

    def release_idempotency(self, room_id: str, key: str) -> None:
        """Undo claim_idempotency when the command was never delivered, so a retry can succeed."""
        self._idem.delete_item(Key={"pk": f"{room_id}#{key}"})
