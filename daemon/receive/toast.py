"""Always-on-top "caught" pop.

A single worker thread pulls requests off a queue and shows one pop at a time
(mainloop blocks until it dismisses), so there's never more than one Tk root
alive at once — the only reliable way to drive tkinter off the main thread.

Shows the caught item's name, a thumbnail for images, and opens the item when
clicked. Auto-dismisses after a few seconds.
"""
import queue
import threading
import tkinter as tk

# Same palette as the desktop app / Android client.
BG = "#1C1D21"
FG = "#F4F2EF"
ACCENT = "#FF6A3D"
SUBTLE = "#9AA0A6"
_q = queue.Queue()
_worker = None


# The desktop app runs its own Tk root on the main thread and shows its own
# "caught" status + flash. If this second (worker-thread) Tk root ever clashes
# with it, set this False to fall back to the app's own notification.
enabled = True


def show(text, image_path=None, on_click=None, duration=4.0):
    """Queue an always-on-top pop. Non-blocking; safe from any thread.

    image_path: show a thumbnail of this image above the text.
    on_click:   called (with no args) if the user clicks the pop.
    """
    if not enabled:
        return
    global _worker
    if _worker is None:
        _worker = threading.Thread(target=_run, daemon=True)
        _worker.start()
    _q.put((text, image_path, on_click, duration))


def _run():
    while True:
        args = _q.get()
        try:
            _show_one(*args)
        except Exception as e:  # a failed pop must never kill the worker
            print("toast failed:", e)


def _show_one(text, image_path, on_click, duration):
    root = tk.Tk()
    root.overrideredirect(True)              # borderless
    root.attributes("-topmost", True)
    root.configure(bg=BG, highlightbackground=ACCENT, highlightthickness=1)

    tk.Label(root, text="CAUGHT", fg=ACCENT, bg=BG,
             font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(10, 0))
    if image_path:
        _add_thumbnail(root, image_path)
    tk.Label(root, text=text, fg=FG, bg=BG,
             font=("Segoe UI", 12), padx=16, pady=8, wraplength=280).pack(anchor="w")
    if on_click:
        tk.Label(root, text="click to open", fg=SUBTLE, bg=BG,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(0, 10))
        _bind_click(root, root, on_click)

    root.update_idletasks()
    x = root.winfo_screenwidth() - root.winfo_width() - 20   # top-right corner
    root.geometry(f"+{x}+40")
    root.after(int(duration * 1000), root.destroy)
    root.mainloop()


def _add_thumbnail(root, image_path):
    """Best effort: a pop without its picture still beats no pop at all."""
    try:
        from PIL import Image, ImageTk
        with Image.open(image_path) as im:
            im.thumbnail((240, 180))
            photo = ImageTk.PhotoImage(im)
        label = tk.Label(root, image=photo, bg=BG)
        label.image = photo                  # keep a ref or Tk garbage-collects it
        label.pack(padx=10, pady=(10, 0))
    except Exception as e:
        print("thumbnail failed:", e)


def _bind_click(root, widget, on_click):
    def clicked(_event):
        root.destroy()
        on_click()
    widget.bind("<Button-1>", clicked)
    for child in widget.winfo_children():
        _bind_click(root, child, on_click)
