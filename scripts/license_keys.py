"""Make the licence-key signing pair, and issue keys for the direct download (plan.md 5F).

    python scripts/license_keys.py new-keypair            # once; prints the public key to paste
    python scripts/license_keys.py issue --name "Jane" --order 1042
    python scripts/license_keys.py check ARMREST1....     # is this key genuine?

The private key lives OUTSIDE the repository
(default %USERPROFILE%\\.armrest\\licence-signing-key.pem).
Back it up somewhere safe: without it no new keys can be issued, and anyone who has it can make
keys. Never commit it, never bundle it (the app only contains the public half).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pc-daemon"))

from licensing import PUBLIC_KEY_B64, _b64e, issue_key, verify_key  # noqa: E402

DEFAULT_KEY = Path.home() / ".armrest" / "licence-signing-key.pem"


def new_keypair(path: Path) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if path.exists():
        print(f"{path} already exists: refusing to replace it (every issued key depends on it).")
        return 1
    if ROOT in path.resolve().parents:
        print("The private key must not live inside the repository.")
        return 1
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print(f"private key: {path}  (back it up; never share or commit it)")
    print(f'paste into pc-daemon/licensing.py:  PUBLIC_KEY_B64 = "{_b64e(public)}"')
    return 0


def issue(path: Path, name: str, order: str) -> int:
    from cryptography.hazmat.primitives import serialization

    private = serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = issue_key(private, name, order, dt.date.today())
    if verify_key(key) is None:
        print("This private key does not match PUBLIC_KEY_B64 in pc-daemon/licensing.py.")
        return 1
    print(key)
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    kp = sub.add_parser("new-keypair")
    kp.add_argument("--out", type=Path, default=DEFAULT_KEY)
    iss = sub.add_parser("issue")
    iss.add_argument("--name", required=True, help="the buyer's name (shown in the app)")
    iss.add_argument("--order", required=True, help="the order or reference number")
    iss.add_argument("--key", type=Path, default=DEFAULT_KEY)
    chk = sub.add_parser("check")
    chk.add_argument("licence")
    args = p.parse_args(argv)
    if args.cmd == "new-keypair":
        return new_keypair(args.out)
    if args.cmd == "issue":
        return issue(args.key, args.name, args.order)
    info = verify_key(args.licence, PUBLIC_KEY_B64)
    print(f"genuine: {info}" if info else "not a genuine key")
    return 0 if info else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
