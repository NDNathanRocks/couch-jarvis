from __future__ import annotations

import socket

import pytest

from hud.qr import detect_lan_ip, qr_matrix


def test_matrix_is_square_with_a_quiet_zone() -> None:
    m = qr_matrix("https://example.com/?room=abc&secret=def", border=2)
    n = len(m)
    assert all(len(row) == n for row in m)
    assert not any(m[0]) and not any(m[1]) and not any(m[-1]) and not any(m[-2])
    assert not any(row[0] or row[1] or row[-1] or row[-2] for row in m)
    assert any(any(row) for row in m)


def test_finder_patterns_sit_in_three_corners() -> None:
    m = qr_matrix("hello", border=0)
    n = len(m)
    # 7x7 finder: solid 3x3 core inside a ring; check the three corners (not bottom-right)
    for top, left in ((0, 0), (0, n - 7), (n - 7, 0)):
        assert all(m[top][left + c] for c in range(7))  # solid top edge
        assert all(m[top + r][left] for r in range(7))  # solid left edge
        assert all(m[top + 3][left + 3 + c] for c in (-1, 0, 1))  # dark core
        assert not m[top + 1][left + 1] and not m[top + 5][left + 5]  # light ring inside the edge
    assert not all(m[n - 7][n - 7 + c] for c in range(7))  # no finder bottom-right


def test_matrix_is_deterministic_and_grows_with_payload() -> None:
    assert qr_matrix("abc") == qr_matrix("abc")
    assert len(qr_matrix("x" * 400)) > len(qr_matrix("x"))


def test_pairing_url_length_stays_scannable() -> None:
    url = "https://armrest-app.pages.dev/?room=" + "r" * 12 + "&secret=" + "s" * 32
    assert len(qr_matrix(url, border=0)) <= 41  # QR version <= 6: big modules, easy on a TV


def test_detect_lan_ip_prefers_the_outbound_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSock:
        def __enter__(self) -> FakeSock:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def connect(self, addr: object) -> None:
            self.addr = addr

        def getsockname(self) -> tuple[str, int]:
            return ("192.168.1.42", 54321)

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    assert detect_lan_ip() == "192.168.1.42"


@pytest.mark.parametrize("bad", ["127.0.0.1", "0.0.0.0"])
def test_detect_lan_ip_never_returns_a_useless_address(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    class FakeSock:
        def __enter__(self) -> FakeSock:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def connect(self, addr: object) -> None: ...

        def getsockname(self) -> tuple[str, int]:
            return (bad, 1)

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    assert detect_lan_ip() is None


def test_detect_lan_ip_survives_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("network unreachable")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "gethostbyname", boom)
    assert detect_lan_ip() is None
