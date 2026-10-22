"""Deterministic intent shortcuts: common commands never wait on an LLM."""

from __future__ import annotations

import re

from agent.state import ActionStep, Intent, IntentPlan
from contracts import QuickAction

_FILLER = re.compile(r"^(?:hey |ok |okay )?(?:jarvis[, ]+)?(?:please[, ]+)?")
_TRAILING = re.compile(r"[\s.!?,]+$")
_MEDIA_NOUN = r"(?: the)?(?: video| music| song| track| it)?"

_NUM = r"(\d{1,3})"


def normalize(utterance: str) -> str:
    text = utterance.strip().lower()
    text = _FILLER.sub("", text)
    return _TRAILING.sub("", text).strip()


def _plan(
    intent: Intent, steps: list[ActionStep], slots: dict[str, str | int | float] | None = None
) -> IntentPlan:
    return IntentPlan(
        intent=intent, confidence=1.0, slots=slots or {}, steps=steps, source="fastpath"
    )


def _media(key: str, expect: str | None = None, **args: str | int | bool) -> IntentPlan:
    return _plan(
        Intent.MEDIA_CONTROL, [ActionStep(op="media_key", args={"key": key, **args}, expect=expect)]
    )


def _volume_key(key: str, repeat: int = 5) -> IntentPlan:
    return _plan(Intent.VOLUME, [ActionStep(op="media_key", args={"key": key, "repeat": repeat})])


def _mute(state: str) -> IntentPlan:
    expect = {"on": "muted", "off": "unmuted"}.get(state)
    return _plan(
        Intent.VOLUME,
        [ActionStep(op="media_key", args={"key": "mute", "state": state}, expect=expect)],
    )


def _seek(delta: int) -> IntentPlan:
    return _plan(Intent.MEDIA_CONTROL, [ActionStep(op="seek", args={"delta_s": delta})])


_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}  # fmt: skip
_ORD = rf"(\d{{1,3}})(?:st|nd|rd|th)|({'|'.join(_ORDINALS)})"
_PICK_VERB = r"(?:(?:play|watch|put on|open|start|click|select|choose|pick)(?: on)? )?"


def _ordinal(m: re.Match[str], first_group: int) -> int:
    digits, word = m.group(first_group), m.group(first_group + 1)
    return int(digits) if digits else _ORDINALS[word]


def _pick_plan(**args: int) -> IntentPlan:
    return _plan(Intent.MEDIA_PLAY, [ActionStep(op="click_item", args=dict(args))])


def _search_plan(query: str, music: bool) -> IntentPlan:
    source = "ytmusic" if music else "youtube"
    return _plan(
        Intent.MEDIA_PLAY,
        [ActionStep(op="navigate", args={"query": query, "source": source, "play": False})],
        {"query": query},
    )


def _browser(action: str) -> IntentPlan:
    return _plan(Intent.TAB, [ActionStep(op="browser_nav", args={"action": action})])


def _plan_browser(t: str) -> IntentPlan | None:
    """Tabs, history, reload and scrolling: "new tab" must never close one."""
    if re.fullmatch(r"(?:open |make |start |create |give me )?(?:a )?new tab(?: please)?", t):
        return _browser("new_tab")
    if re.fullmatch(
        r"(?:go back|back a page|navigate back|(?:go (?:back )?to )?(?:the )?previous page"
        r"|go back to (?:the )?(?:previous|last) page)",
        t,
    ):
        return _browser("back")
    if re.fullmatch(r"(?:go forward|forward a page|navigate forward|next page)", t):
        return _browser("forward")
    if re.fullmatch(r"(?:reload|refresh)(?: the| this)?(?: page| tab)?", t):
        return _browser("reload")
    if re.fullmatch(r"(?:next|switch to the next|go to the next) tab", t):
        return _browser("next_tab")
    if re.fullmatch(r"(?:previous|prev|last|switch to the previous|go to the previous) tab", t):
        return _browser("prev_tab")
    m = re.fullmatch(r"scroll (up|down)(?: a (?:bit|little)| some| more| a lot)?", t)
    if m:
        return _browser(f"scroll_{m.group(1)}")
    return None


