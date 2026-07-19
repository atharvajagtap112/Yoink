"""Checks for the parts of the grab that don't need a mouse.

The OS-facing bits (which window is focused, what Ctrl+C produces) can only be
tested by hand — see the manual steps in the milestone notes. What IS testable
is everything that turns found content into an envelope: the url-vs-text call,
the size guard, and that the bytes survive the trip. Those are the parts that
would silently send the wrong thing.

Run: python -m grab.test_grab
"""
import base64
import contextlib
import io
import shutil
import tempfile
from pathlib import Path

from PIL import Image

import config
from . import docpath, router, webfetch


def _log(_msg):
    pass


@contextlib.contextmanager
def _fake_urlopen(body, content_type):
    """Stand in for the network: hand back these bytes and this Content-Type."""
    class _Resp(io.BytesIO):
        headers = {"Content-Type": content_type}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    real = webfetch.urllib.request.urlopen
    webfetch.urllib.request.urlopen = lambda *a, **k: _Resp(body)
    try:
        yield
    finally:
        webfetch.urllib.request.urlopen = real


def test_url_vs_text():
    url = router._text_env("https://youtube.com/watch?v=abc&t=142s", "A", _log)
    assert url["type"] == "url", url["type"]
    assert url["data"].startswith("https://")

    txt = router._text_env("just some notes", "A", _log)
    assert txt["type"] == "text", txt["type"]

    # a URL with surrounding prose is prose, not a link to open
    mixed = router._text_env("see https://example.com for more", "A", _log)
    assert mixed["type"] == "text", mixed["type"]

    # whitespace around a bare URL shouldn't hide it
    padded = router._text_env("  https://example.com \n", "A", _log)
    assert padded["type"] == "url" and padded["data"] == "https://example.com"


def test_file_envelope_roundtrip(tmp):
    p = tmp / "notes.pdf"
    p.write_bytes(b"%PDF-1.4 hello")
    env = router._file_env(p, "A", _log)
    assert env["type"] == "file"
    assert env["filename"] == "notes.pdf"
    assert env["mime"] == "application/pdf"
    assert base64.b64decode(env["data"]) == b"%PDF-1.4 hello"


def test_size_guard_refuses_big_files(tmp):
    big = tmp / "huge.bin"
    big.write_bytes(b"x" * (config.MAX_GRAB_BYTES + 1))
    assert router._file_env(big, "A", _log) is None, "size guard let a huge file through"


def test_folder_is_not_a_file(tmp):
    d = tmp / "a folder"
    d.mkdir()
    assert router._file_env(d, "A", _log) is None


