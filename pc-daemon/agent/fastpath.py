"""Deterministic intent shortcuts: common commands never wait on an LLM."""

from __future__ import annotations

import re

from agent.state import ActionStep, Intent, IntentPlan
from contracts import QuickAction
from executors.subtitles import SPOKEN_LANGUAGES

_FILLER = re.compile(r"^(?:hey |ok |okay )?(?:jarvis[, ]+)?(?:please[, ]+)?")
_TRAILING = re.compile(r"[\s.!?,]+$")
_MEDIA_NOUN = r"(?: the)?(?: video| music| song| track| it)?"

_NUM = r"(\d{1,3})"


# Speech recognition spells the same thing several ways.
_SPOKEN_FIXES = (
    (re.compile(r"\byou tube\b"), "youtube"),
    (re.compile(r"\b(?:yt|y t) music\b"), "youtube music"),
    (re.compile(r"\byoutube[.,] music\b"), "youtube music"),
    (re.compile(r"\bnew tab[.,] and\b"), "new tab and"),
)


def normalize(utterance: str) -> str:
    text = utterance.strip().lower()
    text = _FILLER.sub("", text)
    for pattern, fix in _SPOKEN_FIXES:
        text = pattern.sub(fix, text)
    text = _TRAILING.sub("", text).strip()
    return re.sub(r"(?:,? please)+$", "", text).strip()


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


_SUBS = r"(?:subtitles?|closed captions?|captions?|cc)"
_LANG = "|".join(sorted(SPOKEN_LANGUAGES, key=len, reverse=True))


def _subtitles(state: str, lang: str | None = None) -> IntentPlan:
    args: dict[str, str | int | float | bool] = {"state": state}
    if lang:
        args["lang"] = SPOKEN_LANGUAGES[lang]
    return _plan(Intent.MEDIA_CONTROL, [ActionStep(op="subtitles", args=args)])


def _plan_subtitles(t: str) -> IntentPlan | None:
    """ "turn on subtitles", "subtitles off", "spanish subtitles", "captions in german"."""
    end = r"(?: please)?"
    if re.fullmatch(rf"(?:turn off|switch off|disable|hide|remove|stop)(?: the)? {_SUBS}{end}", t):
        return _subtitles("off")
    if re.fullmatch(rf"{_SUBS} off{end}", t):
        return _subtitles("off")
    on = r"(?:turn on|switch on|enable|show|display|put on|activate)"
    m = re.fullmatch(rf"{on}(?: the)?(?: ({_LANG}))? {_SUBS}(?: in ({_LANG}))?{end}", t)
    if m:
        return _subtitles("on", m.group(1) or m.group(2))
    m = re.fullmatch(rf"(?:the )?(?:({_LANG}) )?{_SUBS}(?: on)?(?: in ({_LANG}))?{end}", t)
    if m:
        lang = m.group(1) or m.group(2)
        return _subtitles("on" if lang or t.endswith(" on") else "toggle", lang)
    return None


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
    site = r"(youtube music|yt music|youtube)"
    m = re.fullmatch(rf"(?:search|look up)(?: for)? (.+?) on {site} in a new tab", t)
    if m:  # the follow-up offered when the page has no search bar
        music = "music" in m.group(2) or m.group(2) == "yt music"
        return _plan(
            Intent.MEDIA_PLAY,
            [
                ActionStep(op="browser_nav", args={"action": "new_tab"}),
                *_search_plan(m.group(1), music).steps,
            ],
            {"query": m.group(1)},
        )
    m = re.fullmatch(rf"(?:search|look up)(?: for)? (.+?) on {site}", t)
    if m:
        return _search_plan(m.group(1), "music" in m.group(2) or m.group(2) == "yt music")
    m = re.fullmatch(rf"search {site} for (.+)", t)
    if m:
        return _search_plan(m.group(2), "music" in m.group(1) or m.group(1) == "yt music")
    m = re.fullmatch(r"(?:search(?: for)?|look up) (.+)", t)
    if m:  # no site named: use the search bar of the page being shown
        query = m.group(1).strip()
        return _plan(
            Intent.NAVIGATE,
            [ActionStep(op="page_search", args={"query": query})],
            {"query": query},
        )

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


_SITES = {"youtube music": "ytmusic", "youtube": "youtube"}
_SITE_URLS = {"youtube music": "https://music.youtube.com/", "youtube": "https://www.youtube.com/"}
_NEW_TAB = ActionStep(op="browser_nav", args={"action": "new_tab"})


