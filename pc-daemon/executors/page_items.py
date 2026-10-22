"""Choosing one clickable thing on the current page ("episode 5", "the pilot", "second result").

The browser reports numbered candidates; this module only decides which index the user meant, so
the model (or a fast-path rule) never supplies a selector and never clicks anything that was not
on the reported list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ITEM_NOT_FOUND = "I could not find that on this page."
NO_SEARCH_BAR = "I couldn't find a search bar on this page."
NO_SUBTITLES = "I couldn't find subtitles on this player."
REDIRECT_BLOCKED = "That site tried to open another website, so I blocked it."


@dataclass(frozen=True)
class Candidate:
    index: int
    text: str
    group: str = ""  # "result" for rows that are known search/content results


_SEP = r"(?:[.:)\-–—·|]|\n|$)"


def _episode_score(text: str, n: int) -> int:
    t = text.strip().lower()
    num = rf"0*{n}(?!\d)"
    if re.match(rf"{num}\s*{_SEP}", t) or re.match(rf"(?:episode|ep\.?|e)\s*{num}", t):
        return 3
    if re.match(rf"{num}\s+[a-z]", t):
        return 2
    return 0  # "Torrents for episode 5", "Continue S1 · E5" etc. must not match


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def pick_item(
    items: list[Candidate],
    *,
    index: int | None = None,
    text: str | None = None,
    episode: int | None = None,
    result: int | None = None,
) -> int | None:
    """Return the index (as reported by the browser) of the best match, or None."""
    if index is not None:
        hit = next((c for c in items if c.index == index), None)
        if hit is not None and (not text or text.strip().lower() in hit.text.lower()):
            return hit.index
        if not text:
            return None  # the page changed under us: do not click whatever sits at that slot now

    if episode is not None:
        best: tuple[int, int] | None = None  # (score, index)
        for c in items:
            if len(c.text) > 160:
                continue
            score = _episode_score(c.text, episode)
            if score and (best is None or score > best[0]):
                best = (score, c.index)
        return best[1] if best else None

    if result is not None and result >= 1:
        rows = [c for c in items if c.group == "result"] or [
            c for c in items if len(c.text.strip()) >= 12
        ]
        return rows[result - 1].index if result <= len(rows) else None

    if text:
        want = text.strip().lower()
        contains = [c for c in items if want and want in c.text.lower()]
        if contains:
            return min(contains, key=lambda c: len(c.text)).index
        wanted = _words(want)
        scored = [(len(wanted & _words(c.text)) / len(wanted), c.index) for c in items if wanted]
        scored = [s for s in scored if s[0] >= 0.6]
        return max(scored)[1] if scored else None
    return None