def test_image_envelope_is_png(tmp):
    env = router._image_env(Image.new("RGB", (40, 30), "red"), "A", "shot.png", _log)
    assert env["type"] == "image" and env["mime"] == "image/png"
    raw = base64.b64decode(env["data"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not actually a PNG"


def test_envelope_matches_the_protocol(tmp):
    """Every strategy must emit what 5a's dispatcher already understands."""
    p = tmp / "x.txt"
    p.write_bytes(b"hi")
    for env in (router._text_env("hello", "A", _log),
                router._file_env(p, "A", _log),
                router._image_env(Image.new("RGB", (4, 4)), "A", "s.png", _log)):
        assert set(env) == {"v", "kind", "type", "filename", "mime", "data",
                            "sender", "ts"}, set(env)
        assert env["kind"] == "payload" and env["sender"] == "A"


def _put_image_on_clipboard(img):
    import io as _io

    import win32clipboard
    import win32con
    buf = _io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]                 # a DIB is a BMP minus its file header
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def test_clipboard_restores_what_you_had(tmp):
    """The promise: a grab must not cost you what you'd already copied."""
    import pyperclip

    from . import clipboard_win as C
    original = "the user's precious clipboard"
    pyperclip.copy(original)
    saved, had_other = C._snapshot()
    pyperclip.copy("whatever the synthetic Ctrl+C produced")
    C._restore(saved, had_other, _log)
    assert pyperclip.paste() == original, f"clobbered: {pyperclip.paste()!r}"


def test_stale_clipboard_is_never_sent(tmp):
    """The bug this guards: gesture at a PDF/YouTube page with nothing selected,
    Ctrl+C copies nothing, and the clipboard still holds text you copied an hour
    ago. Reading it blind ships that instead of what you're looking at."""
    import pyperclip

    from . import clipboard_win as C
    pyperclip.copy("text I copied an hour ago")
    real = C._send_ctrl_c
    C._send_ctrl_c = lambda: None            # an app that ignores Ctrl+C
    try:
        assert C.grab(log=_log) is None, "stale clipboard got sent as a fresh grab"
    finally:
        C._send_ctrl_c = real
    assert pyperclip.paste() == "text I copied an hour ago", "and it clobbered it too"


def test_clipboard_reads_text_then_image(tmp):
    import pyperclip

    from . import clipboard_win as C
    pyperclip.copy("https://example.com")
    kind, content = C._read(_log)
    assert (kind, content) == ("text", "https://example.com"), (kind, content)
    # and that text routes to a url envelope, not a text one
    assert router._text_env(content, "A", _log)["type"] == "url"

    _put_image_on_clipboard(Image.new("RGB", (20, 10), "blue"))
    kind, content = C._read(_log)
    assert kind == "image", f"image on the clipboard read as {kind}"
    assert content.size == (20, 10), content.size


def test_file_url_to_real_path(tmp):
    p = tmp / "a doc.pdf"
    p.write_bytes(b"%PDF-1.4 x")
    # what a browser shows for a local PDF: percent-encoded, slash before drive
    url = "file:///" + str(p).replace("\\", "/").replace(" ", "%20")
    got = router._file_url_to_path(url)
    assert got == p, f"{got} != {p}"
    assert got.is_file(), "couldn't read back the file the browser was showing"


def test_pdf_url_detection(tmp):
    assert webfetch.looks_like_pdf_url("https://x.com/a/report.pdf")
    assert webfetch.looks_like_pdf_url("https://x.com/view?id=9", "report.pdf")
    # a normal page must NOT trigger a download
    assert not webfetch.looks_like_pdf_url("https://youtube.com/watch?v=abc",
                                           "(14419) YouTube")


def test_login_page_is_never_sent_as_a_pdf(tmp):
    """The trap this guards: a PDF behind a login returns 200 with an HTML
    sign-in page. Saved as .pdf that looks like success and is a broken file."""
    with _fake_urlopen(b"<!doctype html><html>Please sign in</html>", "text/html"):
        assert webfetch.fetch_pdf("https://x.com/secret.pdf", _log) is None

    with _fake_urlopen(b"%PDF-1.4 real bytes", "application/pdf"):
        assert webfetch.fetch_pdf("https://x.com/real.pdf", _log) == b"%PDF-1.4 real bytes"

    # headers can lie; the magic number is the ground truth
    with _fake_urlopen(b"%PDF-1.4 mislabelled", "text/html"):
        assert webfetch.fetch_pdf("https://x.com/x.pdf", _log) is not None


def test_edge_local_pdf_is_a_file_not_a_website(tmp):
    """Edge shows a local PDF as a bare drive path in the address bar, not a
    file:// url. Normalising it to https:// would send a dead link, so it must
    become a file:// url the router can turn back into a real path."""
    got = docpath._normalise("D:/RESUME2%20-%20Copy/Resume_Atharva_Jagtap.pdf")
    assert got == "file:///D:/RESUME2%20-%20Copy/Resume_Atharva_Jagtap.pdf", got
    assert docpath._normalise(r"C:\docs\a b.pdf") == "file:///C:/docs/a b.pdf"
    # a real hostname still becomes https, not a file
    assert docpath._normalise("example.com/x").startswith("https://")


def test_youtube_page_offers_its_link_not_a_screenshot(tmp):
    """Gesturing at a video used to send a screenshot of it, even though we'd
    already read the URL. _from_browser hands the link back so grab() can send
    it -- but only AFTER the clipboard, so a selection still wins."""
    real_url, real_doc = docpath.browser_url, docpath.open_document
    docpath.browser_url = lambda h: "https://www.youtube.com/watch?v=hL-cD-qy06E"
    docpath.open_document = lambda h, e, t="": None
    try:
        env, page_url = router._from_browser(0, "(14463) Shark Tank", "A", _log)
        assert env is None, "a plain web page is not a file to send"
        assert page_url == "https://www.youtube.com/watch?v=hL-cD-qy06E", page_url
    finally:
        docpath.browser_url, docpath.open_document = real_url, real_doc

    # and a local pdf still returns the file, offering no link to fall back to
    p = tmp / "local.pdf"
    p.write_bytes(b"%PDF-1.4 x")
    url = "file:///" + str(p).replace("\\", "/")
    docpath.browser_url = lambda h: url
    try:
        env, page_url = router._from_browser(0, "local.pdf", "A", _log)
        assert env["type"] == "file" and page_url is None
    finally:
        docpath.browser_url = real_url


def test_never_ctrl_c_into_a_terminal(tmp):
    """The bug this guards: a console with nothing selected reads Ctrl+C as
    INTERRUPT, not copy. Gesturing at the terminal running the daemon sent
    SIGINT to its own process and killed it mid-grab."""
    assert not router._may_send_ctrl_c("windowsterminal.exe", True)
    assert not router._may_send_ctrl_c("cmd.exe", True)
    assert not router._may_send_ctrl_c("PowerShell.exe", True), "case slipped through"
    # ordinary apps are still fair game
    assert router._may_send_ctrl_c("photos.exe", True)
    # and never when another window holds the keyboard
    assert not router._may_send_ctrl_c("photos.exe", False)


def test_internal_browser_pages_are_never_sent(tmp):
    """chrome://startpageshared/ reached the other device and Windows popped
    'Get an app to open this chrome link'. Those pages stay home."""
    for bad in ("chrome://startpageshared/", "edge://settings", "about:blank",
                "devtools://devtools/x", "opera://startpage"):
        assert bad.lower().startswith(router.INTERNAL_SCHEMES), bad
    for good in ("https://youtube.com/watch?v=a", "http://x.dev",
                 "file:///D:/a.pdf"):
        assert not good.lower().startswith(router.INTERNAL_SCHEMES), good


def test_download_filename(tmp):
    assert webfetch.filename_for("https://x.com/docs/my%20report.pdf") == "my report.pdf"
    assert webfetch.filename_for("https://x.com/view?id=9").endswith(".pdf")


if __name__ == "__main__":
    import pyperclip
    tmp = Path(tempfile.mkdtemp())
    user_clipboard = pyperclip.paste()        # these tests use the real clipboard
    try:
        test_url_vs_text()
        for fn in (test_file_envelope_roundtrip, test_size_guard_refuses_big_files,
                   test_folder_is_not_a_file, test_image_envelope_is_png,
                   test_envelope_matches_the_protocol,
                   test_clipboard_restores_what_you_had,
                   test_stale_clipboard_is_never_sent,
                   test_clipboard_reads_text_then_image,
                   test_file_url_to_real_path, test_pdf_url_detection,
                   test_login_page_is_never_sent_as_a_pdf,
                   test_edge_local_pdf_is_a_file_not_a_website,
                   test_youtube_page_offers_its_link_not_a_screenshot,
                   test_never_ctrl_c_into_a_terminal,
                   test_internal_browser_pages_are_never_sent,
                   test_download_filename):
            fn(tmp)
            print(f"  ok: {fn.__name__}")
        print("grab self-checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        pyperclip.copy(user_clipboard)        # put your clipboard back
