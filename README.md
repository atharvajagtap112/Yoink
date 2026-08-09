# Yoink

**Move a link, a file, a photo or some text between your own devices with a hand gesture.
Over your WiFi. No cloud, no account, no upload.**

Close your hand at your laptop — it grabs whatever you're pointing at and throws it.
Open your hand at your phone — it catches it and opens it.

```
   ✊  fist          =  SEND      grab what's on screen and throw it to your devices
   🖐  open hand     =  RECEIVE   catch the last thing thrown and open it
```

Nothing leaves your local network. There is no server to sign up for, no database, and
no company in the middle — your devices talk straight to each other.

---

## Contents

1. [What it can actually move](#1-what-it-can-actually-move)
2. [Quick start](#2-quick-start)
3. [Requirements](#3-requirements)
4. [Desktop — run it from a terminal](#4-desktop--run-it-from-a-terminal)
5. [Desktop — build a standalone `Yoink.exe`](#5-desktop--build-a-standalone-yoinkexe)
6. [Browser extension (optional but worth it)](#6-browser-extension-optional-but-worth-it)
7. [Android — build the APK and install it](#7-android--build-the-apk-and-install-it)
8. [Pairing: the PIN, and what it protects](#8-pairing-the-pin-and-what-it-protects)
9. [Using it — what gets sent, from where, and every fallback](#9-using-it--what-gets-sent-from-where-and-every-fallback)
10. [Catching — what happens when something arrives](#10-catching--what-happens-when-something-arrives)
11. [How it works under the hood](#11-how-it-works-under-the-hood)
12. [Security model — honestly](#12-security-model--honestly)
13. [Limits you should know about](#13-limits-you-should-know-about)
14. [Troubleshooting](#14-troubleshooting)
15. [Testing it on one machine (loopback)](#15-testing-it-on-one-machine-loopback)
16. [Repo layout](#16-repo-layout)

---

## 1. What it can actually move

Yoink moves four kinds of thing — `url`, `text`, `image`, `file`. What a device *can grab*
depends on the operating system, and the honest matrix is:

| From ➜ | Windows PC | Android phone |
|---|---|---|
| **Web link you're on** | ✅ automatic (address bar, or the extension) | ⚠️ only if you copy it first, or Share → Yoink |
| **Text you selected** | ✅ automatic (extension, or synthetic Ctrl+C) | ⚠️ clipboard only, and only while Yoink is on screen |
| **File in File Explorer** | ✅ the real file, bytes and all | — |
| **PDF / Word doc open in an app** | ✅ the real file (best effort) | ❌ impossible — use Share → Yoink |
| **Image you're hovering in a browser** | ✅ with the extension | ❌ use Share → Yoink |
| **Any file at all, deliberately** | ✅ copy it in Explorer, then fist | ✅ **Share → Yoink** from any app |
| **Anything else on screen** | ✅ screenshot fallback (always works) | ✅ screenshot fallback (needs one-time consent) |

| To ➜ | Windows PC | Android phone |
|---|---|---|
| **link** | opens in your default browser | opens in your default browser |
| **text** | lands on your clipboard | lands on your clipboard |
| **image** | saved, preview pop | saved, preview pop, tap to open |
| **file (PDF, docx, zip, anything)** | saved and opened by the app Windows associates with it | saved and opened by the app Android associates with it |

**PC → PC, PC → phone, phone → PC all work.** Phone → phone is not supported (the Android
client is a client only — see [§11](#11-how-it-works-under-the-hood)).

The one thing to internalise: **on Windows, Yoink can send you the real original file.
On Android, a gesture can only get clipboard text or a screenshot** — Android gives no app
permission to ask Chrome for its URL or a PDF reader for its file path. That's an OS wall,
not a missing feature. The way around it is the share sheet: **Share → Yoink** sends the real
bytes of any file, from any app. It's a tap instead of a gesture, and it's the only way.

---

## 2. Quick start

The fastest path to "it works", assuming a Windows laptop and an Android phone.

```
┌── on the laptop ──────────────────────────────────────────────┐
│ 1. pip install -r requirements.txt                            │
│ 2. cd daemon && python main.py                                │
│    → a black window with your hand skeleton opens             │
└───────────────────────────────────────────────────────────────┘
┌── on the phone (same WiFi!) ──────────────────────────────────┐
│ 3. cd android && .\gradlew.bat installDebug                   │
│ 4. open Yoink, allow Camera                                   │
│ 5. tap "Display over other apps" → turn Yoink ON              │
└───────────────────────────────────────────────────────────────┘
┌── pair them, once, forever ───────────────────────────────────┐
│ 6. the laptop window shows a 4-digit PIN                      │
│ 7. the phone pops a dialog — type that PIN → Pair             │
│    → laptop says "1 linked", phone says "1 linked"            │
└───────────────────────────────────────────────────────────────┘
┌── throw something ────────────────────────────────────────────┐
│ 8. select a file in Explorer, make a FIST at the laptop       │
│ 9. OPEN YOUR HAND at the phone → the file opens               │
└───────────────────────────────────────────────────────────────┘
```

Don't want to build anything? Skip to [§5](#5-desktop--build-a-standalone-yoinkexe) for a
double-clickable `Yoink.exe` with no Python required.

---

## 3. Requirements

**All devices must be on the same WiFi network.** This is not a soft requirement — it is
the entire design. Yoink has no server anywhere, so devices find each other by shouting on
the local network (mDNS) and then connecting directly. Different networks = they cannot
see each other, and no amount of configuration will change that.

Things that count as "the same network":

| Setup | Works? | Notes |
|---|---|---|
| Both on your home WiFi | ✅ | the normal case |
| Both on a phone hotspot (laptop joins the phone) | ✅ | the most reliable setup — see the firewall note below |
| Both on college / café WiFi | ⚠️ | often works, but many public networks enable **AP isolation**, which blocks device-to-device traffic entirely |
| Laptop on Ethernet, phone on the same router's WiFi | ✅ | same subnet, so fine |
| One on WiFi, one on mobile data | ❌ | different networks |
| One on a VPN | ❌ usually | a VPN can capture the route and hide the LAN |

**Desktop:** Windows 10/11, Python 3.10+ (tested on 3.12), and a webcam.
The desktop side is Windows-only today — the grab logic uses Windows APIs (`pywin32`,
UI Automation) to find out what you're pointing at.

**Phone:** Android 8.0 (API 26) or newer, with a front camera. Developed and verified on
Android 14.

**To build the Android app:** JDK 17 and the Android SDK (Android Studio installs both).

---

## 4. Desktop — run it from a terminal

```powershell
git clone <your-repo-url> yoink
cd yoink
pip install -r requirements.txt

cd daemon
python main.py
```

That's it. A window opens showing your hand as a skeleton on black (the camera image itself
is never displayed — only the landmarks), the number of linked devices, and a small control
panel.

> **Why is the camera image not shown?** Deliberate. You need to see *that tracking works*,
> not a video of yourself. Drawing only the skeleton makes it obvious at a glance whether
> your hand is being read, and it means there is never a live picture of your room on screen.

### The window

| Control | What it does |
|---|---|
| **Pose chip** (top) | `Fist` / `Open hand` / `Show your hand` — live feedback that tracking works |
| **`N linked` pill** | how many paired devices are connected right now |
| **Camera** dropdown | pick which webcam to use; remembered for next launch |
| **Send to** | tick which devices a SEND goes to. New devices are ticked automatically |
| **Send clipboard** | send whatever's on your clipboard right now, no gesture needed |
| **Auto-send when I copy** | every Ctrl+C is thrown to the ticked devices automatically |
| **PIN card** | appears only while a new device is pairing |

### Command-line flags

```powershell
python main.py [--name NAME] [--port PORT] [--peer HOST:PORT] [--camera N]
               [--no-camera] [--send-demo TYPE] [--demo-file PATH]
```

| Flag | Default | What it's for |
|---|---|---|
| `--name` | your computer's hostname | the name other devices show for you. Also picks which settings/pairing folder is used |
| `--port` | `8765` | the port other devices connect to |
| `--peer HOST:PORT` | *(off)* | skip auto-discovery and connect to one fixed address. Useful when mDNS is blocked |
| `--camera N` | remembered, else 0 | which webcam index to use |
| `--no-camera` | off | **headless receive mode** — no camera, no gestures. Anything received opens immediately. Good for a desktop that should only catch |
| `--send-demo TYPE` | *(off)* | send one fake payload (`text`/`url`/`image`/`file`) and exit. For testing the receive side |
| `--demo-file PATH` | a generated sample | the file `--send-demo image/file` should send |

### Where your stuff is kept

Everything Yoink stores lives in one folder per device name:

```
C:\Users\<you>\.yoink\<name>\
    device_id       your stable 12-char ID (pairing is keyed on this)
    paired.json     the devices you've trusted — delete to re-pair everything
    settings.json   your camera choice
    received\       every file you've caught
```

Delete the folder and Yoink is factory-fresh. Nothing is written anywhere else, and nothing
is ever written to a database.

---

## 5. Desktop — build a standalone `Yoink.exe`

For someone who shouldn't have to touch Python at all.

```powershell
pip install pyinstaller
cd daemon
pyinstaller Yoink.spec
```

Output: **`daemon/dist/Yoink/`** — a self-contained folder. `Yoink.exe` inside it is the app.
Zip that folder and send it; the recipient unzips and double-clicks. Nothing to install.

It's around 300 MB, almost all of it MediaPipe and OpenCV. That's the price of running hand
tracking locally instead of shipping your camera to someone's server.

**First launch on another machine:**
- Windows SmartScreen will warn about an unsigned exe → **More info → Run anyway**.
  (The build isn't code-signed. Sign it with a real certificate if you're distributing widely.)
- Allow camera access.
- Allow it through the Windows Firewall so devices can find each other — **tick the network
  profile your WiFi actually uses.** A phone hotspot usually shows up as **Public**, not Private,
  and that catches people out.
- Want an icon? Drop a `Yoink.ico` next to `Yoink.spec` and rebuild.

> Built as a folder (`onedir`), not a single file, on purpose: a one-file exe re-extracts
> hundreds of MB to a temp directory on *every* launch, and MediaPipe is unhappy about it.

---

## 6. Browser extension (optional but worth it)

Without the extension, grabbing from a browser gets you the page URL by reading the address
bar. **With** it, a fist in the browser can also grab:

- the **text you've selected** on the page,
- the **image you're hovering over**,
- a **YouTube/video link with the timestamp baked in** — you get `?t=142s`, so the other
  device opens at the exact moment you were watching.

None of that is visible from outside the browser, which is the entire reason the extension
exists.

**Install (Chrome / Edge / Brave / Opera / Vivaldi):**

1. Go to `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and select the `extension/` folder from this repo.

That's all. The extension connects to the running daemon on `ws://localhost:8777` — a
loopback-only socket that never leaves your machine. If the daemon isn't running, the
extension quietly does nothing; if the extension isn't installed, the daemon quietly falls
back to reading the address bar. Neither one breaks the other.

Only the browser window you're actually *looking at* answers. Four browsers can be connected
at once and an unfocused one will not volunteer its tab — otherwise a background video would
get sent instead of the PDF on your screen.

---

## 7. Android — build the APK and install it

There's no Play Store listing; you build it yourself. It takes two commands.

### One-time setup

You need **JDK 17** and the **Android SDK**. Easiest route is to install
[Android Studio](https://developer.android.com/studio), which brings both.

Then point Gradle at the SDK by creating **`android/local.properties`**:

```properties
sdk.dir=C:/Users/<you>/AppData/Local/Android/Sdk
```

> ⚠️ **Use forward slashes.** `local.properties` is a Java properties file, so backslashes
> are escape characters and get silently mangled. `C:\Users\...` will fail with a confusing
> error; `C:/Users/...` works.

### Build

```powershell
cd android

# build an installable APK
.\gradlew.bat assembleDebug
# → android/app/build/outputs/apk/debug/app-debug.apk

# or build AND install straight to a plugged-in phone (USB debugging on)
.\gradlew.bat installDebug
```

The first build downloads Gradle and the Android toolchain and can take several minutes.
Later builds take seconds.

To share the APK with someone else, send them `app-debug.apk`. They'll need to allow
"Install unknown apps" for whatever app they open it with (Files, Chrome, etc.).

> Release build: `.\gradlew.bat assembleRelease` produces an **unsigned** APK — this project
> has no signing config set up, so for anything beyond your own devices you'll need to
> [create a keystore and sign it](https://developer.android.com/studio/publish/app-signing).

### First run: the three permissions, and why each one matters

Open Yoink. It will ask for some of these; the others are buttons in the app.

| Permission | Asked how | What breaks without it |
|---|---|---|
| **Camera** | popup on first launch | everything — no gestures at all |
| **Notifications** | popup on first launch | nothing functional. It only hides the "Yoink is watching" notification; the service still runs |
| **Display over other apps** | tap the button in the app → toggle Yoink on | **catching while you're in another app.** Android blocks a background app from opening anything; this permission is the documented exemption. Without it, an open-hand on your homescreen does nothing |
| **Screen capture** | tap **Enable screen capture** → Allow | the screenshot fallback for sending. Decline it and SEND is clipboard-only |

**"Display over other apps" is the one people skip, and it's the one that makes Yoink feel
magic.** It's what lets a caught PDF open while you're on the homescreen, and it's also what
draws the "caught" pop.

### Living with it

- The ongoing notification (**"Yoink is watching — Fist = send, open hand = catch"**) is
  Android's requirement for a background camera, not decoration. Its **Stop** action is the
  off switch.
- **Swipe Yoink out of recents** and the camera and networking shut down completely. Closing
  the app means closing it.
- The **Camera on/off** button in the app does the same thing without leaving.
- The camera + hand tracking running continuously *is* expensive on battery. That's the cost
  of the feature working while you're in another app, not an oversight.
- On OnePlus/Oppo/Xiaomi phones, aggressive battery managers kill background services. If
  gestures stop firing when you leave the app, set
  **Settings → Battery → App battery usage → Yoink → Unrestricted**.

---

## 8. Pairing: the PIN, and what it protects

### Why there's a PIN at all

Discovery finds *everyone* running Yoink on the network. On your home WiFi that's just you.
On college or café WiFi, it could be a stranger. Pairing is the gate that decides which of
those discovered devices is actually yours.

**An unpaired device is not on the mesh.** It cannot send you anything and it cannot receive
anything from you. If it tries to push a payload mid-handshake, the payload is dropped and
logged:

```
[net] REJECTED payload from unpaired peer 'someone-else'
```

### Step 1 — the PIN, once per pair of devices

Pairing does two things: it proves a human is standing at both devices, and it establishes a
**pairing key** — 32 random bytes that every later reconnection is authenticated with. The
6-digit PIN is displayed on one device and typed into the other, and is never used again
after this exchange.

```
        Device A  (dials)                        Device B  (accepts)
              │                                          │
              │ ───────── hello (my ID, my name) ──────► │
              │ ◄──────── hello (my ID, my name) ─────── │
              │                                          │
              │ ─── "do I already know you?"  new ──────►│
              │                                          │
              │                            generates PIN 482193
              │                              ┌───────────────────────┐
              │                              │  shows it on screen   │
              │                              │       482193          │
              │                              └───────────────────────┘
              │ ◄──────────── pair: request ──────────── │
              │                                          │
    ┌─────────────────────┐                              │
    │ asks YOU for a PIN  │   👀 you read 482193 off B   │
    │ you type: 482193    │      and type it into A      │
    └─────────────────────┘                              │
              │ ───────── pair: confirm "482193" ──────► │
              │                                  compares
              │ ◄────── pair: ok + a 32-byte KEY ─────── │
              │                                          │
     both store {peer ID, name, key} — permanently, and never ask again
```

### Step 2 — every reconnect after that: proving you hold the key

This runs silently every time your devices meet, and it's the part that actually keeps
strangers out.

A device ID is **not a secret** — it's announced in the clear in every `hello`, so anyone who
watched one handshake knows it. *Claiming* to be a paired device therefore proves nothing.
Instead each side challenges the other to prove it holds the pairing key, over a fresh random
nonce, using HMAC-SHA256:

```
        Device A                                  Device B
              │ ──── pair: known + nonce_A ────────────► │
              │                                          │
              │ ◄─── challenge: nonce_B + proof over ─── │   B proves first
              │        nonce_A, using the pairing key    │
              │                                          │
     A checks B's proof. Wrong? drop the connection      │
              │                                          │
              │ ──── proof over nonce_B ───────────────► │   then A proves
              │                                          │
              │                        B checks. Wrong? deny + count a failure
              │ ◄─────────────── ok ──────────────────── │
```

Three properties worth knowing:

- **It's mutual on purpose.** If only the dialer had to prove itself, someone could
  impersonate *your laptop* to *your phone* and quietly receive everything you throw.
- **The nonce is fresh every time**, so a recording of yesterday's handshake is worthless,
  and the proof binds *who is proving to whom*, so an attacker can't reflect your own
  challenge back at you.
- **There is no fallback.** A peer that tries to skip the challenge is refused, not waved
  through. An optional check is one an attacker simply declines to perform.

### In practice

**Desktop pairing with desktop:** one window shows a big PIN card (*"Pairing with X — type
this PIN on it"*), the other pops a dialog asking for it. Which is which is decided
automatically and deterministically — you don't choose.

**Desktop pairing with phone:** the phone always dials, so **the desktop shows the PIN and
the phone asks you for it.** Look at the laptop, type into the phone.

**It happens once.** Both sides write the other's device ID, name and pairing key into
`paired.json` (desktop) or private app storage (phone). Every reconnection after that —
reboot, WiFi drop, app restart — authenticates silently with the key. You will never see the
PIN again.

**Getting it wrong** is safe and non-destructive: the connection is refused, and the dialer
waits **30 seconds** before retrying. That delay is on purpose — without it, a failed pairing
would re-prompt you for a PIN once a second forever. On top of that, the device *showing* the
PIN locks a peer out for **60 seconds after 5 failed attempts**, so an attacker writing their
own client can't grind through six digits at speed.

> **Upgrading from a version before pairing keys existed?** Your old `paired.json` records a
> name but no key, so those devices will ask for a PIN one more time. That's deliberate — the
> alternative would be trusting them on an assertion, which is exactly the hole the key
> closes. Both devices need the new version; a new desktop and an old phone will not connect.

**The phone only pairs while the app is open.** Pairing needs a human to type a number, and
there's no way to ask one when the app is in the background — so the phone declines rather
than pairing silently behind your back. If a pairing prompt never appears, open the Yoink app
and wait a moment.

### To un-pair / start over

- **Desktop:** delete `C:\Users\<you>\.yoink\<name>\paired.json`
- **Phone:** Settings → Apps → Yoink → Storage → Clear data

Both sides must forget each other, or the one that still remembers will say "we're already
paired" and the handshake won't produce a fresh PIN.

---

## 9. Using it — what gets sent, from where, and every fallback

### The gesture

Yoink fires on the **transition** between hand poses, never on a held pose. Holding a fist
does nothing. Holding your palm open does nothing. Only the *motion* counts:

- **open → fist** = **SEND**
- **fist → open** = **RECEIVE**

That single decision is what kills false triggers. Your hand resting in frame, gesturing
while you talk, or typing will not fire anything.

Two more guards on top:
- **Debounce** — a new pose must hold for **4 consecutive frames** (~130 ms) before it counts,
  so one badly-tracked frame can't misfire.
- **Cooldown** — after firing, events are ignored for **0.8 s**, so one deliberate motion
  produces exactly one event, not a burst of five.

And if your hand leaves the frame for ~8 frames, Yoink forgets the baseline pose entirely —
so dropping your hand and raising an open palm doesn't read as "fist → open" and fire a
phantom catch.

### What a fist grabs on Windows — the full ladder

When you make a fist, the desktop works out what you were pointing at by asking the
foreground window, in this order. Every step is logged in the window so you can see exactly
which one fired.

```
FIST
 │
 ├─ Is Yoink's own window focused?
 │     → step aside and use the last real app you were in
 │       (you have to look at the preview to gesture, so this is the normal case)
 │
 ├─ File Explorer?
 │     → the selected file's REAL path → sends the actual bytes          ✅ perfect case
 │       (multiple files selected → sends the first)
 │
 ├─ A browser?
 │     ├─ extension says you've selected text   → sends that text
 │     ├─ extension says you're hovering an image → downloads & sends it
 │     ├─ a PDF open in the browser              → sends the real PDF file
 │     ├─ a file:///... page                     → sends that real file
 │     └─ otherwise                              → the page URL (+ video timestamp)
 │        · chrome:// edge:// about: pages are skipped — useless on another device
 │
 ├─ An app with a document open (Acrobat, Word, PowerPoint, Excel…)?
 │     → asks the OS which file that process has open → sends the real file
 │       (documents only — .pdf .docx .xlsx .pptx .csv .epub … not its DLLs)
 │
 ├─ Anything else?
 │     → presses Ctrl+C for you, then reads the clipboard:
 │         · real file references (copied in Explorer)  → sends the files
 │         · image data (copied from Photos, Paint…)     → sends a PNG
 │         · a URL string                                → sends it as a link
 │         · plain text                                  → sends the text
 │       ⚠️ never done in a terminal window — a console reads Ctrl+C as "interrupt",
 │          which would kill whatever is running there (including Yoink itself)
 │
 └─ Still nothing?
       → SCREENSHOT of the focused window                                ✅ never fails
```

**The honest tradeoff.** For getting the *original file* out of an *arbitrary* app,
*automatically*, you can have any two of those three — not all three. That's a Windows
limitation, not a missing feature. Explorer and the browser expose real paths, so those cases
get all three. For everything else, Yoink presses Ctrl+C for you (so it still feels automatic)
and takes real clipboard data, or falls back to a screenshot. For "get this photo onto my
other device", a screenshot is usually exactly what you wanted anyway.

**Two ways to send without a gesture** (desktop): the **Send clipboard** button, and the
**Auto-send when I copy** checkbox, which throws every Ctrl+C to your ticked devices
automatically. Both skip the foreground poking entirely and just read what's already on your
clipboard.

### What a fist grabs on Android

Much shorter, because Android allows much less:

```
FIST
 │
 ├─ Clipboard has text?      → sends it as a link (if http/https) or as text
 │     ⚠️ only readable while Yoink itself is on screen — Android blocks
 │        background apps from reading the clipboard entirely
 │
 ├─ Screen capture enabled?  → screenshot of the screen → sends a PNG
 │
 └─ Neither?                 → "nothing to grab"
```

That's the whole ladder. **No app on Android can ask Chrome for its URL or a PDF reader for
its file path** — there is no equivalent to the Windows tricks above. So a gesture from the
phone is clipboard-or-screenshot, always.

### Sending a real file from the phone: Share → Yoink

This is the way. In any app — Files, Drive, Gallery, WhatsApp, a PDF reader:

**Share → Yoink.**

Yoink reads the actual bytes, filename and MIME type the source app handed over, and throws
them to your desktops. A shared PDF arrives on the laptop as a real PDF and opens in your
PDF viewer. Multi-select works — share five photos and five arrive.

It's a tap, not a gesture, and that's the point: it's the complement to the gesture, not a
replacement. The gesture is fast and hands-free; the share sheet is the one that can send
anything.

> Sharing briefly turns the camera on — the networking lives inside the same service that
> owns the camera. Known, documented, cosmetic.

### Size limit

**25 MB per item.** Anything bigger is refused with a message in the log rather than silently
failing. This is a clipboard, not a file-sync tool: the whole payload is base64-encoded and
held in memory on both ends, so the ceiling stays modest on purpose.

---

## 10. Catching — what happens when something arrives

When a payload lands, the receiving device **holds it and opens nothing.** It shows a "caught"
pop and waits. Nothing launches an app behind your back — that waits for you to open your
hand, or tap the pop.

(Small platform difference: the **phone decodes and writes the file to disk the moment it
arrives**, so the pop can show a real thumbnail, and only the *opening* is deferred. The
**desktop defers both** — the payload sits in memory until you gesture, and is written out
then. Headless desktops, `--no-camera`, have no gesture to wait for, so they save and open on
arrival.)

Then, by type:

| Type | What happens |
|---|---|
| **url** | opens in your default browser |
| **text** | copied to your clipboard, with a pop showing a preview |
| **image** | saved to disk, pop shows a thumbnail; tap/click to open full size |
| **file** | saved to disk, then **opened by whatever app your OS associates with that extension** |

That last row is why one small dispatcher handles a PDF, a spreadsheet and a photo without
knowing anything about any of them. Yoink never hardcodes "open PDFs in Acrobat". It hands the
saved file to the operating system and lets the file association answer — `os.startfile` on
Windows, an `Intent` with a MIME type on Android. Install a different PDF reader and Yoink
starts using it, with no code change.

The reason this works is that **the sender already decided the type.** By the time a payload
crosses the network, "this is a file called `notes.pdf` of type `application/pdf`" is settled.
The receiver never has to guess. This is why the receiver is simple and the sender is where
all the complexity lives.

**Where caught things land:**

| Device | Path |
|---|---|
| Windows | `C:\Users\<you>\.yoink\<name>\received\` |
| Android | `Android/data/com.yoink/files/received/` (visible in your Files app) |

Same-name files never overwrite each other: `report.pdf` becomes `report (1).pdf`.

### Filename safety

Filenames arrive from another machine, so they're treated as untrusted input. A payload named
`../../../Windows/System32/evil.dll` cannot escape the received folder — only the last path
component is kept, characters the filesystem rejects are scrubbed, `.` and `..` are replaced
with a safe default, and the length is capped. The Windows and Android implementations are
deliberate line-by-line ports of each other so a name that survives one survives the other.

---

## 11. How it works under the hood

### The shape of it

```
   ┌───────────────────┐                          ┌───────────────────┐
   │  Windows laptop   │◄──── ws:// LAN ─────────►│   Windows PC #2   │
   │                   │                          │                   │
   │  camera+gestures  │                          │  camera+gestures  │
   │  mesh networking  │                          │  mesh networking  │
   │  grab / dispatch  │                          │  grab / dispatch  │
   └─────────┬─────────┘                          └─────────┬─────────┘
             │      ▲                                       │
             │      │ ws://localhost:8777                    │
             │  ┌───┴────────────┐                          │
             │  │ browser ext.   │                          │
             │  └────────────────┘                          │
             │                                              │
             └────────────┐              ┌──────────────────┘
                          ▼              ▼
                   ┌────────────────────────────┐
                   │      Android phone         │
                   │  camera in a foreground    │
                   │  service · dials out only  │
                   └────────────────────────────┘
```

Three pieces, glued by a shared JSON protocol rather than shared code — which is exactly why
Python on the desktop and Kotlin on the phone coexist without either knowing about the other:

1. **The desktop daemon (Python)** — owns the camera, classifies gestures, runs the
   networking, performs the grab, shows the catch pop.
2. **The browser extension (JavaScript)** — answers "what's in the browser right now", over a
   loopback-only socket.
3. **The Android client (Kotlin)** — a native reimplementation of the same client role. The
   camera lives in a **foreground service**, not the Activity, which is the only way Android
   allows a camera to keep running when you leave the app.

### Mesh, not a hub

**Every device is an equal peer connected directly to every other peer.** There is no
central node, no coordinator, no leader election, and therefore no single point of failure.
Turn any device off and the rest carry on unaffected.

At this scale the arithmetic is trivial — 2 devices is 1 connection, 3 devices is 3. A
coordinator would be a scale-up path for many more devices, and deliberately isn't built.

Each connection is completely independent: its own dial loop, its own heartbeat, its own
reconnect. One peer freezing or dropping WiFi has zero effect on the others.

### Event-based, not stateful — and why there's no database

This is the question people ask most, so directly:

**There is no database because there is nothing to store.** A gesture is an *event*, pushed
to peers the moment it happens. There is no shared state that multiple devices need to agree
on, no "current clipboard" that anyone owns, no history, no sync log, no conflict resolution.

Each receiving device simply keeps **its own copy of the last thing it received**, in memory —
plus the actual file on disk if one arrived. That's it. That single design choice is what
makes a mesh work without a coordinator: if there's no shared truth, nobody has to be in
charge of it.

Compare with what a stateful design would have forced: a store of record, an authority
deciding whose clipboard wins, sync/merge logic, and — because a hub is the obvious way to
host that — a single point of failure. Redis, a message broker, a sync server were all
considered and rejected. None of them buys anything here.

**What happens when several things are sent at once?**

Nothing dramatic — and worth understanding:

- **Two devices send to you at the same time.** Both payloads arrive independently over
  separate sockets and neither connection blocks the other. But only **one** is held as "the
  thing an open hand will open" — **the last one to arrive wins.** What happens to the earlier
  one depends on the device: **on the phone**, every arriving payload is written to disk
  immediately, so the earlier file is still sitting in your `received` folder even though the
  gesture opens the newer one. **On the desktop**, a payload isn't written until you gesture,
  so an earlier one that gets superseded before you open your hand is simply dropped. If you
  expect a burst, catch each one as it lands — or run a headless desktop (`--no-camera`),
  which saves and opens every arrival immediately.
- **You send to three devices at once.** One grab, one envelope, broadcast to every ticked
  peer. Each device gets its own independent copy and does what it likes with it — one might
  open it, another might not be watching yet. There's no acknowledgement and no coordination
  between receivers, and none is needed.
- **A device is offline when you send.** It simply doesn't get it. There's no queue and no
  retry. Yoink is a throw, not a mailbox — if you want the thing to wait for you, it belongs
  in a file sync tool, not here.
- **Order.** Each connection is a single TCP-backed WebSocket, so payloads from one sender
  arrive in the order they were sent. Across different senders there's no global ordering,
  because there's no global anything.

If you're in headless mode (`--no-camera`), there's no gesture to wait for, so every arrival
opens immediately — meaning two payloads land as two opened things.

### Finding each other

Each device advertises itself over **mDNS** (the same mechanism your printer and Chromecast
use) as a `_yoink._tcp.local.` service, carrying its stable device ID, and browses for others
of the same type. When one appears, its address is handed to the mesh, which dials it.

The desktop advertises its **LAN IP**, not localhost, so real devices can reach it, and
listens on `0.0.0.0` for the same reason. Pairing is the trust gate, which is what makes
listening on the LAN safe by design.

**Both sides see each other, so who dials?** A deterministic rule with no negotiation:
**the lower device ID dials, the higher waits.** Without it you'd get two sockets per pair.
A second defensive check drops any redundant connection that still slips through.

**The phone is client-only.** It never listens and never advertises — it dials out to
discovered desktops, and the one full-duplex socket carries traffic in both directions. Since
the desktop mesh already broadcasts to every live connection, an inbound phone connection
receives payloads with no special handling. It also means the dedupe rule doesn't apply to the
phone at all: the desktop can't discover it, so it can never dial it, so there's no second
socket. The consequence is that **phone → phone doesn't work** — neither could ever dial the
other.

### Staying alive

Every connection sends a heartbeat every **3 seconds**. Any inbound message counts as a sign
of life. **10 seconds** of silence (~3 missed beats) and the link is declared dead and closed,
then re-dialled.

A peer whose process is killed closes the socket cleanly and is noticed instantly. The
heartbeat is there for the *ungraceful* case — a frozen machine, or WiFi vanishing — where the
socket is left half-open and would otherwise look fine forever.

The phone's reconnect is deliberately more stubborn than the desktop's: it keeps retrying the
last known address even after mDNS says the service is gone, because on a phone "service lost"
usually means WiFi blipped, not that your laptop left. That's what makes "drop WiFi, bring it
back" rejoin on its own.

### The wire protocol

One JSON object per WebSocket message, UTF-8. Binary content is base64 in `data`.

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

| Field | Meaning |
|---|---|
| `kind` | `hello` · `pair` · `heartbeat` · `payload` |
| `type` | on a payload: `url` · `text` · `image` · `file` |
| `filename`, `mime` | set for `image`/`file`, null otherwise |
| `data` | raw string for `url`/`text`; base64 for `image`/`file` |
| `sender` | the device name you see on screen |
| `ts` | unix seconds |

Deliberately boring and human-readable. You can watch the whole conversation in a terminal,
and adding a device to the mesh means implementing this table — nothing more.

### Gesture recognition

Every camera frame goes through MediaPipe Hands to get **21 hand landmarks**. A classifier
labels the single frame `OPEN` (all fingers extended), `CLOSED` (all fingertips curled toward
the palm) or neither, based purely on finger-curl geometry — no training, no model of your
particular hand, and nothing sent anywhere. Ambiguous mid-motion frames are deliberately
labelled neither, because a real fist→open passes through them.

Those per-frame labels go into a state machine that tracks the last *stable* pose and fires on
the edges, with the debounce and cooldown described in [§9](#9-using-it--what-gets-sent-from-where-and-every-fallback).

The Windows and Android implementations of both the classifier and the state machine are
deliberate line-by-line ports of each other, with the same test suite on both sides, so a
gesture feels identical on your laptop and your phone.

### Ports

| Port | Bound to | What it is |
|---|---|---|
| `8765` | `0.0.0.0` | the mesh. Configurable with `--port` |
| `8777` | `localhost` only | the browser extension bridge. Never reachable from the network |

---

## 12. Security model — honestly

### What Yoink does protect

- **Nothing goes to the internet.** No cloud, no account, no telemetry, no analytics. Payloads
  travel directly between your devices over your local network and nowhere else. There is no
  server that could be breached, subpoenaed, or shut down, because there is no server.
- **Nothing is stored beyond what you can see.** No database, no history, no sync log. Received
  files sit in one visible folder; the "last received" payload lives in memory and dies with
  the process.
- **Pairing gates every connection.** An unpaired device cannot send you anything or receive
  anything from you, even on the same WiFi. Payloads sent before the handshake completes are
  explicitly rejected and logged.
- **Every reconnection is cryptographically authenticated.** Devices prove they hold the
  32-byte key agreed at pairing, by HMAC-SHA256 over a fresh nonce, in both directions. A
  device ID alone — which travels in the clear and is therefore public — buys an attacker
  nothing. Replaying a recorded handshake fails (fresh nonce), reflecting your own challenge
  back fails (the proof binds prover and verifier), and skipping the challenge is refused
  rather than tolerated, so there's no downgrade path.
- **A bad guess costs 30 seconds, and five of them cost a minute.** The dialer backs off after
  a failure, and the device showing the PIN locks a peer out for 60 s after 5 failures — so a
  6-digit PIN can't be ground down by a custom client.
- **Filenames from the network can't escape their folder.** Path traversal is stripped;
  see [§10](#10-catching--what-happens-when-something-arrives).
- **Only `http` and `https` links are ever handed to the OS.** A received `chrome://`,
  `file://` or exotic-scheme URL is copied to your clipboard instead of launched — a string
  that arrived over the network never gets to invoke an arbitrary protocol handler.
- **Files are opened by association, never by launching a named executable.** The received
  bytes are saved to disk and given to the OS; Yoink never executes anything itself.
- **A 25 MB cap** on any single payload, and a hard message-size limit on the socket, so a
  peer can't push unbounded data into your memory.
- **The browser bridge is loopback-only.** Port 8777 is never exposed to the network.
- **Nothing opens itself.** An arriving payload is saved but not opened until you gesture or
  tap. Catching is always a deliberate act.

### What Yoink does *not* protect against — read this

Being straight with you, because "no cloud" is not the same as "encrypted":

- **Traffic is not encrypted.** Peers talk plain `ws://`, not `wss://`. Anyone who can capture
  packets on your WiFi — an attacker on an open network, or a network administrator — can read
  the contents of what you send. Pairing controls *who can connect*, not *who can listen*.
  **Don't use Yoink to move secrets across a network you don't trust.** On your own home WiFi
  or your own phone hotspot, this is a non-issue; on café or campus WiFi, assume anything you
  throw is readable.
- **A paired device is fully trusted.** There's no per-payload confirmation and no allow-list
  of types. Any peer you've paired with can push you a file that opens on your machine. Only
  pair with devices you own.
- **An attacker sniffing at the exact moment you pair learns the pairing key.** The key is
  handed over on the pairing connection, which isn't encrypted yet — and the PIN crosses the
  wire in the same exchange. This is trust-on-first-use, the same bargain Bluetooth pairing
  makes. It means the attack window is *the few seconds you were pairing*, not "any handshake,
  forever" — but if you paired while someone was capturing your network, they can impersonate
  that device. Pair at home, not on café WiFi. Closing this needs a key exchange a sniffer
  can't follow (ECDH), which is planned and not yet built.
- **The PIN handshake isn't protected against an active man-in-the-middle** who can intercept
  and relay the connection in real time.
- **Android cleartext is enabled app-wide.** Android's network security config can scope
  exceptions by domain but not by IP range, and LAN peers are bare IPs from mDNS — so there's
  no rule to write. The app makes no other network requests, so practical exposure is nil, but
  it's stated rather than hidden.
- **The screenshot fallback captures whatever is on screen.** If you gesture while something
  private is visible, that's what gets sent. The gesture is intentional, but the *contents*
  are whatever was there.
- **The desktop synthesizes Ctrl+C** into the focused app when it has no better option. It
  refuses to do this in terminals (where Ctrl+C means "interrupt"), but it does mean a fist can
  cause a copy in an app you didn't expect.
- **The built exe is unsigned**, so SmartScreen warns. That warning is legitimate — verify
  what you're running.

**Summary:** Yoink is built for devices you own on a network you control. Within that, it's
sound and stays entirely local. Outside it — public WiFi, shared networks, sensitive
material — the lack of transport encryption is the real limitation, and worth knowing before
you rely on it.

---

## 13. Limits you should know about

Not bugs — consequences of the design or of the operating systems. Documented so you don't
waste time chasing them.

| Limit | Why |
|---|---|
| Desktop is **Windows-only** | the grab logic is built on Windows APIs. The receive side is portable; the send side isn't |
| **Phone → phone doesn't work** | the phone is client-only: neither can discover or dial the other |
| **A phone gesture can't grab a real file** | Android permits no cross-app content access, at all. Use Share → Yoink |
| **A backgrounded phone can't read the clipboard** | Android 10+ restricts clipboard reads to the focused app. A background gesture is screenshot-only |
| **Screen capture consent is per app-session** | Android asks once per run; you re-enable it after the service restarts |
| **25 MB per item** | payloads are base64'd and held in memory on both ends |
| **No offline queue** | send to an offline device and it's gone. It's a throw, not a mailbox |
| **Only the last payload is "catchable"** | one slot per device. On the phone earlier ones are still saved to disk; on the desktop they're dropped |
| **Some public WiFi blocks it entirely** | AP isolation prevents any device-to-device traffic; nothing app-side can fix that |
| **Two devices can't share a camera** | if another app holds the webcam, pick a different index or close it |

---

## 14. Troubleshooting

**The devices never find each other.**
1. Confirm both are genuinely on the same network (not one on mobile data, not one on a VPN).
2. Windows Firewall — the daemon needs inbound access on **the profile your WiFi actually
   uses**. A phone hotspot usually registers as **Public**. Allow `python.exe` (or `Yoink.exe`)
   on that profile.
3. Public WiFi may have AP isolation. Try tethering off your phone instead — it's the most
   reliable setup and needs no network cooperation.
4. As a last resort, skip discovery entirely: `python main.py --peer 192.168.1.42:8765`.

**The PIN dialog never appears on the phone.** The phone only pairs while the app is open —
pairing needs a human. Open Yoink and wait a few seconds.

**Pairing keeps failing.** Both sides must forget each other: delete `paired.json` on the
desktop *and* clear the phone's app data. If only one forgets, the other insists you're
already paired and no fresh PIN is generated. Also note the deliberate 30-second wait after a
failed attempt.

**Gestures don't fire.** Watch the pose chip — if it never says `Fist` or `Open hand`, it's
tracking, not logic. Improve the lighting, get your whole hand in frame, and make the poses
distinct (fingers fully spread for open, fully curled for fist). Remember a *held* pose does
nothing; you must transition.

**Gestures stop when I leave the app (Android).** Battery optimisation.
**Settings → Battery → App battery usage → Yoink → Unrestricted.** Also confirm the "Yoink is
watching" notification is still there — if it's gone, the service was killed.

**Open hand on the homescreen does nothing (Android).** "Display over other apps" isn't
granted. Open Yoink, tap the overlay-permission button, toggle Yoink on. Android silently
swallows background activity launches without it, which is exactly what this looks like.

**The wrong thing gets sent.** Read the status line — it names the strategy that fired
(`grab: strategy=explorer`, `strategy=screenshot`, …). That tells you which rung of the ladder
in [§9](#9-using-it--what-gets-sent-from-where-and-every-fallback) matched, and usually why.

**It sends a picture of the Yoink window.** The window was focused and the last app couldn't
be refocused. Click your target app first, then gesture — or use the **Send clipboard** button.

**Nothing happens on a big file.** Anything over 25 MB is refused; the log says so explicitly.

**"can't listen on port 8765".** Another Yoink already has it. Stop it, or use `--port 8766`.

**Camera won't open.** Another app is holding it. Close it, or pick a different index from the
Camera dropdown.

---

## 15. Testing it on one machine (loopback)

You don't need two computers to exercise the whole stack. Run two instances side by side, with
different names and ports — this covers the full protocol, mesh, pairing, dispatch and pops.
Only the "does it feel like magic across the room" check needs real devices.

```powershell
# Terminal 1 — the sender, with a camera and gestures
cd daemon
python main.py --name A --port 8765

# Terminal 2 — the catcher, headless (opens everything immediately)
cd daemon
python main.py --name B --port 8766 --no-camera
```

They discover each other, one shows a PIN, you type it into the other, and from then on a fist
at instance A sends to instance B. Each instance keeps its own identity, pairing list and
`received/` folder under `~/.yoink/A` and `~/.yoink/B`, so they never tread on each other.

**Test the receive side without gesturing** — fire one fake payload of each type:

```powershell
python main.py --name C --port 8767 --send-demo file
python main.py --name C --port 8767 --send-demo url
python main.py --name C --port 8767 --send-demo image
python main.py --name C --port 8767 --send-demo text
```

**Unit checks** — no camera, no network, no framework:

```powershell
cd daemon
python -m gesture.test_gesture     # classifier + debounce/cooldown/edges
python -m net.test_mesh            # broadcast fan-out, PIN rejection, peer loss
python -m net.test_heartbeat       # heartbeat + dead-link detection
python -m receive.test_dispatch    # typed dispatch, nothing pops up
python -m grab.test_grab           # envelope building, size guard, byte fidelity
python -m grab.test_bridge         # extension bridge protocol
python net/protocol.py             # wire-format self-check
```

```powershell
cd android
.\gradlew.bat test                 # JVM tests — no emulator needed
```

The Android gesture tests mirror the Python ones case for case, so both implementations are
held to the same behaviour.

---

## 16. Repo layout

```
yoink/
  DESIGN.md                  the design document — every decision and why
  README.md                  this file
  requirements.txt

  daemon/                    the Windows desktop app (Python)
    main.py                  entry point
    config.py                flags, paths, device identity, size limits
    Yoink.spec               PyInstaller build → a standalone exe
    BUILD.md                 packaging notes
    gui/app.py               the desktop window
    gesture/
      camera.py              capture + MediaPipe hand landmarks
      classifier.py          landmarks → OPEN / CLOSED
      state_machine.py       transitions, debounce, cooldown → SEND / RECEIVE
    net/
      protocol.py            the JSON envelope
      discovery.py           mDNS advertise + browse
      mesh.py                peer set, broadcast, dial/reconnect
      pairing.py             the PIN handshake
      peer.py                one connection + its heartbeat
    grab/
      router.py              foreground routing — the whole send ladder
      explorer.py            real file paths out of File Explorer
      clipboard_win.py       text / file refs / image data
      docpath.py             which document an app has open
      screenshot.py          focused-window capture (the universal fallback)
      browser_bridge.py      localhost socket the extension talks to
      webfetch.py            fetch a remote image / PDF the browser is showing
    receive/
      dispatch.py            type → save + open by association
      toast.py               the always-on-top catch pop

  extension/                 browser extension (Manifest V3)
    manifest.json  background.js  content.js

  android/                   the Android client (Kotlin, native)
    app/src/main/
      assets/hand_landmarker.task        bundled MediaPipe model
      kotlin/com/yoink/
        MainActivity.kt                  thin viewer + permission buttons
        GestureService.kt                foreground service: camera + mesh
        ShareActivity.kt                 Share → Yoink
        ScreenCaptureService.kt          MediaProjection screenshots
        OverlayView.kt                   skeleton + pose + flash
        gesture/                         Classifier · StateMachine · HandTracker
        net/                             Protocol · Discovery · Mesh · Pairing · Peer
        grab/                            Grab (clipboard/screenshot) · Share
        receive/                         Dispatch · Paths · Pop
    app/src/test/                        JVM unit tests
```

The Kotlin `gesture/` files are deliberate line-by-line ports of the Python ones — matched
names, identical behaviour, the same test cases on both sides. Change one, change the other.

---

## Design decisions

These are settled, and `DESIGN.md` records the reasoning behind each in full:

- **LAN only, no cloud** — lower latency, nothing to host, nothing leaves the network.
- **Mesh, not a hub** — every device is an equal peer, so there's no single point of failure.
- **Event-based, not stateful** — a gesture is an event, not a write. This is why the mesh
  needs no coordinator and why there's no database.
- **Gestures are transitions, not poses** — fire on the motion, never on a held hand. This is
  what kills false triggers.
- **The sender is hard, the receiver is simple** — all the messy "what am I pointing at?"
  work happens before anything is sent, so the receiver only ever saves-and-opens by type.

If you're reading the code, read `DESIGN.md` first. It explains what was tried, what the
operating systems refused to allow, and why each piece looks the way it does.
