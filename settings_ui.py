"""Screen Buddy settings window. Saves to settings.json; the running server picks changes up live.

Run with the venv's pythonw.exe (the `open_settings` MCP tool does this).
`--selftest` builds the window and closes it after a second.
"""

import ctypes
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

import settings

HERE = os.path.dirname(os.path.abspath(__file__))
VOICES_FILE = os.path.join(HERE, "models", "voices-v1.0.bin")
MODEL_FILE = os.path.join(HERE, "models", "kokoro-v1.0.onnx")
WHISPER_MODELS = ["tiny", "base", "small", "medium"]
SAMPLE_TEXT = "Hi! This is how I'll sound while we work together."

LANG_NAMES = {
    "a": "American", "b": "British", "e": "Spanish", "f": "French", "h": "Hindi",
    "i": "Italian", "j": "Japanese", "p": "Portuguese", "z": "Mandarin",
}
KOKORO_LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}

VISION_LABELS = {"images": "Images (best)", "crop": "Cursor crop only (cheaper)", "text": "Text via OCR (cheapest)"}

BG, PANEL, FG, MUTED, ACCENT = "#1e1f22", "#2b2d30", "#dfe1e5", "#8c8f94", "#3574f0"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


def list_voices():
    try:
        import numpy as np

        return sorted(np.load(VOICES_FILE).keys())
    except Exception:
        return [settings.DEFAULTS["voice"]]


