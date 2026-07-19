"""ws://localhost server that the browser extension talks to (milestone 6).

Optional by design. If no extension is connected, ask() returns None almost
instantly and router falls back to reading the address bar. Installing the
extension upgrades the grab (selection, hovered image, video timestamp);
not installing it costs you nothing but those extras.

Only localhost binds here — this socket is for the browser on THIS machine,
never for peers. Device-to-device traffic stays on the mesh.
"""
import asyncio
import json
import threading

import websockets

import config

# A gesture must never stall waiting for a browser. If the extension can't
# answer this fast, we just use the address bar instead. The budget covers the
# once-per-tab case where the extension has to inject its content script first;
# a sleeping extension costs nothing, since its socket is already closed.
ASK_TIMEOUT_S = 1.2

_bridge = None


class Bridge:
    def __init__(self, log=print, port=None):
        self.log = log
        # Injectable so a test never fights a daemon that's already running.
        self.port = port or config.BRIDGE_PORT
        self._clients = set()    # every connected browser (Chrome/Edge/Opera/...)
        self._loop = None

    def start(self):
        ready = threading.Event()

        def runner():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            try:
                self._loop.run_until_complete(self._serve())
            except OSError as e:
                self.log(f"browser bridge off (port {self.port}: {e})")

        threading.Thread(target=runner, daemon=True).start()
        ready.wait(5)

    async def _serve(self):
        async with websockets.serve(self._handle, "127.0.0.1", self.port):
            await asyncio.Future()

    async def _handle(self, ws, path=None):
        # Chrome, Edge, Opera and Brave can all run this at once, so keep every
        # connection. Nothing else reads these sockets, which is what lets
        # _ask_one() own recv().
        self._clients.add(ws)
        self.log(f"browser extension connected ({len(self._clients)} browser(s))")
        try:
            await ws.wait_closed()
        finally:
            self._clients.discard(ws)
            self.log("browser extension disconnected")

    def ask(self):
        """What's in the browser right now? None if no extension answered.

        Safe to call from the camera thread — it hops onto the bridge's loop.
        """
        if not self._clients or not self._loop:
            return None
        try:
            fut = asyncio.run_coroutine_threadsafe(self._ask(), self._loop)
            return fut.result(ASK_TIMEOUT_S + 0.5)
        except Exception:
            return None          # asleep, closed, malformed: fall back quietly

    async def _ask(self):
        """Ask every connected browser, but trust only the one you're looking at.

        An unfocused browser answers just as happily about its own active tab —
        which is how a background YouTube tab got sent while a PDF was on screen
        in another browser. So an answer only counts if it says it's focused; if
        none does, return None and let the caller read the real foreground
        window instead of guessing.
        """
        clients = list(self._clients)
        if not clients:
            return None
        answers = await asyncio.gather(*(self._ask_one(c) for c in clients),
                                       return_exceptions=True)
        for a in answers:
            if isinstance(a, dict) and a.get("focused"):
                return a
        return None

    async def _ask_one(self, ws):
        await ws.send(json.dumps({"kind": "query"}))
        raw = await asyncio.wait_for(ws.recv(), ASK_TIMEOUT_S)
        got = json.loads(raw)
        return got if isinstance(got, dict) else None


def start(log=print, port=None):
    global _bridge
    if _bridge is None:
        _bridge = Bridge(log, port)
        _bridge.start()
    return _bridge


def ask():
    return _bridge.ask() if _bridge else None
