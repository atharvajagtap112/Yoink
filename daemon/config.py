"""CLI flags, state paths, and device identity (DESIGN.md sections 7 and 9).

State lives in ~/.yoink/<name>/ — keyed by device name so that several loopback
instances on ONE laptop each get their own identity and paired-peer list, which
is exactly what the single-machine test in section 9 needs.
"""
import argparse
import re
import socket
import uuid
from pathlib import Path

# Illegal on Windows, plus control chars. Anything here becomes '_'.
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

# Biggest thing a grab will send. Base64 over a WebSocket costs ~4/3 the bytes
# and the whole envelope is buffered in memory on both ends, so this stays
# modest — it's a clipboard, not a file-sync tool.
# The browser extension talks to us here (localhost only, milestone 6).
BRIDGE_PORT = 8777

MAX_GRAB_MB = 25
MAX_GRAB_BYTES = MAX_GRAB_MB * 1_000_000

# websockets caps incoming messages at 1 MiB by default, which would reject
# anything but a tiny file. Must comfortably exceed MAX_GRAB_BYTES * 4/3.
MAX_WS_MESSAGE = 48 * 1024 * 1024


def state_dir(name):
    d = Path.home() / ".yoink" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_dir(name):
    """Where caught files land. Per instance, so loopback A/B/C don't write over
    each other and you can see which one actually caught something."""
    d = state_dir(name) / "received"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_filename(name, default="yoink-payload"):
    """Make a filename from the wire safe to join onto save_dir.

    The name came off another machine, so it is untrusted: '../../evil.exe' or
    'C:\\windows\\system32\\x.dll' must not escape the save directory. We keep
    only the last path component and scrub what Windows won't accept.
    """
    if not name:
        return default
    name = str(name).replace("\\", "/").split("/")[-1]   # drop any path part
    name = _BAD_CHARS.sub("_", name).strip().rstrip(". ")
    if not name or set(name) <= {"."}:                   # '', '.', '..'
        return default
    return name[:120]


def unique_path(directory, filename):
    """A path that doesn't clobber an existing file: photo.png -> photo (1).png."""
    p = directory / filename
    if not p.exists():
        return p
    for i in range(1, 1000):
        p = directory / f"{Path(filename).stem} ({i}){Path(filename).suffix}"
        if not p.exists():
            return p
    raise OSError(f"too many files named like {filename}")


def device_id(name):
    """Stable id for this instance: made once, then reused every launch. Pairing
    is keyed on this, which is what makes pairing one-time."""
    f = state_dir(name) / "device_id"
    if f.exists():
        did = f.read_text().strip()
        if did:
            return did
    did = uuid.uuid4().hex[:12]
    f.write_text(did)
    return did


def paired_path(name):
    return state_dir(name) / "paired.json"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Yoink daemon")
    ap.add_argument("--port", type=int, default=8765,
                    help="local websocket port to listen on")
    ap.add_argument("--name", default=socket.gethostname(),
                    help="stable device name used as the sender")
    ap.add_argument("--peer", default=None,
                    help="peer address as host:port, e.g. 127.0.0.1:8766")
    ap.add_argument("--no-camera", action="store_true",
                    help="run headless: receive only, no gestures (for loopback B)")
    ap.add_argument("--camera", type=int, default=None,
                    help="camera index; omit to pick one interactively")
    ap.add_argument("--send-demo", choices=("text", "url", "image", "file"),
                    default=None,
                    help="test aid (5a): broadcast a fake payload of this type "
                         "and exit, instead of running the camera")
    ap.add_argument("--demo-file", default=None,
                    help="file to send with --send-demo image/file "
                         "(default: a generated sample)")
    return ap.parse_args(argv)


def parse_peer(s):
    """'host:port' -> (host, int(port)); None -> None."""
    if not s:
        return None
    host, port = s.rsplit(":", 1)
    return host, int(port)
