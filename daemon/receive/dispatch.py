"""Type -> save + open by association (CLAUDE.md section 3, receive path).

    url   -> open in the default browser
    image -> save to disk -> preview pop (no auto-open; you asked for a look)
    file  -> save to disk -> open with the OS default app
    text  -> write to the clipboard -> pop

The whole point: the sender already decided the type, so this side never asks
"what app opens a .docx". It hands the path to the OS and lets the file
association answer. That's why one dispatcher covers a PDF, a spreadsheet and a
photo without knowing anything about any of them.
"""
import base64
import os
import subprocess
import sys
import webbrowser

import pyperclip

import config
from . import toast


def open_path(path):
    """Open a saved file with whatever the OS says owns that extension."""
    if hasattr(os, "startfile"):                     # Windows
        os.startfile(str(path))
    else:                                            # macOS / Linux
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)])


def dispatch(env, save_dir, log=print):
    """Act on one received payload. Returns the saved Path, or None."""
    kind = env.get("type")
    sender = env.get("sender", "?")
    data = env.get("data")
    if data is None:
        log(f"dispatch: payload from {sender} has no data — ignored")
        return None

    if kind == "text":
        return _text(data, sender, log)
    if kind == "url":
        return _url(data, sender, log)
    if kind in ("image", "file"):
        return _blob(env, kind, data, sender, save_dir, log)
    log(f"dispatch: unknown payload type {kind!r} from {sender} — ignored")
    return None


def _text(data, sender, log):
    try:
        pyperclip.copy(data)
    except Exception as e:
        log(f"clipboard write failed: {e}")
    toast.show(f"caught: {data[:40]}")
    log(f"CAUGHT text <- {sender}: {data[:60]!r}")
    return None


def _url(data, sender, log):
    # Only ever hand http(s) to the OS. A browser-internal link (chrome://...)
    # pops "Get an app to open this link", and an exotic scheme would launch
    # whatever protocol handler happens to be registered for it — not something
    # to do with a string that arrived over the network. Keep it as text.
    if not data.lower().startswith(("http://", "https://")):
        log(f"CAUGHT url <- {sender}: {data[:60]!r} isn't openable, copied instead")
        return _text(data, sender, log)
    webbrowser.open(data)
    toast.show(f"caught: {data[:40]}", on_click=lambda: webbrowser.open(data))
    log(f"CAUGHT url <- {sender}: {data}")
    return None


def _blob(env, kind, data, sender, save_dir, log):
    try:
        raw = base64.b64decode(data)
    except Exception as e:
        log(f"dispatch: bad base64 in {kind} from {sender}: {e}")
        return None

    name = config.safe_filename(env.get("filename"),
                                default=f"yoink-{kind}")
    path = config.unique_path(save_dir, name)
    path.write_bytes(raw)
    log(f"CAUGHT {kind} <- {sender}: saved {path} ({len(raw)} bytes)")

    if kind == "image":
        toast.show(f"caught: {path.name}", image_path=path,
                   on_click=lambda: open_path(path))
    else:
        toast.show(f"caught: {path.name}", on_click=lambda: open_path(path))
        open_path(path)                              # let the association decide
    return path
