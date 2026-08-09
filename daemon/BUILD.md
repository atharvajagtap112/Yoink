# Packaging the desktop app

Turns the Python daemon into a standalone Windows app anyone can run — no Python,
no pip, no terminal. Uses PyInstaller (a build-time tool only; it does not ship).

## Build

```
pip install pyinstaller
cd daemon
pyinstaller Yoink.spec
```

Output: `dist/Yoink/` — a self-contained folder. `dist/Yoink/Yoink.exe` is the app.

## Share it

Zip `dist/Yoink/` and send it. The other person unzips and double-clicks
`Yoink.exe` — nothing to install. (It's ~300 MB, mostly MediaPipe + OpenCV.)

First launch:
- Windows SmartScreen may warn on an unsigned exe → **More info → Run anyway**.
- Allow camera access; allow it through the firewall for LAN discovery.
- To catch a payload on the homescreen etc., the phone/other device pairs with a
  PIN shown in the window.

## Notes

- **onedir, not onefile** (see `Yoink.spec`): onefile re-extracts hundreds of MB
  to a temp dir on every launch and MediaPipe is finicky about it. onedir starts
  fast and unpacks its models once.
- Code-signing is not set up, so SmartScreen will always warn. Sign the exe with
  a real certificate if you distribute widely.
- Icon: drop a `Yoink.ico` next to `Yoink.spec` and rebuild; the spec picks it up.
