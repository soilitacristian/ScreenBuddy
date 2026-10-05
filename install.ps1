# Sets up Screen Buddy: creates .venv, installs dependencies, and registers the
# MCP server with Claude Code (if the `claude` CLI is on PATH).
# Usage:  powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$server = Join-Path $root 'server.py'

if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating virtual environment...'
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv (Join-Path $root '.venv') }
    else { python -m venv (Join-Path $root '.venv') }
}

Write-Host 'Installing dependencies...'
& $venvPython -m pip install --upgrade pip | Out-Null
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')

if (Get-Command claude -ErrorAction SilentlyContinue) {
    Write-Host 'Registering with Claude Code...'
    claude mcp add screen-buddy --scope user -e HF_HUB_DISABLE_SYMLINKS_WARNING=1 -- $venvPython $server
} else {
    Write-Host 'claude CLI not found; register manually with:'
    Write-Host "  command: $venvPython"
    Write-Host "  args:    $server"
}

Write-Host 'Done. In Claude Code run /mcp__screen-buddy__buddy to start.'
