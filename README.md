# Screen Buddy

Local MCP server that lets any MCP-capable AI client see your screen (following your mouse),
hear you, and talk back. The model runs on that client's own login/subscription — no API key.

Tools: `wait_for_event` (blocks until you speak or your screen settles after a change),
`look`, `speak`, `set_coaching`, `open_settings`, `open_history`, `stop`. Prompts: `buddy` (starts the loop),
`settings` (opens the settings window).

Mic and screen watching only start on the first `wait_for_event` call. Speech-to-text
(faster-whisper) and text-to-speech (Kokoro, or the Windows voice) run fully locally.

## Install (Windows, Python 3.10+)
```
powershell -ExecutionPolicy Bypass -File install.ps1
```
Creates `.venv`, installs `requirements.txt`, and registers the server with Claude Code.
Pick a push-to-talk key and voice in the settings window (below).

## Start it
- **Claude Code:** `/mcp__screen-buddy__buddy` (or just say "start screen buddy")
- **Codex CLI:** "start screen buddy and follow its loop instructions"
- Press Esc to end the loop; say "be quieter" to make it comment less.

## Settings window
- **Claude Code:** `/mcp__screen-buddy__settings`, or just ask the buddy to "open settings"
- **Any client / by hand:** `.venv\Scripts\pythonw.exe settings_ui.py`

Choose the voice engine and Kokoro voice (with a ▶ Preview), speed, push-to-talk key (click
the button, then press any key or mouse side button), speech model, language and coaching interval.
Save writes `settings.json`; the running server applies it within a second, except the speech
model, which needs a restart (reconnect screen-buddy in `/mcp`).

## History window
When the loop starts, a window docks to the right edge of the screen showing everything you said
and everything the buddy said, so you can re-read anything you missed. It is always on top by
default (untick to let it go behind). Ask the buddy to "show the history" to reopen it, or run
`.venv\Scripts\pythonw.exe history_ui.py`. Turn it off with "History window" in settings.
The log is `history.jsonl`, cleared each time the server starts.

## Register with other clients
Command: `<screen-buddy folder>\.venv\Scripts\python.exe <screen-buddy folder>\server.py`

- Claude Code: `claude mcp add screen-buddy --scope user -- <command>` (done)
- Codex: `[mcp_servers.screen-buddy]` in `~/.codex/config.toml` (done)
- Claude Desktop / Gemini CLI: add under `mcpServers` in `claude_desktop_config.json` /
  `~/.gemini/settings.json`:
  `"screen-buddy": {"command": "<python.exe>", "args": ["<server.py>"]}`

## Settings (env vars)
Each value is taken from `settings.json` (written by the settings window) first, then the env var,
then the default — so once you save in the window, it overrides the env vars for those keys.
Delete `settings.json` to go back to env vars.

| Var | Default | Meaning |
|---|---|---|
| BUDDY_COACH_INTERVAL | 60 | min seconds between unprompted check-ins (0 = only when spoken to; `wait_for_event` then waits with no timeout, so idle costs no tokens) |
| BUDDY_SETTLE_SECONDS | 3 | screen must be still this long before a check-in |
| BUDDY_WHISPER_MODEL | base | tiny / base / small / medium / large-v3-turbo (bigger = more accurate, slower). Runs on an NVIDIA GPU when the CUDA libraries are installed (`install.ps1` does it when `nvidia-smi` exists; by hand: `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"`), where `large-v3-turbo` is both the most accurate and fast. Falls back to the CPU otherwise |
| BUDDY_VOCABULARY | GitHub, Claude, … | names and jargon you say, comma-separated; passed to whisper as a hint so it spells them right |
| BUDDY_LANGUAGE | auto | force a language, e.g. `en`, or a list like `en,ro` to pick the likelier of those (stops short clips being misheard as random languages) |
| BUDDY_TTS | kokoro | `kokoro` (local neural voice; ~350 MB model downloaded to `models/` on first start) or `windows` (built-in voice). Uses the Windows voice until Kokoro is loaded, or if it fails |
| BUDDY_VOICE | af_heart | Kokoro voice, e.g. `af_heart`, `af_bella`, `am_michael`, `bf_emma`, `bm_george` |
| BUDDY_TTS_SPEED | 1.0 | Kokoro voice speed |
| BUDDY_TTS_RATE | 1 | Windows voice speed, -10..10 |
| BUDDY_PTT_KEY | (empty) | push-to-talk: only listen while this key is held, e.g. `RCTRL`, `F8`, `MOUSE4`, or a hex VK code like `0xA3`. Empty = always listening |
| BUDDY_VISION | images | what `wait_for_event` sends: `images` (full screenshot + cursor crop), `crop` (cursor crop only), or `text` (window title + Windows OCR of the area around the cursor, no images). `look` always sends images |
| BUDDY_OCR_WIDTH / BUDDY_OCR_HEIGHT | 1600 / 900 | size of the area around the cursor that `text` mode reads |
| BUDDY_HISTORY_WINDOW | true | open the conversation history window when the loop starts |
| BUDDY_COMPACT_HINT_TOKENS | 60000 | after ~this many tokens of screen data, the buddy suggests running `/compact` (repeats every N; counter resets when the server restarts). 0 = off |

Rough cost per event: full screenshot ~1.1k tokens, cursor crop ~0.7k, OCR text typically a few hundred.
Voice and speech-to-text run locally and cost nothing.