def voice_label(v):
    lang = LANG_NAMES.get(v[:1], v[:1])
    gender = {"f": "female", "m": "male"}.get(v[1:2], "")
    return f"{v} — {lang} {gender}".strip()


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = settings.load()
        self.kokoro = None
        self.capturing = False

        root.title("Screen Buddy settings")
        root.configure(bg=BG)
        root.resizable(False, False)
        self._style()

        f = ttk.Frame(root, padding=18)
        f.grid(sticky="nsew")
        f.columnconfigure(1, weight=1)
        row = 0

        def section(text):
            nonlocal row
            ttk.Label(f, text=text, style="Head.TLabel").grid(row=row, column=0, columnspan=3, sticky="w", pady=(10 if row else 0, 6))
            row += 1

        def field(label, widget, hint=None):
            nonlocal row
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            row += 1
            if hint:
                ttk.Label(f, text=hint, style="Hint.TLabel").grid(row=row, column=1, columnspan=2, sticky="w")
                row += 1

        # Voice
        section("Voice")
        self.tts = tk.StringVar(value=self.cfg["tts"])
        engines = ttk.Frame(f)
        ttk.Radiobutton(engines, text="Kokoro (natural, local)", value="kokoro", variable=self.tts).pack(side="left")
        ttk.Radiobutton(engines, text="Windows voice", value="windows", variable=self.tts).pack(side="left", padx=(14, 0))
        field("Engine", engines)

        self.voices = list_voices()
        if self.cfg["voice"] not in self.voices:
            self.voices.insert(0, self.cfg["voice"])
        self.labels = [voice_label(v) for v in self.voices]
        self.voice = tk.StringVar(value=voice_label(self.cfg["voice"]))
        field("Kokoro voice", ttk.Combobox(f, textvariable=self.voice, values=self.labels, state="readonly", width=34))

        self.speed = tk.DoubleVar(value=self.cfg["tts_speed"])
        speed_row = ttk.Frame(f)
        self.speed_lbl = ttk.Label(speed_row, width=5)
        ttk.Scale(speed_row, from_=0.5, to=2.0, variable=self.speed, command=lambda _: self._show_speed()).pack(side="left", fill="x", expand=True)
        self.speed_lbl.pack(side="left", padx=(8, 0))
        self._show_speed()
        field("Kokoro speed", speed_row)

        self.rate = tk.IntVar(value=self.cfg["tts_rate"])
        field("Windows speed", ttk.Spinbox(f, from_=-10, to=10, textvariable=self.rate, width=6), "-10 (slow) to 10 (fast)")

        prev = ttk.Frame(f)
        self.preview_btn = ttk.Button(prev, text="▶ Preview", command=self.preview)
        self.preview_btn.pack(side="left")
        self.preview_status = ttk.Label(prev, style="Hint.TLabel")
        self.preview_status.pack(side="left", padx=10)
        field("", prev)

        self.history = tk.BooleanVar(value=self.cfg["history_window"])
        field("History window", ttk.Checkbutton(f, text="Show what we both said in a side window", variable=self.history))

        # Listening
        section("Listening")
        self.ptt = tk.StringVar(value=self.cfg["ptt_key"])
        ptt_row = ttk.Frame(f)
        self.ptt_btn = ttk.Button(ptt_row, width=22, command=self.capture_key)
        self.ptt_btn.pack(side="left")
        ttk.Button(ptt_row, text="Clear (always listen)", command=lambda: self._set_ptt("")).pack(side="left", padx=(8, 0))
        self._set_ptt(self.cfg["ptt_key"])
        field("Push-to-talk key", ptt_row, "Click, then press a key or mouse side button. Esc cancels.")

        self.whisper = tk.StringVar(value=self.cfg["whisper_model"])
        field("Speech model", ttk.Combobox(f, textvariable=self.whisper, values=WHISPER_MODELS, state="readonly", width=12),
              "Bigger = more accurate, slower. Needs a restart (reconnect in /mcp).")

        self.language = tk.StringVar(value=self.cfg["language"])
        field("Language", ttk.Entry(f, textvariable=self.language, width=8), "e.g. en, or en,ro to pick between those — blank = auto-detect")

        # Coaching
        section("Coaching")
        self.coach = tk.IntVar(value=int(self.cfg["coach_interval"]))
        field("Check-in every (s)", ttk.Spinbox(f, from_=0, to=3600, increment=15, textvariable=self.coach, width=6),
              "0 = only when you talk to it")

        self.vision = tk.StringVar(value=VISION_LABELS.get(self.cfg["vision"], VISION_LABELS["images"]))
        field("Vision", ttk.Combobox(f, textvariable=self.vision, values=list(VISION_LABELS.values()), state="readonly", width=30),
              "What it sends each time: ~1.8k / ~0.7k / a few hundred tokens")

        self.compact = tk.IntVar(value=int(self.cfg["compact_hint_tokens"]))
        field("Remind me to /compact after ~N tokens of screen data",
              ttk.Spinbox(f, from_=0, to=1000000, increment=10000, textvariable=self.compact, width=8), "0 = off")

        buttons = ttk.Frame(f)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right")
        ttk.Button(buttons, text="Save & Close", style="Accent.TButton", command=self.save).pack(side="right", padx=(0, 8))
        root.bind("<Escape>", lambda e: None if self.capturing else root.destroy())

    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL,
                    lightcolor=PANEL, darkcolor=PANEL, troughcolor=PANEL, font=("Segoe UI", 10))
        s.configure("TButton", background=PANEL, padding=(10, 4))
        s.map("TButton", background=[("active", "#3c3f44")])
        s.configure("Accent.TButton", background=ACCENT, foreground="white")
        s.map("Accent.TButton", background=[("active", "#4a85f5")])
        s.configure("Head.TLabel", font=("Segoe UI Semibold", 11))
        s.configure("Hint.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        s.map("TRadiobutton", background=[("active", BG)])
        s.map("TCheckbutton", background=[("active", BG)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)

    def _show_speed(self):
        self.speed_lbl.configure(text=f"{self.speed.get():.2f}×")

    def _voice_id(self):
        return self.voices[self.labels.index(self.voice.get())] if self.voice.get() in self.labels else self.cfg["voice"]

    # --- push-to-talk key capture -----------------------------------------------------
    def _set_ptt(self, name):
        self.ptt.set(name)
        self.ptt_btn.configure(text=f"Key: {name}" if name else "Set a key…")

    def capture_key(self):
        self.capturing = True
        self.ptt_btn.configure(text="Press a key…")
        self._released = False
        self.root.after(30, self._poll_keys)

    def _poll_keys(self):
        get = ctypes.windll.user32.GetAsyncKeyState
        # Skip left/right mouse (the click itself) and generic shift/ctrl/alt (we want the side-specific code).
        candidates = [vk for vk in range(3, 0xFF) if vk not in (0x10, 0x11, 0x12)]
        down = [vk for vk in candidates if get(vk) & 0x8000]
        if not self._released:  # wait until whatever was held when we started is let go
            self._released = not down
        elif down:
            vk = down[0]
            self.capturing = False
            if vk == 0x1B:  # Esc
                self._set_ptt(self.ptt.get())
            else:
                self._set_ptt(settings.vk_name(vk))
            return
        self.root.after(30, self._poll_keys)

    # --- preview ------------------------------------------------------------------------
    def preview(self):
        self.preview_btn.state(["disabled"])
        threading.Thread(target=self._preview, daemon=True).start()

    def _status(self, text):
        self.root.after(0, lambda: self.preview_status.configure(text=text))

    def _preview(self):
        try:
            if self.tts.get() == "windows":
                self._status("speaking…")
                script = (
                    "Add-Type -AssemblyName System.Speech;"
                    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                    f"$s.Rate={max(-10, min(10, int(self.rate.get())))};"
                    f"$s.Speak('{SAMPLE_TEXT.replace(chr(39), chr(39) * 2)}')"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", script], creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import sounddevice as sd

                if self.kokoro is None:
                    if not os.path.exists(MODEL_FILE):
                        self._status("Kokoro model not downloaded yet (the server fetches it on start)")
                        return
                    self._status("loading voice model…")
                    from kokoro_onnx import Kokoro

                    self.kokoro = Kokoro(MODEL_FILE, VOICES_FILE)
                self._status("speaking…")
                voice = self._voice_id()
                samples, sr = self.kokoro.create(SAMPLE_TEXT, voice=voice, speed=float(self.speed.get()),
                                                 lang=KOKORO_LANGS.get(voice[:1], "en-us"))
                sd.play(samples, sr)
                sd.wait()
            self._status("")
        except Exception as e:
            self._status(f"preview failed: {e}")
        finally:
            self.root.after(0, lambda: self.preview_btn.state(["!disabled"]))

    # --- save -----------------------------------------------------------------------------
    def save(self):
        try:
            coach, rate = int(self.coach.get()), int(self.rate.get())
        except (tk.TclError, ValueError):
            coach, rate = int(self.cfg["coach_interval"]), self.cfg["tts_rate"]
        try:
            compact = int(self.compact.get())
        except (tk.TclError, ValueError):
            compact = self.cfg["compact_hint_tokens"]
        vision = next((k for k, v in VISION_LABELS.items() if v == self.vision.get()), self.cfg["vision"])
        settings.save({
            "tts": self.tts.get(),
            "voice": self._voice_id(),
            "tts_speed": round(float(self.speed.get()), 2),
            "tts_rate": max(-10, min(10, rate)),
            "ptt_key": self.ptt.get(),
            "coach_interval": max(0, coach),
            "whisper_model": self.whisper.get(),
            "language": self.language.get(),
            "vision": vision,
            "compact_hint_tokens": max(0, compact),
            "history_window": bool(self.history.get()),
        })
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    if "--selftest" in sys.argv:
        root.after(1000, root.destroy)
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()


if __name__ == "__main__":
    main()
