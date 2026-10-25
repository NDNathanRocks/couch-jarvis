from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from room import RoomStore, sha256_hex


def test_created_once_and_persisted(tmp_path: Path) -> None:
    path = tmp_path / "room.json"
    first = RoomStore(path).load_or_create()
    second = RoomStore(path).load_or_create()  # simulates a daemon restart
    assert first == second
    assert len(first.room_secret) >= 24
    assert first.pc_key != first.room_secret


def test_secrets_not_stored_in_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "room.json"
    ident = RoomStore(path).load_or_create()
    raw = path.read_text(encoding="utf-8")
    assert ident.room_id in raw
    assert ident.room_secret not in raw
    assert ident.pc_key not in raw


def test_rotation_keeps_room_id_and_pc_key(tmp_path: Path) -> None:
    path = tmp_path / "room.json"
    store = RoomStore(path)
    before = store.load_or_create()
    after = store.rotate_secret()
    assert after.room_id == before.room_id
    assert after.pc_key == before.pc_key
    assert after.room_secret != before.room_secret
    assert RoomStore(path).load_or_create() == after  # rotation is durable


def test_pairing_url_and_hashes(tmp_path: Path) -> None:
    ident = RoomStore(tmp_path / "room.json").load_or_create()
    url = ident.pairing_url("https://example.app/")
    qs = parse_qs(urlparse(url).query)
    assert qs["room"] == [ident.room_id]
    assert qs["secret"] == [ident.room_secret]
    assert ident.room_secret_hash == sha256_hex(ident.room_secret)
    assert len(ident.pc_key_hash) == 64


def test_every_install_gets_its_own_keys(tmp_path: Path) -> None:
    """Two PCs must never share a room: that would let one household control the other's."""
    made = [RoomStore(tmp_path / f"pc{i}" / "room.json").load_or_create() for i in range(50)]
    for field in ("room_id", "room_secret", "pc_key"):
        values = [getattr(identity, field) for identity in made]
        assert len(set(values)) == len(values), field
    assert all(len(i.room_secret) >= 32 and len(i.pc_key) >= 43 for i in made)  # 192 / 256 bit


def test_the_keys_come_from_the_pc_not_from_the_install_folder(tmp_path: Path) -> None:
    """A copied install (or the portable zip) carries no identity: a new folder gets new keys."""
    first = RoomStore(tmp_path / "a" / "room.json").load_or_create()
    again = RoomStore(tmp_path / "a" / "room.json").load_or_create()
    other = RoomStore(tmp_path / "b" / "room.json").load_or_create()
    assert again.room_id == first.room_id and other.room_id != first.room_id
