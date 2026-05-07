# Hushdoc one-time setup (PowerShell, called from setup.bat).
#
# Walks a non-technical user through everything needed before the first
# launch: Python venv, pip install, npm install, llama-server binary,
# and a default GGUF model. Each step is idempotent -- safe to re-run.
#
# llama.cpp build selection (no flags, default behavior):
#   - If `nvidia-smi` is on PATH and reports a working GPU, download the
#     CUDA 12.4 build of llama-server PLUS the matching cudart bundle so
#     the binary has every DLL it needs to start.
#   - Otherwise, fall back to the CPU build (~15 MB) which works on every
#     machine but is slower for big models.
#
# Flags:
#   -Cpu       Force the CPU build even if an NVIDIA GPU is detected.
#              Use this if your CUDA install is broken or you just want
#              a smaller download.
#   -GpuBuild  Force the CUDA build even if nvidia-smi is missing.
#              Useful when CUDA is installed but nvidia-smi is not on PATH.
#   -Force     Re-download the llama-server runtime AND the GGUF model
#              even if they're already on disk. (Does NOT recreate the
#              venv or re-run npm install -- those are idempotent.)

param(
    [switch]$Cpu,
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

# -Force does NOT recreate the venv: it would be destructive (deleting any
# manually-installed local packages) AND slow, and pip install is already
# idempotent for the requirements.txt path below.
if (Test-Path $venvPython) {
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
if (Test-Path (Join-Path $webDir "node_modules")) {
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
# Auto-pick CUDA build if an NVIDIA GPU is present (much faster), else CPU.
# `-Cpu` and `-GpuBuild` flags override the detection.
#
# For the CUDA path we ALSO download the matching cudart bundle and unpack
# it next to llama-server.exe -- the binary loads cudart64_*.dll at startup
# and most users don't have those system-wide unless they installed CUDA
# Toolkit.
# ===========================================================================
$runtimeDir = Join-Path $root "runtime"
$serverExe = Join-Path $runtimeDir "llama-server.exe"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

# --- Decide CPU vs GPU ---
function Test-NvidiaGpu {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) { return $false }
    & nvidia-smi -L *> $null
    return ($LASTEXITCODE -eq 0)
}

if ($Cpu) {
    $useGpu = $false
    $kind = "CPU (forced)"
} elseif ($GpuBuild) {
    $useGpu = $true
    $kind = "CUDA 12.x (forced)"
} elseif (Test-NvidiaGpu) {
    $useGpu = $true
    $kind = "CUDA 12.x (auto-detected NVIDIA GPU)"
    Write-Step "NVIDIA GPU detected via nvidia-smi -- using the CUDA build."
} else {
    $useGpu = $false
    $kind = "CPU (no NVIDIA GPU detected)"
    Write-Step "No NVIDIA GPU detected -- using the CPU build."
    Write-Host "         If you DO have an NVIDIA GPU, re-run with -GpuBuild." -ForegroundColor DarkGray
}

if ((Test-Path $serverExe) -and (-not $Force)) {
    Write-Skip "runtime\llama-server.exe already exists -- skipping download. ($kind)"
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
    if ($useGpu) {
        # Match e.g. llama-bXXXX-bin-win-cuda-12.4-x64.zip
        # Prefer 12.4 specifically (broadest compat); fall back to any cuda.
        $binPattern = "llama-*-bin-win-cuda-12.4-x64.zip"
        $cudartPattern = "cudart-llama-bin-win-cuda-12.4-x64.zip"
    } else {
        # Match e.g. llama-bXXXX-bin-win-cpu-x64.zip
        $binPattern = "llama-*-bin-win-cpu-x64.zip"
        $cudartPattern = $null
    }

    $binAsset = $release.assets | Where-Object { $_.name -like $binPattern } | Select-Object -First 1
    if (-not $binAsset -and $useGpu) {
        # 12.4 not present in this release -- try any cuda variant.
        $binAsset = $release.assets | Where-Object { $_.name -like "llama-*-bin-win-cuda-*-x64.zip" } | Select-Object -First 1
    }
    if (-not $binAsset) {
        Write-Fail "Couldn't find a matching Windows build in release $($release.tag_name)."
        Write-Host "         Asset names available:"
        $release.assets | ForEach-Object { Write-Host "           - $($_.name)" }
        Read-Host "Press Enter to close"
        exit 1
    }

    function Download-And-Unpack($url, $name, $sizeBytes) {
        $zipPath = Join-Path $runtimeDir $name
        $tmpExtract = Join-Path $runtimeDir "_extract"
        Write-Step "Downloading $name ($([math]::Round($sizeBytes/1MB,1)) MB) ..."
        Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
        Write-Step "Extracting $name ..."
        if (Test-Path $tmpExtract) { Remove-Item -Recurse -Force $tmpExtract }
        Expand-Archive -Path $zipPath -DestinationPath $tmpExtract -Force
        # Copy every file that landed in the extract dir (and its subdirs)
        # into .\runtime, flattening only the top archive directory.
        $srcRoot = Get-ChildItem -Path $tmpExtract -Directory | Select-Object -First 1
        if (-not $srcRoot) { $srcRoot = Get-Item $tmpExtract }
        Copy-Item -Path (Join-Path $srcRoot.FullName "*") -Destination $runtimeDir -Recurse -Force
        Remove-Item -Recurse -Force $tmpExtract
        Remove-Item -Force $zipPath
    }

    Download-And-Unpack $binAsset.browser_download_url $binAsset.name $binAsset.size

    if ($useGpu) {
        $cudartAsset = $release.assets | Where-Object { $_.name -like $cudartPattern } | Select-Object -First 1
        if (-not $cudartAsset) {
            $cudartAsset = $release.assets | Where-Object { $_.name -like "cudart-llama-bin-win-cuda-*-x64.zip" } | Select-Object -First 1
        }
        if ($cudartAsset) {
            Download-And-Unpack $cudartAsset.browser_download_url $cudartAsset.name $cudartAsset.size
        } else {
            Write-Warn "cudart bundle not found in this release. If llama-server fails to start with"
            Write-Warn "a 'missing cudart64_*.dll' error, install CUDA Toolkit 12.x or pass -Cpu."
        }
    }

    if (-not (Test-Path $serverExe)) {
        Write-Fail "llama-server.exe not found inside the downloaded archive."
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Ok "llama-server installed at .\runtime\llama-server.exe ($kind, $($release.tag_name))."
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
