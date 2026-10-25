"""Deterministic intent shortcuts: common commands never wait on an LLM."""

from __future__ import annotations

import re

from agent.state import ActionStep, Intent, IntentPlan
from contracts import QuickAction
from executors.subtitles import SPOKEN_LANGUAGES

_FILLER = re.compile(r"^(?:hey |ok |okay )?(?:(?:armrest|jarvis)[, ]+)?(?:please[, ]+)?")
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
    engines = r"(google|bing|duckduckgo|edge|the web|the internet)"
    for pattern in (
        rf"(?:search|look up|find)(?: for)? (.+?) (?:on|in|with|using) {engines}",
        rf"(?:search|look up) {engines} for (.+)",
        r"google (.+)",
    ):
        m = re.fullmatch(pattern, t)
        if m:
            named = [g for g in m.groups() if g in _ENGINE_NAMES]
            query = next(g for g in m.groups() if g not in _ENGINE_NAMES).strip()
            engine = _ENGINE_NAMES[named[0]] if named else "google"
            return _plan(
                Intent.NAVIGATE,
                [ActionStep(op="web_search", args={"query": query, "engine": engine})],
                {"query": query},
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


_CLICK_NOT_TEXT = {"it", "that", "this", "there", "here", "screen", "the screen", "mouse", "button"}


def _plan_click_text(t: str) -> IntentPlan | None:
    """ "click on Season 1", "press Play", "tap the button that says Next": by what it says."""
    q = chr(34) + chr(39) + chr(8220) + chr(8221)
    m = re.fullmatch(
        r"(?:click|press|tap|hit|select|choose|touch)(?: on)?(?: the)?"
        r"(?: (?:button|link|option|tab|text|word|menu|dropdown|drop down))?"
        r"(?: (?:that |which )?(?:says|reads|called|named|labeled|labelled|with))?"
        rf"[ {q}]*(.+?)[ {q}]*"
        r"(?: (?:on|in) (?:the|this) (?:screen|page|tv))?(?: (?:button|link|option|tab))?"
        r"(?: please)?",
        t,
    )
    if m is None:
        return None
    text = m.group(1).strip()
    if text in _CLICK_NOT_TEXT or len(text) > 60:
        return None
    return _plan(
        Intent.NAVIGATE, [ActionStep(op="click_text", args={"text": text})], {"text": text}
    )


_ENGINE_NAMES = {
    "google": "google", "bing": "bing", "duckduckgo": "duckduckgo",
    "edge": "bing", "the web": "bing", "the internet": "bing",
}  # fmt: skip
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


# Sterling / Aurora are the names the phone shows; the older Jarvis / Friday still work when spoken.
_VOICE_NAMES = {
    "sterling": "british_male", "aurora": "british_female",
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


_AD = r"(?:ads?|ad ?block(?:ing|er)?|popups?|pop[- ]ups?|popup blocker|ad blocker)"


def _plan_browser_prefs(t: str) -> IntentPlan | None:
    """ "turn off pop-up protection" / "block pop-ups" / "allow popups": the phone's switch.

    The switch only closes tabs a site opens by itself and undoes redirects; it never blocks
    ads. The older "ad blocking" wording still works for people used to it.
    """
    blocker = (
        r"(?:ad ?block(?:ing|er)?|pop[- ]?up blocker|pop[- ]?up blocking|pop[- ]?up protection)"
    )
    allow = r"(?:allow|let|stop blocking) (?:all )?(?:ads?|pop[- ]?ups?)(?: through)?"
    if re.fullmatch(rf"(?:turn off|switch off|disable|stop|deactivate) (?:the )?{blocker}", t) or (
        re.fullmatch(allow, t)
    ):
        return _plan(Intent.SYSTEM, [ActionStep(op="browser_pref", args={"adblock": False})])
    if re.fullmatch(rf"(?:turn on|switch on|enable|start|activate) (?:the )?{blocker}", t) or (
        re.fullmatch(r"block (?:all )?(?:ads?|pop[- ]?ups?)", t)
    ):
        return _plan(Intent.SYSTEM, [ActionStep(op="browser_pref", args={"adblock": True})])
    return None


_SAVED = r"(?:bookmarks?|bookmarked|saved|favou?rites?|favou?rite)"


def _bookmark(**args: str | bool) -> IntentPlan:
    return _plan(Intent.SYSTEM, [ActionStep(op="bookmark", args=dict(args))])


def _plan_bookmarks(t: str) -> IntentPlan | None:
    """ "save this page", "open my bookmark netflix", "remove the bookmark for bbc"."""
    this = r"(?:this|the current|the open)(?: page| site| tab| link| website)?"
    mine = rf"(?:my |the )?{_SAVED}(?: pages| list)?"
    if re.fullmatch(rf"(?:save|bookmark|favou?rite|pin|keep) {this}(?: for later)?", t):
        return _bookmark(action="add")
    if re.fullmatch(rf"(?:add|put) {this} (?:to|in|into|on) {mine}", t):
        return _bookmark(action="add")
    if re.fullmatch(rf"(?:remove|delete|forget|unsave|unbookmark|take) {this} (?:from )?{mine}", t):
        return _bookmark(action="remove", current=True)
    if re.fullmatch(r"(?:unsave|unbookmark) (?:this|it)(?: page)?", t):
        return _bookmark(action="remove", current=True)
    named = r"(?: page| site| link)?(?: for| called| named|:)? (.+)"
    m = re.fullmatch(rf"(?:remove|delete|forget|unsave|unbookmark) (?:the |my )?{_SAVED}{named}", t)
    if m:
        return _bookmark(action="remove", name=m.group(1).strip())
    m = re.fullmatch(
        rf"(?:open|go to|load|show|pull up|take me to|play) (?:my |the |a )?{_SAVED}{named}", t
    ) or re.fullmatch(rf"(?:open|go to|load|pull up|take me to) (.+?) (?:from|in|on) {mine}", t)
    if m:
        return _bookmark(action="open", name=m.group(1).strip())
    return None


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
        m.group(1) in ("sterling", "aurora", "jarvis", "friday", "chill", "aria")
        or " " in m.group(1)
        or "voice" in t
    ):
        return _voice(voice=_VOICE_NAMES[m.group(1)])
    return None


def plan_text(utterance: str) -> IntentPlan | None:
    """Return a plan for a recognized command, else None (caller falls back to the LLM).

    A request made of several recognized commands ("open a new tab and play X on youtube music")
    becomes ONE plan whose steps run in order."""
    if _SEARCH_THEN_PICK.match(normalize(utterance)):
        return (
            None  # "search X and play a song with Y": needs a look at the results, so ask the LLM
        )
    return _plan_single(utterance) or _plan_web_question(utterance) or _plan_compound(utterance)


_SEARCH_THEN_PICK = re.compile(
    r"^(?:search|look up|find|google)\b.+?\b(?:and|then|and then)\s+(?:play|open|click|pick|choose|"
    r"select|watch|put on|start)\b.+\b(?:with|by|featuring|feat|from|where|that|which|any|the "
    r"(?:best|newest|latest|first|top|most))\b"
)


# Questions about the world, not the screen: Edge/Bing (with Copilot's summary) answers them.
_WEB_QUESTION = re.compile(
    r"^(?:what(?:'s| is| are| was| were)|who(?:'s| is| was| are| won| wins| invented| made| wrote)|"
    r"when (?:is|was|did|does)|"
    r"where (?:is|are|was)|why (?:is|are|do|does|did)|"
    r"how (?:do|does|did|many|much|old|tall|far|long)"
    r"(?: i| you| to)?|how's|how is|can you tell me|tell me|do you know)\b.+"
    r"|^.*\b(?:weather|forecast|temperature|news|score|scores|stock price|exchange rate)\b.*$"
)
# "what is playing", "what episode is this", "who sings this": about the screen, the LLM answers.
_ABOUT_THE_SCREEN = re.compile(
    r"\b(?:this|that|these|those|it|its|playing|on (?:the )?screen|here|current|now playing|"
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


_KEY_NAMES = {
    "escape": "esc", "esc": "esc", "enter": "enter", "return": "enter", "tab": "tab",
    "space": "space", "spacebar": "space", "space bar": "space", "backspace": "backspace",
    "delete": "delete", "home": "home", "end": "end", "page up": "pageup", "page down": "pagedown",
    "left": "left", "right": "right", "up": "up", "down": "down",
    "left arrow": "left", "right arrow": "right", "up arrow": "up", "down arrow": "down",
}  # fmt: skip
_MOD_NAMES = {"control": "ctrl", "ctrl": "ctrl", "alt": "alt", "shift": "shift"}
_KEY_WORD = "|".join(sorted((re.escape(k) for k in _KEY_NAMES), key=len, reverse=True))
_MOD_WORD = "|".join(_MOD_NAMES)
_ONE_KEY = rf"(?:f ?(?:1[0-2]|[1-9])|{_KEY_WORD}|[a-z0-9])"
_PRESS = re.compile(
    rf"(?:press|hit|tap|push|send)(?: the)?"
    rf"((?: (?:{_MOD_WORD})(?: key)?(?: and| plus|\+)?)*) ?({_ONE_KEY})(?: key| button)?"
)


def _plan_keypress(t: str) -> IntentPlan | None:
    """ "press F11", "hit escape", "press control L", "press alt left": a key chord, as said."""
    m = _PRESS.fullmatch(t)
    if m is None:
        return None
    mods = [_MOD_NAMES[w] for w in re.findall(_MOD_WORD, m.group(1))]
    name = m.group(2).replace("f ", "f")  # "f 11" is how it hears "F11"
    key = _KEY_NAMES.get(name, name)
    args: dict[str, str | int | float | bool] = {"key": key}
    if mods:
        args["mods"] = "+".join(dict.fromkeys(mods))
    return _plan(Intent.SYSTEM, [ActionStep(op="press_key", args=args)])


def _plan_single(utterance: str) -> IntentPlan | None:
    t = normalize(utterance)

    page = (
        _plan_voice(t)
        or _plan_keypress(t)
        or _plan_bookmarks(t)
        or _plan_browser_prefs(t)
        or _plan_subtitles(t)
        or _plan_browser(t)
        or _plan_site(t)
        or _plan_page_actions(t)
        or _plan_click_text(t)
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

    qr = r"(?:the |a |my )?(?:pairing )?qr(?: code)?(?: on (?:the )?(?:tv|screen))?"
    if re.fullmatch(
        rf"(?:hide|remove|clear|dismiss|get rid of|turn off|take down|stop showing) {qr}", t
    ):
        return _plan(Intent.SYSTEM, [ActionStep(op="qr_control", args={"action": "hide"})])
    if re.fullmatch(rf"(?:toggle|flip) {qr}", t):
        return _plan(Intent.SYSTEM, [ActionStep(op="qr_control", args={"action": "toggle"})])
    if re.fullmatch(
        rf"(?:reset|refresh|regenerate|renew|rotate|change|new|make a new|generate a new) {qr}", t
    ):
        return _plan(Intent.SYSTEM, [ActionStep(op="qr_control", args={"action": "reset"})])
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
    if action in ("volume_set", "seek_to"):
        raise ValueError(f"{action} is a slider: the service applies it directly")
    if action == "buy":
        raise ValueError("buy is handled by the service (licensing), never by the agent")
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
