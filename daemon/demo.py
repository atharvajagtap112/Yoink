"""Fake payloads for testing typed dispatch before the real grab exists (5b).

The receive side can't be tested without a sender, and the smart grab isn't
built yet — so this fabricates one payload of each type. The samples generate
themselves (Pillow writes both PNG and PDF), so there's nothing to go find.

Delete this once milestone 5b can grab real content.
"""
import base64
import mimetypes
from pathlib import Path

from net import protocol

SAMPLES = Path(__file__).parent / "samples"
DEMO_TEXT = "hello from yoink — typed dispatch test"
DEMO_URL = "https://example.com"


def sample_png():
    SAMPLES.mkdir(exist_ok=True)
    p = SAMPLES / "sample.png"
    if not p.exists():
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (320, 200), "#2b6cb0")
        d = ImageDraw.Draw(im)
        d.ellipse((60, 30, 260, 150), fill="#f6ad55")
        d.text((105, 170), "yoink sample png", fill="white")
        im.save(p)
    return p


def sample_pdf():
    SAMPLES.mkdir(exist_ok=True)
    p = SAMPLES / "sample.pdf"
    if not p.exists():
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (612, 792), "white")
        d = ImageDraw.Draw(im)
        d.rectangle((40, 40, 572, 752), outline="#2b6cb0", width=4)
        d.text((80, 90), "Yoink sample PDF", fill="black")
        d.text((80, 120), "If this opened in your PDF viewer,", fill="black")
        d.text((80, 140), "typed dispatch works.", fill="black")
        im.save(p, "PDF", resolution=100)
    return p


def build(kind, sender, path=None):
    """Build one fake payload envelope of the given type."""
    if kind == "text":
        return protocol.text_payload(DEMO_TEXT, sender)
    if kind == "url":
        return protocol.envelope("payload", type="url", data=DEMO_URL,
                                 sender=sender)

    p = Path(path) if path else (sample_png() if kind == "image" else sample_pdf())
    if not p.exists():
        raise FileNotFoundError(p)
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return protocol.envelope(
        "payload", type=kind, filename=p.name, mime=mime,
        data=base64.b64encode(p.read_bytes()).decode(), sender=sender)
