#!/usr/bin/env bash
# Hushdoc one-time setup for Linux / macOS.
#
# Mirrors setup.bat: creates the Python venv, installs frontend deps,
# downloads llama-server for the right OS+arch, downloads the default
# Qwen3-1.7B Q4_K_M model. Re-runnable -- every step skips itself when
# the work is already done.
#
# Flags:
#   --cpu        Force CPU build of llama-server even if NVIDIA is detected.
#   --gpu-build  Force CUDA build (Linux only) even if nvidia-smi is missing.
#   --force      Re-download the runtime + model (NOT the venv -- that flag
#                would be destructive and pip install is already idempotent).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
FORCE=0
FORCE_CPU=0
FORCE_GPU=0
for arg in "$@"; do
    case "$arg" in
        --force)      FORCE=1 ;;
        --cpu)        FORCE_CPU=1 ;;
        --gpu-build)  FORCE_GPU=1 ;;
        -h|--help)
            sed -n '2,14p' "$0"
            exit 0
            ;;
        *)
            echo "[setup] unknown flag: $arg" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RESET='\033[0m'; C_CYAN='\033[36m'; C_GREEN='\033[32m'
    C_YELLOW='\033[33m'; C_RED='\033[31m'; C_DIM='\033[2m'
else
    C_RESET=''; C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''
fi
step() { printf "${C_CYAN}[setup]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[setup]${C_RESET} %s\n" "$*"; }
warn() { printf "${C_YELLOW}[setup]${C_RESET} %s\n" "$*"; }
skip() { printf "${C_DIM}[setup] %s${C_RESET}\n" "$*"; }
fail() { printf "${C_RED}[setup]${C_RESET} %s\n" "$*" >&2; }

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "$1 is not on your PATH."
        echo "         $2"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo
printf "${C_DIM}============================================================${C_RESET}\n"
printf "  Hushdoc setup\n"
printf "  This is a one-time install. Re-running is safe.\n"
printf "${C_DIM}============================================================${C_RESET}\n"
echo

# ---------------------------------------------------------------------------
# 1. Python venv + pip install
# ---------------------------------------------------------------------------
PY_BIN=""
for cand in python3.12 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>/dev/null; then
            PY_BIN="$cand"; break
        fi
    fi
done
if [[ -z "$PY_BIN" ]]; then
    fail "Python 3.12 not found."
    echo "         macOS:  brew install python@3.12"
    echo "         Ubuntu: sudo apt install python3.12 python3.12-venv"
    exit 1
fi

VENV_PY="$ROOT/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
    skip ".venv/ already exists -- skipping create."
else
    step "Creating Python 3.12 virtual environment in ./.venv ..."
    "$PY_BIN" -m venv .venv
fi

step "Installing Python dependencies (this can take a few minutes)..."
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r requirements.txt
ok "Python deps ready."
echo

# ---------------------------------------------------------------------------
# 2. npm install
# ---------------------------------------------------------------------------
if [[ -d "$ROOT/web/node_modules" ]]; then
    skip "web/node_modules already exists -- skipping npm install."
else
    need npm "Install Node.js 20+ (https://nodejs.org/) -- LTS is fine."
    step "Installing frontend dependencies (one-time)..."
    (cd web && npm install)
    ok "Frontend deps ready."
fi
echo

# ---------------------------------------------------------------------------
# 3. llama-server binary
# ---------------------------------------------------------------------------
RUNTIME_DIR="$ROOT/runtime"
mkdir -p "$RUNTIME_DIR"

# Detect platform.
UNAME_S="$(uname -s)"
UNAME_M="$(uname -m)"
case "${UNAME_S}-${UNAME_M}" in
    Linux-x86_64)   PLATFORM="ubuntu-x64";   SERVER_BIN="llama-server" ;;
    Darwin-x86_64)  PLATFORM="macos-x64";    SERVER_BIN="llama-server" ;;
    Darwin-arm64)   PLATFORM="macos-arm64";  SERVER_BIN="llama-server" ;;
    *)
        fail "Unsupported platform: $UNAME_S $UNAME_M"
        echo "         Hushdoc auto-detects builds for Linux x64, macOS x64, and macOS arm64."
        echo "         You can drop a llama-server binary into ./runtime/ manually and re-run hushdoc."
        exit 1
        ;;
esac

# CPU vs GPU pick. CUDA builds only exist for Linux on the llama.cpp release page.
USE_GPU=0
if (( FORCE_CPU )); then
    KIND="CPU (forced)"
elif (( FORCE_GPU )); then
    if [[ "$PLATFORM" == "ubuntu-x64" ]]; then
        USE_GPU=1; KIND="CUDA 12.x (forced)"
    else
        warn "CUDA build only available for Linux; ignoring --gpu-build on $PLATFORM."
        KIND="CPU"
    fi
elif [[ "$PLATFORM" == "ubuntu-x64" ]] && command -v nvidia-smi >/dev/null 2>&1 \
     && nvidia-smi -L >/dev/null 2>&1; then
    USE_GPU=1; KIND="CUDA 12.x (auto-detected NVIDIA GPU)"
    step "NVIDIA GPU detected via nvidia-smi -- using the CUDA build."
