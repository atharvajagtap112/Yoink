"""Foreground routing: decide WHAT to grab (CLAUDE.md section 3, send path).

    Explorer focused  -> real selected file      (clean case: all three at once)
    browser focused   -> milestone 6 owns this; fall through to the clipboard
    anything else     -> synthetic Ctrl+C -> files / image data / text / url
    nothing usable    -> screenshot of the focused window (never comes up empty)

The strategies only FIND content; this module turns it into the envelope that
5a's dispatcher already understands. Every decision is logged, so the routing is
visible live rather than guessed at.
"""
import base64
import io
import mimetypes
import os
import re
import threading
import time
from pathlib import Path

import win32gui
import win32process

import config
from net import protocol
from . import (browser_bridge, clipboard_win, docpath, explorer, screenshot,
               webfetch)

EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass"}
BROWSER_PROCS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
                 "opera.exe", "vivaldi.exe"}
URL_RE = re.compile(r"^https?://\S+$", re.I)

# Browser-internal pages. Sending one is useless on the other device — Windows
# just pops "Get an app to open this 'chrome' link" — so they never leave here.
INTERNAL_SCHEMES = ("chrome://", "edge://", "opera://", "brave://", "vivaldi://",
                    "about:", "devtools://", "view-source:", "chrome-search://",
                    "chrome-extension://", "moz-extension://")

# Files worth pulling out of an app that has them open (Acrobat, Word, ...).
# Deliberately documents only: we don't want to ship an app's log or its DLLs.
DOC_EXTS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
            ".odt", ".ods", ".odp", ".rtf", ".epub", ".csv"}

# Never synthesize Ctrl+C into a terminal. With nothing selected, a console
# reads Ctrl+C as INTERRUPT rather than copy — so gesturing at the window this
# daemon is running in sends SIGINT to itself and kills it mid-grab. Even a
# different terminal would interrupt whatever is running there. Screenshot it.
TERMINAL_PROCS = {"windowsterminal.exe", "cmd.exe", "conhost.exe", "wt.exe",
                  "powershell.exe", "pwsh.exe", "openconsole.exe",
                  "alacritty.exe", "wezterm-gui.exe", "mintty.exe"}

_last_other_window = None
_tracker = None


def start_tracker():
    """Remember the last app that wasn't us.

    You have to look at the camera preview to gesture, and clicking it makes
    Yoink itself the foreground app — at which point a naive grab captures our
    own window instead of your PDF. So we keep a note of the last real app you
    were in and fall back to that.
    """
    global _tracker
    if _tracker:
        return
    def loop():
        global _last_other_window
        while True:
            try:
                h = win32gui.GetForegroundWindow()
                if h and win32gui.IsWindowVisible(h) and not _is_own_window(h):
                    _last_other_window = h
            except Exception:
                pass
            time.sleep(0.2)
    _tracker = threading.Thread(target=loop, daemon=True)
    _tracker.start()


def grab(sender, log=print):
    """Grab whatever the user is pointing at. Returns an envelope, or None."""
    hwnd = win32gui.GetForegroundWindow()
    focused = True
    if hwnd and _is_own_window(hwnd):
        hwnd, focused = _step_aside(log)
    if not hwnd:
        log("grab: no foreground window")
        return None

    cls = win32gui.GetClassName(hwnd) or ""
    proc = _process_name(hwnd)
    title = win32gui.GetWindowText(hwnd) or ""
    log(f"grab: foreground {proc or cls!r} — {title[:40]!r}")

    page_url = None            # the web page we're on, if any (see below)

    # Explorer needs no keyboard focus: the shell just tells us the path.
    if cls in EXPLORER_CLASSES:
        env = _from_explorer(hwnd, sender, log)
        if env:
            return env
    elif proc in BROWSER_PROCS:
        env, page_url = _from_browser(hwnd, title, sender, log)
        if env:
            return env
    else:
        # An app that has the document open (Acrobat, Word) will hand us the
        # real file even though its window never shows a path.
        env = _from_open_document(hwnd, title, sender, log)
        if env:
            return env

    if not focused:
        log("grab: skipping the clipboard — Ctrl+C would go to the wrong window")
    elif not _may_send_ctrl_c(proc, focused):
        log("grab: skipping Ctrl+C — a terminal reads it as interrupt, not copy")
    else:
        env = _from_clipboard(sender, log)
        if env:
            return env

    # Nothing selected, but we know which page you're on. The link beats a
    # screenshot of it — you can actually open a link on the other device.
    # (Selection / hovered image / YouTube timestamp are milestone 6's job.)
    if page_url:
        log(f"grab: strategy=browser url -> {page_url[:70]}")
        return protocol.envelope("payload", type="url", data=page_url,
                                 sender=sender)

    img = screenshot.capture(hwnd)
    if img is None:
        log("grab: strategy=none — nothing to grab")
        return None
    log("grab: strategy=screenshot (fallback)")
    return _image_env(img, sender, "screenshot.png", log)


