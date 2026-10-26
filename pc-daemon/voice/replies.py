"""What the TV says after a command: short, varied, in the style the household picked.

Pure text in, text out (no audio, no network), so every phrase is unit-testable. The facts come
from the executed plan, never from the page, so web content can not put words in its mouth.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from string import capwords
from urllib.parse import urlsplit

from agent.state import ActionStep, IntentPlan

STYLES = ("jarvis", "friendly", "brief")
SITE_LABELS = {"ytmusic": "YouTube Music", "youtube": "YouTube"}
_SITE_BY_NAME = {"youtube music": "YouTube Music", "youtube": "YouTube"}
MAX_SUBJECT = 60

# kind -> style -> fragments. A fragment has no capital letter or full stop of its own: several
# of them are joined into one sentence ("new tab is open, and now playing X on YouTube Music").
PHRASES: dict[str, dict[str, tuple[str, ...]]] = {
    "play": {
        "jarvis": (
            "now playing {q} on {site}",
            "playing {q} on {site}, as requested",
            "{q} is now playing on {site}",
        ),
        "friendly": ("playing {q} on {site}", "{q} is coming right up on {site}", "here comes {q}"),
        "brief": ("playing {q}", "{q}"),
    },
    "search": {
        "jarvis": ("here are the results for {q}", "showing results for {q}"),
        "friendly": ("here's what I found for {q}", "results for {q} coming up"),
        "brief": ("results for {q}",),
    },
    "open_site": {
        "jarvis": ("opening {site}", "{site} is up"),
        "friendly": ("opening {site}", "{site} is on the way"),
        "brief": ("{site}",),
    },
    "open_item": {
        "jarvis": ("opening that for you", "opened"),
        "friendly": ("opening that now", "here we go"),
        "brief": ("opened",),
    },
    "new_tab": {
        "jarvis": ("a new tab is open", "new tab ready"),
        "friendly": ("new tab is open", "fresh tab ready"),
        "brief": ("new tab",),
    },
    "tab": {
        "jarvis": ("done", "that's taken care of"),
        "friendly": ("done", "all set"),
        "brief": ("done",),
    },
    "close_tab": {
        "jarvis": ("tab closed", "I've closed that tab"),
        "friendly": ("tab closed", "closed it"),
        "brief": ("closed",),
    },
    "pause": {
        "jarvis": ("paused", "playback paused"),
        "friendly": ("paused", "okay, paused"),
        "brief": ("paused",),
    },
    "resume": {
        "jarvis": ("resuming playback", "playing"),
        "friendly": ("playing again", "back to it"),
        "brief": ("playing",),
    },
    "next": {
        "jarvis": ("skipping ahead", "on to the next one"),
        "friendly": ("next one coming up", "skipping"),
        "brief": ("next",),
    },
    "prev": {
        "jarvis": ("going back", "back to the previous one"),
        "friendly": ("going back", "previous one coming up"),
        "brief": ("previous",),
    },
    "volume": {
        "jarvis": ("volume adjusted", "volume is now {level} percent"),
        "friendly": ("volume changed", "how's that"),
        "brief": ("volume",),
    },
    "volume_level": {
        "jarvis": ("volume set to {level} percent",),
        "friendly": ("volume at {level} percent",),
        "brief": ("volume {level}",),
    },
    "mute": {
        "jarvis": ("muted", "the TV is muted"),
        "friendly": ("muted", "shh, muted"),
        "brief": ("muted",),
    },
    "unmute": {
        "jarvis": ("sound restored", "unmuted"),
        "friendly": ("unmuted", "sound is back"),
        "brief": ("unmuted",),
    },
    "fullscreen": {
        "jarvis": ("going fullscreen", "fullscreen engaged"),
        "friendly": ("fullscreen it is", "going big"),
        "brief": ("fullscreen",),
    },
    "windowed": {
        "jarvis": ("leaving fullscreen", "back to the normal view"),
        "friendly": ("out of fullscreen", "back to normal"),
        "brief": ("fullscreen off",),
    },
    "speed": {
        "jarvis": ("speed adjusted", "playback speed changed"),
        "friendly": ("speed changed", "there's your new speed"),
        "brief": ("speed set",),
    },
    "ad": {
        "jarvis": ("ad dismissed", "the ad is out of the way"),
        "friendly": ("ad skipped", "that ad is gone"),
        "brief": ("ad skipped",),
    },
    "subs_on": {
        "jarvis": ("subtitles are on", "subtitles enabled"),
        "friendly": ("subtitles are on", "captions on"),
        "brief": ("subtitles on",),
    },
    "subs_off": {
        "jarvis": ("subtitles are off", "subtitles disabled"),
        "friendly": ("subtitles are off", "captions off"),
        "brief": ("subtitles off",),
    },
    "seek": {
        "jarvis": ("skipped", "moving along"),
        "friendly": ("skipped", "there you go"),
        "brief": ("skipped",),
    },
    "timer": {
        "jarvis": ("timer set", "I'll keep an eye on the time"),
        "friendly": ("timer's set", "I'll let you know"),
        "brief": ("timer set",),
    },
    "screen_off": {
        "jarvis": ("turning the screen off", "screen off"),
        "friendly": ("screen off", "lights out"),
        "brief": ("screen off",),
    },
    "qr": {
        "jarvis": ("the pairing code is on screen", "pairing code displayed"),
        "friendly": ("here's the code", "code's on the screen"),
        "brief": ("pairing code",),
    },
    "done": {
        "jarvis": ("done", "that's done"),
        "friendly": ("done", "all done"),
        "brief": ("done",),
    },
}

OPENERS: dict[str, tuple[str, ...]] = {
    "jarvis": ("", "", "Certainly. ", "Right away. "),
    "friendly": ("You got it! ", "On it! ", "", "Sure thing! "),
    "brief": ("",),
}
FAIL_PREFIX = {
    "jarvis": "I'm afraid that didn't work.",
    "friendly": "Oops, that didn't work.",
    "brief": "",
}
SAMPLE = {
    "jarvis": "Certainly. Now playing your favourite playlist on YouTube Music.",
    "friendly": "You got it! Playing your favourite playlist on YouTube Music.",
    "brief": "Playing your favourite playlist.",
}


@dataclass(frozen=True)
class Clause:
    kind: str
    q: str = ""
    site: str = ""
    level: int = 0


def _subject(text: str) -> str:
    """A title as you would write it: "the weeknd" becomes "The Weeknd"."""
    return capwords(text[:MAX_SUBJECT])


def _site_of_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("music."):
        return "YouTube Music"
    return "YouTube" if "youtu" in host else host.removeprefix("www.")


def _clause(step: ActionStep, plan: IntentPlan) -> Clause | None:
    a = step.args
    op = step.op
    if op == "navigate":
        if "query" in a:
            site = SITE_LABELS.get(str(a.get("source", "youtube")), "YouTube")
            kind = "play" if a.get("play", True) else "search"
            return Clause(kind, _subject(str(a["query"])), site)
        return Clause("open_site", site=_site_of_url(str(a.get("url", ""))))
    if op == "click_item":
        return Clause("open_item")
    if op == "page_search":
        return Clause("search", _subject(str(a.get("query", "that"))))
    if op == "subtitles":
        return Clause("subs_off" if a.get("state") == "off" else "subs_on")
    if op == "fullscreen":
        return Clause("fullscreen")
    if op == "close_ad":
        return Clause("ad")
    if op == "close_tab":
        return Clause("close_tab")
    if op == "volume_set":
        return Clause("volume_level", level=int(a.get("level", 0)))
    if op == "seek":
        return Clause("seek")
    if op == "timer_start":
        return Clause("timer")
    if op == "sleep_display":
        return Clause("screen_off")
    if op == "show_qr":
        return Clause("qr")
    if op == "speed":
        return Clause("speed")
    if op == "browser_nav":
        if a.get("action") == "exit_fullscreen":
            return Clause("windowed")
        return Clause("new_tab" if a.get("action") == "new_tab" else "tab")
    if op == "media_key":
        key = str(a.get("key", ""))
        if key == "mute":
            return Clause("unmute" if a.get("state") == "off" else "mute")
        return {
            "pause": Clause("pause"),
            "play": Clause("resume"),
            "play_pause": Clause("resume"),
            "next": Clause("next"),
            "prev": Clause("prev"),
            "vol_up": Clause("volume"),
            "vol_down": Clause("volume"),
        }.get(key)
    return None


def clauses_for(plan: IntentPlan) -> list[Clause]:
    found: list[Clause] = []
    for step in plan.steps:
        clause = _clause(step, plan)
        if clause is not None and clause not in found:
            found.append(clause)
    return found


def written_message(plan: IntentPlan) -> str | None:
    """The on-screen result line for a plan of several steps ("New tab, then playing X")."""
    found = clauses_for(plan)
    if len(found) < 2:
        return None
    parts = [
        PHRASES.get(c.kind, PHRASES["done"])["brief"][0].format(q=c.q, site=c.site, level=c.level)
        for c in found
    ]
    return parts[0][:1].upper() + parts[0][1:] + "".join(f", then {x}" for x in parts[1:])


class Phraser:
    """Picks a phrase, avoiding an immediate repeat so it never sounds like a recording."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._last: dict[str, str] = {}

    def _pick(self, key: str, options: tuple[str, ...]) -> str:
        fresh = [o for o in options if o != self._last.get(key)] or list(options)
        choice = self._rng.choice(fresh)
        self._last[key] = choice
        return choice

    def success(self, style: str, plan: IntentPlan | None) -> str:
        style = style if style in STYLES else "jarvis"
        found = clauses_for(plan) if plan is not None else []
        if not found:
            found = [Clause("done")]
        parts: list[str] = []
        for clause in found:
            table = PHRASES.get(clause.kind, PHRASES["done"])
            template = self._pick(f"{style}:{clause.kind}", table[style])
            parts.append(template.format(q=clause.q, site=clause.site, level=clause.level))
        body = parts[0] if len(parts) == 1 else ", and ".join(parts)
        opener = self._pick(f"{style}:opener", OPENERS[style]) if len(parts) == 1 else ""
        sentence = body[:1].upper() + body[1:]
        end = "!" if style == "friendly" and not opener.endswith("! ") and len(parts) == 1 else "."
        return f"{opener}{sentence}{end}"

    def failure(self, style: str, message: str) -> str:
        style = style if style in STYLES else "jarvis"
        text = message.strip().rstrip(".!") or "that did not work"
        if text.lower().startswith(("sorry", "i ")):
            return f"{text}."
        prefix = FAIL_PREFIX[style]
        return f"{prefix} {text}.".strip()

    def sample(self, style: str) -> str:
        return SAMPLE.get(style, SAMPLE["jarvis"])
