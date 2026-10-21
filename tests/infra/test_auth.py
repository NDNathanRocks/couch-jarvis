from __future__ import annotations

import base64
import json

import jwt

from common import auth

NOW = 1_800_000_000.0


def test_sha256_and_constant_time_compare() -> None:
    assert (
        auth.sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert auth.equals("same", "same") and not auth.equals("same", "diff")
    assert not auth.equals("a", "ab")


def test_room_keys_are_deterministic_and_isolated() -> None:
    a1 = auth.derive_room_key("master", "room-aaaaaaaa")
    assert a1 == auth.derive_room_key("master", "room-aaaaaaaa")
    assert a1 != auth.derive_room_key("master", "room-bbbbbbbb")
    assert a1 != auth.derive_room_key("other-master", "room-aaaaaaaa")
    assert len(auth.key_id(a1)) == 12 and auth.key_id(a1) != a1[:12]


def test_token_round_trip() -> None:
    key = auth.derive_room_key("m", "room-aaaaaaaa")
    token, exp = auth.mint_device_token(key, "room-aaaaaaaa", "dev-1", NOW)
    assert exp == NOW + 180 * 24 * 3600
    claims = auth.verify_device_token(key, "room-aaaaaaaa", token, NOW + 60)
    assert claims is not None and claims["sub"] == "dev-1" and claims["rid"] == "room-aaaaaaaa"


def test_token_rejected_for_wrong_room_key_or_tampering() -> None:
    key = auth.derive_room_key("m", "room-aaaaaaaa")
    token, _ = auth.mint_device_token(key, "room-aaaaaaaa", "dev-1", NOW)
    other_key = auth.derive_room_key("m", "room-bbbbbbbb")
    assert auth.verify_device_token(other_key, "room-bbbbbbbb", token, NOW) is None  # bad signature
    assert auth.verify_device_token(key, "room-bbbbbbbb", token, NOW) is None  # rid mismatch
    head, body, sig = token.split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=="))
    forged["sub"] = "dev-admin"
    new_body = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    assert auth.verify_device_token(key, "room-aaaaaaaa", f"{head}.{new_body}.{sig}", NOW) is None
    assert auth.verify_device_token(key, "room-aaaaaaaa", "garbage", NOW) is None
    assert auth.verify_device_token(key, "room-aaaaaaaa", "", NOW) is None


def test_expired_token_rejected_even_inside_clock_skew() -> None:
    key = auth.derive_room_key("m", "room-aaaaaaaa")
    token, exp = auth.mint_device_token(key, "room-aaaaaaaa", "dev-1", NOW, ttl_s=100)
    assert auth.verify_device_token(key, "room-aaaaaaaa", token, exp - 1) is not None
    assert auth.verify_device_token(key, "room-aaaaaaaa", token, exp) is None


def test_alg_none_and_wrong_issuer_rejected() -> None:
    key = auth.derive_room_key("m", "room-aaaaaaaa")
    claims = {"iss": auth.ISSUER, "sub": "dev-1", "rid": "room-aaaaaaaa", "exp": int(NOW + 1000)}
    none_token = jwt.encode(claims, key=None, algorithm="none")  # type: ignore[arg-type]
    assert auth.verify_device_token(key, "room-aaaaaaaa", none_token, NOW) is None
    bad_iss = jwt.encode({**claims, "iss": "evil"}, key, algorithm="HS256")
    assert auth.verify_device_token(key, "room-aaaaaaaa", bad_iss, NOW) is None
    missing_sub = jwt.encode(
        {k: v for k, v in claims.items() if k != "sub"}, key, algorithm="HS256"
    )
    assert auth.verify_device_token(key, "room-aaaaaaaa", missing_sub, NOW) is None
