"""User settings shared by the server and the settings UI.

Precedence per key: settings.json > BUDDY_* env var > built-in default.
settings.json lives next to this file and is written by settings_ui.py.
"""

import json
import os

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULTS = {
    "tts": "kokoro",  # kokoro / windows
    "voice": "af_heart",  # kokoro voice
    "tts_speed": 1.0,  # kokoro speed
    "tts_rate": 1,  # Windows voice speed, -10..10
    "ptt_key": "",  # push-to-talk key name; "" = always listening
    "coach_interval": 60.0,  # 0 = only when spoken to
    "whisper_model": "base",  # tiny/base/small/medium (needs a restart)
    "language": "",  # e.g. "en", or "en,ro" = most likely of those; "" = auto
    "vision": "images",  # images (full + crop) / crop (cursor crop only) / text (OCR, no images)
    "compact_hint_tokens": 60000,  # remind to /compact after ~this many screen tokens; 0 = off
    "history_window": True,  # open the conversation history window when the loop starts
}

ENV = {
    "tts": "BUDDY_TTS",
    "voice": "BUDDY_VOICE",
    "tts_speed": "BUDDY_TTS_SPEED",
    "tts_rate": "BUDDY_TTS_RATE",
    "ptt_key": "BUDDY_PTT_KEY",
    "coach_interval": "BUDDY_COACH_INTERVAL",
    "whisper_model": "BUDDY_WHISPER_MODEL",
    "language": "BUDDY_LANGUAGE",
    "vision": "BUDDY_VISION",
    "compact_hint_tokens": "BUDDY_COMPACT_HINT_TOKENS",
    "history_window": "BUDDY_HISTORY_WINDOW",
}


def _coerce(key, value):
    kind = type(DEFAULTS[key])
    if kind is bool:
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind is str:
        value = str(value).strip()
        return value.lower() if key in ("tts", "whisper_model", "language", "vision") else value
    return kind(value)


def load_file():
    try:
        with open(FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load():
    cfg = dict(DEFAULTS)
    sources = [{k: os.environ[v] for k, v in ENV.items() if v in os.environ}, load_file()]
    for source in sources:
        for key, value in source.items():
            if key in DEFAULTS:
                try:
                    cfg[key] = _coerce(key, value)
                except (TypeError, ValueError):
                    pass
    return cfg


def save(updates):
    """Merge `updates` into settings.json (atomically)."""
    data = load_file()
    data.update({k: _coerce(k, v) for k, v in updates.items() if k in DEFAULTS})
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, FILE)


def mtime():
    try:
        return os.path.getmtime(FILE)
    except OSError:
        return None


# --- push-to-talk key names <-> Windows virtual-key codes ---------------------------
VK_NAMES = {
    "LCTRL": 0xA2, "RCTRL": 0xA3, "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LALT": 0xA4, "RALT": 0xA5,
    "CAPSLOCK": 0x14, "SCROLLLOCK": 0x91, "NUMLOCK": 0x90, "PAUSE": 0x13, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22, "APPS": 0x5D, "RWIN": 0x5C,
    "MOUSE4": 0x05, "MOUSE5": 0x06, "MMB": 0x04,
}
for _i in range(10):
    VK_NAMES[f"NUM{_i}"] = 0x60 + _i


def parse_vk(name):
    """Virtual-key code for a key name (RCTRL, F8, MOUSE4, A, 0xA3...), or None for "always listening"."""
    if not name:
        return None
    n = name.strip().upper()
    if n in VK_NAMES:
        return VK_NAMES[n]
    if n.startswith("F") and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
        return 0x6F + int(n[1:])
    if len(n) == 1 and n.isalnum():
        return ord(n)
    try:
        return int(n, 0)
    except ValueError:
        return None


def vk_name(vk):
    """Inverse of parse_vk: the friendliest name for a virtual-key code."""
    for name, code in VK_NAMES.items():
        if code == vk:
            return name
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    return f"0x{vk:02X}"
