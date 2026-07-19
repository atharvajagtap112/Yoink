/// Single-frame gesture classifier: 21 hand landmarks -> OPEN / CLOSED / UNKNOWN.
///
/// Direct port of daemon/gesture/classifier.py — keep the two in sync.
/// Pure geometry, no trained model. A finger counts as *extended* when its tip
/// sits clearly farther from the wrist than its PIP joint, and *curled* when
/// clearly closer. Using distances (not up/down) keeps it independent of hand
/// orientation.
library;

import 'dart:ui' show Offset;

enum Pose { open, closed, unknown, noHand }

const int _wrist = 0;
// (pip, tip) landmark indices for index, middle, ring, pinky
const List<List<int>> _fingers = [
  [6, 8],
  [10, 12],
  [14, 16],
  [18, 20],
];
const List<int> _thumb = [2, 4]; // (mcp, tip)

// ponytail: two thresholds with a dead zone in between so mid-motion frames read
// as unknown instead of flickering open<->closed. Tune on the live overlay.
const double extendedRatio = 1.05; // tip this much farther from wrist -> extended
const double curledRatio = 0.95; // tip this much closer than pip -> curled

double _ratio(List<Offset> lm, int base, int tip) {
  final dBase = (lm[_wrist] - lm[base]).distance;
  if (dBase == 0) return 1.0;
  return (lm[_wrist] - lm[tip]).distance / dBase;
}

/// [lm]: 21 normalized (x, y) landmarks.
Pose classify(List<Offset> lm) {
  if (lm.length < 21) return Pose.unknown;

  var extended = 0, curled = 0;
  for (final f in _fingers) {
    final r = _ratio(lm, f[0], f[1]);
    if (r >= extendedRatio) {
      extended++;
    } else if (r <= curledRatio) {
      curled++;
    }
  }

  final thumbExtended = _ratio(lm, _thumb[0], _thumb[1]) >= extendedRatio;

  // Forgiving on the thumb (it folds sideways, not toward the wrist): open needs
  // all four fingers out; closed needs all four curled.
  if (extended == 4 && thumbExtended) return Pose.open;
  if (curled == 4) return Pose.closed;
  return Pose.unknown;
}