def _step_aside(log):
    """Our own window has focus. Return (hwnd_to_grab, we_have_its_focus)."""
    target = _last_other_window
    if not target or not win32gui.IsWindow(target):
        log("grab: Yoink's own window is focused, and no earlier app was seen")
        return win32gui.GetForegroundWindow(), True
    name = (win32gui.GetWindowText(target) or "")[:40]
    log(f"grab: Yoink's own window is focused -> using your last app {name!r}")
    try:
        win32gui.SetForegroundWindow(target)
        time.sleep(0.25)
    except Exception as e:
        log(f"grab: couldn't refocus it ({e})")
    if win32gui.GetForegroundWindow() != target:
        log("grab: Windows blocked the refocus -> screenshot only, no Ctrl+C")
        return target, False
    return target, True


def _from_explorer(hwnd, sender, log):
    paths = explorer.selected_paths(hwnd)
    if not paths:
        log("grab: explorer has no usable selection -> trying the clipboard")
        return None
    if len(paths) > 1:
        log(f"grab: {len(paths)} items selected, sending the first only")
    log(f"grab: strategy=explorer -> {paths[0]}")
    return _file_env(paths[0], sender, log)


def _from_browser(hwnd, title, sender, log):
    """A PDF open in a browser -> the real file.

    Returns (envelope_or_None, page_url_or_None). The page URL is handed back
    rather than used here so grab() can try your SELECTION first — if you've
    highlighted something, that beats the bare link. The link is only the
    fallback that replaces a useless screenshot of the page.
    """
    link = None
    info = browser_bridge.ask()
    if info:
        # Selection and hovered image are things NO amount of window-poking can
        # see — this is the whole reason the extension exists.
        env = _from_extension(info, sender, log)
        if env:
            return env, None
        link = _page_link(info)      # canonical url, + &t= if you're mid-video

    page_url = None
    url = link or docpath.browser_url(hwnd)
    if url and url.lower().startswith(INTERNAL_SCHEMES):
        log(f"grab: {url[:40]!r} is a browser-internal page -> screenshot instead")
        return None, None
    if url:
        log(f"grab: {'extension' if link else 'address bar'} -> {url[:70]}")
        if url.lower().startswith("file:"):
            path = _file_url_to_path(url)
            if path and path.is_file():
                log(f"grab: strategy=browser file:// -> {path}")
                return _file_env(path, sender, log), None
            log("grab: that file:// url isn't a readable file -> open-file/clipboard")
        else:
            page_url = url                # a real web page: worth sending as a link
            if webfetch.looks_like_pdf_url(url, title):
                raw = webfetch.fetch_pdf(url, log)
                if raw:
                    name = webfetch.filename_for(url)
                    log(f"grab: strategy=browser download -> {name} ({len(raw)} bytes)")
                    return protocol.envelope("payload", type="file", filename=name,
                                             mime="application/pdf", data=_b64(raw),
                                             sender=sender), None
                # Verified NOT a real PDF (or unreachable): send the link.
                log("grab: strategy=browser url (download didn't yield a PDF)")
                return protocol.envelope("payload", type="url", data=url,
                                         sender=sender), None
    else:
        log("grab: couldn't read the address bar (Opera hides it) -> open PDF?")

    # Address bar empty/unreadable, or a file:// that didn't resolve: a LOCAL pdf
    # is still held open by the browser's process tree, so read it off there.
    path = docpath.open_document(hwnd, {".pdf"}, title)
    if path:
        log(f"grab: strategy=browser open-file -> {path}")
        return _file_env(path, sender, log), None

    log("grab: no PDF in the browser -> your selection, else the page link")
    return None, page_url


def _may_send_ctrl_c(proc, focused):
    """Ctrl+C is only safe when we hold the foreground AND it isn't a terminal.

    A console with no selection treats Ctrl+C as SIGINT, so gesturing at the
    window running this daemon would kill it mid-grab.
    """
    return focused and (proc or "").lower() not in TERMINAL_PROCS


