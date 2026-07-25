# Yoink — gesture-driven cross-device clipboard

> Codename "Yoink" (grab-and-throw). Rename freely — just search-replace.

This file is the source of truth for the project. Read it fully at the start of every
session before writing code. Do not re-architect the decisions recorded here; they were
made deliberately. If something here genuinely needs to change, flag it and ask first.

---

## 1. What we're building

A tool that moves content between your own devices with a hand gesture, over the local
network, with no cloud.

- **Close your hand (open → fist)** on the device you're looking at → it grabs whatever
  you're pointing at (a link, an image, a PDF, selected text) and sends it.
- **Open your hand (fist → open)** on another device → it "catches" the content and pops
  it open: a link opens in the browser, a file opens in its default app, an image shows a
  preview.

The feel we're after: fist to grab, open hand to drop. Your hand is the UI.

---

## 2. Core design decisions (fixed)

These are settled. Don't reverse them without asking.

- **LAN only, no cloud.** Devices talk directly over the local network. No server to host,
  lower latency, nothing leaves the network. No Redis, no Kafka, no message broker — those
  were considered and rejected for this scale.
- **Mesh, not a hub.** Every device is an equal peer and connects directly to every other
  peer. There is no central/hub node, so there is no single point of failure. At 2–3 devices
  the connection count is trivial (2 devices = 1 link, 3 = 3 links). Leader election / a
  coordinator is the *future* scale-up path only, not for now.
- **Event-based, not stateful.** A gesture is an event pushed to peers. There is no shared
  database and no authority that owns "the clipboard." Each receiver holds its own copy of
  the last thing received. This is why mesh works without a coordinator.
- **Gestures are transitions, not poses.** We fire on the *motion* between poses, never on a
  held pose. A held fist or held palm does nothing. Only open→fist (send) and fist→open
  (receive) fire. This is what kills false triggers.
- **Sender is hard, receiver is simple.** The sender does all the messy work of figuring out
  *what* to grab (browser vs Explorer vs arbitrary app). By the time a payload arrives, its
  type is already decided, so the receiver just saves-and-opens by type. Build the receiver
  once; it handles everything.

### The honest tradeoff on grabbing real files

For getting the *original* file automatically from an *arbitrary* app, you can have any two
of {automatic, any-app, original-file} — not all three. This is a Windows API limitation,
not a code problem, and no prompt fixes it:

- Browser (via extension) and File Explorer (via UI Automation) expose the real path → all
  three at once. These are the clean cases.
- Any other app (Photos, a PDF viewer) → either accept a keystroke (we synthesize Ctrl+C
  for the user, so it still feels automatic) and take the real clipboard data, or fall back
  to a focused-window screenshot. For "get this photo/page onto my other device," clipboard
  image data or a screenshot is usually exactly what's wanted anyway.

---

## 3. Architecture

Two cooperating processes per desktop device, plus a browser extension:

1. **Gesture + daemon (Python).** Owns the camera, classifies gestures, runs the mesh
   networking, performs the grab, and shows the receive pop. This is the workhorse.
2. **Browser extension (JS, Manifest V3).** Connects to the daemon over `ws://localhost` and
   answers "what's in the browser right now" (active URL, selection, hovered image, YouTube
   timestamp). Only consulted when the browser is the focused app.
3. **Android client (Kotlin).** Reimplements the daemon's client role natively. The camera
   lives in a **foreground service**, not the Activity — see section 3a for why that is
   forced, and why it killed an earlier Flutter version of this client.

Everything is glued by a shared **JSON wire protocol** (section 6), not shared code — that's
why Python-on-desktop and Kotlin-on-phone coexist cleanly.

**The phone is client-only.** It dials out to discovered desktops and never listens or
advertises. One full-duplex socket carries both directions, and since the desktop mesh
already broadcasts to every live connection, an inbound phone connection receives payloads
with no desktop changes. This also removes the "lower device_id dials" dedupe rule on the
phone side: the desktop can't discover the phone, so it can never dial it, so there is no
second socket to dedupe.

**Two desktop changes were needed for real cross-device use** (both were loopback-only
choices that made a phone connection impossible):
- `net/mesh.py` binds `0.0.0.0`, not `127.0.0.1`, so a LAN peer can connect. Pairing is the
  trust gate, so listening on the LAN is safe by design.
- `net/discovery.py` advertises the machine's LAN IP, not `127.0.0.1`. Loopback testing
  still works — two instances on one laptop just reach each other via the LAN IP.

