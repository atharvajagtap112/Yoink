"""PIN pairing: the trust gate that must pass before any payload is accepted.

Why it exists: on shared WiFi (college, cafe) a stranger must not be able to
read or write your clipboard. Discovery finds everyone; pairing decides who is
actually you. Unpaired peers never join the mesh, so they can neither send nor
receive payloads.

The PIN is deliberately NOT sent by the side that shows it — it is displayed on
one console and the user types it into the other. Only the guess crosses the
wire, so watching the traffic doesn't reveal the PIN.

Handshake (both sides use the same envelope; `type` carries the step):
    both  -> hello   (data = my device_id, sender = my name)
    dialer-> pair    type=known|new     ("do I already have you stored?")
    if neither side needs pairing:
      acceptor-> pair type=ok           -> done, already trusted
    otherwise:
      acceptor        shows a PIN on its console
      acceptor-> pair type=request
      dialer          asks the user for the PIN
      dialer  -> pair type=confirm, data=<pin the user typed>
      acceptor-> pair type=ok | deny    -> both persist on ok
"""
import asyncio
import json
import random

from . import protocol

PAIR_TIMEOUT_S = 120     # generous: a human is typing a PIN


class PairedStore:
    """The peers we've already trusted, persisted so pairing is one-time."""

    def __init__(self, path):
        self.path = path
        self._peers = {}
        if path.exists():
            try:
                self._peers = json.loads(path.read_text())
            except ValueError:
                self._peers = {}          # corrupt file: start clean

    def is_paired(self, device_id):
        return device_id in self._peers

    def add(self, device_id, name):
        self._peers[device_id] = name
        self.path.write_text(json.dumps(self._peers, indent=2))

    def names(self):
        return dict(self._peers)


def _pair_msg(step, sender, pin=None):
    return protocol.encode(
        protocol.envelope("pair", type=step, data=pin, sender=sender))


async def _recv_kind(ws, kind, log):
    """Read until a message of `kind` arrives.

    Payloads that show up before pairing is done are refused here — this is the
    rejection the whole feature exists for.
    """
    while True:
        raw = await asyncio.wait_for(ws.recv(), PAIR_TIMEOUT_S)
        env = protocol.decode(raw)
        if not env:
            continue
        if env.get("kind") == "payload":
            log(f"REJECTED payload from unpaired peer {env.get('sender')!r}")
            continue
        if env.get("kind") == kind:
            return env
        # anything else (e.g. a stray heartbeat) is ignored during the handshake


async def handshake(ws, *, dialer, me_name, me_id, store, log, prompt):
    """Run the handshake on a fresh connection.

    Returns (peer_id, peer_name) if the peer is trusted and may join the mesh,
    or None if it must be dropped.
    """
    await ws.send(protocol.encode(
        protocol.envelope("hello", data=me_id, sender=me_name)))
    hello = await _recv_kind(ws, "hello", log)
    peer_id, peer_name = hello.get("data"), hello.get("sender")
    if not peer_id or peer_id == me_id:
        return None                       # nameless, or ourselves

    if dialer:
        return await _dial_side(ws, peer_id, peer_name, me_name, store, log, prompt)
    return await _accept_side(ws, peer_id, peer_name, me_name, store, log)


async def _dial_side(ws, peer_id, peer_name, me_name, store, log, prompt):
    await ws.send(_pair_msg("known" if store.is_paired(peer_id) else "new", me_name))
    resp = await _recv_kind(ws, "pair", log)
    step = resp.get("type")

    if step == "ok":                      # both sides already trusted each other
        return peer_id, peer_name
    if step != "request":
        return None

    log(f"PAIR REQUEST from {peer_name} — check the PIN on its console")
    pin = await prompt(peer_name)
    await ws.send(_pair_msg("confirm", me_name, pin))
    if (await _recv_kind(ws, "pair", log)).get("type") != "ok":
        log(f"PAIR DENIED by {peer_name} (wrong PIN)")
        return None
    store.add(peer_id, peer_name)
    log(f"PAIRED with {peer_name} (saved; won't ask again)")
    return peer_id, peer_name


async def _accept_side(ws, peer_id, peer_name, me_name, store, log):
    verdict = await _recv_kind(ws, "pair", log)
    if verdict.get("type") == "known" and store.is_paired(peer_id):
        await ws.send(_pair_msg("ok", me_name))
        return peer_id, peer_name

    pin = f"{random.randint(0, 9999):04d}"
    log(f"PAIR REQUEST from {peer_name} — PIN: {pin}  <- type this on {peer_name}")
    await ws.send(_pair_msg("request", me_name))

    conf = await _recv_kind(ws, "pair", log)
    if conf.get("type") != "confirm" or conf.get("data") != pin:
        await ws.send(_pair_msg("deny", me_name))
        log(f"PAIR REJECTED for {peer_name} (wrong PIN)")
        return None

    await ws.send(_pair_msg("ok", me_name))
    store.add(peer_id, peer_name)
    log(f"PAIRED with {peer_name} (saved; won't ask again)")
    return peer_id, peer_name
