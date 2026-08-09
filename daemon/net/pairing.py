"""PIN pairing + authenticated reconnect: the trust gate before any payload.

Why it exists: on shared WiFi (college, cafe) a stranger must not be able to
read or write your clipboard. Discovery finds everyone; pairing decides who is
actually you. Unpaired peers never join the mesh, so they can neither send nor
receive payloads.

Two separate jobs, and the split matters:

  * **Pairing** (first meeting) — a PIN is displayed on one screen and typed on
    the other. On success the acceptor generates a random 32-byte **pairing
    key** and both sides store it against the peer's device_id.
  * **Authentication** (every reconnect after) — each side proves it holds that
    key by HMAC over a fresh nonce from the other. Mutual, so a rogue *acceptor*
    can't impersonate your laptop to your phone either.

The authentication step is what makes a device_id worthless on its own. Before
it existed, a peer that merely *asserted* a paired device_id was trusted — and
device_ids travel in the clear in every `hello`, so anyone who watched one
handshake could impersonate that device forever. Now the id only names which
key to challenge with; possession of the key is what grants trust.

    Threat model, stated plainly. The pairing key is handed over on the
    *pairing* connection, which is not yet encrypted — so an attacker sniffing
    the network *at the exact moment you pair* learns it (they'd also see the
    PIN, which crosses the wire as the dialer's guess). This is trust-on-first-
    use, the same bargain Bluetooth pairing makes. What it buys: the window
    shrinks from "any handshake, forever" to "the one moment you paired".
    Closing that window needs a key exchange the sniffer can't follow (ECDH),
    which is the next piece of security work, not this one.

There is deliberately **no fallback to the old unauthenticated path**. A peer
that answers a `known` with a bare `ok` is refused. Keeping a fallback would be
a downgrade attack: an attacker would simply decline to support the new step and
be waved through. The cost is that pairings made before this change must be
redone once — `is_paired` reports False for a record with no key, which puts the
peer back through the PIN flow.

Handshake (both sides use the same envelope; `type` carries the step):
    both  -> hello   (data = my device_id, sender = my name)

    dialer-> pair    type=known, data=<nonce>   if we hold a key for this peer
          -> pair    type=new                    otherwise

    already paired (authenticate):
      acceptor-> pair type=challenge, data="<nonce_a>:<proof_a>"
      dialer          verifies proof_a, else drops the connection
      dialer  -> pair type=proof, data=<proof_d>
      acceptor-> pair type=ok | deny

    not yet paired (pair):
      acceptor        shows a PIN on its screen
      acceptor-> pair type=request
      dialer          asks the user for the PIN
      dialer  -> pair type=confirm, data=<pin the user typed>
      acceptor-> pair type=ok, data=<new pairing key>  |  type=deny
"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time

from . import protocol

PAIR_TIMEOUT_S = 120     # generous: a human is typing a PIN
PIN_DIGITS = 6           # 10^6 guesses, and the limiter below caps the rate
NONCE_BYTES = 16
KEY_BYTES = 32

# Rate limit on the acceptor. The dialer already backs off 30s after a failure,
# but an attacker writes their own client and won't — so the limit that matters
# is enforced here, by the side being attacked.
MAX_FAILURES = 5
LOCKOUT_S = 60

# Domain separator. Bound into every proof so a MAC computed for Yoink's
# authentication can never be replayed as anything else.
AUTH_TAG = "yoink-auth-v1"


class PairedStore:
    """The peers we've trusted, and the key each one proves itself with.

    On disk: {device_id: {"name": ..., "secret": <hex>}}. The pre-key format
    ({device_id: name}) is still read, but reports as unpaired so the peer is
    put back through the PIN flow — see the module docstring on downgrades.
    """

    def __init__(self, path):
        self.path = path
        self._peers = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
            except ValueError:
                raw = {}                      # corrupt file: start clean
            if isinstance(raw, dict):
                for did, val in raw.items():
                    if isinstance(val, str):          # legacy: name only, no key
                        self._peers[did] = {"name": val, "secret": None}
                    elif isinstance(val, dict):
                        self._peers[did] = {"name": val.get("name", "?"),
                                            "secret": val.get("secret")}

    def is_paired(self, device_id):
        """Trusted *and* able to prove it. A key-less record is not enough."""
        return self.secret(device_id) is not None

    def secret(self, device_id):
        """The pairing key as raw bytes, or None if we can't authenticate them."""
        rec = self._peers.get(device_id)
        if not rec or not rec.get("secret"):
            return None
        try:
            return bytes.fromhex(rec["secret"])
        except ValueError:
            return None                       # mangled file: treat as unpaired

    def add(self, device_id, name, secret):
        self._peers[device_id] = {"name": name, "secret": secret.hex()}
        self._save()

    def names(self):
        return {did: rec["name"] for did, rec in self._peers.items()}

    def _save(self):
        self.path.write_text(json.dumps(self._peers, indent=2))
        try:
            os.chmod(self.path, 0o600)        # this file now holds key material
        except OSError:
            pass                              # best effort; Windows ACLs differ


