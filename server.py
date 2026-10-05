"""Screen Buddy: an MCP server that gives any MCP-capable AI client (Claude Code,
Claude Desktop, Codex CLI, Gemini CLI, ...) eyes, ears and a voice.

The client's own login/subscription runs the model; this server only provides
local tools. The core is `wait_for_event`, a blocking tool that returns when
you speak or when your screen changes and then settles, so the model can sit
in a loop: wait -> look -> maybe speak -> wait.

Never print to stdout here: it is the MCP stdio transport. Log to stderr.
"""

import ctypes
import io
import logging
import os
import queue
import subprocess
import sys
import threading
import time

import mss
import numpy as np
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage, ImageDraw

import settings

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[screen-buddy] %(message)s")
log = logging.getLogger("screen-buddy")

# Make cursor coordinates match physical screen pixels on scaled displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# --- configuration -------------------------------------------------------------
# User settings (voice, push-to-talk key, ...) come from settings.py: settings.json > env > default.
# They are reloaded live when settings.json changes; see settings_watcher.
cfg = settings.load()
SETTLE_SECONDS = float(os.environ.get("BUDDY_SETTLE_SECONDS", "3"))
MAX_SIDE = int(os.environ.get("BUDDY_MAX_SIDE", "1400"))
ZOOM_W, ZOOM_H = 900, 560
STALE_EVENT_SECONDS = 120

SAMPLE_RATE = 16000
BLOCK = 480  # 30 ms
END_SILENCE = 0.9
MIN_SPEECH = 0.4
MAX_SPEECH = 30.0
HALLUCINATIONS = {"", "you", "thank you.", "thanks for watching!", "thank you for watching.", "bye."}


# --- screen -------------------------------------------------------------------
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos():
    p = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def grab_monitor_under_cursor():
    """Return (PIL image, cursor x, cursor y relative to that image)."""
    cx, cy = cursor_pos()
    with getattr(mss, "MSS", mss.mss)() as sct:
        mons = sct.monitors[1:]
        mon = next(
            (m for m in mons if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]),
            mons[0],
        )
        shot = sct.grab(mon)
    img = PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return img, cx - mon["left"], cy - mon["top"]


