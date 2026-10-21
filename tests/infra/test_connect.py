from __future__ import annotations

import time

import boto3

from common import auth
from common.ddb import Store, TableNames
from infra_support import PC_HEADERS, ROOM, ROOM_SECRET, World


def test_bad_room_id_and_missing_pc_credentials_are_rejected(world: World) -> None:
    assert world.connect_pc(room="x")["statusCode"] == 400
    assert world.connect_pc(headers={})["statusCode"] == 401
    assert world.connect_pc(headers={"Authorization": "Bearer k"})["statusCode"] == 401  # no hash
    bad_hash = {**PC_HEADERS, "X-Room-Secret-Hash": "nothex"}
    assert world.connect_pc(headers=bad_hash)["statusCode"] == 401
    assert world.store.get_room(ROOM) is None  # nothing registered by failed attempts


def test_first_pc_registers_the_room_trust_on_first_use(world: World) -> None:
    assert world.connect_pc()["statusCode"] == 200
    room = world.store.get_room(ROOM)
    assert room is not None
    assert room["pc_key_hash"] == auth.sha256_hex("pc-key-secret-value")
    assert room["room_secret_hash"] == auth.sha256_hex(ROOM_SECRET)
    assert "pc-key-secret-value" not in str(room)  # only hashes are stored
    assert room["pc_connection_id"] == "pc-1"
    assert world.store.get_connection("pc-1")["role"] == "pc"  # type: ignore[index]


def test_imposter_pc_cannot_take_over_a_registered_room(world: World) -> None:
    world.connect_pc()
    evil = {**PC_HEADERS, "Authorization": "Bearer stolen-room-id-guess"}
    assert world.connect_pc("pc-evil", headers=evil)["statusCode"] == 401
    assert world.store.get_room(ROOM)["pc_connection_id"] == "pc-1"  # type: ignore[index]
    assert world.store.get_connection("pc-evil") is None


def test_pc_reconnect_replaces_and_closes_the_previous_connection(world: World) -> None:
    world.connect_pc("pc-1")
    assert world.connect_pc("pc-2")["statusCode"] == 200
    assert world.store.get_room(ROOM)["pc_connection_id"] == "pc-2"  # type: ignore[index]
    assert "pc-1" in world.api.closed and world.store.get_connection("pc-1") is None


def test_rotated_qr_secret_hash_is_updated_by_the_authenticated_pc(world: World) -> None:
    world.connect_pc("pc-1")
    new_hash = auth.sha256_hex("brand-new-secret")
    world.connect_pc("pc-2", headers={**PC_HEADERS, "X-Room-Secret-Hash": new_hash})
    assert world.store.get_room(ROOM)["room_secret_hash"] == new_hash  # type: ignore[index]


def test_phone_cannot_join_an_unknown_room(world: World) -> None:
    assert world.connect_phone("p1", pair="1")["statusCode"] == 403


def test_phone_needs_a_token_or_pair_flag(world: World) -> None:
    world.connect_pc()
    assert world.connect_phone("p1")["statusCode"] == 401
    assert world.connect_phone("p1", role="admin")["statusCode"] == 400
    assert world.connect_phone("p1", pair="1")["statusCode"] == 200
    conn = world.store.get_connection("p1")
    assert conn is not None and conn["authed"] is False


def test_returning_phone_authenticates_with_its_token(world: World) -> None:
    world.connect_pc()
    granted = world.pair_phone("p1")
    world.disconnect("p1")
    world.api.sent.clear()
    assert world.connect_phone("p2", token=granted["device_token"])["statusCode"] == 200
    conn = world.store.get_connection("p2")
    assert conn is not None and conn["authed"] is True and conn["device_id"] == granted["device_id"]
    # the PC learns a friend is back
    presence = world.api.to("pc-1", "presence")
    assert presence and presence[-1]["payload"]["devices"][0]["online"] is True


def test_connecting_socket_is_never_messaged_or_pruned(world: World) -> None:
    """Regression: posting to a not-yet-established socket raises Gone and used to delete it."""
    world.connect_pc()
    granted = world.pair_phone("p1")
    world.api.gone.add("p2")  # API Gateway has not established p2 yet during $connect
    assert world.connect_phone("p2", token=granted["device_token"])["statusCode"] == 200
    assert world.store.get_connection("p2") is not None
    world.api.gone.add("pc-2")
    world.connect_pc("pc-2")
    assert world.store.get_connection("pc-2") is not None


def test_bad_expired_foreign_and_revoked_tokens_are_refused(world: World) -> None:
    world.connect_pc()
    granted = world.pair_phone("p1")
    key = world.room_key()
    assert world.connect_phone("x", token="garbage")["statusCode"] == 401
    expired, _ = auth.mint_device_token(
        key, ROOM, granted["device_id"], time.time() - 1000, ttl_s=10
    )
    assert world.connect_phone("x", token=expired)["statusCode"] == 401
    other_room_key = auth.derive_room_key(world.store.master_key(), "room-other-1234")
    foreign, _ = auth.mint_device_token(
        other_room_key, "room-other-1234", granted["device_id"], time.time()
    )
    assert world.connect_phone("x", token=foreign)["statusCode"] == 401
    ghost, _ = auth.mint_device_token(key, ROOM, "dev-never-paired", time.time())
    assert world.connect_phone("x", token=ghost)["statusCode"] == 401  # signed but unknown device
    world.store.revoke_device(ROOM, granted["device_id"])
    assert world.connect_phone("x", token=granted["device_token"])["statusCode"] == 401
    assert world.store.get_connection("x") is None


def test_master_key_is_created_once_and_shared(world: World) -> None:
    first = world.store.master_key()
    other = Store(
        boto3.resource("dynamodb"),
        TableNames("rooms", "connections", "devices", "idempotency", "config"),
    )
    assert other.master_key() == first and len(first) == 64
