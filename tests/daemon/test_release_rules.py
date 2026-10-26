"""plan.md section 12: the release rules every agent must keep (version source, in-place updates,
user data never touched by an update)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISS = (ROOT / "installer" / "CouchJarvis.iss").read_text(encoding="utf-8")
BUILD = (ROOT / "installer" / "build.ps1").read_text(encoding="utf-8")
VERSION_PY = (ROOT / "pc-daemon" / "version.py").read_text(encoding="utf-8")

# Frozen forever: this id is how Windows and every later installer recognise an existing install.
APP_ID = "7B1E3C52-5A9D-4B5E-9C1A-2F4D8E6A0C31"


def test_the_version_is_a_plain_semver_in_one_place() -> None:
    match = re.search(r'__version__ = "(\d+\.\d+\.\d+)"', VERSION_PY)
    assert match, "pc-daemon/version.py must hold __version__ = 'X.Y.Z'"
    package = json.loads((ROOT / "web-remote" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == match[1], "package.json and version.py must agree"


def test_the_build_takes_its_version_from_that_one_place() -> None:
    assert (
        "pc-daemon\\version.py" in BUILD and "[string]$Version," in BUILD
    )  # no hard-coded default


def test_the_installer_id_never_changes() -> None:
    assert f"AppId={{{{{APP_ID}}}" in ISS
    assert f"{{{APP_ID}}}_is1" in ISS  # the update check reads the same id back from the registry


def test_the_installer_detects_and_updates_an_existing_install() -> None:
    for needed in (
        "InstalledVersion",
        "CompareVersions",
        "PreviousVersion",
        "is installed. This will update it",
    ):
        assert needed in ISS, needed
    assert "UsePreviousAppDir=no" not in ISS and "DisableDirPage=no" not in ISS
    assert "newer" in ISS  # a downgrade asks first


def test_an_update_keeps_the_users_settings_and_stops_the_running_app() -> None:
    assert "ShouldSkipPage" in ISS and "KeyPage.ID" in ISS  # the key page is not re-asked
    assert "taskkill.exe" in ISS and "StopCouchJarvis" in ISS  # Setup cannot close a windowless app
    assert "RelaunchAfterSilentUpdate" in ISS  # a silent update starts the app again


def test_only_the_uninstaller_can_delete_user_data_and_it_asks_first() -> None:
    deletes = [m.start() for m in re.finditer(r"DelTree\(", ISS)]
    assert deletes, "uninstall should still be able to remove user data when asked"
    uninstall = ISS.index("procedure CurUninstallStepChanged")
    assert all(pos > uninstall for pos in deletes), (
        "DelTree outside the uninstaller would wipe data on update"
    )
    assert "Also delete your {#AppName} settings" in ISS and "MB_DEFBUTTON2" in ISS  # default is No
    assert "[UninstallDelete]" not in ISS
    # An update may only remove old shortcuts (the 0.x name), never files or folders of the user.
    if "[InstallDelete]" in ISS:
        section = ISS.split("[InstallDelete]", 1)[1].split("\n[", 1)[0]
        entries = [ln for ln in section.splitlines() if ln.strip() and not ln.startswith(";")]
        assert entries
        for entry in entries:
            assert re.fullmatch(
                r'Type: files; Name: "\{(autoprograms|autodesktop)\}\\[^"*?\\]+\.lnk"',
                entry.strip(),
            ), entry


def test_the_installer_never_writes_the_room_identity() -> None:
    assert "room.json" not in ISS.replace("{app}", "")


def test_every_prompt_can_be_answered_by_a_silent_install_or_uninstall() -> None:
    """A plain MsgBox ignores /SUPPRESSMSGBOXES, so a silent (scripted, winget) run would wait forever."""
    assert not re.search(r"(?<![A-Za-z])MsgBox\(", ISS), (
        "use SuppressibleMsgBox with a safe default"
    )


def test_the_phone_site_sends_its_security_headers() -> None:
    """Cloudflare Pages serves public/_headers: the pairing link holds a secret (no-referrer), and
    the page may only talk to itself and the relay."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[2] / "web-remote"
    headers = (web / "public" / "_headers").read_text(encoding="utf-8")
    for line in (
        "Referrer-Policy: no-referrer",
        "X-Frame-Options: DENY",
        "Permissions-Policy: microphone=(self), camera=(), geolocation=()",
        "connect-src 'self' wss://*.execute-api.us-east-2.amazonaws.com;",
        "script-src 'self';",
        "frame-ancestors 'none'",
    ):
        assert line in headers
    assert "/*  /index.html  200" in (web / "public" / "_redirects").read_text(encoding="utf-8")
    assert not (web / "vercel.json").exists()  # one host only