def _plan_site(t: str) -> IntentPlan | None:
    """ "play X on youtube music (in a new tab)" and "open youtube": the site is named, so
    leaving the current page is exactly what was asked for."""
    site = r"(youtube music|youtube)"
    m = re.fullmatch(
        rf"(?:play|put on|queue up|start)(?: some)? (.+?) (?:on|in|from|using|with) (?:the )?{site}"
        r"(?: (?:app|website|site))?( in a new tab)?",
        t,
    )
    if m:
        query, name = m.group(1).strip(), m.group(2)
        play = ActionStep(
            op="navigate",
            args={"query": query, "source": _SITES[name], "play": True},
            expect="video_playing",
        )
        steps = [_NEW_TAB, play] if m.group(3) else [play]
        return _plan(Intent.MEDIA_PLAY, steps, {"query": query, "site": name})
    m = re.fullmatch(
        rf"(?:open|go to|launch|take me to|bring up|start)(?: up)?(?: the)? {site}"
        r"(?: (?:app|website|site|home ?page))?( in a new tab)?",
        t,
    )
    if m:
        name = m.group(1)
        go = ActionStep(op="navigate", args={"url": _SITE_URLS[name]}, expect="navigated")
        steps = [_NEW_TAB, go] if m.group(2) else [go]
        return _plan(Intent.NAVIGATE, steps, {"site": name})
    return None


_VOICE_NAMES = {
    "jarvis": "british_male", "british male": "british_male", "british": "british_male",
    "butler": "british_male", "friday": "british_female", "british female": "british_female",
    "chill": "us_male", "american male": "us_male", "us male": "us_male",
    "aria": "us_female", "american female": "us_female", "us female": "us_female",
}  # fmt: skip
_STYLE_NAMES = {
    "jarvis": "jarvis", "butler": "jarvis", "formal": "jarvis", "polite": "jarvis",
    "friendly": "friendly", "casual": "friendly", "chatty": "friendly",
    "brief": "brief", "short": "brief", "concise": "brief", "minimal": "brief",
}  # fmt: skip


def _voice(**args: str | int | float | bool) -> IntentPlan:
    return _plan(Intent.SYSTEM, [ActionStep(op="voice_pref", args=args)])


def _plan_voice(t: str) -> IntentPlan | None:
    """Spoken-reply settings, so the phone's settings sheet and "stop talking" share one path."""
    noun = r"(?:the )?(?:voice|spoken|talking|audio) (?:replies|reply|responses|feedback)"
    m = re.fullmatch(rf"(?:(?:turn|switch|set) )?{noun} (on|off)", t) or re.fullmatch(
        rf"(?:turn|switch) (on|off) {noun}", t
    )
    if m:
        return _voice(enabled=m.group(1) == "on")
    if re.fullmatch(r"stop talking|stop replying|stop speaking|no more talking", t):
        return _voice(enabled=False)
    if re.fullmatch(r"start talking|talk to me|speak to me|start replying", t):
        return _voice(enabled=True)
    if re.fullmatch(r"(?:also )?(?:speak|talk|reply) (?:for|on) (?:the )?buttons?(?: taps?)?", t):
        return _voice(buttons=True)
    if re.fullmatch(r"(?:don't|do not|stop) (?:speak|talk|reply) (?:for|on) (?:the )?buttons?", t):
        return _voice(buttons=False)
    if re.fullmatch(r"(?:test|try|check)(?: out)?(?: the| your)? voice|say something", t):
        return _voice(test=True)
    styles = "|".join(sorted(_STYLE_NAMES, key=len, reverse=True))
    m = re.fullmatch(
        rf"(?:(?:set|change|use) )?(?:the )?(?:voice |reply )?style(?: to)? ({styles})", t
    )
    m = m or re.fullmatch(rf"(?:be|sound|talk|reply)(?: a bit)?(?: more)? ({styles})", t)
    if m:
        return _voice(style=_STYLE_NAMES[m.group(1)])
    names = "|".join(sorted(_VOICE_NAMES, key=len, reverse=True))
    m = re.fullmatch(rf"(?:use|switch to|change to|set|try)(?: the)? ({names}) voice", t)
    m = m or re.fullmatch(rf"(?:use|switch to|change to|set|try)(?: the)? ({names})", t)
    m = m or re.fullmatch(
        rf"(?:change|set|switch)(?: the| your)? voice(?: to)?(?: the)? ({names})", t
    )
    if m and (
        m.group(1) in ("jarvis", "friday", "chill", "aria") or " " in m.group(1) or "voice" in t
    ):
        return _voice(voice=_VOICE_NAMES[m.group(1)])
    return None