else
    KIND="CPU"
    if [[ "$PLATFORM" == "ubuntu-x64" ]]; then
        step "No NVIDIA GPU detected -- using the CPU build."
    fi
fi

if [[ -x "$RUNTIME_DIR/$SERVER_BIN" ]] && (( ! FORCE )); then
    skip "runtime/$SERVER_BIN already exists -- skipping download. ($KIND)"
else
    step "Looking up the latest llama.cpp release on GitHub..."
    RELEASE_JSON="$(curl -fsSL https://api.github.com/repos/ggml-org/llama.cpp/releases/latest)"
    TAG="$(printf '%s' "$RELEASE_JSON" | grep -oE '"tag_name": *"[^"]+"' | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"

    # Asset patterns (match the same naming convention as setup.ps1).
    if (( USE_GPU )); then
        PATTERN="llama-${TAG}-bin-${PLATFORM/ubuntu/linux-cuda-12.4}"
        # Linux CUDA assets are named e.g. llama-bXXXX-bin-ubuntu-cuda-12.4-x64.zip
        PATTERN="llama-${TAG}-bin-ubuntu-cuda-12.4-x64.zip"
    else
        PATTERN="llama-${TAG}-bin-${PLATFORM}.zip"
    fi
    URL="$(printf '%s' "$RELEASE_JSON" \
        | grep -oE '"browser_download_url": *"[^"]+"' \
        | sed -E 's/.*"([^"]+)".*/\1/' \
        | grep -F "$PATTERN" | head -1 || true)"

    if [[ -z "$URL" ]]; then
        fail "Couldn't find a $PLATFORM build matching '$PATTERN' in release $TAG."
        echo "         See https://github.com/ggml-org/llama.cpp/releases/tag/$TAG"
        echo "         and drop a llama-server binary into ./runtime/ manually."
        exit 1
    fi

    ZIP="$RUNTIME_DIR/llama-cpp.zip"
    step "Downloading $(basename "$URL") ..."
    echo  "         (Ctrl+C to cancel; if stuck for >60 s the download self-aborts.)"
    curl -L --fail --progress-bar \
         --max-time 1800 --speed-limit 1024 --speed-time 60 \
         --retry 3 --retry-delay 2 \
         -o "$ZIP" "$URL"

    step "Extracting ..."
    TMP="$RUNTIME_DIR/_extract"
    rm -rf "$TMP"
    if command -v unzip >/dev/null 2>&1; then
        unzip -q -o "$ZIP" -d "$TMP"
    else
        # macOS ships without unzip on minimal images but does have ditto.
        ditto -x -k "$ZIP" "$TMP"
    fi
    SRC="$(find "$TMP" -mindepth 1 -maxdepth 2 -type d -print -quit)"
    [[ -z "$SRC" ]] && SRC="$TMP"
    cp -R "$SRC"/* "$RUNTIME_DIR"/
    chmod +x "$RUNTIME_DIR/$SERVER_BIN" 2>/dev/null || true
    rm -rf "$TMP" "$ZIP"

    if [[ ! -x "$RUNTIME_DIR/$SERVER_BIN" ]]; then
        fail "$SERVER_BIN not found inside the downloaded archive."
        exit 1
    fi
    ok "llama-server installed at ./runtime/$SERVER_BIN ($KIND, $TAG)."
fi
echo

# ---------------------------------------------------------------------------
# 4. Default model: Qwen3-1.7B Q4_K_M (~1.2 GB)
# ---------------------------------------------------------------------------
MODELS_DIR="$ROOT/models"
MODEL_PATH="$MODELS_DIR/model.gguf"
mkdir -p "$MODELS_DIR"
MODEL_URL="https://huggingface.co/MaziyarPanahi/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B.Q4_K_M.gguf"

if [[ -f "$MODEL_PATH" ]] && (( ! FORCE )); then
    skip "models/model.gguf already exists -- skipping download."
else
    echo "         Source: $MODEL_URL"
    echo "         This is the slow step (~1.2 GB). Expect a few minutes on broadband."
    step "Downloading Qwen3-1.7B Q4_K_M (~1.2 GB) ..."
    if ! curl -L --fail --progress-bar \
         --max-time 1800 --speed-limit 1024 --speed-time 60 \
         --retry 3 --retry-delay 2 \
         -o "$MODEL_PATH" "$MODEL_URL"; then
        rm -f "$MODEL_PATH"
        fail "Model download failed."
        echo "         Download manually from https://huggingface.co/MaziyarPanahi/Qwen3-1.7B-GGUF"
        echo "         and save it as ./models/model.gguf"
        exit 1
    fi
    SIZE_MB=$(( $(stat -c%s "$MODEL_PATH" 2>/dev/null || stat -f%z "$MODEL_PATH") / 1048576 ))
    ok "Model saved to ./models/model.gguf (${SIZE_MB} MB)."
fi
echo

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
printf "${C_DIM}============================================================${C_RESET}\n"
ok "Setup complete!"
echo
echo  "  Next step: run ./dev.sh to launch the app."
echo
echo  "  To swap the model later, replace ./models/model.gguf with"
echo  "  any other .gguf file from HuggingFace."
echo
printf "${C_DIM}============================================================${C_RESET}\n"