def _mark_cursor(img, x, y, r):
    d = ImageDraw.Draw(img)
    d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 0, 60), width=max(2, r // 5))
    d.line([x - r * 2, y, x - r, y], fill=(255, 0, 60), width=2)
    d.line([x + r, y, x + r * 2, y], fill=(255, 0, 60), width=2)


def _to_mcp_image(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return Image(data=buf.getvalue(), format="jpeg")


# Rough token estimate of screen data sent this server run, for the /compact reminder.
FULL_TOKENS, CROP_TOKENS = 1100, 700
_screen_tokens = 0


def snapshot(full=True, zoom=True):
    """Full screen (downscaled, cursor marked) and/or a native-res crop around the cursor."""
    global _screen_tokens
    img, x, y = grab_monitor_under_cursor()
    _screen_tokens += (FULL_TOKENS if full else 0) + (CROP_TOKENS if zoom else 0)
    out = []
    if full:
        f = img.copy()
        _mark_cursor(f, x, y, 14)
        f.thumbnail((MAX_SIDE, MAX_SIDE))
        out.append(_to_mcp_image(f))
    if zoom:
        left = min(max(0, x - ZOOM_W // 2), max(0, img.width - ZOOM_W))
        top = min(max(0, y - ZOOM_H // 2), max(0, img.height - ZOOM_H))
        z = img.crop((left, top, left + ZOOM_W, top + ZOOM_H))
        _mark_cursor(z, x - left, y - top, 10)
        out.append(_to_mcp_image(z))
    return out


# --- OCR (cheap "text" vision mode, built-in Windows OCR) -----------------------------
OCR_W = int(os.environ.get("BUDDY_OCR_WIDTH", "1600"))
OCR_H = int(os.environ.get("BUDDY_OCR_HEIGHT", "900"))
OCR_SCALE = 2  # upscaling small UI text helps Windows OCR a lot
_ocr_engine = None
_ocr_failed = False


def foreground_title():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _ocr(img):
    """Return [(x, y, h, text)] for each OCR line of a PIL image, in image pixels. Raises if OCR is unavailable."""
    global _ocr_engine
    import asyncio

    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    if _ocr_engine is None:
        _ocr_engine = OcrEngine.try_create_from_user_profile_languages()
        if _ocr_engine is None:
            raise RuntimeError("no OCR language installed")
    big = img.resize((img.width * OCR_SCALE, img.height * OCR_SCALE), PILImage.LANCZOS)
    r, g, b = big.convert("RGB").split()
    w = DataWriter()
    w.write_bytes(PILImage.merge("RGBA", (b, g, r, PILImage.new("L", big.size, 255))).tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.BGRA8, big.width, big.height)
    res = asyncio.run(_ocr_engine.recognize_async(bmp))
    out = []
    for line in res.lines:
        rects = [wd.bounding_rect for wd in line.words]
        if rects:
            x, y = min(r.x for r in rects), min(r.y for r in rects)
            h = max(r.y + r.height for r in rects) - y
            out.append((x / OCR_SCALE, y / OCR_SCALE, h / OCR_SCALE, " ".join(line.text.split())))
    return out


def text_snapshot():
    """Window title + OCR text around the cursor, rows in reading order, cursor row marked with >>.
    Returns None if OCR is unavailable (caller falls back to the crop image)."""
    global _ocr_failed
    if _ocr_failed:
        return None
    img, x, y = grab_monitor_under_cursor()
    left = min(max(0, x - OCR_W // 2), max(0, img.width - OCR_W))
    top = min(max(0, y - OCR_H // 2), max(0, img.height - OCR_H))
    region = img.crop((left, top, min(img.width, left + OCR_W), min(img.height, top + OCR_H)))
    try:
        lines = _ocr(region)
    except Exception as e:
        _ocr_failed = True
        log.warning("OCR unavailable (%s); using the cursor crop image instead", e)
        return None
    # Merge OCR lines that sit on the same visual row (e.g. editor gutter + code, side-by-side panes).
    rows = []
    for lx, ly, lh, text in sorted(lines, key=lambda l: l[1]):
        if rows and abs(ly - rows[-1]["y"]) < max(lh, rows[-1]["h"]) * 0.5:
            rows[-1]["parts"].append((lx, text))
        else:
            rows.append({"y": ly, "h": lh, "parts": [(lx, text)]})
    cy = y - top
    nearest = min(range(len(rows)), key=lambda i: abs(rows[i]["y"] + rows[i]["h"] / 2 - cy), default=-1)
    body = []
    for i, row in enumerate(rows):
        text = "   ".join(t for _, t in sorted(row["parts"]))
        body.append((">> " if i == nearest else "   ") + text)
    return (
        f'Window: "{foreground_title()}"\n'
        f"Cursor at ({x}, {y}) on a {img.width}x{img.height} screen. "
        f"OCR of the {region.width}x{region.height} area around it (>> = row under the cursor):\n"
        + ("\n".join(body) if body else "(no text found)")
    )


def vision_snapshot():
    """What wait_for_event attaches, per the `vision` setting: images / crop / text."""
    global _screen_tokens
    mode = cfg["vision"]
    hint = "\n(Call `look` for a full screenshot if you need to see layout or visuals.)"
    if mode == "text":
        text = text_snapshot()
        if text is not None:
            _screen_tokens += len(text + hint) // 4
            return [text + hint]
        mode = "crop"
    if mode == "crop":
        return [hint.strip(), *snapshot(full=False)]
    return snapshot()


# --- shared state ---------------------------------------------------------------
events: "queue.Queue[dict]" = queue.Queue()
_stop = threading.Event()
_started = False
_start_lock = threading.Lock()
_coach_interval = cfg["coach_interval"]
_tts_proc = None
_tts_lock = threading.Lock()
_tts_gen = 0  # bumped on every interruption; stale kokoro threads check it and bail
_kokoro = None
_kokoro_loading = False
_kokoro_busy = False  # kokoro is synthesizing or playing


def is_speaking():
    return _kokoro_busy or (_tts_proc is not None and _tts_proc.poll() is None)


# --- conversation history (shown by history_ui.py) -------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(HERE, "history.jsonl")
_history_lock = threading.Lock()
_history_proc = None


def log_history(who, text=""):
    """Append one utterance (who = you / buddy / session) to history.jsonl."""
    import json

    line = json.dumps({"who": who, "text": text, "t": time.time()}, ensure_ascii=False) + "\n"
    try:
        with _history_lock, open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        log.warning("could not write history: %s", e)


def _launch_ui(script):
    """Start a tkinter window (settings_ui.py / history_ui.py) detached, with the venv's pythonw."""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return subprocess.Popen(
        [pythonw if os.path.exists(pythonw) else sys.executable, os.path.join(HERE, script)],
        cwd=HERE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def show_history():
    """Open the history window unless this server already has one open."""
    global _history_proc
    if _history_proc is None or _history_proc.poll() is not None:
        _history_proc = _launch_ui("history_ui.py")


# --- screen watcher -----------------------------------------------------------------
def _fingerprint():
    img, _, _ = grab_monitor_under_cursor()
    return np.asarray(img.convert("L").resize((96, 54)), dtype=np.int16)


def screen_watcher(stop_ev):
    reported = _fingerprint()
    prev = reported
    last_motion = time.time()
    last_event = time.time()
    while not stop_ev.is_set():
        time.sleep(1.0)
        try:
            cur = _fingerprint()
        except Exception as e:  # monitor changes, lock screen, etc.
            log.warning("capture failed: %s", e)
            continue
        if np.abs(cur - prev).mean() > 0.5:
            last_motion = time.time()
        prev = cur
        now = time.time()
        changed = np.abs(cur - reported).mean() > 2.0
        settled = now - last_motion >= SETTLE_SECONDS
        due = _coach_interval > 0 and now - last_event >= _coach_interval
        if changed and settled and due:
            reported = cur
            last_event = now
            events.put({"kind": "screen", "t": now})


# --- settings live reload -------------------------------------------------------------
def settings_watcher():
    """Poll settings.json and apply changes without a restart (whisper_model needs one)."""
    global cfg, _coach_interval
    last = settings.mtime()
    while True:
        time.sleep(1.0)
        m = settings.mtime()
        if m == last:
            continue
        last = m
        new = settings.load()
        changed = {k: v for k, v in new.items() if v != cfg.get(k)}
        if not changed:
            continue
        log.info("settings changed: %s", changed)
        cfg = new
        if "coach_interval" in changed:
            _coach_interval = new["coach_interval"]
        if "whisper_model" in changed:
            log.info("whisper model change applies after a restart")
        if new["tts"] == "kokoro" and _kokoro is None and not _kokoro_loading:
            threading.Thread(target=load_kokoro, daemon=True, name="load_kokoro").start()


# --- microphone + speech-to-text --------------------------------------------------------
_warned_keys = set()


def _ptt_vk():
    """Current push-to-talk key code, or None to listen all the time. Re-read so settings apply live."""
    name = cfg["ptt_key"]
    vk = settings.parse_vk(name)
    if name and vk is None and name not in _warned_keys:
        _warned_keys.add(name)
        log.warning("unknown push-to-talk key %r; listening all the time", name)
    return vk


def key_down(vk):
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def pick_language(model, audio):
    """The `language` setting: "" = auto-detect, "en" = force one, "en,ro" = most likely of those.
    Restricting the choice stops short clips being transcribed as some random language."""
    langs = [l.strip() for l in cfg["language"].split(",") if l.strip()]
    if len(langs) <= 1:
        return langs[0] if langs else None
    try:
        _, _, probs = model.detect_language(audio)
    except Exception as e:
        log.warning("language detection failed (%s); using %s", e, langs[0])
        return langs[0]
    scores = dict(probs)
    return max(langs, key=lambda l: scores.get(l, 0.0))


def mic_listener(stop_ev):
    import sounddevice as sd
    from faster_whisper import WhisperModel

    log.info("loading whisper model '%s'...", cfg["whisper_model"])
    model = WhisperModel(cfg["whisper_model"], device="cpu", compute_type="int8")
    log.info("listening (%s)", f"push-to-talk: {cfg['ptt_key']}" if _ptt_vk() is not None else "voice activity")

    blocks: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, t, status):
        blocks.put(indata[:, 0].copy())

    def transcribe(speech):
        audio = np.concatenate(speech)
        segs, _ = model.transcribe(audio, language=pick_language(model, audio), vad_filter=True, beam_size=1)
        text = " ".join(s.text.strip() for s in segs).strip()
        if text.lower() not in HALLUCINATIONS:
            log.info("heard: %s", text)
            log_history("you", text)
            events.put({"kind": "speech", "text": text, "t": time.time()})

    noise = 0.005
    speech, silent_for, in_speech = [], 0.0, False
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK, callback=callback):
        while not stop_ev.is_set():
            try:
                b = blocks.get(timeout=0.5)
            except queue.Empty:
                continue
            ptt_vk = _ptt_vk()
            if ptt_vk is not None:
                if key_down(ptt_vk):
                    if not in_speech:
                        stop_speaking()  # holding the key = barge in
                        in_speech, speech = True, []
                    speech.append(b)
                    if len(speech) * BLOCK / SAMPLE_RATE >= MAX_SPEECH:
                        transcribe(speech)
                        speech = []
                elif in_speech:
                    in_speech = False
                    if len(speech) * BLOCK / SAMPLE_RATE >= MIN_SPEECH:
                        transcribe(speech)
                continue
            if is_speaking():  # don't transcribe our own voice
                speech, silent_for, in_speech = [], 0.0, False
                continue
            rms = float(np.sqrt(np.mean(b * b)))
            loud = rms > max(noise * 3.0, 0.012)
            if not in_speech:
                noise = 0.97 * noise + 0.03 * rms
                if loud:
                    in_speech, speech, silent_for = True, [b], 0.0
                continue
            speech.append(b)
            silent_for = 0.0 if loud else silent_for + BLOCK / SAMPLE_RATE
            duration = len(speech) * BLOCK / SAMPLE_RATE
            if silent_for >= END_SILENCE or duration >= MAX_SPEECH:
                in_speech = False
                if duration - silent_for < MIN_SPEECH:
                    continue
                transcribe(speech)


def _run(target, stop_ev):
    def wrapper():
        try:
            target(stop_ev)
        except Exception:
            log.exception("%s crashed", target.__name__)
    threading.Thread(target=wrapper, daemon=True, name=target.__name__).start()


def ensure_started():
    global _started, _stop
    with _start_lock:
        if _started:
            return
        _stop = threading.Event()
        _run(screen_watcher, _stop)
        _run(mic_listener, _stop)
        _started = True
        if cfg["history_window"]:
            show_history()


# --- text-to-speech (Kokoro, local neural voice; falls back to the Windows voice) -------
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")  # fp32: int8/fp16 are much slower on CPU
KOKORO_LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}


def load_kokoro():
    """Download (first run only) and load the Kokoro model. Until it is ready, say() uses the Windows voice."""
    global _kokoro, _kokoro_loading
    if cfg["tts"] != "kokoro" or _kokoro_loading:
        return
    _kokoro_loading = True
    try:
        import urllib.request
        from kokoro_onnx import Kokoro

        os.makedirs(MODELS_DIR, exist_ok=True)
        paths = []
        for name in KOKORO_FILES:
            path = os.path.join(MODELS_DIR, name)
            if not os.path.exists(path):
                log.info("downloading %s...", name)
                urllib.request.urlretrieve(KOKORO_URL + name, path + ".part")
                os.replace(path + ".part", path)
            paths.append(path)
        _kokoro = Kokoro(*paths)
        log.info("kokoro ready (voice %s)", _kokoro_voice())
    except Exception as e:
        log.warning("kokoro unavailable (%s); using the Windows voice", e)
    finally:
        _kokoro_loading = False


def _kokoro_voice():
    voice = cfg["voice"]
    if voice in _kokoro.get_voices():
        return voice
    log.warning("unknown kokoro voice %r; using %s", voice, settings.DEFAULTS["voice"])
    return settings.DEFAULTS["voice"]


def _kokoro_say(text, gen):
    global _kokoro_busy
    import re
    import sounddevice as sd

    voice, speed = _kokoro_voice(), cfg["tts_speed"]
    lang = KOKORO_LANGS.get(voice[:1], "en-us")
    try:
        # Synthesize sentence by sentence so the first one starts playing quickly.
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            if not sentence:
                continue
            samples, sr = _kokoro.create(sentence, voice=voice, speed=speed, lang=lang)
            if gen != _tts_gen:
                return
            sd.wait()  # previous sentence
            with _tts_lock:
                if gen != _tts_gen:
                    return
                sd.play(samples, sr)
        sd.wait()
    except Exception:
        log.exception("kokoro playback failed")
    finally:
        with _tts_lock:
            if gen == _tts_gen:
                _kokoro_busy = False


# Windows built-in voice, no download needed.
_TTS_SCRIPT = (
    "[Console]::InputEncoding=[Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Speech;"
    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$s.Rate={rate};"
    "$s.Speak([Console]::In.ReadToEnd())"
)


def say(text):
    global _tts_proc, _kokoro_busy
    log_history("buddy", text)
    stop_speaking()
    with _tts_lock:
        if cfg["tts"] == "kokoro" and _kokoro is not None:
            _kokoro_busy = True
            threading.Thread(target=_kokoro_say, args=(text, _tts_gen), daemon=True, name="kokoro").start()
            return
        _tts_proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", _TTS_SCRIPT.format(rate=max(-10, min(10, cfg["tts_rate"])))],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        _tts_proc.stdin.write(text.encode("utf-8"))
        _tts_proc.stdin.close()


def stop_speaking():
    global _tts_gen, _kokoro_busy
    with _tts_lock:
        _tts_gen += 1
        if _kokoro_busy:
            import sounddevice as sd

            _kokoro_busy = False
            sd.stop()
        if _tts_proc is not None and _tts_proc.poll() is None:
            _tts_proc.kill()


# --- MCP ---------------------------------------------------------------------------
LOOP_PROTOCOL = """You are the user's pair-programming buddy. You can see their screen and hear them.
Run this loop until they tell you to stop:
1. Call `wait_for_event`.
2. If it says the user spoke: answer them with `speak` (short, conversational, 1-3 sentences).
   "this"/"here" means what is near the red cursor circle in the zoomed image
   (or the row marked >> when you get OCR text instead of images).
3. If it is a screen check-in: only `speak` if you see something genuinely worth saying
   (a bug, a cleaner idiom, a missed API, a likely mistake). Otherwise stay silent.
   Never narrate what they are doing. At most one tip per check-in.
4. If nothing happened, just call `wait_for_event` again.
Never write long text replies in chat; talk through `speak`. Keep code details for when asked.
If they say to be quiet or less chatty, call `set_coaching` (0 = only when spoken to).
If they ask to change the voice, push-to-talk key or other settings, call `open_settings`.
If they want to re-read what was said (history, transcript, captions), call `open_history`."""

mcp = FastMCP("screen-buddy", instructions=LOOP_PROTOCOL)


@mcp.tool()
def wait_for_event(timeout_seconds: int = 50) -> list:
    """Block until the user speaks or their screen changes and settles (a coaching check-in).
    Returns what happened plus the screen, depending on the vision setting: a full screenshot
    (cursor circled in red) and a zoomed crop around the mouse, just the crop, or OCR text around
    the cursor. Returns 'nothing happened' on timeout; just call it again. With coaching off
    (interval 0) it ignores the timeout and waits until the user speaks, so idle costs nothing."""
    ensure_started()
    deadline = time.time() + max(5, min(timeout_seconds, 600))
    while True:
        # Re-checked each second so turning coaching off mid-wait takes effect.
        remaining = 1.0 if _coach_interval == 0 else deadline - time.time()
        if remaining <= 0:
            return ["Nothing happened. Call wait_for_event again."]
        try:
            ev = events.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if time.time() - ev["t"] > STALE_EVENT_SECONDS:
            continue
        break
    if ev["kind"] == "speech":
        head = f'The user said: "{ev["text"]}"\nReply with `speak`.'
    else:
        head = "Screen check-in (user paused). Speak only if there is a genuinely useful tip; otherwise wait again."
    return [head + _compact_hint(head), *vision_snapshot()]


def _compact_hint(head):
    """Count text tokens (~chars/4) and, past the threshold, ask the model to suggest /compact."""
    global _screen_tokens
    _screen_tokens += len(head) // 4
    limit = cfg["compact_hint_tokens"]
    if limit <= 0 or _screen_tokens < limit:
        return ""
    sent, _screen_tokens = _screen_tokens, 0
    return (
        f"\nContext is getting large (~{sent} screen tokens sent). Briefly suggest to the user via `speak` "
        "that they run /compact (or /clear if starting something new)."
    )


@mcp.tool()
def look(zoom_only: bool = False) -> list:
    """Take a screenshot right now: full screen with the cursor circled, plus a zoomed crop
    around the mouse. Set zoom_only to get just the crop."""
    return snapshot(full=not zoom_only, zoom=True)


@mcp.tool()
def speak(text: str) -> str:
    """Say something out loud to the user. Interrupts anything still being said.
    Keep it short and conversational; don't read code symbols aloud verbatim."""
    say(text)
    return "spoken"


@mcp.tool()
def set_coaching(interval_seconds: int) -> str:
    """How often (at most) to get unprompted screen check-ins. 0 = only when the user speaks."""
    global _coach_interval
    _coach_interval = max(0, interval_seconds)
    return f"coaching interval set to {_coach_interval}s"


@mcp.tool()
def open_settings() -> str:
    """Open the Screen Buddy settings window (voice, push-to-talk key, coaching, speech model)."""
    _launch_ui("settings_ui.py")
    return "Settings window opened. Saved changes apply live (speech model changes need a restart)."


@mcp.tool()
def open_history() -> str:
    """Open the conversation history window (everything the user and the buddy said, docked on the right)."""
    show_history()
    return "History window opened."


@mcp.tool()
def stop() -> str:
    """Stop listening to the microphone and watching the screen until wait_for_event is called again."""
    global _started
    _stop.set()
    _started = False
    while not events.empty():
        events.get_nowait()
    return "stopped"


@mcp.prompt()
def buddy() -> str:
    """Start the screen-watching, voice pair-programming loop."""
    return LOOP_PROTOCOL + "\n\nStart now: say a quick hello with `speak`, then call `wait_for_event`."


@mcp.prompt(name="settings")
def settings_prompt() -> str:
    """Open the Screen Buddy settings window."""
    return (
        "Call the screen-buddy `open_settings` tool to open the settings window, then tell the user "
        "in one short line that it is open. If a buddy loop was running, continue it by calling `wait_for_event`."
    )


if __name__ == "__main__":
    try:  # each server run starts a fresh history
        open(HISTORY_FILE, "w").close()
    except OSError:
        pass
    log_history("session")
    threading.Thread(target=load_kokoro, daemon=True, name="load_kokoro").start()
    threading.Thread(target=settings_watcher, daemon=True, name="settings_watcher").start()
    mcp.run()
