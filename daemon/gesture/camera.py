"""Camera + MediaPipe Hands loop.

Grabs frames, extracts the 21 landmarks, feeds them to the classifier and state
machine, draws the landmarks + current pose on a live window, and calls
`on_event` when a SEND/RECEIVE fires. Press q or Esc to quit.
"""
import time

import cv2
import mediapipe as mp

from .classifier import classify
from .state_machine import GestureStateMachine

mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils

POSE_COLOR = {"OPEN": (0, 200, 0), "CLOSED": (0, 120, 255)}  # BGR


def list_cameras(max_index=6):
    """Return indices that actually open. Probes 0..max_index-1."""
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened() and cap.read()[0]:
            found.append(i)
        cap.release()
    return found


def pick_camera():
    """Live picker: SPACE cycles cameras, ENTER selects. Returns chosen index."""
    cams = list_cameras()
    if not cams:
        raise RuntimeError("No cameras found.")
    if len(cams) == 1:
        return cams[0]
    pos = 0
    while True:
        idx = cams[pos]
        cap = cv2.VideoCapture(idx)
        while cap.isOpened():
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            cv2.putText(frame, f"Camera {idx}  ({pos + 1}/{len(cams)})", (12, 44),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 0), 2)
            cv2.putText(frame, "SPACE = next   ENTER = use this", (12, 88),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow("Yoink — pick a camera", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 32:  # SPACE
                pos = (pos + 1) % len(cams)
                break
            if key in (13, 10):  # ENTER
                cap.release()
                cv2.destroyWindow("Yoink — pick a camera")
                return idx
            if key in (ord("q"), 27):
                cap.release()
                cv2.destroyWindow("Yoink — pick a camera")
                return idx
        cap.release()


def run(on_event=None, camera_index=0):
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera {camera_index}")

    sm = GestureStateMachine()
    last_event = None
    last_event_t = 0.0

    with mp_hands.Hands(max_num_hands=1, min_detection_confidence=0.6,
                        min_tracking_confidence=0.6) as hands:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)  # mirror so it feels like a real mirror
            result = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

            state = "NO_HAND"
            if result.multi_hand_landmarks:
                hand = result.multi_hand_landmarks[0]
                mp_draw.draw_landmarks(frame, hand, mp_hands.HAND_CONNECTIONS)
                state = classify([(p.x, p.y) for p in hand.landmark])

            event = sm.update(state)
            if event:
                last_event, last_event_t = event, time.monotonic()
                if on_event:
                    on_event(event)

            _overlay(frame, state, last_event, last_event_t)
            cv2.imshow("Yoink — gesture (q quit, s=SEND r=RECEIVE)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            # Test aids: fire an event by keypress without contorting your hand.
            if key in (ord("s"), ord("r")) and on_event:
                forced = "SEND" if key == ord("s") else "RECEIVE"
                last_event, last_event_t = forced, time.monotonic()
                on_event(forced)

    cap.release()
    cv2.destroyAllWindows()


def _overlay(frame, state, last_event, last_event_t):
    color = POSE_COLOR.get(state, (150, 150, 150))
    cv2.putText(frame, state, (12, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.3, color, 3)
    # Linger the last event for ~1s so it's readable, not a one-frame flash.
    if last_event and time.monotonic() - last_event_t < 1.0:
        cv2.putText(frame, last_event, (12, 96), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                    (0, 0, 255), 3)
