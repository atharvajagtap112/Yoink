"""Mesh + pairing checks, no camera and no typing required.

Covers the three things that matter for milestone 4:
  1. one SEND fans out to BOTH other peers (broadcast, not 1:1)
  2. a peer that fails the PIN is rejected and gets nothing
  3. losing one peer doesn't stop the rest from receiving

...plus the authentication guarantees added on top of pairing:
  4. knowing a paired device_id is NOT enough — you must hold the pairing key
  5. proofs are direction-bound, so a challenge can't be reflected back
  6. the pre-key paired.json format reports unpaired instead of being trusted
  7. the proof MAC matches the Kotlin client, byte for byte

Run: python -m net.test_mesh
"""
import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .mesh import Mesh
from .pairing import PairedStore, proof
from . import protocol

HOST = "127.0.0.1"


def _shared_key(a_id, b_id):
    """A deterministic stand-in for the key real pairing would have negotiated.

    Both sides of a pair must derive the SAME bytes or authentication fails, so
    it's keyed on the id pair, sorted so either side computes it identically.
    """
    return hashlib.sha256(("test|" + "|".join(sorted([a_id, b_id]))).encode()).digest()


def _make(tmp, name, device_id, port, paired_with=(), prompt=None, sink=None,
          log=None):
    store = PairedStore(Path(tmp) / f"{name}.json")
    for pid, pname in paired_with:
        store.add(pid, pname, _shared_key(device_id, pid))
    m = Mesh(name, device_id, port, store,
             sink or (lambda e: None), log or (lambda msg: None), prompt=prompt)
    m.start()
    for _ in range(50):                      # wait for its event loop
        if m._loop:
            break
        time.sleep(0.05)
    return m