# --- proofs -----------------------------------------------------------------

def proof(secret, nonce, prover_id, verifier_id):
    """One side's proof that it holds `secret`, over the other side's nonce.

    Both device ids are bound in, prover first, so the two directions of a
    mutual exchange produce different MACs — otherwise a peer could reflect our
    own challenge back at us and pass without holding anything.

    Kept byte-for-byte identical to Pairing.proof() in the Kotlin client. Both
    test suites pin the same known-answer vector; if you change the message
    format here, that vector changes and both sides must move together.
    """
    msg = f"{AUTH_TAG}|{nonce}|{prover_id}|{verifier_id}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _nonce():
    return secrets.token_hex(NONCE_BYTES)


# --- lockout (acceptor side, per process) -----------------------------------

_failures = {}          # device_id -> [consecutive failures, blocked_until]


def _blocked(device_id):
    rec = _failures.get(device_id)
    return bool(rec and time.monotonic() < rec[1])


def _note_failure(device_id):
    """Returns True if this failure triggered a lockout."""
    rec = _failures.setdefault(device_id, [0, 0.0])
    rec[0] += 1
    if rec[0] >= MAX_FAILURES:
        rec[0] = 0
        rec[1] = time.monotonic() + LOCKOUT_S
        return True
    return False


def _note_success(device_id):
    _failures.pop(device_id, None)


# --- messages ---------------------------------------------------------------

def _pair_msg(step, sender, data=None):
    return protocol.encode(
        protocol.envelope("pair", type=step, data=data, sender=sender))


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


# --- handshake --------------------------------------------------------------

async def handshake(ws, *, dialer, me_name, me_id, store, log, prompt,
                    show_pin=None):
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
        return await _dial_side(ws, peer_id, peer_name, me_name, me_id,
                                store, log, prompt)
    return await _accept_side(ws, peer_id, peer_name, me_name, me_id,
                              store, log, show_pin)


async def _dial_side(ws, peer_id, peer_name, me_name, me_id, store, log, prompt):
    secret = store.secret(peer_id)
    nonce_d = _nonce() if secret else None
    await ws.send(_pair_msg("known" if secret else "new", me_name, nonce_d))
    resp = await _recv_kind(ws, "pair", log)
    step = resp.get("type")

    if step == "challenge":
        return await _prove(ws, resp, peer_id, peer_name, me_name, me_id,
                            secret, nonce_d, log)
    if step != "request":
        # Note there is no `ok` branch: a peer we hold a key for must challenge
        # us. Accepting a bare `ok` would be the downgrade the module docstring
        # refuses to allow.
        if step == "ok":
            log(f"REFUSED {peer_name}: it skipped authentication "
                f"(old version, or someone pretending to be it)")
        return None

    return await _confirm_pin(ws, peer_id, peer_name, me_name, store, log, prompt)


async def _prove(ws, resp, peer_id, peer_name, me_name, me_id, secret, nonce_d,
                 log):
    """Verify the acceptor's proof, then send ours. Mutual authentication."""
    if not secret:
        return None                       # challenged for a key we don't hold
    nonce_a, _, proof_a = (resp.get("data") or "").partition(":")
    if not nonce_a or not hmac.compare_digest(
            proof_a, proof(secret, nonce_d, peer_id, me_id)):
        log(f"AUTH FAILED: {peer_name} could not prove it holds our pairing "
            f"key — not connecting")
        return None

    await ws.send(_pair_msg("proof", me_name,
                            proof(secret, nonce_a, me_id, peer_id)))
    if (await _recv_kind(ws, "pair", log)).get("type") != "ok":
        log(f"AUTH REJECTED by {peer_name}")
        return None
    return peer_id, peer_name


