# Hushdoc one-time setup (PowerShell, called from setup.bat).
#
# Walks a non-technical user through everything needed before the first
# launch: Python venv, pip install, npm install, llama-server binary,
# and a default GGUF model. Each step is idempotent -- safe to re-run.
#
# Flags:
#   -GpuBuild  Download the CUDA 12.4 build of llama-server instead of the
#              CPU-only build. Only useful if you have an NVIDIA GPU AND
#              the matching CUDA runtime installed.
#   -Force     Re-download everything even if it already looks present.

param(
    [switch]$GpuBuild,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Speeds up Invoke-WebRequest by ~100x for large files. PowerShell 5.1's
# default progress UI is surprisingly expensive to render and dominates
# wall-clock time on multi-hundred-MB downloads.
$ProgressPreference = "SilentlyContinue"

function Write-Step($msg)  { Write-Host "[setup] $msg" -ForegroundColor Cyan }
function Write-Skip($msg)  { Write-Host "[setup] $msg" -ForegroundColor DarkGray }
function Write-Ok($msg)    { Write-Host "[setup] $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "[setup] $msg" -ForegroundColor Yellow }
function Write-Fail($msg)  { Write-Host "[setup] $msg" -ForegroundColor Red }

function Need-Tool($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Fail "$name is not on your PATH."
        Write-Host "         $hint"
        Write-Host ""
        Read-Host "Press Enter to close"
        exit 1
    }
}

# ===========================================================================
# Banner
# ===========================================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor DarkGray
Write-Host "  Hushdoc setup"                                              -ForegroundColor White
Write-Host "  This is a one-time install. Re-running is safe."            -ForegroundColor White
Write-Host "============================================================" -ForegroundColor DarkGray
Write-Host ""

# ===========================================================================
# 1. Python venv + pip install
# ===========================================================================
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if ((Test-Path $venvPython) -and (-not $Force)) {
    Write-Skip "Python venv already exists at .\.venv -- skipping create."
} else {
    Write-Step "Creating Python 3.12 virtual environment in .\.venv ..."
    # `py -3.12` is the Windows launcher's way of asking specifically for 3.12.
    # If 3.12 isn't installed the launcher will say so and we surface that.
    Need-Tool "py" "Install Python 3.12 from https://www.python.org/downloads/release/python-3120/ (tick `"Add to PATH`")."
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Could not create the venv. Make sure Python 3.12 is installed."
        Write-Host "         Try: py -3.12 --version"
        Read-Host "Press Enter to close"
        exit 1
    }
}

Write-Step "Installing Python dependencies (this can take a few minutes)..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install failed. Scroll up for the error."
    Read-Host "Press Enter to close"
    exit 1
}
Write-Ok "Python deps ready."
Write-Host ""

# ===========================================================================
# 2. npm install
# ===========================================================================
$webDir = Join-Path $root "web"
if ((Test-Path (Join-Path $webDir "node_modules")) -and (-not $Force)) {
    Write-Skip "web\node_modules already exists -- skipping npm install."
} else {
    Need-Tool "npm" "Install Node.js 20+ from https://nodejs.org/ (LTS is fine)."
    Write-Step "Installing frontend dependencies (one-time)..."
    Push-Location $webDir
    npm install
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0) {
        Write-Fail "npm install failed. Scroll up for the error."
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Ok "Frontend deps ready."
}
Write-Host ""

# ===========================================================================
# 3. llama-server binary
#
# Default: CPU-only build (works on every machine, no CUDA driver hassle).
# Qwen3-1.7B Q4_K_M is small enough that CPU inference is fine on a modern
# laptop. Pass -GpuBuild if you have an NVIDIA GPU and want CUDA 12.4
# acceleration -- you'll need the matching CUDA runtime installed too.
# ===========================================================================
$runtimeDir = Join-Path $root "runtime"
$serverExe = Join-Path $runtimeDir "llama-server.exe"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

if ((Test-Path $serverExe) -and (-not $Force)) {
    Write-Skip "runtime\llama-server.exe already exists -- skipping download."
} else {
    Write-Step "Looking up the latest llama.cpp release on GitHub..."
    try {
        $release = Invoke-RestMethod `
            -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" `
            -UseBasicParsing
    } catch {
        Write-Fail "Could not reach GitHub: $($_.Exception.Message)"
        Write-Host "         If you're behind a firewall, download a release from"
        Write-Host "         https://github.com/ggml-org/llama.cpp/releases manually,"
        Write-Host "         extract llama-server.exe to .\runtime\, and re-run this script."
        Read-Host "Press Enter to close"
        exit 1
    }

    # Patterns must keep the leading `llama-` so we don't accidentally pick
    # cudart-llama-bin-... which is just the CUDA runtime DLL bundle, not
    # the actual binaries.
    if ($GpuBuild) {
        # Match e.g. llama-bXXXX-bin-win-cuda-12.4-x64.zip
        $pattern = "llama-*-bin-win-cuda-*-x64.zip"
        $kind = "CUDA 12.x"
    } else {
        # Match e.g. llama-bXXXX-bin-win-cpu-x64.zip
        $pattern = "llama-*-bin-win-cpu-x64.zip"
        $kind = "CPU"
    }

    $asset = $release.assets | Where-Object { $_.name -like $pattern } | Select-Object -First 1
    if (-not $asset) {
        Write-Fail "Couldn't find a $kind Windows build in release $($release.tag_name)."
        Write-Host "         Asset names available:"
        $release.assets | ForEach-Object { Write-Host "           - $($_.name)" }
        Read-Host "Press Enter to close"
        exit 1
    }

    $zipPath = Join-Path $runtimeDir "llama-cpp.zip"
    Write-Step "Downloading $($asset.name) ($([math]::Round($asset.size/1MB,1)) MB) ..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

    Write-Step "Extracting llama-server.exe ..."
    $tmpExtract = Join-Path $runtimeDir "_extract"
    if (Test-Path $tmpExtract) { Remove-Item -Recurse -Force $tmpExtract }
    Expand-Archive -Path $zipPath -DestinationPath $tmpExtract -Force

    $found = Get-ChildItem -Path $tmpExtract -Recurse -Filter "llama-server.exe" |
        Select-Object -First 1
    if (-not $found) {
        Write-Fail "llama-server.exe not found inside the downloaded archive."
        Read-Host "Press Enter to close"
        exit 1
    }

    # Copy llama-server.exe AND every .dll alongside it (CUDA runtime,
    # ggml shared libs, etc.) into .\runtime so the binary can find them.
    $srcDir = $found.Directory.FullName
    Copy-Item -Path (Join-Path $srcDir "*") -Destination $runtimeDir -Recurse -Force

    Remove-Item -Recurse -Force $tmpExtract
    Remove-Item -Force $zipPath
    Write-Ok "llama-server installed at .\runtime\llama-server.exe ($kind build, $($release.tag_name))."
}
Write-Host ""