def plan_text(utterance: str) -> IntentPlan | None:
    """Return a plan for a recognized command, else None (caller falls back to the LLM).

    A request made of several recognized commands ("open a new tab and play X on youtube music")
    becomes ONE plan whose steps run in order."""
    return _plan_single(utterance) or _plan_web_question(utterance) or _plan_compound(utterance)


# Questions about the world, not the screen: Edge/Bing (with Copilot's summary) answers them.
_WEB_QUESTION = re.compile(
    r"^(?:what(?:'s| is| are| was| were)|who(?:'s| is| was| are| won| wins| invented| made| wrote| directed| plays)|when (?:is|was|did|does)|"
    r"where (?:is|are|was)|why (?:is|are|do|does|did)|how (?:do|does|did|many|much|old|tall|far|long)"
    r"(?: i| you| to)?|how's|how is|which|is it|are there|can you tell me|tell me|do you know)\b.+"
    r"|^.*\b(?:weather|forecast|temperature|news|score|scores|stock price|exchange rate)\b.*$"
)
# "what is playing", "what episode is this", "who sings this": about the screen, the LLM answers.
_ABOUT_THE_SCREEN = re.compile(
    r"\b(?:this|that|these|those|playing|on (?:the )?screen|here|current|now playing|"
    r"i (?:just )?(?:said|asked|played)|you (?:just )?(?:did|said|played|opened))\b"
)


def _plan_web_question(utterance: str) -> IntentPlan | None:
    t = normalize(utterance)
    if len(t.split()) < 3 or not _WEB_QUESTION.match(t) or _ABOUT_THE_SCREEN.search(t):
        return None
    query = re.sub(r"^(?:can you )?(?:tell me|do you know)\s+", "", t).strip(" ?.!")
    return _plan(
        Intent.NAVIGATE,
        [ActionStep(op="web_search", args={"query": query})],
        {"query": query},
    )


_JOINERS = re.compile(r"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|\band then\b|\bthen\b|\band\b)\s*")
MAX_PARTS = 4


def _plan_compound(utterance: str) -> IntentPlan | None:
    """Split on "and" / "then" / commas where EVERY piece is a command we understand.

    Splitting only when all pieces parse keeps "search tom and jerry" in one piece, and the
    attempt that yields the most pieces wins ("mute, new tab and fullscreen")."""
    t = normalize(utterance)
    cuts = [m.span() for m in _JOINERS.finditer(t)]
    if not cuts or len(cuts) > MAX_PARTS * 2:
        return None
    best: list[IntentPlan] = []
    for mask in range(1, 2 ** len(cuts)):  # every way of choosing which joiners really split
        chosen = [cuts[i] for i in range(len(cuts)) if mask >> i & 1]
        if len(chosen) + 1 > MAX_PARTS or len(chosen) + 1 <= len(best):
            continue
        bounds = [0, *[x for span in chosen for x in span], len(t)]
        pieces = [t[bounds[i] : bounds[i + 1]].strip() for i in range(0, len(bounds), 2)]
        plans = [_plan_single(piece) if piece else None for piece in pieces]
        if all(p is not None for p in plans):
            best = [p for p in plans if p is not None]
    if len(best) < 2:
        return None
    return merge_plans(best)


def merge_plans(plans: list[IntentPlan]) -> IntentPlan | None:
    """One plan out of several, steps in order. The last part decides the overall intent."""
    steps: list[ActionStep] = []
    slots: dict[str, str | int | float] = {}
    for plan in plans:
        for step in plan.steps:
            if not (steps and steps[-1] == step and step.op == "browser_nav"):  # no double tabs
                steps.append(step)
        slots.update(plan.slots)
    if len(steps) > 8:
        return None
    slots["parts"] = len(plans)
    return IntentPlan(
        intent=plans[-1].intent, confidence=1.0, slots=slots, steps=steps, source="fastpath"
    )


def _plan_single(utterance: str) -> IntentPlan | None:
    t = normalize(utterance)

    page = (
        _plan_voice(t)
        or _plan_subtitles(t)
        or _plan_browser(t)
        or _plan_site(t)
        or _plan_page_actions(t)
    )
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
        "subtitles": _subtitles("toggle"),
    }
    return table[action]
