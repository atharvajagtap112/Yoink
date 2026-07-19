"""Focused-window capture: the universal fallback so a SEND is never empty.

The window is the foreground one, so what's on screen in its rectangle IS the
window — no need for PrintWindow gymnastics to capture occluded pixels.
"""
import win32gui
from PIL import ImageGrab


def capture(hwnd):
    """PIL Image of the window's pixels, or None if it has no sane rectangle."""
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None
    if right - left < 2 or bottom - top < 2:
        return None
    # all_screens so a window on a second monitor isn't captured as black.
    return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
