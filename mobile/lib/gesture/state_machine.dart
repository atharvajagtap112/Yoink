/// Turns a stream of per-frame poses into SEND / RECEIVE events.
///
/// Direct port of daemon/gesture/state_machine.py — keep the two in sync.
///
/// Fires on the transition between *stable* poses, never on a held pose:
///     stable open   -> closed = SEND
///     stable closed -> open   = RECEIVE
///
/// Debounce: a new pose must hold N consecutive frames before it becomes stable,
/// so one noisy frame can't misfire. Cooldown: after firing, ignore events for a
/// short window, so one deliberate motion = one event, not a burst.
///
/// Two kinds of "not a pose" are handled differently, and the difference matters:
///   unknown = a hand is visible but mid-motion/ambiguous. A real fist->open
///             passes through these frames, so we keep the last stable pose.
///   noHand  = no hand in frame at all. After it's been gone a moment we forget
///             the baseline, so dropping your hand and raising an open palm
///             doesn't read as closed->open and fire a phantom RECEIVE.
library;

import 'classifier.dart' show Pose;

enum GestureEvent { send, receive }

const int debounceFrames = 4;
const Duration cooldownDuration = Duration(milliseconds: 800);
const int lostHandFrames = 8; // no-hand frames before the baseline is forgotten

class GestureStateMachine {
  GestureStateMachine({
    this.debounce = debounceFrames,
    this.cooldown = cooldownDuration,
    this.lostFrames = lostHandFrames,
  });

  final int debounce;
  final Duration cooldown;
  final int lostFrames;

  Pose? stable; // last accepted stable pose: open | closed
  Pose? _candidate; // pose currently accumulating frames
  int _count = 0;
  int _absent = 0; // consecutive noHand frames
  final _clock = Stopwatch()..start();
  Duration _lastFire = Duration.zero;
  bool _fired = false;

  /// Feed one frame's pose. Returns the fired event, or null.
  GestureEvent? update(Pose pose) {
    if (pose == Pose.noHand) {
      _candidate = null;
      _count = 0;
      _absent++;
      if (_absent >= lostFrames) {
        stable = null; // hand's been gone; next pose is a fresh start
      }
      return null;
    }
    _absent = 0;

    if (pose == Pose.unknown) {
      _candidate = null;
      _count = 0;
      return null;
    }

    if (pose == _candidate) {
      _count++;
    } else {
      _candidate = pose;
      _count = 1;
    }

    if (_count < debounce || pose == stable) return null;

    // Candidate has held long enough: it's the new stable pose.
    final prev = stable;
    stable = pose;
    if (prev == null) return null; // first pose seen — nothing to transition from

    if (_fired && _clock.elapsed - _lastFire < cooldown) return null;

    GestureEvent? event;
    if (prev == Pose.open && pose == Pose.closed) {
      event = GestureEvent.send;
    } else if (prev == Pose.closed && pose == Pose.open) {
      event = GestureEvent.receive;
    }
    if (event != null) {
      _lastFire = _clock.elapsed;
      _fired = true;
    }
    return event;
  }
}
