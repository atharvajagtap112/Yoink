"""Single-frame gesture classifier: 21 hand landmarks -> OPEN / CLOSED / UNKNOWN.

Pure geometry, no trained model. A finger counts as *extended* when its tip sits
clearly farther from the wrist than its PIP joint, and *curled* when clearly
closer. Using distances (not up/down) keeps it independent of hand orientation.
"""
import math

WRIST = 0
# (pip, tip) landmark indices for index, middle, ring, pinky
FINGERS = [(6, 8), (10, 12), (14, 16), (18, 20)]
THUMB = (2, 4)  # (mcp, tip)

# ponytail: two thresholds with a dead zone in between so mid-motion frames read
# as UNKNOWN instead of flickering OPEN<->CLOSED. Tune on the live overlay.
EXTENDED_RATIO = 1.05   # tip this much farther from wrist than pip -> extended
CURLED_RATIO = 0.95     # tip this much closer than pip -> curled


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ratio(lm, base, tip):
    d_base = _dist(lm[WRIST], lm[base])
    if d_base == 0:
        return 1.0
    return _dist(lm[WRIST], lm[tip]) / d_base


def classify(lm):
    """lm: list of 21 (x, y) landmarks. Returns 'OPEN' | 'CLOSED' | 'UNKNOWN'."""
    if not lm or len(lm) < 21:
        return "UNKNOWN"

    extended = curled = 0
    for pip, tip in FINGERS:
        r = _ratio(lm, pip, tip)
        if r >= EXTENDED_RATIO:
            extended += 1
        elif r <= CURLED_RATIO:
            curled += 1

    thumb_extended = _ratio(lm, *THUMB) >= EXTENDED_RATIO

    # Forgiving on the thumb (it folds sideways, not toward the wrist): OPEN needs
    # all four fingers out; CLOSED needs all four curled.
    if extended == 4 and thumb_extended:
        return "OPEN"
    if curled == 4:
        return "CLOSED"
    return "UNKNOWN"
