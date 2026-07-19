"""Fetch a document the browser is showing but that lives on a server.

The catch this exists to handle: a URL is not proof of a file. Ask for
`report.pdf` behind a login and the server cheerfully returns 200 with an HTML
sign-in page. Save that as .pdf and you've sent a broken file that LOOKS like it
worked. So we verify the bytes really are a PDF (magic number first, since
headers lie) and report failure honestly instead of shipping junk.

Note this is the one place Yoink talks to the internet. That doesn't break
DESIGN.md's "LAN only, no cloud" — that rule is about the transport between your
devices, which is still direct. This is just fetching the document you're already
looking at, from where you're already looking at it.
"""
import re
import urllib.request
from urllib.parse import unquote, urlparse

import config

TIMEOUT_S = 10
PDF_MAGIC = b"%PDF"


def looks_like_pdf_url(url, title=""):
    """Only worth a download if it's plausibly a PDF — we don't fetch every page."""
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or (title or "").lower().endswith(".pdf")


def fetch_pdf(url, log=print):
    """Real PDF bytes, or None if it isn't actually a PDF / didn't work."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Yoink"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            raw = r.read(config.MAX_GRAB_BYTES + 1)
    except Exception as e:
        log(f"grab: couldn't download it ({e})")
        return None

    if len(raw) > config.MAX_GRAB_BYTES:
        log(f"grab: PDF is over the {config.MAX_GRAB_MB} MB limit")
        return None
    if not raw.startswith(PDF_MAGIC) and "application/pdf" not in ctype:
        log("grab: that URL didn't return a PDF (a login page?) — sending the link")
        return None
    return raw


def fetch_bytes(url, log=print):
    """(raw, mime) for a URL — used for the image the extension pointed at.

    ponytail: http(s) only. A data: URI would need its own decode path; if
    hovering inline images turns out to matter, add it here.
    """
    if not url.lower().startswith(("http://", "https://")):
        return None, None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Yoink"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            mime = (r.headers.get("Content-Type") or "").split(";")[0].strip()
            raw = r.read(config.MAX_GRAB_BYTES + 1)
    except Exception as e:
        log(f"grab: couldn't download that image ({e})")
        return None, None
    if len(raw) > config.MAX_GRAB_BYTES:
        log(f"grab: that image is over the {config.MAX_GRAB_MB} MB limit")
        return None, None
    return raw, (mime or "application/octet-stream")


def filename_for(url, default="download.pdf"):
    """Last path segment of the URL, scrubbed. config.safe_filename hardens it
    again on the receiving side — this just makes it sensible."""
    last = unquote(urlparse(url).path).rstrip("/").split("/")[-1]
    name = re.sub(r"[^\w.\- ]", "_", last).strip()
    if not name:
        return default
    return name if name.lower().endswith(".pdf") else name + ".pdf"
