"""Screen Buddy conversation history: a small window docked to the right edge of the screen that
shows what you said and what the buddy said, so you can re-read anything you missed.

The server appends one JSON line per utterance to history.jsonl; this window tails that file.
Run with the venv's pythonw.exe (the server opens it on start, or via the `open_history` tool).
`--selftest` builds the window and closes it after a second.
"""

import ctypes
import json
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "history.jsonl")
WIDTH = 420
POLL_MS = 300

BG, PANEL, FG, MUTED, ACCENT, YOU = "#1e1f22", "#2b2d30", "#dfe1e5", "#8c8f94", "#3574f0", "#9ec1ff"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


def work_area():
    """(left, top, right, bottom) of the primary screen minus the taskbar."""
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.l, r.t, r.r, r.b


class App:
    def __init__(self, root):
        self.root = root
        self.pos = 0  # bytes of history.jsonl already shown

        root.title("Screen Buddy history")
        root.configure(bg=BG)
        l, t, r, b = work_area()
        root.geometry(f"{WIDTH}x{b - t - 40}+{r - WIDTH - 16}+{t}")
        root.minsize(260, 200)

        s = ttk.Style(root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("TCheckbutton", background=BG, foreground=MUTED)
        s.map("TCheckbutton", background=[("active", BG)])
        s.configure("TButton", background=PANEL, padding=(8, 2))
        s.map("TButton", background=[("active", "#3c3f44")])

        bar = ttk.Frame(root, padding=(10, 8))
        bar.pack(fill="x")
        self.on_top = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Always on top", variable=self.on_top, command=self._apply_top).pack(side="left")
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="right")

        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        scroll = ttk.Scrollbar(frame, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.text = tk.Text(
            frame, wrap="word", bg=BG, fg=FG, bd=0, highlightthickness=0, padx=6, pady=4,
            font=("Segoe UI", 11), spacing1=2, spacing3=10, yscrollcommand=scroll.set, cursor="arrow",
        )
        self.text.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.text.yview)
        self.text.tag_configure("meta", foreground=MUTED, font=("Segoe UI", 9), spacing3=0)
        self.text.tag_configure("you", foreground=YOU)
        self.text.tag_configure("buddy", foreground=FG)
        self.text.tag_configure("note", foreground=MUTED, font=("Segoe UI", 9, "italic"), justify="center")
        # Read-only but still selectable/copyable.
        self.text.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower() in ("c", "a")) else "break")

        self._apply_top()
        self.poll()

    def _apply_top(self):
        self.root.attributes("-topmost", self.on_top.get())

    def clear(self):
        self.text.delete("1.0", "end")

    def add(self, entry):
        at_bottom = self.text.yview()[1] >= 0.999
        who = entry.get("who", "buddy")
        stamp = time.strftime("%H:%M", time.localtime(entry.get("t", time.time())))
        if who == "session":
            self.text.insert("end", f"— new session {stamp} —\n", "note")
        else:
            self.text.insert("end", f"{'You' if who == 'you' else 'Buddy'} · {stamp}\n", "meta")
            self.text.insert("end", entry.get("text", "") + "\n", who if who in ("you", "buddy") else "buddy")
        if at_bottom:
            self.text.see("end")

    def poll(self):
        try:
            size = os.path.getsize(FILE)
            if size < self.pos:  # server restarted and truncated the file
                self.pos = 0
            if size > self.pos:
                with open(FILE, "rb") as f:
                    f.seek(self.pos)
                    chunk = f.read()
                end = chunk.rfind(b"\n") + 1  # only complete lines
                self.pos += end
                for line in chunk[:end].decode("utf-8", "replace").splitlines():
                    try:
                        self.add(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        self.root.after(POLL_MS, self.poll)


def main():
    root = tk.Tk()
    App(root)
    if "--selftest" in sys.argv:
        root.after(1000, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
