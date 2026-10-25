"""What the Status page shows: one row per part of the app, in words a buyer understands.

``Health`` is filled in by the daemon as things happen (the brain warm-up, the speech model
loading); ``rows`` turns it and a few live checks into the lines on screen, each with the fix
the window offers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Tone = Literal["good", "warn", "bad"]
Fix = Literal["open_browser", "brain_setup", "brain_key", "reconnect", "buy", "enter_key"]


@dataclass
class Health:
    brain_state: str = "checking"  # brain.BrainStatus.state, or "checking"
    brain_detail: str = ""
    gemini_key: bool = False
    speech: Literal["loading", "ready", "failed", "off"] = "loading"
    speech_detail: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Row:
    name: str
    tone: Tone
    text: str
    fix: Fix | None = None
    fix_label: str = ""


def rows(
    health: Health,
    *,
    relay: str | None,  # "up" | "down" | "rejected" | None (no relay configured)
    browser_ok: bool,
    licence_status: str,
    licence_days: int | None,
    licence_source: str,
    phones_online: int,
) -> list[Row]:
    out: list[Row] = []
    if relay is None:
        out.append(Row("Internet relay", "warn", "Not set up: phones work on this Wi-Fi only."))
    elif relay == "up":
        out.append(Row("Internet relay", "good", "Connected: phones work from anywhere."))
    elif relay == "rejected":
        out.append(
            Row("Internet relay", "bad", "The relay refused this PC.", "reconnect", "Try again")
        )
    else:
        out.append(
            Row(
                "Internet relay",
                "warn",
                "Not connected. Phones on this Wi-Fi still work.",
                "reconnect",
                "Reconnect",
            )
        )

    if browser_ok:
        out.append(Row("TV browser", "good", "Microsoft Edge is ready."))
    else:
        out.append(
            Row("TV browser", "bad", "Edge is not running.", "open_browser", "Open the TV browser")
        )

    state = health.brain_state
    if state == "checking":
        out.append(Row("AI brain", "warn", "Starting..."))
    elif state in ("ready", "cloud"):
        out.append(Row("AI brain", "good", health.brain_detail or "Ready."))
    elif health.gemini_key:
        out.append(Row("AI brain", "good", "Using your Gemini key."))
    else:
        missing = {
            "not_installed": "No AI is set up yet: buttons work, free-form commands do not.",
            "not_running": "The local AI (Ollama) is not running.",
            "model_missing": "The local AI model is not downloaded yet.",
        }.get(state, health.brain_detail or "Not ready.")
        out.append(Row("AI brain", "bad", missing, "brain_key", "Set up AI"))

    speech = {
        "loading": Row("Voice", "warn", "Loading the speech model..."),
        "ready": Row("Voice", "good", health.speech_detail or "Ready."),
        "failed": Row("Voice", "bad", health.speech_detail or "Speech recognition failed."),
        "off": Row("Voice", "warn", "No speech recognition configured."),
    }[health.speech]
    out.append(speech)

    if licence_status == "active":
        text = "Licensed." if licence_source != "dev" else "Developer build."
        out.append(Row("Licence", "good", text))
    elif licence_status == "trial":
        days = f"{licence_days} days left" if licence_days is not None else "trial"
        fix: Fix = "enter_key" if licence_source == "trial" else "buy"
        out.append(Row("Licence", "warn", f"Free trial: {days}.", fix, "Buy or enter a key"))
    else:
        out.append(Row("Licence", "bad", "The free trial has ended.", "buy", "Buy"))

    phones = f"{phones_online} connected now" if phones_online else "None connected right now"
    out.append(Row("Phones", "good" if phones_online else "warn", phones + "."))
    return out
