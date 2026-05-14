#!/usr/bin/env bash
# Hushdoc dev launcher (bash).
# Starts FastAPI on :8000 + Vite on :5173, wires Ctrl+C to stop both.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

# Slant-figlet "HUSHDOC" + 🤫 tagline. Same banner used by hushdoc.ps1
# / setup.ps1 so every entry point shares one identity card.
show_banner() {
    local tagline="${1:-dev launcher -- backend + Vite}"
    local version="dev"
    if [[ -f "$ROOT/VERSION" ]]; then
        version="$(tr -d '[:space:]' <"$ROOT/VERSION")"
    fi
    # Only paint colors if we're on a TTY; piped output stays clean.
    local C_CYAN='' C_DIM='' C_RESET=''
    if [[ -t 1 ]]; then
        C_CYAN=$'\033[36m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
    fi
    echo
    echo "${C_CYAN}    __  __           __         __          ${C_RESET}"
    echo "${C_CYAN}   / / / /_  _______/ /_  ____/ /___  _____ ${C_RESET}"
    echo "${C_CYAN}  / /_/ / / / / ___/ __ \\/ __  / __ \\/ ___/ ${C_RESET}"
    echo "${C_CYAN} / __  / /_/ (__  ) / / / /_/ / /_/ / /__   ${C_RESET}"
    echo "${C_CYAN}/_/ /_/\\__,_/____/_/ /_/\\__,_/\\____/\\___/   ${C_RESET}"
    echo
    printf '       \xf0\x9f\xa4\xab  %s\n' "$tagline"
    printf '%s          local-only - offline - your machine - v%s%s\n' \
        "$C_DIM" "$version" "$C_RESET"
    echo
}

show_banner "dev launcher -- backend + Vite (no auto-cleanup)"

# Pick a Python: prefer the project venv, fall back to system python.
if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
    PY="$ROOT/.venv/Scripts/python.exe"   # Git Bash on Windows
else
    echo "[hushdoc] venv not found; create one with:" >&2
    echo "    python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [[ ! -d "$ROOT/web/node_modules" ]]; then
    echo "[hushdoc] web/node_modules missing — running 'npm install'..."
    (cd "$ROOT/web" && npm install)
fi

cleanup() {
    echo
    echo "[hushdoc] stopping..."
    [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
    [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[hushdoc] starting FastAPI backend on http://localhost:8000 ..."
( cd "$ROOT" && "$PY" -m uvicorn server.main:app --port 8000 ) &
BACK_PID=$!

echo "[hushdoc] starting Vite frontend on http://localhost:5173 ..."
( cd "$ROOT/web" && npm run dev ) &
FRONT_PID=$!

wait "$BACK_PID" "$FRONT_PID"