def _from_extension(info, sender, log):
    """What you selected, or the image you're pointing at. None if neither."""
    sel = (info.get("selection") or "").strip()
    if sel:
        log(f"grab: strategy=extension selection ({len(sel)} chars)")
        return _text_env(sel, sender, log)

    src = info.get("image")
    if src:
        raw, mime = webfetch.fetch_bytes(src, log)
        if raw:
            name = _image_name(src, mime)
            log(f"grab: strategy=extension image -> {name} ({len(raw)} bytes)")
            return protocol.envelope("payload", type="image", filename=name,
                                     mime=mime, data=_b64(raw), sender=sender)
    return None


def _page_link(info):
    """The tab's URL, with your position in the video folded in — a link that
    reopens where you actually are is the point of grabbing a video."""
    url = (info.get("url") or "").strip()
    if not url:
        return None
    secs = info.get("videoTime")
    if isinstance(secs, int) and secs > 0 and "t=" not in url:
        url += ("&" if "?" in url else "?") + f"t={secs}s"
    return url


def _image_name(src, mime):
    from urllib.parse import unquote, urlparse
    last = unquote(urlparse(src).path).rstrip("/").split("/")[-1]
    name = re.sub(r"[^\w.\- ]", "_", last).strip()
    if name and "." in name:
        return name
    return (name or "image") + (mimetypes.guess_extension(mime or "") or ".png")


def _from_open_document(hwnd, title, sender, log):
    """Ask the OS which document this app has open (Acrobat, Word, ...)."""
    path = docpath.open_document(hwnd, DOC_EXTS, title)
    if not path:
        return None
    log(f"grab: strategy=open-file -> {path}")
    return _file_env(path, sender, log)


def _file_url_to_path(url):
    """file:///C:/x/y.pdf -> C:\\x\\y.pdf"""
    from urllib.parse import unquote, urlparse
    p = unquote(urlparse(url).path)
    if re.match(r"^/[A-Za-z]:", p):        # strip the slash before a drive letter
        p = p[1:]
    return Path(p)


def _from_clipboard(sender, log):
    got = clipboard_win.grab(log=log)
    if not got:
        return None
    kind, content = got
    if kind == "file":
        log(f"grab: strategy=clipboard CF_HDROP -> {content[0]}")
        return _file_env(content[0], sender, log)
    if kind == "image":
        log("grab: strategy=clipboard image data (not the original file)")
        return _image_env(content, sender, "clipboard.png", log)
    if kind == "text":
        return _text_env(content, sender, log)
    return None


def _text_env(text, sender, log):
    if URL_RE.match(text.strip()):
        log("grab: strategy=clipboard url")
        return protocol.envelope("payload", type="url", data=text.strip(),
                                 sender=sender)
    log("grab: strategy=clipboard text")
    return protocol.envelope("payload", type="text", data=text, sender=sender)


def _file_env(path, sender, log):
    p = Path(path)
    if not p.is_file():
        log(f"grab: {p.name!r} isn't a file (a folder?) -> skipping")
        return None
    size = p.stat().st_size
    if size > config.MAX_GRAB_BYTES:
        log(f"grab: {p.name} is {size / 1e6:.1f} MB, over the "
            f"{config.MAX_GRAB_BYTES / 1e6:.0f} MB limit -> not sending")
        return None
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return protocol.envelope("payload", type="file", filename=p.name, mime=mime,
                             data=_b64(p.read_bytes()), sender=sender)


def _image_env(img, sender, filename, log=print):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    raw = buf.getvalue()
    if len(raw) > config.MAX_GRAB_BYTES:
        log(f"grab: image is {len(raw) / 1e6:.1f} MB, over the limit -> not sending")
        return None
    return protocol.envelope("payload", type="image", filename=filename,
                             mime="image/png", data=_b64(raw), sender=sender)


def _b64(raw):
    return base64.b64encode(raw).decode()


def _process_name(hwnd):
    """Best effort: elevated apps refuse to say, which is fine — we use the class."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        import win32api
        import win32con
        h = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
        try:
            return Path(win32process.GetModuleFileNameEx(h, 0)).name.lower()
        finally:
            win32api.CloseHandle(h)
    except Exception:
        return ""


def _is_own_window(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid == os.getpid()
    except Exception:
        return False
