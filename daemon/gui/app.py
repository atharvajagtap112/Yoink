"""Yoink desktop app — a real window, no terminal needed.

Same palette and shape as the Android client: a skeleton-only view on black (the
camera runs but its image is never shown), a top identity bar with a live peer
count, and a bottom panel to pick the camera, tick which devices to send to, and
send the clipboard without a gesture.

Threading (three owners, marshalled through one queue):
  - main thread runs tkinter (the only thread that may touch widgets),
  - the mesh runs on its own asyncio thread (net/mesh.py),
  - the camera runs on a CameraWorker thread (gesture/camera.py).
Both background threads only ever *enqueue* onto `_ui_queue` (or set a plain
attribute the draw loop reads); the tk `after` loop drains it on the main thread.
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import simpledialog, ttk

import config
from gesture import camera
from grab import router
from receive.dispatch import dispatch

try:
    import win32clipboard
except ImportError:            # non-Windows dev box: auto-send just won't arm
    win32clipboard = None

# One intentional palette, shared with the Android client. Warm charcoal, one
# coral accent, true-black canvas so the skeleton reads. No blue glow.
INK = "#0B0B0D"
SURFACE = "#141416"
PANEL = "#1C1D21"
STROKE = "#2E3036"
TEXT = "#F4F2EF"
SUBTLE = "#9AA0A6"
ACCENT = "#FF6A3D"
ACCENT2 = "#E4552B"
GO = "#5FD08A"
WARN = "#F5B547"
BONE = "#EDEBE7"

FONT = "Segoe UI"

# MediaPipe hand skeleton (same indices as the classifier / Android overlay).
CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),            # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),            # index
    (5, 9), (9, 10), (10, 11), (11, 12),       # middle
    (9, 13), (13, 14), (14, 15), (15, 16),     # ring
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),  # pinky
]
TIPS = {4, 8, 12, 16, 20}


class YoinkApp:
    def __init__(self, root, mesh, name, save_dir, settings):
        self.root = root
        self.mesh = mesh
        self.name = name
        self.save_dir = save_dir
        self.settings = settings

        self._ui_queue = queue.Queue()
        self._cam_events = queue.Queue()
        self._latest = ([], "NO_HAND")     # (landmarks, pose); set from cam thread
        self._flash = None                 # (label, color, monotonic_t)
        self._last_env = None              # last received payload (RECEIVE opens it)

        self._selected = set()             # device_ids ticked to send to
        self._seen = set()                 # device_ids we've defaulted-on already
        self._device_vars = {}
        self._autosend = False
        self._clip_ignore_until = 0.0      # skip clipboard bumps we caused
        self._pin = None                   # (peer_name, pin) while pairing

        self._build_ui()

        # Route net status + pairing (both directions) into the window.
        mesh.on_status = self._log
        mesh.show_pin = self._show_pin_threadsafe
        mesh.prompt = self._gui_prompt

        # Start the camera immediately on the saved/default index so the skeleton
        # shows fast, and probe the *other* indices in the background. Probing
        # opens each camera in turn and is slow on Windows — doing it here on the
        # main thread was what froze the window ("Not Responding") at launch.
        start_idx = settings.get("camera")
        if not isinstance(start_idx, int):
            start_idx = 0
        self._cams = [start_idx]
        self._set_camera_dropdown(start_idx)

        self.worker = camera.CameraWorker(self._on_frame, index=start_idx)
        self.worker.start()

        threading.Thread(target=self._probe_cameras, args=(start_idx,),
                         daemon=True).start()
        threading.Thread(target=self._watch_clipboard, daemon=True).start()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick()
        self._refresh_devices()

    # ---------------------------------------------------------------- UI build

    def _build_ui(self):
        self.root.title("Yoink")
        self.root.geometry("440x720")
        self.root.minsize(400, 620)
        self.root.configure(bg=SURFACE)
        self._init_style()

        # Top identity bar
        top = tk.Frame(self.root, bg=SURFACE)
        top.pack(fill="x", padx=20, pady=(16, 10))
        left = tk.Frame(top, bg=SURFACE)
        left.pack(side="left")
        tk.Label(left, text="Yoink", bg=SURFACE, fg=TEXT,
                 font=(FONT, 20, "bold")).pack(anchor="w")
        self.status = tk.Label(left, text="Starting camera…", bg=SURFACE, fg=SUBTLE,
                               font=(FONT, 9))
        self.status.pack(anchor="w")
        self.peers = tk.Label(top, text="offline", bg=PANEL, fg=SUBTLE,
                              font=(FONT, 9, "bold"), padx=12, pady=5)
        self.peers.pack(side="right")

        # Pairing PIN card (hidden until a peer needs pairing)
        self.pin_card = tk.Frame(self.root, bg=PANEL, highlightbackground=ACCENT,
                                 highlightthickness=1)
        self.pin_title = tk.Label(self.pin_card, bg=PANEL, fg=SUBTLE,
                                  font=(FONT, 9))
        self.pin_title.pack(anchor="w", padx=14, pady=(10, 0))
        self.pin_value = tk.Label(self.pin_card, bg=PANEL, fg=ACCENT,
                                  font=("Consolas", 30, "bold"))
        self.pin_value.pack(anchor="w", padx=14, pady=(0, 10))

        # Hero: skeleton canvas on black
        self.canvas = tk.Canvas(self.root, bg=INK, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=0, pady=0)

        # Bottom control panel
        panel = tk.Frame(self.root, bg=PANEL)
        panel.pack(fill="x", side="bottom")
        inner = tk.Frame(panel, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=16)

        tk.Label(inner, text="Fist to grab · open hand to drop", bg=PANEL,
                 fg=SUBTLE, font=(FONT, 9)).pack(anchor="center", pady=(0, 12))

        cam_row = tk.Frame(inner, bg=PANEL)
        cam_row.pack(fill="x")
        tk.Label(cam_row, text="Camera", bg=PANEL, fg=TEXT,
                 font=(FONT, 10)).pack(side="left")
        self.cam_var = tk.StringVar()
        self.cam_menu = ttk.Combobox(cam_row, textvariable=self.cam_var,
                                     state="readonly", style="Yoink.TCombobox",
                                     width=14)
        self.cam_menu.pack(side="right")
        self.cam_menu.bind("<<ComboboxSelected>>", self._on_camera_pick)

        tk.Label(inner, text="Send to", bg=PANEL, fg=TEXT,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(14, 4))
        self.device_frame = tk.Frame(inner, bg=PANEL)
        self.device_frame.pack(fill="x")

        actions = tk.Frame(inner, bg=PANEL)
        actions.pack(fill="x", pady=(14, 0))
        self.send_btn = tk.Button(actions, text="Send clipboard",
                                  command=self._send_clipboard,
                                  bg=ACCENT, fg=INK, activebackground=ACCENT2,
                                  activeforeground=INK, relief="flat",
                                  font=(FONT, 11, "bold"), bd=0, pady=9,
                                  cursor="hand2")
        self.send_btn.pack(fill="x")
        self.auto_var = tk.BooleanVar(value=False)
        auto = tk.Checkbutton(actions, text="Auto-send when I copy (Ctrl+C)",
                              variable=self.auto_var, command=self._on_autosend,
                              bg=PANEL, fg=SUBTLE, selectcolor=PANEL,
                              activebackground=PANEL, activeforeground=TEXT,
                              font=(FONT, 9), bd=0, highlightthickness=0,
                              anchor="w")
        auto.pack(fill="x", pady=(8, 0))
        if win32clipboard is None:
            auto.configure(state="disabled")

    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Yoink.TCombobox", fieldbackground=SURFACE,
                        background=SURFACE, foreground=TEXT, arrowcolor=SUBTLE,
                        bordercolor=STROKE, lightcolor=STROKE, darkcolor=STROKE,
                        selectbackground=SURFACE, selectforeground=TEXT)
        # The dropdown list itself is a Tk (not ttk) listbox behind the combobox.
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", INK)

    # ------------------------------------------------------------- camera pick

    def _set_camera_dropdown(self, index):
        self.cam_menu.configure(values=[f"Camera {i}" for i in self._cams])
        self.cam_var.set(f"Camera {index}")

    def _on_camera_pick(self, _event=None):
        idx = int(self.cam_var.get().split()[-1])
        self.worker.set_camera(idx)
        self.settings["camera"] = idx
        config.save_settings(self.name, self.settings)   # remembered next launch
        self._log(f"camera -> {idx}")

    def _probe_cameras(self, active):
        """Background: find the other camera indices without touching the one the
        worker already holds, then fill the dropdown via the UI queue."""
        cams = camera.list_cameras(max_index=4, skip=active)
        if active not in cams:
            cams.append(active)
        self._ui_queue.put(("cameras", sorted(set(cams))))

    # ------------------------------------------------------- background inflows

    def _on_frame(self, landmarks, pose, event):
        """CameraWorker thread. Stash the frame; queue any gesture event."""
        self._latest = (landmarks, pose)
        if event:
            self._cam_events.put(event)

    def _log(self, msg):
        """Net/status line. Called from the asyncio thread and our own threads."""
        print(f"[net] {msg}")
        self._ui_queue.put(("status", msg))

    def _on_arrival(self, env):
        """A payload landed (asyncio thread). Hold it; RECEIVE opens it."""
        self._last_env = env
        what = env.get("filename") or (env.get("data") or "")[:40]
        self._ui_queue.put(("status", f"caught {env.get('type')} from "
                                      f"{env.get('sender')}: {what}"))

    def _show_pin_threadsafe(self, peer_name, pin):
        self._ui_queue.put(("pin", peer_name, pin))

    async def _gui_prompt(self, peer_name):
        """Dialer side: ask the user for the PIN shown on the other device."""
        import asyncio
        return await asyncio.to_thread(self._ask_pin_blocking, peer_name)

    def _ask_pin_blocking(self, peer_name):
        box, done = {"pin": ""}, threading.Event()
        self._ui_queue.put(("prompt", peer_name, box, done))
        done.wait(timeout=110)
        return box["pin"] or ""

    # ------------------------------------------------------------- main UI loop

    def _tick(self):
        self._drain_ui_queue()
        self._drain_cam_events()
        self._draw()
        self.root.after(33, self._tick)

    def _drain_ui_queue(self):
        try:
            while True:
                item = self._ui_queue.get_nowait()
                kind = item[0]
                if kind == "status":
                    self.status.configure(text=item[1][:70])
                elif kind == "cameras":
                    self._cams = item[1]
                    self.cam_menu.configure(
                        values=[f"Camera {i}" for i in self._cams])
                elif kind == "pin":
                    self._render_pin(item[1], item[2])
                elif kind == "prompt":
                    _, peer_name, box, done = item
                    box["pin"] = simpledialog.askstring(
                        "Pair", f"Enter the PIN shown on {peer_name}",
                        parent=self.root) or ""
                    done.set()
        except queue.Empty:
            pass

    def _drain_cam_events(self):
        try:
            while True:
                event = self._cam_events.get_nowait()
                if event == "SEND":
                    self._flash = ("Sent", WARN, time.monotonic())
                    self._send_smart()
                elif event == "RECEIVE":
                    self._flash = ("Caught", GO, time.monotonic())
                    self._receive()
        except queue.Empty:
            pass

    def _render_pin(self, peer_name, pin):
        if pin:
            self.pin_title.configure(text=f"Pairing with {peer_name} — type this PIN on it")
            self.pin_value.configure(text=pin)
            self.pin_card.pack(fill="x", padx=20, pady=(0, 8), before=self.canvas)
        else:
            self.pin_card.pack_forget()

    # ------------------------------------------------------------------ sending

    def _selected_ids(self):
        live = set(self.mesh.peers)
        return set(self._selected) & live

    def _send_smart(self):
        """SEND gesture: the full foreground grab, then broadcast to ticked peers."""
        ids = self._selected_ids()
        if not ids:
            self._log("SEND: no devices ticked / connected")
            return
        threading.Thread(target=self._grab_and_send,
                         args=(router.grab, ids), daemon=True).start()

    def _send_clipboard(self):
        """Button / auto-send: send the clipboard as-is, no gesture, no Ctrl+C."""
        ids = self._selected_ids()
        if not ids:
            self._log("send: no devices ticked / connected")
            return
        threading.Thread(target=self._grab_and_send,
                         args=(router.grab_clipboard, ids), daemon=True).start()

    def _grab_and_send(self, grab_fn, ids):
        env = grab_fn(self.name, log=self._log)
        if env is None:
            self._log("nothing to send")
            return
        self.mesh.broadcast(env, targets=ids)
        what = env.get("filename") or (env.get("data") or "")[:40]
        self._log(f"sent {env.get('type')}: {what}")

    def _receive(self):
        if self._last_env is None:
            self._log("RECEIVE: nothing caught yet")
            return
        # dispatch may write the clipboard (text payloads); don't let that bounce
        # straight back out as an auto-send.
        self._clip_ignore_until = time.monotonic() + 1.5
        env = self._last_env
        threading.Thread(target=dispatch,
                         args=(env, self.save_dir, self._log), daemon=True).start()

    # ------------------------------------------------------- clipboard watcher

    def _on_autosend(self):
        self._autosend = self.auto_var.get()

    def _watch_clipboard(self):
        if win32clipboard is None:
            return
        last = self._clip_seq()
        while True:
            time.sleep(0.4)
            if not self._autosend:
                last = self._clip_seq()
                continue
            seq = self._clip_seq()
            if seq == last:
                continue
            last = seq
            if time.monotonic() < self._clip_ignore_until:
                continue
            ids = self._selected_ids()
            if not ids:
                continue
            env = router.grab_clipboard(self.name, log=self._log)
            if env:
                self.mesh.broadcast(env, targets=ids)
                self._log(f"auto-sent {env.get('type')} on copy")

    @staticmethod
    def _clip_seq():
        try:
            return win32clipboard.GetClipboardSequenceNumber()
        except Exception:
            return 0

    # ----------------------------------------------------------- device polling

    def _refresh_devices(self):
        peers = dict(self.mesh.peers)    # device_id -> Peer (name)
        for pid in peers:
            if pid not in self._seen:    # newly connected: ticked by default
                self._seen.add(pid)
                self._selected.add(pid)

        # Rebuild the tick list only when the set of peers changed.
        if set(peers) != set(self._device_vars):
            for child in self.device_frame.winfo_children():
                child.destroy()
            self._device_vars = {}
            if not peers:
                tk.Label(self.device_frame, text="No devices connected",
                         bg=PANEL, fg=SUBTLE, font=(FONT, 9)).pack(anchor="w")
            for pid, peer in peers.items():
                var = tk.BooleanVar(value=pid in self._selected)
                self._device_vars[pid] = var
                tk.Checkbutton(
                    self.device_frame, text=peer.name, variable=var,
                    command=lambda p=pid, v=var: self._toggle_device(p, v),
                    bg=PANEL, fg=TEXT, selectcolor=STROKE,
                    activebackground=PANEL, activeforeground=TEXT,
                    font=(FONT, 10), bd=0, highlightthickness=0, anchor="w",
                ).pack(fill="x", anchor="w")

        n = len(peers)
        self.peers.configure(text=f"{n} linked" if n else "offline",
                             fg=GO if n else SUBTLE)
        self.root.after(700, self._refresh_devices)

    def _toggle_device(self, pid, var):
        if var.get():
            self._selected.add(pid)
        else:
            self._selected.discard(pid)

    # -------------------------------------------------------------- canvas draw

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 1:
            return

        landmarks, pose = self._latest
        for a, b in CONNECTIONS:
            if a < len(landmarks) and b < len(landmarks):
                c.create_line(landmarks[a][0] * w, landmarks[a][1] * h,
                              landmarks[b][0] * w, landmarks[b][1] * h,
                              fill=BONE, width=3, capstyle="round")
        for i, (x, y) in enumerate(landmarks):
            r = 6 if i in TIPS else 4
            px, py = x * w, y * h
            c.create_oval(px - r, py - r, px + r, py + r, fill=ACCENT, outline="")

        self._draw_pose_chip(c, w, pose)
        self._draw_flash(c, w, h)

    def _draw_pose_chip(self, c, w, pose):
        label, color = {
            "OPEN": ("Open hand", GO),
            "CLOSED": ("Fist", ACCENT),
        }.get(pose, ("Show your hand", SUBTLE))
        cx, top = w / 2, 22
        tw = max(len(label) * 8 + 54, 120)
        self._round_rect(c, cx - tw / 2, top, cx + tw / 2, top + 34, 17,
                         fill=PANEL, outline="")
        cy = top + 17
        c.create_oval(cx - tw / 2 + 16 - 4, cy - 4, cx - tw / 2 + 16 + 4, cy + 4,
                      fill=color, outline="")
        c.create_text(cx - tw / 2 + 30, cy, text=label, anchor="w", fill=TEXT,
                      font=(FONT, 10, "bold"))

    def _draw_flash(self, c, w, h):
        if not self._flash:
            return
        label, color, t = self._flash
        if time.monotonic() - t >= 1.0:
            self._flash = None
            return
        c.create_text(w / 2, h / 2, text=label, fill=color,
                      font=(FONT, 26, "bold"))

    @staticmethod
    def _round_rect(c, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return c.create_polygon(pts, smooth=True, **kw)

    # ------------------------------------------------------------------ closing

    def _on_close(self):
        try:
            self.worker.stop()
        except Exception:
            pass
        self.root.destroy()


def launch(mesh, name, save_dir, settings, on_arrival_ref):
    """Build the window and run it. Blocks (owns the main thread) until closed.

    on_arrival_ref: a one-element list; we drop our _on_arrival into it so main
    can point the mesh's receive callback at the live app.
    """
    # A SEND gesture near a focused app synthesizes Ctrl+C to copy. If you run
    # this from a terminal (e.g. VS Code's integrated one), that Ctrl+C can land
    # on our own console as SIGINT and kill the app mid-grab. A GUI app should
    # quit by closing the window, not on a stray SIGINT — so ignore it.
    import signal
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass                             # not on the main thread / no console

    root = tk.Tk()
    app = YoinkApp(root, mesh, name, save_dir, settings)
    on_arrival_ref[0] = app._on_arrival
    root.mainloop()
