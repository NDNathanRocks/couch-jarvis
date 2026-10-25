"""Third-party notices for the shipped app, and a licence gate for a product that is sold.

python scripts/licenses.py                 # check only (exit 1 on a licence we may not ship)
python scripts/licenses.py --write FILE    # also write THIRD_PARTY_NOTICES.txt

Python: every package requirements.txt pulls in at runtime (followed through each package's own
requirements in the installed environment), minus the ones builds never ship. Web: the phone
app's production dependencies from web-remote/node_modules. LGPL is allowed because PySide6/Qt
are shipped as separate, replaceable DLLs (PyInstaller onedir), which is what the LGPL asks.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Excluded from every build (installer/couch_jarvis.spec "excludes"): edge-tts (its service is not
# licensed for a product), and the GPLv3 helpers pyautogui imports only if present.
NOT_SHIPPED = {"edge-tts", "pymsgbox", "mouseinfo"}
# Licences a closed-source paid app cannot include (LGPL is fine: see the module docstring).
FORBIDDEN = re.compile(r"(?<!L)GPL|AGPL|SSPL|non-?commercial|CC-BY-NC|research licen", re.I)


def _name(requirement: str) -> str | None:
    if ";" in requirement:
        _, marker = requirement.split(";", 1)
        if "extra ==" in marker:  # optional extras are not installed by the base requirement
            return None
        if "sys_platform" in marker and "win32" not in marker and sys.platform == "win32":
            return None
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return match.group(1).lower().replace("_", "-") if match else None


def top_level() -> list[str]:
    names = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        name = _name(line) if line else None
        if name and name not in NOT_SHIPPED:
            names.append(name)
    return names


def licence_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    text = (meta.get("License") or "").strip().splitlines()
    return text[0][:80] if text else "UNKNOWN"


def python_packages() -> dict[str, tuple[str, str]]:
    """name -> (version, licence) for the runtime closure."""
    seen: dict[str, tuple[str, str]] = {}
    todo = top_level()
    while todo:
        name = todo.pop()
        if name in seen or name in NOT_SHIPPED:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # a platform-specific dependency that is not installed here
        seen[name] = (dist.version, licence_of(dist))
        for req in dist.requires or []:
            child = _name(req)
            if child:
                todo.append(child)
    return dict(sorted(seen.items()))


def web_packages() -> dict[str, tuple[str, str]]:
    web = ROOT / "web-remote"
    deps = json.loads((web / "package.json").read_text(encoding="utf-8")).get("dependencies", {})
    out: dict[str, tuple[str, str]] = {}
    # the service worker that vite-plugin-pwa generates is built from workbox (MIT)
    todo = list(deps) + [
        p.name for p in (web / "node_modules").glob("workbox-*") if p.name != "workbox-build"
    ]
    while todo:
        name = todo.pop()
        pkg = web / "node_modules" / name / "package.json"
        if name in out or not pkg.is_file():
            continue
        data = json.loads(pkg.read_text(encoding="utf-8"))
        licence = data.get("license") or "UNKNOWN"
        out[name] = (
            str(data.get("version", "?")),
            licence if isinstance(licence, str) else str(licence),
        )
        todo += list((data.get("dependencies") or {}).keys())
    return dict(sorted(out.items()))


def allowed(licence: str) -> bool:
    """A licence we may ship. "A OR B" lets us pick: one acceptable option is enough (PySide6 is
    "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only", used under the LGPL)."""
    if licence == "UNKNOWN":
        return False
    return any(not FORBIDDEN.search(option) for option in re.split(r"\s+OR\s+", licence))


def problems(packages: dict[str, tuple[str, str]]) -> list[str]:
    return [f"{n} {v}: {lic}" for n, (v, lic) in packages.items() if not allowed(lic)]


# Packages whose metadata names no licence, checked by hand (name -> licence and where it says so).
REVIEWED: dict[str, str] = {}


def render(py: dict[str, tuple[str, str]], web: dict[str, tuple[str, str]]) -> str:
    lines = [
        "THIRD-PARTY SOFTWARE NOTICES",
        "",
        "This product includes the following open-source software. Each package is the property",
        "of its authors and is used under the licence named here; the full licence texts ship",
        "inside each package's folder in the installed app (_internal) or at its homepage.",
        "",
        "PySide6 / Qt are used under the GNU LGPL v3: they are separate DLLs in the app folder",
        "that you may replace with your own build. Their source is at https://code.qt.io.",
        "",
        "== PC app (Python) ==",
    ]
    lines += [f"{n} {v} - {lic}" for n, (v, lic) in py.items()]
    lines += ["", "== Phone app (web) =="]
    lines += [f"{n} {v} - {lic}" for n, (v, lic) in web.items()]
    lines += [
        "",
        "AI models are not part of this product: they are downloaded on request from their",
        "publishers (qwen2.5 / qwen2.5vl: Apache-2.0, phi4-mini: MIT, faster-whisper: MIT).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    py, web = python_packages(), web_packages()
    for name, licence in REVIEWED.items():
        if name in py and py[name][1] == "UNKNOWN":
            py[name] = (py[name][0], licence)
    bad = problems(py) + problems(web)
    if argv[:1] == ["--write"]:
        Path(argv[1]).write_text(render(py, web), encoding="utf-8")
        print(f"wrote {argv[1]} ({len(py)} Python, {len(web)} web packages)")
    if bad:
        print("Licences that need a decision before shipping:")
        print("\n".join(f"  {b}" for b in bad))
        return 1
    print(f"licence check passed ({len(py)} Python, {len(web)} web packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
