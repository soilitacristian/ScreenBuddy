# Screen Buddy

Local MCP server that lets any MCP-capable AI client see your screen (following your mouse),
hear you, and talk back. The model runs on that client's own login/subscription — no API key.

Tools: `wait_for_event` (blocks until you speak or your screen settles after a change),
`look`, `speak`, `set_coaching`, `stop`. Prompt: `buddy` (starts the loop).

Mic and screen watching only start on the first `wait_for_event` call. Speech-to-text
(faster-whisper) and text-to-speech (Windows voice) run fully locally.

## Install (Windows, Python 3.10+)
```
powershell -ExecutionPolicy Bypass -File install.ps1
```
Creates `.venv`, installs `requirements.txt`, and registers the server with Claude Code.
To enable push-to-talk, add `-e BUDDY_PTT_KEY=PAUSE` (or another key) to the `claude mcp add` line.

## Start it
- **Claude Code:** `/mcp__screen-buddy__buddy` (or just say "start screen buddy")
- **Codex CLI:** "start screen buddy and follow its loop instructions"
- Press Esc to end the loop; say "be quieter" to make it comment less.

## Register with other clients
Command: `C:\Users\Cristian\screen-buddy\.venv\Scripts\python.exe C:\Users\Cristian\screen-buddy\server.py`

- Claude Code: `claude mcp add screen-buddy --scope user -- <command>` (done)
- Codex: `[mcp_servers.screen-buddy]` in `~/.codex/config.toml` (done)
- Claude Desktop / Gemini CLI: add under `mcpServers` in `claude_desktop_config.json` /
  `~/.gemini/settings.json`:
  `"screen-buddy": {"command": "<python.exe>", "args": ["<server.py>"]}`

## Settings (env vars)
| Var | Default | Meaning |
|---|---|---|
| BUDDY_COACH_INTERVAL | 60 | min seconds between unprompted check-ins (0 = only when spoken to) |
| BUDDY_SETTLE_SECONDS | 3 | screen must be still this long before a check-in |
| BUDDY_WHISPER_MODEL | base | tiny / base / small / medium (bigger = more accurate, slower) |
| BUDDY_LANGUAGE | auto | force a language, e.g. `en`, `ro` |
| BUDDY_TTS_RATE | 1 | voice speed, -10..10 |
| BUDDY_PTT_KEY | (empty) | push-to-talk: only listen while this key is held, e.g. `RCTRL`, `F8`, `MOUSE4`, or a hex VK code like `0xA3`. Empty = always listening |
