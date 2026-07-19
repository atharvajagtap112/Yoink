"""One paired WebSocket connection, with its own heartbeat lifecycle.

A Peer only exists once pairing has passed (see net/pairing.py), so anything
holding a Peer is already trusted. Each Peer's heartbeat is independent: one
peer going dead has no effect on the others in the mesh.

Heartbeats: send a `heartbeat` envelope every heartbeat_s; any inbound message
refreshes "last heard". Silent for dead_s (~3 missed beats) and the link is
declared dead and closed. Note a peer whose process is killed closes the socket
cleanly and is noticed immediately — the heartbeat is for the ungraceful case
(frozen peer, dropped WiFi) where the socket is left half-open.
"""
import asyncio
import time

import websockets

from . import protocol

HEARTBEAT_S = 3
DEAD_S = 10


class Peer:
    def __init__(self, ws, device_id, name, on_receive, on_status,
                 heartbeat_s=HEARTBEAT_S, dead_s=DEAD_S):
        self.ws = ws
        self.device_id = device_id
        self.name = name
        self.on_receive = on_receive
        self.on_status = on_status
        self.heartbeat_s = heartbeat_s
        self.dead_s = dead_s
        self.last_heard = time.monotonic()

    async def send(self, env):
        await self.ws.send(protocol.encode(env))

    async def _pump(self):
        async for raw in self.ws:
            self.last_heard = time.monotonic()
            env = protocol.decode(raw)
            if env and env.get("kind") == "payload":
                self.on_receive(env)
            # heartbeats need no handling; refreshing last_heard above is the point

    async def _heartbeat(self):
        hb = protocol.envelope("heartbeat", sender=self.name)
        while True:
            await asyncio.sleep(self.heartbeat_s)
            if time.monotonic() - self.last_heard > self.dead_s:
                self.on_status(f"heartbeat lost — dropping {self.name}")
                await self.ws.close()
                return
            await self.send(hb)

    async def run(self):
        """Serve this connection until it closes. Returns when the peer is gone."""
        hb = asyncio.create_task(self._heartbeat())
        try:
            await self._pump()
        except websockets.exceptions.WebSocketException:
            pass
        finally:
            hb.cancel()
            await self.ws.close()
