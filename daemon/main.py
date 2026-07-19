"""Yoink daemon — milestone 5a: typed dispatch.

SEND gesture  -> read clipboard text -> broadcast to EVERY paired peer.
RECEIVE gesture (or arrival, when headless) -> dispatch by payload type
(receive/dispatch.py): text/url/image/file each get opened their own way.

Peers are found over mDNS (net/discovery.py), gated by a one-time PIN pairing
(net/pairing.py), and held as an equal-peer mesh (net/mesh.py) — no hub, no
coordinator. --peer stays as a manual override. Networking runs on a background
thread; the camera loop owns the main thread.
"""
import time

import config
import demo
from grab import browser_bridge, router
from net.mesh import Mesh
from net.pairing import PairedStore
from net.discovery import Discovery
from receive.dispatch import dispatch
from gesture import camera


def main():
    args = config.parse_args()
    device_id = config.device_id(args.name)
    store = PairedStore(config.paired_path(args.name))
    save_dir = config.save_dir(args.name)
    last = {"env": None}          # only the last received payload is kept

    def _dispatch(env):
        dispatch(env, save_dir)

    def on_arrival(env):
        # Payload landed over the network. With a camera, a RECEIVE gesture opens
        # it; headless has no gesture, so open it right away.
        last["env"] = env
        # for image/file, `data` is base64 — show the filename, not 60 chars of it
        what = env.get("filename") or (env.get("data") or "")[:60]
        print(f"received <- {env.get('sender')}: {env.get('type')} {what!r}")
        if args.no_camera:
            _dispatch(env)

    def log(msg):
        print(f"[net] {msg}")

    mesh = Mesh(args.name, device_id, args.port, store, on_arrival, log)
    mesh.start()
    print(f"[{args.name}] listening on {args.port} (id {device_id[:8]})")
    if store.names():
        print(f"[net] already paired with: {list(store.names().values())}")

    disc = None
    if args.peer:
        host, port = config.parse_peer(args.peer)
        mesh.add_manual(host, port)
        print(f"[net] manual peer {args.peer} (discovery off)")
    else:
        disc = Discovery(args.name, device_id, args.port,
                         mesh.peer_up, mesh.peer_down)
        disc.start()
        print("[net] discovery on, browsing for peers...")

    def on_event(event):
        if event == "SEND":
            env = router.grab(args.name, log=log)      # foreground routing (5b)
            if env is None:
                print("SEND: nothing to grab")
                return
            mesh.broadcast(env)
            what = env.get("filename") or (env.get("data") or "")[:60]
            print(f"SENT -> {env.get('type')} {what!r}")
        elif event == "RECEIVE":
            if last["env"] is None:
                print("RECEIVE: nothing received yet")
            else:
                _dispatch(last["env"])

    if args.send_demo:
        # Test aid (5a): the real grab doesn't exist yet, so fake one payload.
        env = demo.build(args.send_demo, args.name, args.demo_file)
        print(f"[demo] waiting for a paired peer to send {args.send_demo} to...")
        for _ in range(200):
            if mesh.peers:
                break
            time.sleep(0.1)
        if not mesh.peers:
            print("[demo] no peers connected — is the receiver running and paired?")
        else:
            mesh.broadcast(env)
            time.sleep(1.5)                 # let the send flush before we exit
            print(f"[demo] sent {args.send_demo}")
        if disc:
            disc.close()
        return

    if args.no_camera:
        print("headless receive mode — Ctrl+C to quit")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if disc:
                disc.close()
        return

    # Remember which app you were in before you clicked the camera preview.
    router.start_tracker()
    # Optional upgrade: if the extension is installed it connects here and the
    # browser grab gets selection / hovered image / video timestamp. If it never
    # connects, nothing breaks — the grab falls back to the address bar.
    browser_bridge.start(log)
    print(f"[net] browser bridge on {config.BRIDGE_PORT} "
          f"(install extension/ for selection + video timestamps)")
    idx = args.camera if args.camera is not None else camera.pick_camera()
    print(f"Using camera {idx}. Close hand = SEND, open hand = RECEIVE. q to quit.")
    camera.run(on_event=on_event, camera_index=idx)


if __name__ == "__main__":
    main()