### Send path (on close gesture)

```
close detected → check foreground app
  browser focused?   → ask extension → URL / selection / hovered image / file:/// path
  Explorer focused?  → UI Automation → selected file's real path → send bytes
  PDF viewer?        → try UI Automation for doc path (best effort)
  anything else?     → synthesize Ctrl+C → read clipboard (real file ref or image data)
  nothing usable?    → screenshot the focused window (universal fallback)
→ wrap in JSON envelope → broadcast to all mesh peers
```

### Receive path (on open gesture)

```
open detected → take last received payload → dispatch by type:
  url      → open in default browser
  image    → save to disk → preview toast
  file     → save bytes to disk → open with OS default app (association)
  text     → write to clipboard → toast
→ show always-on-top "caught: <name>" pop (auto-open or tap-to-open)
```

The receiver uses OS default-app association to open things (`os.startfile` on Windows,
`Intent`+MIME on Android, `open` on macOS). It never hardcodes which app to launch.

---

## 3a. Android platform constraints (verified on-device, don't re-litigate)

These were each discovered the hard way. They are OS behaviour, not code problems, and they
shape the Android client's whole design. Verified on a OnePlus CPH2569, Android 14.

**No cross-app content access.** Nothing on Android can ask Chrome for its URL or a PDF
viewer for its file path — there is no equivalent of the Windows UI Automation tricks in
`grab/router.py`. So the phone's grab is only ever: clipboard text, or a screenshot. It can
never send the real PDF. The only path to real files from another app is Android's **share
sheet** (Share → Yoink), which is a tap, not a gesture. This is the phone's version of the
section 2 tradeoff, and it is harsher than Windows'.

**Background camera requires a foreground service.** A backgrounded app loses camera access
(Android 9+). Keeping gestures alive while you're in another app needs a foreground service
with `android:foregroundServiceType="camera"`, plus `FOREGROUND_SERVICE` and
`FOREGROUND_SERVICE_CAMERA`. `CAMERA` is a *while-in-use* permission, so **the service must
be started while the app is visible** — starting it from the background throws
`SecurityException`.

**This is what ended the Flutter client.** `camera_android_camerax` binds CameraX to the
*Activity* lifecycle, so the stream dies when the app backgrounds and no foreground service
changes that. Kotlin owns the `LifecycleOwner` and can hand it to a `LifecycleService`. That
single difference is the reason the mobile client is native.

**Background *opening* requires `SYSTEM_ALERT_WINDOW`.** Background activity starts have
been blocked since Android 10, so `startActivity` from a backgrounded app silently does
nothing — which would break the RECEIVE gesture on the homescreen. Holding "Display over
other apps" is a documented exemption and **it works**: verified at a 30-second delay, with
the framework logging the reason itself:
```
W ActivityTaskManager: Background activity start for com.yoink allowed
                       because SYSTEM_ALERT_WINDOW permission is granted.
I ActivityTaskManager: START ... com.android.chrome ... (BAL_ALLOW_SAW_PERMISSION)
```
We want that permission anyway — the always-on-top "caught" pop *is* an overlay window.

**Beware the BAL grace period when testing this.** A recently-interacted-with app may start
activities regardless of permission; AOSP sets that window to 10 s. The first version of the
spike waited 5 s and "passed" even with the permission revoked — a false green light. Any
test of background launching must wait well past 10 s, and should confirm the reason code in
logcat rather than trusting that something appeared on screen.

**Clipboard is readable only by the focused app** (Android 10+). So a gesture made while
backgrounded cannot read the clipboard, and the background grab is screenshot-only. The
clipboard-first strategy only applies when Yoink itself is on screen.

**MediaProjection consent is per-session and single-use.** Ask once, then keep one
`VirtualDisplay` alive for the app session and pull frames from it; tearing it down means a
new consent dialog on every gesture. The foreground service must be up *before* the
projection is created (Android 14+), which is why it is created in `onServiceConnected`
rather than inline.

---

## 4. Gesture model

- Each camera frame → MediaPipe Hands → 21 landmarks.
- Classify the single frame as `OPEN` (all fingers extended) or `CLOSED` (all fingertips
  curled toward the palm), based on finger-curl geometry. Ambiguous mid-motion frames are
  neither.
- Track the last **stable** state. Fire on the edge:
  - stable `OPEN` → `CLOSED` = **SEND**
  - stable `CLOSED` → `OPEN` = **RECEIVE**