# ===========================================================================
# 4. Default model: Qwen3-1.7B Q4_K_M (~1 GB)
# ===========================================================================
$modelsDir = Join-Path $root "models"
$modelPath = Join-Path $modelsDir "model.gguf"
New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null

# https://huggingface.co/MaziyarPanahi/Qwen3-1.7B-GGUF
$modelUrl = "https://huggingface.co/MaziyarPanahi/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B.Q4_K_M.gguf"

if ((Test-Path $modelPath) -and (-not $Force)) {
    $sizeMB = [math]::Round(((Get-Item $modelPath).Length / 1MB), 1)
    Write-Skip "models\model.gguf already exists ($sizeMB MB) -- skipping download."
} else {
    Write-Step "Downloading default model: Qwen3-1.7B Q4_K_M (~1 GB)..."
    Write-Host "         Source: $modelUrl" -ForegroundColor DarkGray
    Write-Host "         This is the slow step. Get a coffee."             -ForegroundColor DarkGray
    try {
        Invoke-WebRequest -Uri $modelUrl -OutFile $modelPath -UseBasicParsing
    } catch {
        if (Test-Path $modelPath) { Remove-Item -Force $modelPath }
        Write-Fail "Model download failed: $($_.Exception.Message)"
        Write-Host "         You can download it manually from:"
        Write-Host "           https://huggingface.co/MaziyarPanahi/Qwen3-1.7B-GGUF"
        Write-Host "         and save it as .\models\model.gguf"
        Read-Host "Press Enter to close"
        exit 1
    }
    $sizeMB = [math]::Round(((Get-Item $modelPath).Length / 1MB), 1)
    Write-Ok "Model saved to .\models\model.gguf ($sizeMB MB)."
}
Write-Host ""

# ===========================================================================
# Done.
# ===========================================================================
Write-Host "============================================================" -ForegroundColor DarkGray
Write-Ok    "Setup complete!"
Write-Host  ""
Write-Host  "  Next step: double-click hushdoc.bat to launch the app."
Write-Host  ""
Write-Host  "  To swap the model later, replace .\models\model.gguf with"
Write-Host  "  any other .gguf file from HuggingFace (e.g. a larger Qwen,"
Write-Host  "  Llama, or Mistral build that fits your RAM)."
Write-Host  ""
Write-Host "============================================================" -ForegroundColor DarkGray
Read-Host "Press Enter to close"