def _plan_page_actions(t: str) -> IntentPlan | None:
    """Search-only and "pick the Nth thing on this page": never leaves the current site."""
    m = re.fullmatch(r"(?:search|look up)(?: for)? (.+?) on (youtube music|yt music|youtube)", t)
    if m:
        return _search_plan(m.group(1), "music" in m.group(2) or m.group(2) == "yt music")
    m = re.fullmatch(r"search (youtube music|yt music|youtube) for (.+)", t)
    if m:
        return _search_plan(m.group(2), "music" in m.group(1) or m.group(1) == "yt music")
    m = re.fullmatch(r"(?:search(?: for)?|look up) (.+)", t)
    if m:
        return _search_plan(m.group(1), False)

    ep = rf"(?:episode|ep\.?)(?: number)? ?{_NUM}"
    m = re.fullmatch(rf"{_PICK_VERB}(?:the )?{ep}(?: please)?", t)
    if m:
        return _pick_plan(episode=int(m.group(1)))
    m = re.fullmatch(rf"{_PICK_VERB}(?:the )?(?:{_ORD}) episode(?: please)?", t)
    if m:
        return _pick_plan(episode=_ordinal(m, 1))
    m = re.fullmatch(
        rf"{_PICK_VERB}(?:the )?(?:{_ORD})(?: one| result| video| link| item| song| track)?"
        r"(?: please)?",
        t,
    )
    if m and (m.group(1) or m.group(2)):
        return _pick_plan(result=_ordinal(m, 1))
    m = re.fullmatch(rf"{_PICK_VERB}(?:number|no\.?|#) ?{_NUM}(?: please)?", t)
    if m:
        return _pick_plan(result=int(m.group(1)))
    return None


