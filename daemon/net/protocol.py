"""JSON wire envelope (CLAUDE.md section 6). Encode/decode only — no I/O.

Milestone 2 only sends kind="payload", type="text", but the envelope carries
every field from the spec so the other kinds/types slot in later untouched.
"""
import json
import time

VERSION = 1


def envelope(kind, *, type=None, data=None, sender="",
             filename=None, mime=None, ts=None):
    """Build a full envelope. Unused fields stay null, per the spec."""
    return {
        "v": VERSION,
        "kind": kind,          # hello | pair | heartbeat | payload
        "type": type,          # url | text | image | file  (payload only)
        "filename": filename,
        "mime": mime,
        "data": data,          # raw string for url/text; base64 for image/file
        "sender": sender,
        "ts": ts if ts is not None else int(time.time()),
    }


def text_payload(text, sender):
    return envelope("payload", type="text", data=text, sender=sender)


def encode(env):
    return json.dumps(env)


def decode(raw):
    """Parse a wire message. Returns the dict, or None if it's not a valid
    envelope (bad JSON / not an object / missing kind)."""
    try:
        env = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(env, dict) or "kind" not in env:
        return None
    return env


if __name__ == "__main__":
    # self-check
    e = text_payload("hello world", "laptop-a")
    assert decode(encode(e)) == e
    assert e["kind"] == "payload" and e["type"] == "text"
    assert decode("not json") is None
    assert decode("[1,2,3]") is None      # valid JSON, not an envelope
    assert decode('{"foo": 1}') is None   # object but no kind
    print("protocol self-check passed")
