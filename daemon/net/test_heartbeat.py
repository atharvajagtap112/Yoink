"""Heartbeat-loss check for a single Peer connection.

A killed process closes its socket cleanly and is noticed instantly, so to
exercise the heartbeat path we talk to a peer that ACCEPTS the socket and then
goes silent — a frozen peer or a dropped WiFi link. After a few missed beats the
link must be declared dead and dropped.

Run: python -m net.test_heartbeat
"""
import asyncio

import websockets

from .peer import Peer


async def _silent_server(port, ready):
    async def handler(ws):
        await asyncio.Future()              # hold open; never read, never send
    async with websockets.serve(handler, "127.0.0.1", port):
        ready.set()
        await asyncio.Future()


async def main():
    ready = asyncio.Event()
    server = asyncio.create_task(_silent_server(8826, ready))
    await asyncio.wait_for(ready.wait(), 5)

    status = []
    async with websockets.connect("ws://127.0.0.1:8826") as ws:
        peer = Peer(ws, "silent", "SilentPeer", on_receive=lambda e: None,
                    on_status=status.append, heartbeat_s=1, dead_s=2)
        # run() returns once the peer is declared dead and the socket is closed
        await asyncio.wait_for(peer.run(), 10)

    assert any("heartbeat lost" in s for s in status), \
        f"heartbeat loss never fired; status log = {status}"
    print("heartbeat-lost self-check passed")
    server.cancel()


if __name__ == "__main__":
    asyncio.run(main())
