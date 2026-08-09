"""Camera + MediaPipe Hands loop.

Grabs frames, extracts the 21 landmarks, feeds them to the classifier and state
machine, draws the landmarks + current pose on a live window, and calls
`on_event` when a SEND/RECEIVE fires. Press q or Esc to quit.

`CameraWorker` is the headless-of-cv2 version used by the desktop GUI: same
capture + MediaPipe pipeline on a background thread, but it never opens its own
window — it hands the normalized landmarks, pose and events to a callback so the
GUI can draw a skeleton-only view (matching the Android client). It can switch
cameras live and be stopped cleanly.
"""
import threading
import time

import cv2

from .classifier import classify
from .state_machine import GestureStateMachine

# mediapipe is imported lazily, inside the capture loop on the worker thread: it
# pulls in TensorFlow Lite and takes a few seconds, and doing that at module
# import froze the app's launch ("Not Responding"). Deferring it lets the window
# paint immediately while the camera warms up in the background.

POSE_COLOR = {"OPEN": (0, 200, 0), "CLOSED": (0, 120, 255)}  # BGR


class CameraWorker:
    """Runs the gesture pipeline on a background thread, no UI of its own.

    on_frame(landmarks, pose, event): called every frame on the worker thread.
      landmarks is a list of 21 (x, y) floats in 0..1 (mirrored), or [] when no
      hand is seen; pose is 'OPEN'/'CLOSED'/'UNKNOWN'/'NO_HAND'; event is
      'SEND'/'RECEIVE' or None. The callback must be quick and thread-safe (the
      GUI just stashes the data and redraws on its own timer).
    """

    def __init__(self, on_frame, index=0):
        self.on_frame = on_frame
        self._index = index
        self._next_index = index
        self._sm = GestureStateMachine()
        self._stop = threading.Event()
        self._thread = None
        self.error = None       # last capture error, for the GUI to surface

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_camera(self, index):
        """Switch cameras live; the run loop reopens on the next iteration."""
        self._next_index = index

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _open(self, index):
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            self.error = f"Could not open camera {index}"
            cap.release()
            return None
        self.error = None
        self._index = index
        return cap

    def _run(self):
        import mediapipe as mp        # lazy: keeps launch fast (see module note)
        mp_hands = mp.solutions.hands
        cap = self._open(self._index)
        # A closed fist tracks with much lower confidence than an open palm
        # (fingers curl and self-occlude), so at 0.6 the fist dropped out and no
        # landmarks were returned. Low tracking confidence holds the fist; the
        # state machine's debounce/cooldown filters any added jitter.
        with mp_hands.Hands(max_num_hands=1, min_detection_confidence=0.5,
                            min_tracking_confidence=0.3) as hands:
            while not self._stop.is_set():
                if self._next_index != self._index or cap is None:
                    if cap is not None:
                        cap.release()
                    self._sm = GestureStateMachine()   # stale baseline, reset
                    cap = self._open(self._next_index)
                    if cap is None:
                        time.sleep(0.5)                 # bad index; keep trying
                        continue
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.02)
                    continue
                frame = cv2.flip(frame, 1)              # mirror, like a real mirror
                result = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

                landmarks, pose = [], "NO_HAND"
                if result.multi_hand_landmarks:
                    hand = result.multi_hand_landmarks[0]
                    landmarks = [(p.x, p.y) for p in hand.landmark]
                    pose = classify(landmarks)

                event = self._sm.update(pose)
                self.on_frame(landmarks, pose, event)
        if cap is not None:
            cap.release()


def list_cameras(max_index=6, skip=None):
    """Return indices that actually open. Probes 0..max_index-1.

    `skip` is assumed working and never reopened — pass the index the worker is
    already using, because opening the same camera twice on Windows can fail or
    hand back black frames.
    """
    found = []
    for i in range(max_index):
        if i == skip:
            found.append(i)
            continue
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

    import mediapipe as mp            # lazy: keeps launch fast (see module note)
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils

    sm = GestureStateMachine()
    last_event = None
    last_event_t = 0.0

    with mp_hands.Hands(max_num_hands=1, min_detection_confidence=0.5,
                        min_tracking_confidence=0.3) as hands:
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
