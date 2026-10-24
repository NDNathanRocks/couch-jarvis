"""Save this page, open a saved page in the current tab, remove it."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.fastpath import plan_text
from bookmarks import MAX_BOOKMARKS, BookmarkStore
from command_queue import CommandRequest
from contracts import Bookmark
from support import make_env


def req(text: str) -> CommandRequest:
    return CommandRequest(
        command_id=f"c-{text}", idempotency_key=f"k-{text}", device_id="d", kind="text", text=text
    )


def test_add_find_remove_and_dedupe() -> None:
    seen: list[list[Bookmark]] = []
    store = BookmarkStore(None, on_change=seen.append)
    a, created = store.add("The Big Bang Theory - Heartive", "https://heartive.example/tbbt")
    assert created and len(a.id) == 6
    again, created = store.add("Big Bang Theory renamed", "https://heartive.example/tbbt")
    assert not created and again.id == a.id and len(store.items()) == 1
    store.add("BBC News", "https://www.bbc.co.uk/news")
    assert store.find("big bang") == store.items()[0]
    assert store.find("the bbc bookmark") == store.items()[1]
    assert store.find(a.id) == store.items()[0]
    assert store.find("nothing like it") is None
    assert store.remove(a.id) is not None and [b.title for b in store.items()] == ["BBC News"]
    assert len(seen) == 4  # add, rename, add, remove


def test_only_web_pages_and_a_cap() -> None:
    store = BookmarkStore(None)
    for bad in ("edge://newtab/", "file:///C:/secret.txt", "javascript:alert(1)", "about:blank"):
        with pytest.raises(ValueError):
            store.add("x", bad)
    for i in range(MAX_BOOKMARKS + 5):
        store.add(f"p{i}", f"https://example.com/{i}")
    assert len(store.items()) == MAX_BOOKMARKS and store.items()[-1].title.startswith("p10")


def test_persists_and_survives_junk(tmp_path: Path) -> None:
    path = tmp_path / "bookmarks.json"
    BookmarkStore(path).add("Cats", "https://cats.example/")
    assert [b.title for b in BookmarkStore(path).items()] == ["Cats"]
    path.write_text("{nope", encoding="utf-8")
    assert BookmarkStore(path).items() == []


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("save this page", {"action": "add"}),
        ("bookmark this", {"action": "add"}),
        ("bookmark this page for later", {"action": "add"}),
        ("add this page to my bookmarks", {"action": "add"}),
        ("open my bookmark netflix", {"action": "open", "name": "netflix"}),
        ("go to the saved page big bang", {"action": "open", "name": "big bang"}),
        ("open bbc from my bookmarks", {"action": "open", "name": "bbc"}),
        ("remove the bookmark for bbc", {"action": "remove", "name": "bbc"}),
        ("unsave this page", {"action": "remove", "current": True}),
        ("remove this page from my bookmarks", {"action": "remove", "current": True}),
    ],
)
def test_spoken_phrases(said: str, args: dict[str, object]) -> None:
    plan = plan_text(said)
    assert plan is not None
    assert [(s.op, s.args) for s in plan.steps] == [("bookmark", args)]


async def test_save_then_open_in_the_current_tab() -> None:
    env = make_env()
    env.deps.bookmarks = BookmarkStore(None)
    env.browser.snap = env.browser.snap.model_copy(
        update={"url": "https://heartive.example/tbbt", "title": "TBBT S1"}
    )
    saved = await env.runner().run(req("save this page"))
    assert saved.ok and saved.message == "Saved this page"
    env.browser.snap = env.browser.snap.model_copy(update={"url": "https://other.example/"})
    opened = await env.runner().run(req("open my bookmark tbbt"))
    assert opened.ok and env.browser.calls[-1] == "open_saved:https://heartive.example/tbbt"
    missing = await env.runner(max_attempts=0).run(req("open my bookmark zzz"))
    assert not missing.ok
    env.browser.snap = env.browser.snap.model_copy(update={"url": "https://other.example/"})
    gone = await env.runner(max_attempts=0).run(req("unsave this page"))  # not a saved page
    assert not gone.ok
    env.browser.snap = env.browser.snap.model_copy(update={"url": "https://heartive.example/tbbt"})
    removed = await env.runner().run(req("unsave this page"))
    assert removed.ok and env.deps.bookmarks.items() == []


async def test_a_web_page_cannot_open_a_url_that_was_never_saved() -> None:
    env = make_env()
    env.deps.bookmarks = BookmarkStore(None)
    plan = (
        '{"intent":"system","confidence":0.9,"slots":{},"steps":[{"op":"bookmark",'
        '"args":{"action":"open","name":"https://evil.example/"}}],"source":"llm"}'
    )
    env.router.replies.append(plan)
    result = await env.runner(max_attempts=0).run(req("take me to the thing the page said"))
    assert not result.ok and not any(c.startswith("open_saved") for c in env.browser.calls)