def plan_text(utterance: str) -> IntentPlan | None:
    """Return a plan for a recognized command, else None (caller falls back to the LLM)."""
    t = normalize(utterance)

    page = _plan_browser(t) or _plan_page_actions(t)
    if page is not None:
        return page

    if re.fullmatch(rf"(?:pause|stop|hold on|hold up){_MEDIA_NOUN}", t):
        return _media("pause", "paused")
    if re.fullmatch(rf"(?:resume|continue|unpause|play|start){_MEDIA_NOUN}", t):
        return _media("play", "video_playing")
    if re.fullmatch(rf"(?:next|skip|skip to next|play next){_MEDIA_NOUN}", t):
        return _media("next")
    if re.fullmatch(rf"(?:previous|prev|last|play previous){_MEDIA_NOUN}", t):
        return _media("prev")

    m = re.fullmatch(rf"(?:set |turn |change )?(?:the )?volume (?:to|at) {_NUM}(?: percent|%)?", t)
    if m or (m := re.fullmatch(rf"(?:set |make it |turn it to ){_NUM}(?: percent|%)? ?volume", t)):
        level = min(100, int(m.group(1)))
        return _plan(
            Intent.VOLUME,
            [ActionStep(op="volume_set", args={"level": level}, expect="volume_set")],
            {"level": level},
        )
    if re.fullmatch(r"(?:volume up|turn (?:it |the volume )?up|louder|raise the volume)", t):
        return _volume_key("vol_up")
    if re.fullmatch(r"(?:volume down|turn (?:it |the volume )?down|quieter|lower the volume)", t):
        return _volume_key("vol_down")
    if re.fullmatch(r"(?:mute|silence|be quiet)(?: the)?(?: tv| sound| audio)?", t):
        return _mute("on")
    if re.fullmatch(r"(?:unmute|turn the sound back on)(?: the)?(?: tv| sound| audio)?", t):
        return _mute("off")

    if re.fullmatch(
        r"(?:(?:make it |go |turn on |enter )?full ?screen|maximize(?: the video)?)", t
    ):
        return _plan(Intent.FULLSCREEN, [ActionStep(op="fullscreen", expect="is_fullscreen")])
    if re.fullmatch(r"(?:skip|close|get rid of)(?: the| this)? ads?", t):
        return _plan(Intent.MEDIA_CONTROL, [ActionStep(op="close_ad", expect="no_ad")])
    if re.fullmatch(r"close(?: this| the)? tab", t):
        return _plan(Intent.TAB, [ActionStep(op="close_tab")])

    m = re.fullmatch(rf"(?:skip ahead|seek forward|forward|fast forward)(?: {_NUM} seconds?)?", t)
    if m:
        return _seek(int(m.group(1) or 10))
    m = re.fullmatch(rf"(?:skip back|seek back(?:ward)?|rewind|back)(?: {_NUM} seconds?)?", t)
    if m:
        return _seek(-int(m.group(1) or 10))

    m = re.fullmatch(
        rf"(?:set (?:a |an )?|start (?:a |an )?)?(sleep )?timer (?:for |of )?{_NUM} ?"
        r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)?",
        t,
    )
    if m:
        amount = int(m.group(2))
        unit = (m.group(3) or "minutes")[0]
        minutes = amount / 60 if unit == "s" else amount * 60 if unit == "h" else float(amount)
        action = "pause" if m.group(1) else "notify"
        return _plan(
            Intent.TIMER,
            [
                ActionStep(
                    op="timer_start",
                    args={
                        "minutes": minutes,
                        "label": f"{amount} {m.group(3) or 'minutes'}",
                        "action": action,
                    },
                )
            ],
            {"minutes": minutes},
        )

    if re.fullmatch(
        r"(?:show|display|bring up|pull up)(?: me)?(?: the| a)?(?: pairing)? ?qr(?: code)?", t
    ) or re.fullmatch(r"(?:pair|add|connect)(?: a| another)? (?:friend|phone|device)s?", t):
        return _plan(Intent.SYSTEM, [ActionStep(op="show_qr")])
    if re.fullmatch(r"(?:sleep|turn off|blank)(?: the)? (?:screen|display|tv)", t):
        return _plan(Intent.SYSTEM, [ActionStep(op="sleep_display")])
    return None


def plan_button(
    action: QuickAction, args: dict[str, str | int | float | bool] | None = None
) -> IntentPlan:
    """Map a Quick Deck button to its single-step plan."""
    del args  # reserved for parameterized buttons
    table: dict[str, IntentPlan] = {
        "play_pause": _media("play_pause"),
        "next": _media("next"),
        "prev": _media("prev"),
        "vol_up": _volume_key("vol_up"),
        "vol_down": _volume_key("vol_down"),
        "mute": _mute("toggle"),
        "fullscreen": _plan(
            Intent.FULLSCREEN, [ActionStep(op="fullscreen", expect="is_fullscreen")]
        ),
        "skip_ad": _plan(Intent.MEDIA_CONTROL, [ActionStep(op="close_ad", expect="no_ad")]),
        "close_tab": _plan(Intent.TAB, [ActionStep(op="close_tab")]),
        "seek_fwd_10": _seek(10),
        "seek_back_10": _seek(-10),
        "sleep_display": _plan(Intent.SYSTEM, [ActionStep(op="sleep_display")]),
        "show_qr": _plan(Intent.SYSTEM, [ActionStep(op="show_qr")]),
        "panic": _media("pause", "paused"),
        "back": _browser("back"),
        "forward": _browser("forward"),
        "reload": _browser("reload"),
        "new_tab": _browser("new_tab"),
        "next_tab": _browser("next_tab"),
        "prev_tab": _browser("prev_tab"),
        "scroll_up": _browser("scroll_up"),
        "scroll_down": _browser("scroll_down"),
    }
    return table[action]