- **Debounce:** a new state must hold ~3–5 consecutive frames (~100–150 ms) before it's
  accepted, so one noisy frame can't misfire.
- **Cooldown:** after firing, ignore new events for ~800 ms, so one deliberate motion = one
  event, not a burst.

---

## 5. Tech stack

**Desktop daemon (Python)**
- `mediapipe`, `opencv-python` — camera + hand landmarks + classifier
- `zeroconf` — mDNS peer discovery
- `websockets` (asyncio) — mesh connections + heartbeats
- `pywin32` — Windows clipboard, `CF_HDROP` (real file paths), `GetForegroundWindow`,
  synthetic Ctrl+C
- `uiautomation` — read selected/open file paths from Explorer and Acrobat
- `Pillow` — clipboard images + focused-window screenshots
- `tkinter` (built-in) for the MVP toast; `PySide6` later for a nicer pop

**Browser extension (JS, Manifest V3)** — content script + background service worker +
`ws://localhost` client.

**Android client (Kotlin, native)** — in `android/`, built with Gradle. Not Flutter; see
section 3a for why.
- CameraX (`camera-core`/`camera2`/`camera-lifecycle`/`camera-view`) — frames, bound to a
  `LifecycleService` so they survive backgrounding
- `com.google.mediapipe:tasks-vision` — Hand Landmarker, model bundled at
  `app/src/main/assets/hand_landmarker.task`
- `androidx.lifecycle:lifecycle-service` — the `LifecycleOwner` CameraX binds to
- NSD (`android.net.nsd`) for discovery, OkHttp WebSocket for the mesh
- MediaProjection for screen capture, ClipboardManager, `Intent`+MIME to open

Toolchain note: AGP 9+ has **built-in Kotlin** — applying `org.jetbrains.kotlin.android` on
top of it is a hard error. And `local.properties` is a Java properties file, so `sdk.dir`
needs forward slashes; backslashes are escapes and get silently mangled.

**Superseded: `mobile/` (Flutter/Dart).** A complete Flutter client through milestones
7a–7d (gestures, mesh, pairing, typed dispatch, grab). It works, but only while the app is
foreground — section 3a explains why that is unfixable in Flutter. Kept for reference until
the Kotlin client reaches parity, then delete. Don't add features to it.

Ask before adding any dependency not listed here.

---

## 6. Wire protocol

One JSON object per WebSocket message. UTF-8. Binary payloads are base64 in `data`.

```json
{
  "v": 1,
  "kind": "payload",
  "type": "url",
  "filename": null,
  "mime": null,
  "data": "https://youtube.com/watch?v=abc&t=142s",
  "sender": "atharva-laptop",
  "ts": 1720900000
}
```

- `kind`: `hello` | `pair` | `heartbeat` | `payload`
- `type` (when `kind` = `payload`): `url` | `text` | `image` | `file`
- `filename`, `mime`: set for `image`/`file`, else null
- `data`: raw string for `url`/`text`; base64 for `image`/`file`
- `sender`: stable device name; `ts`: unix seconds

**Pairing:** on first connection between two peers, exchange a short PIN (`kind: "pair"`)
that the user confirms, so a stranger on shared WiFi can't connect. Required even on LAN.

---

## 7. Repo layout

```
yoink/
  CLAUDE.md              # this file
  README.md
  daemon/
    main.py              # entry point: wire modules, run asyncio loop
    config.py            # ports, device name, save paths
    gesture/
      camera.py          # opencv capture + mediapipe hands
      classifier.py      # landmarks -> OPEN / CLOSED
      state_machine.py   # transitions, debounce, cooldown -> SEND / RECEIVE
    net/
      protocol.py        # envelope encode/decode + schema
      peer.py            # one websocket peer connection + heartbeat
      discovery.py       # zeroconf advertise + browse
      mesh.py            # peer set, broadcast, reconnect
      pairing.py         # PIN handshake
    grab/
      router.py          # GetForegroundWindow -> choose strategy
      clipboard_win.py   # text / CF_HDROP / image via pywin32 + Pillow
      explorer.py        # uiautomation -> selected file path
      screenshot.py      # focused-window capture
      browser_bridge.py  # ws://localhost server for the extension
    receive/
      dispatch.py        # type -> save + open by association
      toast.py           # always-on-top pop
  extension/             # MV3 browser extension (milestone 6)
  android/               # Kotlin client (milestones K1+) — the live mobile client
    app/src/main/
      assets/hand_landmarker.task    # bundled MediaPipe model
      kotlin/com/yoink/
        MainActivity.kt              # thin viewer: binds the service, draws the overlay
        GestureService.kt            # LifecycleService: owns the camera, fires gestures
        OverlayView.kt               # landmarks + pose + SEND/RECEIVE flash
        gesture/
          Classifier.kt              # landmarks -> OPEN / CLOSED  (port of classifier.py)
          StateMachine.kt            # debounce/cooldown           (port of state_machine.py)
          HandTracker.kt             # CameraX + MediaPipe -> classifier -> state machine
    app/src/test/                    # JVM unit tests, no emulator needed
  mobile/                # SUPERSEDED Flutter client (7a-7d). Reference only; see section 5.
```

The Kotlin `gesture/` files are deliberate line-by-line ports of the Python ones. Keep the
names matched and the behaviour identical — when one changes, change both, and both have the
same self-check suite (`test_gesture.py` / `GestureTest.kt`) covering the same cases.

---

## 8. Milestone ladder

Build one slice per session. Each slice must **run and be visibly testable** on its own —
these are vertical slices, not horizontal layers. Run it, confirm, commit, then move on.
Keep each prompt scoped to a single milestone so the generated code stays small enough to
read and debug.

1. **Gesture detection, standalone.** Camera → landmarks → classifier → state machine.
   Prints `SEND` on close and `RECEIVE` on open, with debounce + cooldown. No networking.
   *Riskiest piece, so it goes first.* **Testable now on one laptop.**
2. **Two instances, text only, over localhost.** Gesture fires a WebSocket send; the other
   instance receives text and shows a toast. Hardcoded ports, no discovery yet.
   **Testable now via loopback (see section 9).**
3. **Auto-discovery.** Replace hardcoded addresses with mDNS + heartbeats + auto-reconnect.
   **Loopback-testable now.**
4. **Mesh + pairing.** Generalize 1:1 → broadcast to all peers; add the PIN handshake.
   **Loopback-testable now.**
5. **Smart grab + typed dispatch.** Foreground routing (Explorer/clipboard real files, image
   data, screenshot fallback); receiver opens by type. Files and images join text here.
   **Send side testable on one laptop; full round-trip via loopback.**
6. **Browser extension.** The `ws://localhost` bridge → URL, selection, hovered image,
   YouTube timestamp. **Testable on one laptop.**
7. **Android client.** Reimplement the client last, protocol already proven.
   **Done in Flutter as 7a–7d, then superseded** — see the K ladder below.

### The K ladder — Kotlin client (current work)

Milestones 1–6 (desktop) are done. The Flutter client 7a–7d is done and works, but only
while the app is foreground, which is unfixable in Flutter (section 3a). The Kotlin client
replaces it. Same vertical-slice rule: each one runs and is testable on its own.

- **K1 — gesture detection standalone.** ✅ Done. CameraX + MediaPipe + the ported classifier
  and state machine, with a live landmark overlay. Carries the background-activity-launch
  spike (kept as a regression check — if an OS update revokes `BAL_ALLOW_SAW_PERMISSION`,
  background catching breaks silently and that button is how you find out).
- **K2 — camera into a foreground service.** ✅ Done. `GestureService` owns the camera;
  gestures fire with the app backgrounded. Verified: gesture toasts over the launcher, and
  `dumpsys` showing `types=00000040` (`FOREGROUND_SERVICE_TYPE_CAMERA`).
- **K3 — networking.** ✅ Done. Protocol, NSD discovery, OkHttp WebSocket mesh, PIN pairing.
  A port, not a redesign: 7b settled the client-only dial rule, the dialer-side handshake,
  and heartbeat/reconnect. No desktop changes. Verified: paired with a fresh PIN and caught
  a real PDF payload from the daemon.
  - Networking lives in `GestureService` alongside the camera, so it survives backgrounding.
    The one exception is the PIN prompt, which needs a human: it is set only while the
    Activity is bound, and with no UI the mesh declines and backs off rather than pairing
    silently.
  - Two Android traps, both silent: `NsdManager.resolveService` answers a *concurrent*
    resolve with `FAILURE_ALREADY_ACTIVE`, so resolves are queued; and org.json's
    `put(key, null)` *deletes* the key, which would drop `type`/`filename`/`mime` from every
    envelope — use `JSONObject.NULL`, and read it back with `isNull` rather than `optString`
    (which returns the literal string "null").
