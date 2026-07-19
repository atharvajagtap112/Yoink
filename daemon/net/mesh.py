"""The mesh: every instance is an equal peer, connected to every other peer.

No hub, no coordinator, no election (DESIGN.md section 2). A gesture is an event
broadcast to all live peers; each peer's connection has its own heartbeat and
its own dial/reconnect loop, so one peer dying never touches the others.

Dedupe — both sides discover each other, so without a rule both would dial and
we'd get two sockets per pair. Rule: **the lower device_id dials, the higher one
waits**. It's deterministic and needs no negotiation. A second, defensive check
in _session drops any redundant socket that still slips through (e.g. via the
manual --peer path, where we can't know the peer's id before connecting).
"""
import asyncio
import os
import threading

import websockets

import config
from . import pairing, protocol
from .peer import Peer

RETRY_S = 1        # between ordinary reconnect attempts
PAIR_RETRY_S = 30  # after a failed pairing, so a denial can't spam the prompt


class Mesh:
    def __init__(self, name, device_id, port, store, on_receive, on_status,
                 prompt=None):
        self.name = name
        self.device_id = device_id
        self.port = port
        self.store = store
        self.on_receive = on_receive
        self.on_status = on_status
        self.prompt = prompt or self._console_prompt
        self.peers = {}       # device_id -> Peer   (paired + live only)
        self._targets = {}    # device_id -> (host, port)
        self._dialers = {}    # device_id -> asyncio.Task
        self._loop = None
        # stdin is one shared resource: pairing with several peers at once must
        # ask one PIN at a time, or the user's answer lands in the wrong
        # handshake and pairing is denied through no fault of theirs.
        self._prompt_lock = asyncio.Lock()

    # --- discovery callbacks (called from the zeroconf thread) ---

    def peer_up(self, peer_id, host, port):
        if self._loop:
            self._loop.call_soon_threadsafe(self._peer_up, peer_id, host, port)

    def peer_down(self, peer_id):
        if self._loop:
            self._loop.call_soon_threadsafe(self._peer_down, peer_id)

    def add_manual(self, host, port):
        """--peer fallback: dial an address without knowing its id up front."""
        if self._loop:
            self._loop.call_soon_threadsafe(
                lambda: self._dialers.setdefault(
                    f"manual:{host}:{port}",
                    asyncio.create_task(self._dial_loop(None, (host, port)))))

    # --- loop-side ---

    def _peer_up(self, peer_id, host, port):
        self._targets[peer_id] = (host, port)
        if peer_id in self.peers or peer_id in self._dialers:
            return                        # already linked or already dialing
        if self.device_id < peer_id:
            self.on_status(f"discovered {peer_id[:8]} at {host}:{port} — dialing")
            self._dialers[peer_id] = asyncio.create_task(self._dial_loop(peer_id))
        else:
            self.on_status(f"discovered {peer_id[:8]} — it dials us (higher id waits)")

    def _peer_down(self, peer_id):
        self._targets.pop(peer_id, None)
        peer = self.peers.get(peer_id)
        self.on_status(f"peer gone from discovery: {peer_id[:8]}")
        if peer:
            asyncio.create_task(peer.ws.close())

    async def _dial_loop(self, peer_id, fixed=None):
        """Own one peer's connection lifecycle: dial, serve, retry. Independent
        of every other peer."""
        while fixed or peer_id in self._targets:
            if peer_id and peer_id in self.peers:
                await asyncio.sleep(RETRY_S)
                continue
            host, port = fixed or self._targets[peer_id]
            ok = True
            try:
                async with websockets.connect(
                        f"ws://{host}:{port}",
                        max_size=config.MAX_WS_MESSAGE) as ws:
                    ok = await self._session(ws, dialer=True)
            except (OSError, asyncio.TimeoutError,
                    websockets.exceptions.WebSocketException):
                pass
            await asyncio.sleep(RETRY_S if ok else PAIR_RETRY_S)
        self._dialers.pop(peer_id, None)

    async def _session(self, ws, dialer):
        """Pair, then serve the connection until it drops.

        Returns False only when pairing itself failed, so the dial loop can back
        off instead of re-prompting for a PIN every second.
        """
        try:
            res = await pairing.handshake(
                ws, dialer=dialer, me_name=self.name, me_id=self.device_id,
                store=self.store, log=self.on_status, prompt=self.prompt)
        except (asyncio.TimeoutError, websockets.exceptions.WebSocketException):
            return True
        if not res:
            await ws.close()
            return False

        peer_id, peer_name = res
        if peer_id in self.peers:         # defensive dedupe: keep the first link
            self.on_status(f"duplicate link to {peer_name} — dropping the extra")
            await ws.close()
            return True

        peer = Peer(ws, peer_id, peer_name, self.on_receive, self.on_status)
        self.peers[peer_id] = peer
        self.on_status(f"peer UP: {peer_name} ({len(self.peers)} live)")
        try:
            await peer.run()
        finally:
            self.peers.pop(peer_id, None)
            self.on_status(f"peer DOWN: {peer_name} ({len(self.peers)} live)")
        return True

    async def _accept(self, ws, path=None):
        await self._session(ws, dialer=False)

    async def _serve(self):
        # max_size: the default 1 MiB would reject anything but a tiny file.
        async with websockets.serve(self._accept, "127.0.0.1", self.port,
                                    max_size=config.MAX_WS_MESSAGE):
            await asyncio.Future()

    async def _console_prompt(self, peer_name):
        def ask():
            try:
                return input(f"\n>>> Enter the PIN shown on {peer_name}: ")
            except EOFError:
                return ""      # no console (piped/detached): decline, don't crash
        # One question at a time (see _prompt_lock), and in a thread so a
        # blocking input() never stalls the event loop or the other peers.
        async with self._prompt_lock:
            return (await asyncio.to_thread(ask)).strip()

    async def _broadcast(self, env):
        peers = list(self.peers.values())
        if not peers:
            self.on_status("broadcast: no paired peers yet — nothing sent")
            return
        sent = []
        for p in peers:
            try:
                await p.send(env)
                sent.append(p.name)
            except websockets.exceptions.WebSocketException:
                pass
        self.on_status(f"broadcast -> {len(sent)}/{len(peers)} peers: {sent}")

    # --- thread-safe API (camera / main thread) ---

    def start(self):
        # Every thread-safe entry point below no-ops while _loop is None, so
        # start() must not return until it exists — otherwise main.py's very next
        # line (add_manual) is silently dropped and nothing ever dials.
        ready = threading.Event()

        def runner():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            try:
                self._loop.run_until_complete(self._serve())
            except OSError as e:
                print(f"\n[net] can't listen on port {self.port}: {e}\n"
                      f"      Another Yoink is probably using it. "
                      f"Pick a different --port or stop the other one.")
                os._exit(1)
        threading.Thread(target=runner, daemon=True).start()
        ready.wait(5)

    def broadcast(self, env):
        """Fan a payload out to every paired peer. Safe from any thread."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._broadcast(env), self._loop)
