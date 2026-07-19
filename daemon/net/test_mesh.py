"""Mesh + pairing checks, no camera and no typing required.

Covers the three things that matter for milestone 4:
  1. one SEND fans out to BOTH other peers (broadcast, not 1:1)
  2. a peer that fails the PIN is rejected and gets nothing
  3. losing one peer doesn't stop the rest from receiving

Run: python -m net.test_mesh
"""
import asyncio
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .mesh import Mesh
from .pairing import PairedStore
from . import protocol

HOST = "127.0.0.1"


def _make(tmp, name, device_id, port, paired_with=(), prompt=None, sink=None,
          log=None):
    store = PairedStore(Path(tmp) / f"{name}.json")
    for pid, pname in paired_with:
        store.add(pid, pname)
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
    pin_re = re.compile(r"PIN: (\d{4})")

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
    f_store = PairedStore(Path(tmp) / "F4.json")
    f_store.add("ggg", "G4")
    g_store = PairedStore(Path(tmp) / "G4.json")
    g_store.add("fff", "F4")

    g = Mesh("G4", "ggg", 8831, g_store, g_got.append, lambda m: None)
    g.start()

    f = Mesh("F4", "fff", 8830, f_store, lambda e: None, lambda m: None)
    f.start()
    f.add_manual(HOST, 8831)          # the very next line, exactly like main.py

    assert _wait(lambda: len(f.peers) == 1), "--peer never dialed (event loop race)"
    f.broadcast(protocol.text_payload("via --peer", "F4"))
    assert _wait(lambda: bool(g_got)), "manual peer received nothing"
    print("--peer dials even when added immediately after start()")


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_broadcast_and_peer_loss(tmp)
        test_unpaired_rejected(tmp)
        test_pin_pairing_succeeds(tmp)
        test_pin_prompts_serialize(tmp)
        test_manual_peer_dials_right_after_start(tmp)
        print("mesh + pairing self-checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
