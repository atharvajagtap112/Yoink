"""Turns a stream of per-frame poses into SEND / RECEIVE events.

Fires on the transition between *stable* poses, never on a held pose:
    stable OPEN   -> CLOSED = SEND
    stable CLOSED -> OPEN   = RECEIVE

Debounce: a new pose must hold N consecutive frames before it becomes stable,
so one noisy frame can't misfire. Cooldown: after firing, ignore events for a
short window, so one deliberate motion = one event, not a burst.

Two kinds of "not a pose" are handled differently, and the difference matters:
  UNKNOWN  = a hand is visible but mid-motion/ambiguous. A real fist->open passes
             through these frames, so we keep the last stable pose across them.
  NO_HAND  = no hand in frame at all. After it's been gone a moment we forget the
             baseline, so dropping your hand and raising an open palm doesn't read
             as CLOSED->OPEN and fire a phantom RECEIVE.
"""
import time

DEBOUNCE_FRAMES = 4
COOLDOWN_S = 0.8
LOST_HAND_FRAMES = 8     # no-hand frames before the baseline pose is forgotten


class GestureStateMachine:
    def __init__(self, debounce=DEBOUNCE_FRAMES, cooldown=COOLDOWN_S,
                 lost_frames=LOST_HAND_FRAMES):
        self.debounce = debounce
        self.cooldown = cooldown
        self.lost_frames = lost_frames
        self.stable = None       # last accepted stable pose: 'OPEN' | 'CLOSED'
        self._candidate = None   # pose currently accumulating frames
        self._count = 0
        self._absent = 0         # consecutive NO_HAND frames
        self._last_fire = 0.0

    def update(self, state):
        """Feed one frame's pose. Returns 'SEND' | 'RECEIVE' | None."""
        if state == "NO_HAND":
            self._candidate = None
            self._count = 0
            self._absent += 1
            if self._absent >= self.lost_frames:
                self.stable = None   # hand's been gone; next pose is a fresh start
            return None
        self._absent = 0

        if state == "UNKNOWN":
            self._candidate = None
            self._count = 0
            return None

        if state == self._candidate:
            self._count += 1
        else:
            self._candidate = state
            self._count = 1

        if self._count < self.debounce or state == self.stable:
            return None

        # Candidate has held long enough: it's the new stable pose.
        prev, self.stable = self.stable, state
        if prev is None:
            return None  # first pose seen — nothing to transition from

        if time.monotonic() - self._last_fire < self.cooldown:
            return None

        event = None
        if prev == "OPEN" and state == "CLOSED":
            event = "SEND"
        elif prev == "CLOSED" and state == "OPEN":
            event = "RECEIVE"
        if event:
            self._last_fire = time.monotonic()
        return event
