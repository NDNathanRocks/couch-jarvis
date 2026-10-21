"""QR matrix generation and LAN address discovery for the pairing code on the TV."""

from __future__ import annotations

import socket


def qr_matrix(text: str, border: int = 2) -> list[list[bool]]:
    """Dark modules as True, with a quiet-zone border. Error correction M survives TV glare."""
    import segno

    code = segno.make(text, error="m", micro=False)
    rows = [[bool(cell) for cell in row] for row in code.matrix]
    width = len(rows[0]) + 2 * border
    blank = [False] * width
    padded = [[False] * border + row + [False] * border for row in rows]
    return [blank[:] for _ in range(border)] + padded + [blank[:] for _ in range(border)]


def detect_lan_ip() -> str | None:
    """The address phones on the same Wi-Fi can reach. No packets are sent: connecting a UDP
    socket only makes the OS pick the outbound interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ip = str(sock.getsockname()[0])
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip
