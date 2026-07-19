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
3. **Android client (Kotlin) — later.** Reimplements the daemon's client role natively.
   Deferred until the protocol is battle-tested on desktop.

Everything is glued by a shared **JSON wire protocol** (section 6), not shared code — that's
why Python-on-desktop and Kotlin-on-phone coexist cleanly.

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

**Android (Kotlin, later)** — CameraX, MediaPipe Tasks (Hand Landmarker), NSD for discovery,
OkHttp/Ktor WebSocket, MediaProjection for capture, ClipboardManager, Intent+MIME to open.

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
```

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
7. **Android client (Kotlin).** Reimplement the client last, protocol already proven.
   Needs the phone; deferred.

---

## 9. Testing without a second laptop

There is currently only one laptop and no Android device. Do **not** let this block the
networking milestones. Every networking feature must be runnable as **two daemon instances
on the same machine**, on different ports, talking over `127.0.0.1`:

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
- Keep modules small and readable. The human is vibecoding and must be able to follow the
  code to debug it later. Prefer clarity over cleverness.
- No cloud, no message broker, no heavy frameworks. Ask before adding a new dependency.
- Every networking feature ships with a single-machine loopback test (section 9).
- After each milestone: a quick run instruction and a one-line manual test to confirm it
  works before committing.