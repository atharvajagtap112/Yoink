"""Best-effort: find the REAL document behind a window that hides its path.

CLAUDE.md's "any two of three" says an arbitrary app won't hand you the original
file. These are the two tricks that claw some of it back anyway:

  browser_url(hwnd)        read the address bar via UI Automation -> a URL,
                           which for file:/// IS a real path
  open_document(hwnd, ...) ask the OS which files the process has open (psutil)
                           -> catches Acrobat/SumatraPDF, which show a filename
                           in the title but never a path

Both are genuinely best-effort and WILL fail sometimes: the address bar's name
varies by browser/locale, browsers split work across processes, and open_files()
can hit AccessDenied. Every caller must fall back. That's why this module only
ever returns an answer or None — it never raises.
"""
import re
from pathlib import Path

import win32gui
import win32process


def browser_url(hwnd):
    """The URL in this browser window's address bar, or None.

    Opera has two edit controls: 'Address bar' (empty decoy) and 'Address field'
    (the real URL), and the real one sits ~15 levels deep — so we walk deep and
    take the first edit that actually holds a URL, checking address-named ones
    first for speed.
    """
    try:
        import uiautomation as auto  # noqa: F401 (import proves it's installed)
    except ImportError:
        return None
    try:
        win = auto.ControlFromHandle(hwnd)
        if not win:
            return None
        edits = []
        _collect_edits(win, edits, depth=0)
        edits.sort(key=lambda c: 0 if _is_named_address(c) else 1)
        for edit in edits:
            value = _value_of(edit)
            if value and _looks_like_url(value):
                return _normalise(value)
    except Exception:
        pass
    return None


def open_document(hwnd, exts, title=""):
    """A document the focused process currently has open, or None.

    When several are open we prefer the one named in the window title — that's
    the one the user is actually looking at.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
    except Exception:
        return None                      # AccessDenied, gone, etc. -> fall back

    # A browser's window belongs to the main process, but a local PDF is held
    # open by a renderer child — so scan the whole tree. Acrobat is one process,
    # so this still works there (children is just empty).
    # ponytail: open_files() per process is a syscall walk; Chromium has ~20
    # processes, so this is a beat slow. Fine for an occasional gesture; cap the
    # walk if it ever shows up in a trace.
    procs = [proc]
    try:
        procs += proc.children(recursive=True)
    except Exception:
        pass
    hits = []
    for p in procs:
        try:
            for f in p.open_files():
                if Path(f.path).suffix.lower() in exts:
                    hits.append(Path(f.path))
        except Exception:
            continue                     # this child denied us; try the rest
    if not hits:
        return None
    title = (title or "").lower()
    for p in hits:
        if p.name.lower() in title:      # the doc the title names wins
            return p
    return hits[0]


def _collect_edits(ctrl, out, depth, max_depth=20, cap=60):
    """Every EditControl in the tree. The real address bar can sit ~15 deep
    (Opera), so we go well past that; cap keeps a weird tree from running away."""
    if depth > max_depth or len(out) >= cap:
        return
    try:
        if ctrl.ControlTypeName == "EditControl":
            out.append(ctrl)
        for child in ctrl.GetChildren():
            _collect_edits(child, out, depth + 1, max_depth, cap)
    except Exception:
        pass


def _is_named_address(ctrl):
    name = (getattr(ctrl, "Name", "") or "").lower()
    return "address" in name or "location" in name or "url" in name


def _value_of(ctrl):
    try:
        return ctrl.GetValuePattern().Value
    except Exception:
        return None


def _looks_like_url(value):
    v = value.strip()
    if not v or " " in v:                # a search phrase, not a URL
        return False
    return v.lower().startswith(("http://", "https://", "file:")) or "." in v


def _normalise(value):
    """Address bars vary. Chrome/Edge hide the http scheme, and they show a
    LOCAL pdf as a bare drive path (D:/x.pdf) — that's a file, not a website."""
    v = value.strip()
    if v.lower().startswith(("http://", "https://", "file:")):
        return v
    if re.match(r"^[A-Za-z]:[\\/]", v):            # D:/x.pdf or D:\x.pdf
        return "file:///" + v.replace("\\", "/")
    return "https://" + v


def window_title(hwnd):
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""
