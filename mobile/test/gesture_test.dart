/// Port of daemon/gesture/test_gesture.py — same cases, same expectations.
/// Run: flutter test
library;

import 'dart:ui' show Offset;

import 'package:flutter_test/flutter_test.dart';
import 'package:yoink_mobile/gesture/classifier.dart';
import 'package:yoink_mobile/gesture/state_machine.dart';

/// 21 fake landmarks. curl=false -> open (tips far from the wrist at 0,1),
/// curl=true -> closed (tips pulled back near the wrist).
List<Offset> hand({required bool curl}) {
  final lm = List<Offset>.filled(21, const Offset(0.5, 0.5), growable: true);
  lm[0] = const Offset(0.5, 1.0); // wrist at the bottom
  for (final f in [
    [2, 4],
    [6, 8],
    [10, 12],
    [14, 16],
    [18, 20],
  ]) {
    lm[f[0]] = const Offset(0.5, 0.6);
    lm[f[1]] = curl ? const Offset(0.5, 0.7) : const Offset(0.5, 0.3);
  }
  return lm;
}

void main() {
  test('classifier', () {
    expect(classify(hand(curl: false)), Pose.open);
    expect(classify(hand(curl: true)), Pose.closed);
    expect(classify([]), Pose.unknown);
  });

  test('debounce and edge', () {
    final sm = GestureStateMachine(debounce: 3, cooldown: Duration.zero);
    // First stable pose (open) establishes state, fires nothing.
    for (var i = 0; i < 3; i++) {
      expect(sm.update(Pose.open), isNull);
    }
    expect(sm.update(Pose.closed), isNull); // debounce
    expect(sm.update(Pose.closed), isNull);
    expect(sm.update(Pose.closed), GestureEvent.send); // third frame -> edge

    expect(sm.update(Pose.open), isNull);
    expect(sm.update(Pose.open), isNull);
    expect(sm.update(Pose.open), GestureEvent.receive);
  });

  test('lost hand resets baseline', () {
    // Fist (SEND), hand leaves the frame, open palm reappears. That must NOT
    // fire RECEIVE — the baseline was forgotten while the hand was gone.
    final sm = GestureStateMachine(
      debounce: 1,
      cooldown: Duration.zero,
      lostFrames: 3,
    );
    sm.update(Pose.closed); // baseline = closed
    for (var i = 0; i < 3; i++) {
      expect(sm.update(Pose.noHand), isNull);
    }
    expect(sm.update(Pose.open), isNull); // fresh open, no phantom RECEIVE
  });

  test('brief ambiguity keeps baseline', () {
    final sm = GestureStateMachine(debounce: 1, cooldown: Duration.zero);
    sm.update(Pose.closed);
    sm.update(Pose.unknown); // mid-motion, hand still visible
    expect(sm.update(Pose.open), GestureEvent.receive);
  });

  test('cooldown', () async {
    final sm = GestureStateMachine(
      debounce: 1,
      cooldown: const Duration(milliseconds: 300),
    );
    sm.update(Pose.open);
    expect(sm.update(Pose.closed), GestureEvent.send);
    // Immediate re-open is inside the cooldown window -> suppressed (but the
    // pose still flips, so stable is now open again).
    expect(sm.update(Pose.open), isNull);
    await Future<void>.delayed(const Duration(milliseconds: 350));
    expect(sm.update(Pose.closed), GestureEvent.send);
  });
}
