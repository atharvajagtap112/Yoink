"""Typed-dispatch checks. Nothing pops up: the toast, browser, clipboard and
"open with the OS" calls are all swapped for recorders.

Run: python -m receive.test_dispatch
"""
import base64
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import config
from . import dispatch as D


class _Spy:
    """Records calls instead of touching the screen / clipboard / OS.

    SimpleNamespace, not a class: a lambda set as a class attribute becomes a
    bound method and eats the first argument as self.
    """

    def __init__(self):
        self.toasts, self.opened, self.clipboard, self.urls = [], [], [], []

    def install(self):
        D.toast = SimpleNamespace(
            show=lambda *a, **k: self.toasts.append((a, k)))
        D.pyperclip = SimpleNamespace(copy=lambda s: self.clipboard.append(s))
        D.webbrowser = SimpleNamespace(open=lambda u: self.urls.append(u))
        D.open_path = lambda p: self.opened.append(Path(p))
        return self


def _payload(kind, data, filename=None):
    return {"v": 1, "kind": "payload", "type": kind, "filename": filename,
            "mime": None, "data": data, "sender": "TESTER", "ts": 0}


def _b64(b):
    return base64.b64encode(b).decode()


def test_text(tmp, spy):
    D.dispatch(_payload("text", "hello there"), tmp, log=lambda m: None)
    assert spy.clipboard == ["hello there"], spy.clipboard
    assert spy.toasts, "no toast for text"


def test_url(tmp, spy):
    D.dispatch(_payload("url", "https://example.com"), tmp, log=lambda m: None)
    assert spy.urls == ["https://example.com"], spy.urls


def test_image_saves_but_does_not_autoopen(tmp, spy):
    png = (Path(__file__).parent.parent / "samples" / "sample.png")
    if not png.exists():
        import demo
        png = demo.sample_png()
    p = D.dispatch(_payload("image", _b64(png.read_bytes()), "shot.png"), tmp,
                   log=lambda m: None)
    assert p and p.exists() and p.read_bytes() == png.read_bytes()
    assert p.name == "shot.png"
    assert not spy.opened, "an image should preview, not auto-open"


def test_file_saves_and_opens_by_association(tmp, spy):
    p = D.dispatch(_payload("file", _b64(b"%PDF-1.4 fake"), "doc.pdf"), tmp,
                   log=lambda m: None)
    assert p and p.read_bytes() == b"%PDF-1.4 fake"
    assert spy.opened == [p], f"file should open via the OS association: {spy.opened}"


def test_unknown_type_does_not_crash(tmp, spy):
    logs = []
    assert D.dispatch(_payload("hologram", "???"), tmp, log=logs.append) is None
    assert D.dispatch({"kind": "payload", "type": "text"}, tmp,
                      log=logs.append) is None          # missing data
    assert any("unknown" in m for m in logs), logs
    assert any("no data" in m for m in logs), logs


def test_no_overwrite(tmp, spy):
    a = D.dispatch(_payload("file", _b64(b"first"), "dup.txt"), tmp, log=lambda m: None)
    b = D.dispatch(_payload("file", _b64(b"second"), "dup.txt"), tmp, log=lambda m: None)
    assert a != b, "second file silently overwrote the first"
    assert a.read_bytes() == b"first" and b.read_bytes() == b"second"
    assert b.name == "dup (1).txt", b.name


def test_hostile_filenames_cannot_escape(tmp, spy):
    # These arrive from another machine, so they are untrusted input.
    for evil in ("../../evil.exe", r"..\..\evil.exe", "/etc/passwd",
                 r"C:\windows\system32\bad.dll", "..", ""):
        p = D.dispatch(_payload("file", _b64(b"x"), evil), tmp, log=lambda m: None)
        assert p.parent == tmp, f"{evil!r} escaped the save dir -> {p}"
    # sanity: the sanitiser keeps ordinary names intact
    assert config.safe_filename("holiday photo.png") == "holiday photo.png"


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp())
    spy = _Spy().install()
    try:
        for fn in (test_text, test_url, test_image_saves_but_does_not_autoopen,
                   test_file_saves_and_opens_by_association,
                   test_unknown_type_does_not_crash, test_no_overwrite,
                   test_hostile_filenames_cannot_escape):
            spy.__init__()
            fn(tmp, spy)
            print(f"  ok: {fn.__name__}")
        print("dispatch self-checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
