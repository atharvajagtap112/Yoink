"""Synthesize Ctrl+C to the focused app, then read whatever landed.

The universal middle ground: almost every app copies *something* on Ctrl+C, so
this covers the apps that expose no path (Photos, PDF viewers, editors). Per
DESIGN.md's "any two of three": we trade the original file for a keystroke, and
what we get back is real clipboard data — file paths when the app offers them,
otherwise the actual image/text bytes.

Clipboard courtesy: the synthetic Ctrl+C overwrites whatever the user had
copied, so we snapshot it first and put it back afterwards. Text and images are
restored; other formats (files, HTML, app-specific ones) are NOT restorable
cheaply and are lost — a deliberate limit, logged when it happens.
"""
import contextlib
import time

import win32api
import win32clipboard
import win32con
from PIL import Image, ImageGrab

# How long to wait for the app to act on Ctrl+C. A real copy returns as soon as
# the clipboard changes, so only a no-op copy ever waits this long — which means
# we can afford to be patient with slow apps.
COPY_WAIT_S = 0.6
_KEEP = (win32con.CF_UNICODETEXT, win32con.CF_DIB)


def grab(log=print):
    """Ctrl+C the focused app and read the result.

    Returns ('file', [paths]) | ('image', PIL.Image) | ('text', str) | None.

    Crucially, we only trust the clipboard if our Ctrl+C actually CHANGED it.
    Reading it blind can't tell "the app just copied this" from "this has been
    sitting here since an hour ago" — an app with nothing selected (a PDF you're
    only reading) copies nothing, and we'd ship your old clipboard instead. The
    OS clipboard sequence number is the ground truth for "did a copy happen".
    """
    saved, had_other = _snapshot()
    try:
        before = _sequence()
        _send_ctrl_c()
        if not _wait_for_copy(before):
            log("grab: Ctrl+C copied nothing (app had no selection) -> "
                "ignoring the stale clipboard")
            return None
        return _read(log)
    finally:
        _restore(saved, had_other, log)


def _sequence():
    try:
        return win32clipboard.GetClipboardSequenceNumber()
    except Exception:
        return None


def _wait_for_copy(before, timeout=COPY_WAIT_S):
    """True as soon as the clipboard actually changes; False if it never does."""
    if before is None:
        time.sleep(timeout)          # can't tell -> old behaviour, trust it
        return True
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.02)
        if _sequence() != before:
            return True
    return False


def _send_ctrl_c():
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord("C"), 0, 0, 0)
    win32api.keybd_event(ord("C"), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def _read(log):
    # Files first: the real original always beats a rendering of it.
    paths = _files()
    if paths:
        return ("file", paths)
    img = ImageGrab.grabclipboard()
    if isinstance(img, Image.Image):
        return ("image", img)
    text = _text()
    if text and text.strip():
        return ("text", text)
    return None


def _files():
    try:
        with _clipboard():
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                return list(win32clipboard.GetClipboardData(win32con.CF_HDROP))
    except Exception:
        pass
    return []


def _text():
    try:
        with _clipboard():
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except Exception:
        pass
    return None


def _snapshot():
    """Remember the user's clipboard. Returns (restorable, had_unrestorable)."""
    saved, had_other = {}, False
    try:
        with _clipboard():
            for fmt in _KEEP:
                if win32clipboard.IsClipboardFormatAvailable(fmt):
                    try:
                        saved[fmt] = win32clipboard.GetClipboardData(fmt)
                    except Exception:
                        pass
            had_other = (win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP)
                         and not saved)
    except Exception:
        pass
    return saved, had_other


def _restore(saved, had_other, log):
    try:
        with _clipboard():
            win32clipboard.EmptyClipboard()
            for fmt, data in saved.items():
                try:
                    win32clipboard.SetClipboardData(fmt, data)
                except Exception:
                    pass
    except Exception as e:
        log(f"grab: couldn't restore your clipboard: {e}")
        return
    if had_other:
        log("grab: your clipboard held copied FILES, which can't be restored — "
            "that copy is gone (text and images are restored fine)")


@contextlib.contextmanager
def _clipboard(retries=10):
    """The clipboard is a global lock; another app may hold it for a moment."""
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            time.sleep(0.05)
    else:
        raise RuntimeError("clipboard busy")
    try:
        yield
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
