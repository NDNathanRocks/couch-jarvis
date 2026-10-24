"""Spoken replies: the TV tells you what it just did. On by default, remembered per TV.

``VoiceService`` owns the saved preferences (this PC is "the TV", so the file lives here and every
phone sees the same choice), turns a finished command into a sentence, and speaks it without ever
holding up the command pipeline. A newer reply cuts off an older one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.state import IntentPlan
from contracts import VoiceState
from executors.base import ActionResult
from voice.player import NullPlayer, Player
from voice.replies import STYLES, Phraser
from voice.tts import VOICES, TtsEngine

log = logging.getLogger(__name__)

STYLE_LABELS = {"jarvis": "Jarvis", "friendly": "Friendly", "brief": "Brief"}


def pref_message(args: dict[str, str | int | float | bool]) -> str:
    """The on-screen line for a settings command (also what the phone toast shows)."""
    if "enabled" in args:
        return "Voice replies on" if args["enabled"] else "Voice replies off"
    if "style" in args:
        return f"Reply style: {STYLE_LABELS.get(str(args['style']), args['style'])}"
    if "voice" in args:
        spec = VOICES.get(str(args["voice"]))
        return f"Voice: {spec.label.split(' (')[0] if spec else args['voice']}"
    if "buttons" in args:
        return "Speaking after button taps" if args["buttons"] else "Quiet after button taps"
    return "Testing the voice"


class VoicePrefsStore:
    """voice.json next to the room identity. A missing or damaged file means the defaults."""

    def __init__(self, path: Path | None) -> None:
        self._path = path

    def load(self) -> VoiceState:
        if self._path is not None:
            with contextlib.suppress(OSError, ValueError, ValidationError):
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return VoiceState.model_validate({**raw, "engine": "none"})
        return VoiceState()

    def save(self, state: VoiceState) -> None:
        if self._path is None:
            return
        data = state.model_dump(exclude={"engine"})
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self._path)  # atomic: a crash never leaves half a file
        except OSError:
            log.warning("could not save voice preferences", exc_info=True)


class VoiceService:
    def __init__(
        self,
        store: VoicePrefsStore,
        engine: TtsEngine | None,
        player: Player | None = None,
        *,
        phraser: Phraser | None = None,
        on_change: Callable[[VoiceState], None] | None = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._player = player or NullPlayer()
        self._phraser = phraser or Phraser()
        self._on_change = on_change or (lambda _state: None)
        self._prefs = store.load()
        self._task: asyncio.Task[None] | None = None
        self.spoken: list[str] = []  # recent lines, for tests and the phone's log

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> VoiceState:
        active = self._engine.active if self._engine is not None else None
        return self._prefs.model_copy(update={"engine": active or "none"})

    def _publish(self) -> None:
        self._on_change(self.state)

    # ------------------------------------------------------------------ settings

    def apply(self, args: dict[str, str | int | float | bool]) -> ActionResult:
        """Change preferences (from "stop talking", the settings sheet or the phone's text)."""
        update: dict[str, Any] = {}
        if "enabled" in args:
            update["enabled"] = bool(args["enabled"])
        if "buttons" in args:
            update["buttons"] = bool(args["buttons"])
        if "style" in args:
            if str(args["style"]) not in STYLES:
                return ActionResult(False, "os", "Unknown reply style", "unsupported")
            update["style"] = str(args["style"])
        if "voice" in args:
            if str(args["voice"]) not in VOICES:
                return ActionResult(False, "os", "Unknown voice", "unsupported")
            update["voice"] = str(args["voice"])
        if update:
            self._prefs = self._prefs.model_copy(update=update)
            self._store.save(self._prefs)
        # Preview what changed so you hear the new voice or style straight away.
        wants_preview = bool(args.get("test")) or (
            self._prefs.enabled
            and bool({"style", "voice"} & update.keys() or update.get("enabled"))
        )
        if wants_preview:
            self.speak(self._phraser.sample(self._prefs.style))
        self._publish()
        return ActionResult(True, "os")

    # ------------------------------------------------------------------ speaking

    def announce(
        self,
        *,
        kind: str,
        plan: IntentPlan | None,
        ok: bool,
        message: str,
        spoken: str | None = None,
    ) -> None:
        """Say how a command went. Spoken commands always; buttons only if asked; typed never."""
        if not self._prefs.enabled or kind == "text":
            return
        if kind == "button" and not self._prefs.buttons:
            return
        if plan is not None and any(step.op == "voice_pref" for step in plan.steps):
            return  # settings changes speak their own preview
        if spoken:  # the assistant's own answer or question: say it as written
            self.speak(spoken)
            return
        text = (
            self._phraser.success(self._prefs.style, plan)
            if ok
            else self._phraser.failure(self._prefs.style, message)
        )
        self.speak(text)

    def speak(self, text: str) -> None:
        """Start saying ``text`` now, cutting off whatever was being said. Never blocks."""
        if self._engine is None:
            return
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._player.stop()
        self._task = asyncio.create_task(self._say(text), name="voice-reply")

    async def _say(self, text: str) -> None:
        assert self._engine is not None
        speech = None
        try:
            speech = await self._engine.synthesize(text, self._prefs.voice)
            if speech is None:
                self._publish()  # every engine is down: show it
                return
            self.spoken.append(text)
            del self.spoken[:-20]
            with tempfile.NamedTemporaryFile(
                suffix=speech.suffix, prefix="cj-voice-", delete=False
            ) as handle:
                handle.write(speech.data)
            path = Path(handle.name)
            try:
                await self._player.play(path)
            finally:
                with contextlib.suppress(OSError):
                    await asyncio.to_thread(path.unlink)
            self._publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("spoken reply failed", exc_info=True)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._player.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