def _wait(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_broadcast_and_peer_loss(tmp):
    b_got, c_got = [], []
    # ids are sortable: aaa < bbb < ccc, so A dials B and C, B dials C.
    a = _make(tmp, "A", "aaa", 8821, [("bbb", "B"), ("ccc", "C")])
    b = _make(tmp, "B", "bbb", 8822, [("aaa", "A"), ("ccc", "C")], sink=b_got.append)
    c = _make(tmp, "C", "ccc", 8823, [("aaa", "A"), ("bbb", "B")], sink=c_got.append)

    for m, peers in ((a, [("bbb", 8822), ("ccc", 8823)]),
                     (b, [("aaa", 8821), ("ccc", 8823)]),
                     (c, [("aaa", 8821), ("bbb", 8822)])):
        for pid, port in peers:
            m.peer_up(pid, HOST, port)

    assert _wait(lambda: len(a.peers) == 2), f"A linked to {len(a.peers)}/2 peers"
    print("mesh formed: A has 2 peers, no duplicates")

    # 1. one broadcast reaches BOTH peers
    a.broadcast(protocol.text_payload("fan-out", "A"))
    assert _wait(lambda: b_got and c_got), f"B={len(b_got)} C={len(c_got)}"
    assert b_got[0]["data"] == "fan-out" and c_got[0]["data"] == "fan-out"
    print("broadcast reached both B and C")

    # 3. lose B; C must be unaffected
    a.peer_down("bbb")
    assert _wait(lambda: "bbb" not in a.peers), "A still holds the dead peer"
    b_got.clear(); c_got.clear()
    a.broadcast(protocol.text_payload("after B died", "A"))
    assert _wait(lambda: bool(c_got)), "C stopped receiving after B died"
    assert not b_got, "B received despite being dropped"
    print("after B dropped: C still receives, B does not")


def test_unpaired_rejected(tmp):
    async def wrong_pin(peer_name):
        return "definitely-not-the-pin"

    d_got = []
    # Neither knows the other, and the PIN typed is wrong -> never paired.
    a = _make(tmp, "A2", "aaa", 8824, prompt=wrong_pin)
    d = _make(tmp, "D2", "ddd", 8825, sink=d_got.append)
    a.peer_up("ddd", HOST, 8825)

    time.sleep(4)                            # let the handshake fail
    assert not a.peers, f"unpaired peer joined A's mesh: {a.peers}"
    assert not d.peers, f"unpaired peer joined D's mesh: {d.peers}"

    a.broadcast(protocol.text_payload("should not arrive", "A2"))
    time.sleep(1)
    assert not d_got, "an UNPAIRED peer received a payload"
    print("unpaired peer rejected: not in mesh, got no payload")


def test_pin_pairing_succeeds(tmp):
    """The happy path: the PIN shown on one side, typed on the other, pairs them
    and is remembered."""
    e_logs, e_got = [], []
    pin_re = re.compile(r"PIN: (\d{6})")

    async def read_pin_off_es_console(peer_name):
        for _ in range(100):                 # the user reading E's console
            for line in list(e_logs):
                m = pin_re.search(line)
                if m:
                    return m.group(1)
            await asyncio.sleep(0.05)
        return "never-saw-a-pin"

    a = _make(tmp, "A3", "aaa", 8827, prompt=read_pin_off_es_console)
    e = _make(tmp, "E3", "eee", 8828, sink=e_got.append, log=e_logs.append)
    a.peer_up("eee", HOST, 8828)

    assert _wait(lambda: len(a.peers) == 1 and len(e.peers) == 1), \
        "correct PIN did not complete pairing"
    assert a.store.is_paired("eee") and e.store.is_paired("aaa"), \
        "pairing wasn't persisted, so it would ask again next launch"

    a.broadcast(protocol.text_payload("now trusted", "A3"))
    assert _wait(lambda: bool(e_got)), "paired peer got no payload"
    print("correct PIN -> paired, persisted, payload flows")


def test_pin_prompts_serialize(tmp):
    """Pairing with two peers at once must ask ONE PIN at a time. Otherwise both
    input() calls race for stdin and the user's answer lands in the wrong
    handshake, denying a pairing they typed correctly."""
    import builtins

    live, peak, guard = [], [0], threading.Lock()

    def fake_input(prompt=""):
        with guard:
            live.append(1)
            peak[0] = max(peak[0], len(live))
        time.sleep(0.3)                      # a human, typing
        with guard:
            live.pop()
        return "1234"

    m = _make(tmp, "S1", "sss", 8829)
    real_input, builtins.input = builtins.input, fake_input
    try:
        f1 = asyncio.run_coroutine_threadsafe(m._console_prompt("P1"), m._loop)
        f2 = asyncio.run_coroutine_threadsafe(m._console_prompt("P2"), m._loop)
        f1.result(10), f2.result(10)
    finally:
        builtins.input = real_input
    assert peak[0] == 1, f"{peak[0]} PIN prompts fought over stdin at once"
    print("concurrent pairings ask one PIN at a time")


def test_manual_peer_dials_right_after_start(tmp):
    """The --peer bug: start() returned before its event loop existed, so
    add_manual() on the very next line saw `self._loop` as None and was silently
    dropped. Nothing ever dialed and every send hit "no paired peers yet".

    Deliberately does NOT use _make(), whose wait-for-loop masked this.
    """
    g_got = []
    key = _shared_key("fff", "ggg")
    f_store = PairedStore(Path(tmp) / "F4.json")
    f_store.add("ggg", "G4", key)
    g_store = PairedStore(Path(tmp) / "G4.json")
    g_store.add("fff", "F4", key)

    g = Mesh("G4", "ggg", 8831, g_store, g_got.append, lambda m: None)
    g.start()

    f = Mesh("F4", "fff", 8830, f_store, lambda e: None, lambda m: None)
    f.start()
    f.add_manual(HOST, 8831)          # the very next line, exactly like main.py

    assert _wait(lambda: len(f.peers) == 1), "--peer never dialed (event loop race)"
    f.broadcast(protocol.text_payload("via --peer", "F4"))
    assert _wait(lambda: bool(g_got)), "manual peer received nothing"
    print("--peer dials even when added immediately after start()")


def test_stolen_device_id_is_not_enough(tmp):
    """THE point of authentication.

    device_ids travel in the clear in every `hello`, so anyone who watched one
    handshake knows them. An impostor here claims a device_id that its target
    genuinely has paired — and holds a different key. Before authentication
    existed this peer was trusted outright; now it must be refused.
    """
    h_got = []
    # H has paired with "iii" and holds the real key. The impostor also calls
    # itself "iii" but its store holds a key it made up.
    h = _make(tmp, "H5", "hhh", 8832, [("iii", "I5")], sink=h_got.append)

    fake_store = PairedStore(Path(tmp) / "impostor.json")
    fake_store.add("hhh", "H5", b"\x00" * 32)          # wrong key, right id
    impostor = Mesh("I5", "iii", 8833, fake_store,
                    lambda e: None, lambda m: None)
    impostor.start()
    impostor.peer_up("hhh", HOST, 8832)                # iii < hhh is False, so
    h.peer_up("iii", HOST, 8833)                       # drive it from both ends

    time.sleep(4)
    assert not h.peers, f"an impostor with the right device_id joined: {h.peers}"
    assert not impostor.peers, "impostor believes it is connected"

    h.broadcast(protocol.text_payload("must not reach an impostor", "H5"))
    time.sleep(1)
    assert not h_got, "the impostor's payload was accepted"
    print("stolen device_id without the pairing key: refused")


def test_impostor_dialing_in_is_refused(tmp):
    """The same theft, from the other side — and the direction that matters most.

    Above, the impostor answered our dial. Here it dials US, which is how an
    attacker would actually push a file onto your machine. The acceptor must
    demand a proof and refuse when it doesn't verify.
    """
    k_got = []
    # "aab" < "kkk", so the impostor is the one that dials.
    k = _make(tmp, "K7", "kkk", 8834, [("aab", "L7")], sink=k_got.append)

    fake_store = PairedStore(Path(tmp) / "impostor2.json")
    fake_store.add("kkk", "K7", b"\xff" * 32)          # wrong key, right id
    impostor = Mesh("L7", "aab", 8835, fake_store,
                    lambda e: None, lambda m: None)
    impostor.start()
    impostor.peer_up("kkk", HOST, 8834)                # dials K

    time.sleep(4)
    assert not k.peers, f"an impostor dialed in and was accepted: {k.peers}"

    # And the thing we actually care about: it cannot push us a payload.
    impostor.broadcast(protocol.text_payload("malicious file", "L7"))
    time.sleep(1)
    assert not k_got, "an impostor injected a payload"
    print("impostor dialing in without the pairing key: refused, nothing injected")


def test_proof_is_direction_bound():
    """A proof names who is proving to whom. If it didn't, an attacker could
    bounce our own challenge straight back and pass without holding the key."""
    key = _shared_key("aaa", "bbb")
    nonce = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert proof(key, nonce, "aaa", "bbb") != proof(key, nonce, "bbb", "aaa"), \
        "proof is reflectable — swapping prover and verifier gives the same MAC"

    # ...and a different key gives a different answer, obviously, but assert it
    # so a stubbed-out MAC can't pass this file.
    assert proof(key, nonce, "aaa", "bbb") != proof(b"\x00" * 32, nonce, "aaa", "bbb")
    print("proofs are direction-bound and key-bound")


def test_proof_matches_the_kotlin_client():
    """Known-answer vector, pinned identically in GestureTest.kt / PairingTest.kt.

    The desktop and the phone must compute this MAC the same way or they simply
    stop connecting, with no useful error anywhere. A shared constant is the
    cheapest way to catch a drift in the message format.
    """
    key = bytes.fromhex(
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    got = proof(key, "a1b2c3d4e5f60718293a4b5c6d7e8f90",
                "aaaaaaaaaaaa", "bbbbbbbbbbbb")
    assert got == ("afdfe4991915e62fc4e6fa3c80cbabe0"
                   "dde97d77b67e73476cfb34ba166ff2c5"), got
    print("proof MAC matches the pinned cross-language vector")


def test_legacy_store_forces_repairing(tmp):
    """A paired.json written before keys existed holds a name and nothing else.

    It must read as UNPAIRED rather than trusted, or the old assert-an-id path
    survives as a downgrade. The name is still readable so the UI can show it.
    """
    path = Path(tmp) / "legacy.json"
    path.write_text(json.dumps({"jjj": "J6"}))         # the pre-key format
    store = PairedStore(path)

    assert store.names() == {"jjj": "J6"}, "the old name should still be readable"
    assert store.secret("jjj") is None, "a legacy record has no key"
    assert not store.is_paired("jjj"), \
        "a legacy record was trusted without a key — that IS the downgrade"

    store.add("jjj", "J6", _shared_key("jjj", "me"))   # re-paired
    assert store.is_paired("jjj")
    assert PairedStore(path).is_paired("jjj"), "the key didn't survive a reload"
    print("pre-key pairings are refused until re-paired, then persist")


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_broadcast_and_peer_loss(tmp)
        test_unpaired_rejected(tmp)
        test_pin_pairing_succeeds(tmp)
        test_pin_prompts_serialize(tmp)
        test_manual_peer_dials_right_after_start(tmp)
        test_stolen_device_id_is_not_enough(tmp)
        test_impostor_dialing_in_is_refused(tmp)
        test_proof_is_direction_bound()
        test_proof_matches_the_kotlin_client()
        test_legacy_store_forces_repairing(tmp)
        print("mesh + pairing self-checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
