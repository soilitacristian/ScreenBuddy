# Screen Buddy

Local MCP server that lets any MCP-capable AI client see your screen (following your mouse),
hear you, and talk back. The model runs on that client's own login/subscription — no API key.

Tools: `wait_for_event` (blocks until you speak or your screen settles after a change),
`look`, `speak`, `set_coaching`, `open_settings`, `stop`. Prompts: `buddy` (starts the loop),
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
| BUDDY_COACH_INTERVAL | 60 | min seconds between unprompted check-ins (0 = only when spoken to) |
| BUDDY_SETTLE_SECONDS | 3 | screen must be still this long before a check-in |
| BUDDY_WHISPER_MODEL | base | tiny / base / small / medium (bigger = more accurate, slower) |
| BUDDY_LANGUAGE | auto | force a language, e.g. `en`, `ro` |
| BUDDY_TTS | kokoro | `kokoro` (local neural voice; ~350 MB model downloaded to `models/` on first start) or `windows` (built-in voice). Uses the Windows voice until Kokoro is loaded, or if it fails |
| BUDDY_VOICE | af_heart | Kokoro voice, e.g. `af_heart`, `af_bella`, `am_michael`, `bf_emma`, `bm_george` |
| BUDDY_TTS_SPEED | 1.0 | Kokoro voice speed |
| BUDDY_TTS_RATE | 1 | Windows voice speed, -10..10 |
| BUDDY_PTT_KEY | (empty) | push-to-talk: only listen while this key is held, e.g. `RCTRL`, `F8`, `MOUSE4`, or a hex VK code like `0xA3`. Empty = always listening |
