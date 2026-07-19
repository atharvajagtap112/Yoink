"""Self-check for the gesture logic. Run: python -m gesture.test_gesture

No framework — just asserts. Guards the classifier's ratios and the state
machine's debounce / edge / cooldown behavior.
"""
import time

from .classifier import classify
from .state_machine import GestureStateMachine


def _hand(curl):
    """Build 21 fake landmarks. curl=False -> open (tips far from wrist at 0,1),
    curl=True -> closed (tips pulled back near the wrist)."""
    lm = [(0.5, 0.5)] * 21
    lm[0] = (0.5, 1.0)  # wrist at the bottom
    for base, tip in [(2, 4), (6, 8), (10, 12), (14, 16), (18, 20)]:
        lm[base] = (0.5, 0.6)
        lm[tip] = (0.5, 0.3) if not curl else (0.5, 0.7)  # far vs near the wrist
    return lm


def test_classifier():
    assert classify(_hand(curl=False)) == "OPEN"
    assert classify(_hand(curl=True)) == "CLOSED"
    assert classify([]) == "UNKNOWN"


def test_debounce_and_edge():
    sm = GestureStateMachine(debounce=3, cooldown=0.0)
    # First stable pose (OPEN) establishes state, fires nothing.
    assert [sm.update("OPEN") for _ in range(3)] == [None, None, None]
    # One CLOSED frame must not fire (debounce).
    assert sm.update("CLOSED") is None
    assert sm.update("CLOSED") is None
    assert sm.update("CLOSED") == "SEND"        # third frame -> stable -> edge
    # Back to open fires RECEIVE.
    assert [sm.update("OPEN") for _ in range(3)][-1] == "RECEIVE"


def test_lost_hand_resets_baseline():
    # Fist (SEND), then hand leaves the frame, then an open palm reappears.
    # That must NOT fire RECEIVE — the baseline was forgotten while the hand
    # was gone, so OPEN is just a fresh start.
    sm = GestureStateMachine(debounce=1, cooldown=0.0, lost_frames=3)
    sm.update("CLOSED")                       # baseline = CLOSED
    for _ in range(3):
        assert sm.update("NO_HAND") is None   # hand gone -> baseline forgotten
    assert sm.update("OPEN") is None          # fresh OPEN, no phantom RECEIVE


def test_brief_ambiguity_keeps_baseline():
    # A real fist->open passes through ambiguous frames; those must be tolerated
    # so the transition still fires RECEIVE.
    sm = GestureStateMachine(debounce=1, cooldown=0.0)
    sm.update("CLOSED")
    sm.update("UNKNOWN")                       # mid-motion, hand still visible
    assert sm.update("OPEN") == "RECEIVE"


def test_cooldown():
    sm = GestureStateMachine(debounce=1, cooldown=1.0)
    sm.update("OPEN")
    assert sm.update("CLOSED") == "SEND"
    # Immediate re-open is inside the cooldown window -> suppressed (but the
    # pose still flips, so stable is now OPEN again).
    assert sm.update("OPEN") is None
    time.sleep(1.0)
    # Cooldown has expired: the next real edge fires again.
    assert sm.update("CLOSED") == "SEND"


if __name__ == "__main__":
    test_classifier()
    test_debounce_and_edge()
    test_lost_hand_resets_baseline()
    test_brief_ambiguity_keeps_baseline()
    test_cooldown()
    print("all gesture self-checks passed")