- **K4 — receive + typed dispatch + overlay pop.** ✅ Done. On a payload: decode + save,
  then a `SYSTEM_ALERT_WINDOW` overlay pop (`Pop.kt`); the RECEIVE gesture or a tap opens it
  by type — clipboard / browser / FileProvider `content://` intent. Verified: open hand on
  the **homescreen** and a caught PDF opens over the launcher. That is the payoff the whole
  rewrite was for, and the thing Flutter could not do.
  - Files land in the app-specific external dir and are handed out via a FileProvider
    (`content://`, not `file://` — the latter throws `FileUriExposedException`). Opening from
    the service needs `FLAG_ACTIVITY_NEW_TASK`, and from the background needs the same
    `SYSTEM_ALERT_WINDOW` the pop uses. A blocked open is caught and surfaced, not crashed.
- **K5 — send/grab.** Clipboard (foreground only) + MediaProjection screenshot. The
  `ScreenCaptureService.kt` written for Flutter 7d ports over nearly as-is.

- **K6 — share-sheet sending** (Share → Yoink). ✅ Done. The only way to get a *real* file
  off the phone rather than a screenshot (section 3a). A tap, not a gesture, so it complements
  K5. `ShareActivity` (no UI, translucent) parses the SEND/SEND_MULTIPLE intent, reads the
  `content://` bytes, and broadcasts via the mesh. ponytail: it reuses the camera service to
  reach the mesh, so a share turns the camera on; the upgrade path is a standalone
  NetworkService if share-without-camera ever matters.

Phone UI (post-K5 polish): the preview shows **landmarks only on black** (the camera runs but
its surface is never attached), a **Camera on/off toggle**, and the service stops on
`onTaskRemoved` so swiping Yoink from recents releases the camera.

---

## 9. Testing

**Now available:** one Windows laptop (the daemon) and a OnePlus CPH2569 on Android 14 (the
client), on the same WiFi — usually the phone's own hotspot, with the laptop as a client.
Real two-device testing is possible; use it for the final "does this feel like magic" check.

Practicalities for the real-device path:
- The laptop's inbound rules must cover the daemon's Python binary on the **network profile
  the WiFi is actually on** (a hotspot usually shows as Public, not Private). Existing
  program-scoped rules for `python.exe` may already cover it — check before adding any.
- Install the Kotlin client with `cd android && .\gradlew.bat installDebug`.
- MediaPipe logs ~60 lines/second, which evicts sparse entries from the logcat buffer fast.
  Filter by PID and grep for the specific tag, and don't read absence of a log line as
  absence of the event.
- OxygenOS is aggressive about killing background services. If gestures stop firing while
  backgrounded, check Settings → Battery → App battery usage → Yoink → **Unrestricted**
  before assuming a code bug.

**Loopback stays the primary harness** for anything networking. Every networking feature
must still be runnable as **two daemon instances on the same machine**, on different ports,
talking over `127.0.0.1`:

- Instance A on port 8765, instance B on port 8766, same codebase.
- This exercises the full protocol, mesh logic, pairing, dispatch, and toast — everything
  except the real cross-device feel.
- Provide a `--port`, `--name`, and optional `--peer 127.0.0.1:PORT` flag (or a
  `--loopback` convenience flag that spins up the pair) so this is a one-command test.
- Only the final "does it actually feel magic across two real devices" check is deferred
  until a second machine exists. Build and validate everything else solo.

Keep this loopback path working as milestones are added — it's the primary test harness.

---

## 10. Working agreements for Claude Code

- One milestone per session. Don't jump ahead or scaffold future milestones early.
- Don't re-architect. LAN-only, mesh (no hub), event-based, JSON protocol, transition-based
  gestures — all fixed. Flag and ask before changing any of them.
- **Section 3a is settled too.** Those constraints were each paid for on-device. Don't
  propose working around them without new evidence, and don't promise the phone can grab a
  real file from another app — it can't.
- **Prove platform assumptions before building on them.** The BAL spike cost twenty lines
  and saved four milestones of work resting on a guess. When an OS behaviour is load-bearing,
  test it in isolation first, wait past any grace period, and confirm the reason code rather
  than trusting what appears on screen.
- Keep modules small and readable. The human is vibecoding and must be able to follow the
  code to debug it later. Prefer clarity over cleverness.
- No cloud, no message broker, no heavy frameworks. Ask before adding a new dependency.
- Every networking feature ships with a single-machine loopback test (section 9).
- After each milestone: a quick run instruction and a one-line manual test to confirm it
  works before committing.