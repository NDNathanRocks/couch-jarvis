"""Export the envelope JSON Schema from pc-daemon/contracts.py.

Usage:
    python scripts/export_contracts.py          # (re)write shared/contracts/envelope.schema.json
    python scripts/export_contracts.py --check  # exit 1 if the committed file has drifted
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pc-daemon"))

from contracts import build_schema  # noqa: E402

TARGET = ROOT / "shared" / "contracts" / "envelope.schema.json"


def render() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    rendered = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != rendered:
            print("contracts drift: run `python scripts/export_contracts.py`", file=sys.stderr)
            return 1
        print("contracts: up to date")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
