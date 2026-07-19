"""Checks for the extension bridge (milestone 6).

The property that matters most: the extension is OPTIONAL. A gesture must never
stall or fail because the browser isn't running one. So we test the missing and
asleep cases as hard as the happy path.

Run: python -m grab.test_bridge
"""
import asyncio
import json
import threading
import time

import websockets

import config
from . import browser_bridge, router


# Never config.BRIDGE_PORT: a real daemon may be running and would win the bind.
TEST_PORT = 8791


def _log(_msg):
    pass


def _fake_extension(answer, delay=0.0, stop=None):
    """Run a stand-in extension in a thread; returns a stop() callable."""
    ready = threading.Event()

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def client():
            async with websockets.connect(
                    f"ws://127.0.0.1:{TEST_PORT}") as ws:
                ready.set()
                while True:
                    raw = await ws.recv()
                    assert json.loads(raw)["kind"] == "query"
                    if delay:
                        await asyncio.sleep(delay)
                    await ws.send(json.dumps(answer))
        try:
            loop.run_until_complete(client())
        except Exception:
            pass

    threading.Thread(target=runner, daemon=True).start()
    ready.wait(5)


def test_no_extension_degrades_instantly():
    """No extension installed: ask() must return None fast, not hang a gesture."""
    b = browser_bridge.start(_log, port=TEST_PORT)
    time.sleep(0.3)
    began = time.time()
    assert b.ask() is None, "answered without an extension connected"
    assert time.time() - began < 0.2, "a missing extension stalled the grab"
    print("  ok: no extension -> instant None")


def test_extension_answers():
    _fake_extension({"focused": True, "url": "https://youtube.com/watch?v=abc",
                     "title": "vid", "selection": "", "image": None,
                     "videoTime": 142})
    time.sleep(0.3)
    got = browser_bridge.ask()
    assert got and got["url"].endswith("v=abc"), got
    assert got["videoTime"] == 142
    print("  ok: extension answers the query")


def test_video_time_becomes_a_timestamped_link():
    """CLAUDE.md's own protocol example. The address bar can never produce this
    — the URL there has no &t=, so only the extension makes it possible."""
    link = router._page_link({"url": "https://youtube.com/watch?v=abc",
                              "videoTime": 142})
    assert link == "https://youtube.com/watch?v=abc&t=142s", link
    # no video playing -> a plain link, not a bogus t=0
    assert router._page_link({"url": "https://x.com/a", "videoTime": None}) \
        == "https://x.com/a"
    # a url that already carries a timestamp isn't doubled up
    assert router._page_link({"url": "https://x.com/a?t=9s", "videoTime": 50}) \
        == "https://x.com/a?t=9s"
    print("  ok: video position folds into the link")


def test_selection_beats_the_link():
    env = router._from_extension(
        {"url": "https://x.com/page", "selection": "  the bit I highlighted  ",
         "image": None}, "A", _log)
    assert env["type"] == "text" and env["data"] == "the bit I highlighted", env
    # nothing selected and no image -> nothing; the caller uses the page link
    assert router._from_extension({"url": "https://x.com/p", "selection": "",
                                   "image": None}, "A", _log) is None
    print("  ok: a selection wins over the bare link")


def test_image_names():
    assert router._image_name("https://x.com/a/cat.jpg", "image/jpeg") == "cat.jpg"
    assert router._image_name("https://x.com/img?id=9", "image/png") \
        .endswith(".png")
    print("  ok: hovered images get sensible filenames")


def test_unfocused_browser_is_ignored():
    """The bug: Edge showing a PDF was focused, but Opera's extension answered
    about its own background YouTube tab — so the video got sent instead of the
    PDF. An answer only counts if that browser says it's the focused one."""
    b = browser_bridge._bridge
    unfocused = {"focused": False, "url": "https://youtube.com/watch?v=bg"}
    focused = {"focused": True, "url": "https://example.com/real"}
    real_ask_one, real_clients = b._ask_one, b._clients

    async def answer(value):
        return value

    try:
        # two browsers connected, neither focused -> no answer at all
        b._clients = {"opera", "edge"}
        b._ask_one = lambda ws: answer(unfocused)
        assert b.ask() is None, "an unfocused browser answered"

        # the focused one wins even when it isn't the first to reply
        seq = iter([unfocused, focused])
        b._ask_one = lambda ws: answer(next(seq))
        got = b.ask()
        assert got and got["url"].endswith("/real"), got
    finally:
        b._ask_one, b._clients = real_ask_one, real_clients
    print("  ok: only the focused browser is trusted")


def test_slow_extension_gives_up():
    """An extension that takes too long must not hold the gesture hostage."""
    original = browser_bridge.ASK_TIMEOUT_S
    browser_bridge.ASK_TIMEOUT_S = 0.2
    try:
        began = time.time()
        browser_bridge.ask()          # answer may or may not arrive; must return
        assert time.time() - began < 2, "a slow extension stalled the grab"
    finally:
        browser_bridge.ASK_TIMEOUT_S = original
    print("  ok: a slow extension times out instead of hanging")


if __name__ == "__main__":
    test_no_extension_degrades_instantly()
    test_video_time_becomes_a_timestamped_link()
    test_selection_beats_the_link()
    test_image_names()
    test_extension_answers()
    test_slow_extension_gives_up()
    test_unfocused_browser_is_ignored()
    print("bridge self-checks passed")