async def _confirm_pin(ws, peer_id, peer_name, me_name, store, log, prompt):
    log(f"PAIR REQUEST from {peer_name} — check the PIN on its screen")
    pin = await prompt(peer_name)
    await ws.send(_pair_msg("confirm", me_name, (pin or "").strip()))
    final = await _recv_kind(ws, "pair", log)
    if final.get("type") != "ok":
        log(f"PAIR DENIED by {peer_name} (wrong PIN)")
        return None

    try:
        key = bytes.fromhex(final.get("data") or "")
    except ValueError:
        key = b""
    if len(key) != KEY_BYTES:
        log(f"PAIR FAILED: {peer_name} sent no usable pairing key "
            f"(is it running an older Yoink?)")
        return None

    store.add(peer_id, peer_name, key)
    log(f"PAIRED with {peer_name} (saved; won't ask again)")
    return peer_id, peer_name


async def _accept_side(ws, peer_id, peer_name, me_name, me_id, store, log,
                       show_pin=None):
    if _blocked(peer_id):
        log(f"{peer_name} is locked out after repeated failures — refusing")
        return None

    verdict = await _recv_kind(ws, "pair", log)
    secret = store.secret(peer_id)
    if verdict.get("type") == "known" and secret:
        return await _challenge(ws, verdict, peer_id, peer_name, me_name, me_id,
                                secret, log)
    return await _run_pin(ws, peer_id, peer_name, me_name, me_id, store, log,
                          show_pin)


async def _challenge(ws, verdict, peer_id, peer_name, me_name, me_id, secret,
                     log):
    """Prove we hold the key, and demand the same back."""
    nonce_d = verdict.get("data")
    if not nonce_d:
        log(f"{peer_name} claimed to know us but sent no nonce — refusing")
        return None

    nonce_a = _nonce()
    await ws.send(_pair_msg(
        "challenge", me_name,
        f"{nonce_a}:{proof(secret, nonce_d, me_id, peer_id)}"))

    reply = await _recv_kind(ws, "pair", log)
    expected = proof(secret, nonce_a, peer_id, me_id)
    if reply.get("type") != "proof" or not hmac.compare_digest(
            reply.get("data") or "", expected):
        await ws.send(_pair_msg("deny", me_name))
        if _note_failure(peer_id):
            log(f"AUTH FAILED from {peer_name} — locked out for {LOCKOUT_S}s")
        else:
            log(f"AUTH FAILED: {peer_name} could not prove it holds our "
                f"pairing key — refusing")
        return None

    _note_success(peer_id)
    await ws.send(_pair_msg("ok", me_name))
    return peer_id, peer_name


async def _run_pin(ws, peer_id, peer_name, me_name, me_id, store, log, show_pin):
    """First meeting: show a PIN, and on success hand over a fresh pairing key."""
    pin = f"{secrets.randbelow(10 ** PIN_DIGITS):0{PIN_DIGITS}d}"
    log(f"PAIR REQUEST from {peer_name} — PIN: {pin}  <- type this on {peer_name}")
    if show_pin:
        show_pin(peer_name, pin)          # GUI shows it prominently; cleared below
    await ws.send(_pair_msg("request", me_name))

    try:
        conf = await _recv_kind(ws, "pair", log)
        if conf.get("type") != "confirm" or not hmac.compare_digest(
                str(conf.get("data") or ""), pin):
            await ws.send(_pair_msg("deny", me_name))
            if _note_failure(peer_id):
                log(f"PAIR REJECTED for {peer_name} (wrong PIN) — "
                    f"locked out for {LOCKOUT_S}s")
            else:
                log(f"PAIR REJECTED for {peer_name} (wrong PIN)")
            return None

        key = secrets.token_bytes(KEY_BYTES)
        await ws.send(_pair_msg("ok", me_name, key.hex()))
        store.add(peer_id, peer_name, key)
        _note_success(peer_id)
        log(f"PAIRED with {peer_name} (saved; won't ask again)")
        return peer_id, peer_name
    finally:
        if show_pin:
            show_pin(peer_name, None)     # take the PIN card down, pass or fail
