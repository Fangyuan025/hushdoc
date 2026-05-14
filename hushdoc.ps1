# Hushdoc one-click launcher (PowerShell, called from start.bat).
#
# 1. Validate the venv + node_modules; auto-run `npm install` if missing.
# 2. Spawn FastAPI (uvicorn) on :8000 and Vite on :5173 as child processes.
# 3. Wait for both to bind their ports, then open the default browser.
# 4. Block until the user presses Ctrl+C OR either child exits.
# 5. Kill every descendant including the lazy-spawned llama-server.exe.
# 6. Prompt the user whether to wipe chat_history / data/uploads /
#    chroma_db, then exit. Each category is a separate yes/no so the
#    user can keep, e.g., uploaded documents but clear conversations.
#
#   .\start.ps1            # default
#   .\start.ps1 -NoOpen    # skip auto-open of the browser

param([switch]$NoOpen)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Force UTF-8 on the console so the banner's 🤫 emoji renders as a glyph
# instead of a literal "?" on PowerShell 5.1 / classic conhost. Has to
# happen BEFORE any Write-Host call that contains a non-ASCII char.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Step($msg) { Write-Host "[hushdoc] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[hushdoc] $msg" -ForegroundColor Yellow }
function Write-Ok($msg)   { Write-Host "[hushdoc] $msg" -ForegroundColor Green }

# Slant-figlet "HUSHDOC" + 🤫 tagline. Same banner used by setup.ps1 /
# dev.ps1 / dev.sh; keep the art identical across them so the user sees
# a consistent identity card no matter which entry point they hit.
function Show-HushdocBanner {
    param([string]$Tagline = "chat with your documents -- privately")
    # ConvertFromUtf32 dodges the PS 5.1 file-encoding trap where a raw
    # UTF-8 emoji byte sequence in a .ps1 saved without BOM gets read
    # as Windows-1252 and prints as "ð¤«".
    $emoji = [char]::ConvertFromUtf32(0x1F92B)  # 🤫 shushing face
    $version = "dev"
    try {
        $vfile = Join-Path $root "VERSION"
        if (Test-Path $vfile) {
            $version = (Get-Content $vfile -Raw -Encoding UTF8).Trim()
        }
    } catch { }
    Write-Host ""
    Write-Host "    __  __           __         __          " -ForegroundColor Cyan
    Write-Host "   / / / /_  _______/ /_  ____/ /___  _____ " -ForegroundColor Cyan
    Write-Host "  / /_/ / / / / ___/ __ \/ __  / __ \/ ___/ " -ForegroundColor Cyan
    Write-Host " / __  / /_/ (__  ) / / / /_/ / /_/ / /__   " -ForegroundColor Cyan
    Write-Host "/_/ /_/\__,_/____/_/ /_/\__,_/\____/\___/   " -ForegroundColor Cyan
    Write-Host ""
    Write-Host "       $emoji  $Tagline" -ForegroundColor White
    Write-Host "          local-only - offline - your machine - v$version" -ForegroundColor DarkGray
    Write-Host ""
}

Show-HushdocBanner

# ---------------------------------------------------------------------------
# 0. Sanity check the environment.
# ---------------------------------------------------------------------------
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host ""
    Write-Host "[hushdoc] ERROR: Python venv not found at $venvPython" -ForegroundColor Red
    Write-Host "         Create it with:" -ForegroundColor Red
    Write-Host "           py -3.12 -m venv .venv"
    Write-Host "           .venv\Scripts\pip install -r requirements.txt"
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

$webDir = Join-Path $root "web"
if (-not (Test-Path (Join-Path $webDir "node_modules"))) {
    Write-Warn "web\node_modules missing -- running 'npm install' (one-time)..."
    Push-Location $webDir
    npm install
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-Host "[hushdoc] ERROR: npm install failed." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
    Pop-Location
}

# ---------------------------------------------------------------------------
# 1. Spawn backend + frontend.
# ---------------------------------------------------------------------------
Write-Step "starting FastAPI backend on http://localhost:8000 ..."
$backend = Start-Process -FilePath $venvPython `
    -ArgumentList "-m","uvicorn","server.main:app","--port","8000" `
    -WorkingDirectory $root -PassThru -NoNewWindow

# `npm run dev` on Windows is actually npm.cmd, which spawns a node child;
# we stash both PIDs so cleanup can reach the real Vite process via the
# job tree later.
Write-Step "starting Vite frontend on http://localhost:5173 ..."
$frontend = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c","npm run dev" `
    -WorkingDirectory $webDir -PassThru -NoNewWindow

# ---------------------------------------------------------------------------
# 2. Wait for both ports to bind, then open the browser.
# ---------------------------------------------------------------------------
function Wait-ForPort([int]$port, [int]$timeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $listening = Get-NetTCPConnection -State Listen `
            -LocalPort $port -ErrorAction SilentlyContinue
        if ($listening) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

if (-not (Wait-ForPort 5173 60)) {
    Write-Warn "Vite did not come up within 60s; continuing anyway."
}
if (-not (Wait-ForPort 8000 60)) {
    Write-Warn "FastAPI did not come up within 60s; continuing anyway."
}

if (-not $NoOpen) {
    Write-Ok "opening http://localhost:5173 in your default browser"
    Start-Process "http://localhost:5173/"
}

Write-Host ""
Write-Host "  --[ running ]------------------------------------" -ForegroundColor DarkGray
Write-Host "    backend  http://localhost:8000" -ForegroundColor Gray
Write-Host "    web      http://localhost:5173" -ForegroundColor Gray
Write-Host "    stop     Ctrl+C  (or close this window)"      -ForegroundColor Gray
Write-Host "  -------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# 3. Block until either child exits or the user hits Ctrl+C.
#    (Ctrl+C lands in `finally` because we wired Stop on the cmdlet level.)
# ---------------------------------------------------------------------------
try {
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Milliseconds 500
    }
    if ($backend.HasExited) {
        Write-Warn "backend exited (code=$($backend.ExitCode)); shutting down."
    } elseif ($frontend.HasExited) {
        Write-Warn "frontend exited (code=$($frontend.ExitCode)); shutting down."
    }
} finally {
    # -----------------------------------------------------------------------
    # 4. Kill every descendant. Order matters:
    #    a) terminate vite/uvicorn process trees by PID,
    #    b) sweep llama-server.exe (spawned lazily by the chain),
    #    c) sweep any orphan node.exe / python.exe that match our cwd.
    # -----------------------------------------------------------------------
    Write-Host ""
    Write-Step "stopping..."

    function Stop-ProcessTree([int]$pid_) {
        try {
            $children = Get-CimInstance Win32_Process `
                -Filter "ParentProcessId=$pid_" -ErrorAction SilentlyContinue
            foreach ($c in $children) { Stop-ProcessTree $c.ProcessId }
            Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
        } catch { }
    }

    foreach ($p in @($frontend, $backend)) {
        if ($p) { Stop-ProcessTree $p.Id }
    }

    # llama-server.exe was spawned with DETACHED_PROCESS so it isn't a
    # descendant of either tracked PID -- sweep by name.
    Get-Process -Name "llama-server" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue

    Write-Ok "all processes stopped."

    # -----------------------------------------------------------------------
    # 5. Cleanup. By default we prompt per category. If the user has
    #    flipped "Auto-cleanup on exit" in Settings (persisted in
    #    hushdoc_config.json), we just wipe everything silently and
    #    let the window close -- no prompts.
    # -----------------------------------------------------------------------
    $autoCleanup = $false
    try {
        $cfgFile = Join-Path $root "hushdoc_config.json"
        if (Test-Path $cfgFile) {
            $cfg = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfg.auto_cleanup_on_exit) { $autoCleanup = $true }
        }
    } catch {
        # Corrupt / unreadable config -> default to safe prompt behavior.
    }

    function Wipe-Path($label, $path) {
        if (-not (Test-Path $path)) { return }
        $items = Get-ChildItem -LiteralPath $path -Force `
            -ErrorAction SilentlyContinue
        if (-not $items -or $items.Count -eq 0) { return }
        try {
            $items | Remove-Item -Recurse -Force -ErrorAction Stop
            Write-Ok "$label cleared."
        } catch {
            Write-Warn "could not fully clear $label : $($_.Exception.Message)"
        }
    }

    function Confirm-Cleanup($label, $path) {
        if (-not (Test-Path $path)) { return }
        $items = Get-ChildItem -LiteralPath $path -Force `
            -ErrorAction SilentlyContinue
        if (-not $items -or $items.Count -eq 0) { return }
        Write-Host ""
        $ans = Read-Host "Delete $label ($path, $($items.Count) item(s))? [y/N]"
        if ($ans -match '^(y|yes)$') {
            Wipe-Path $label $path
        }
    }

    $convPath = Join-Path $root "chat_history"
    $upPath   = Join-Path $root "data\uploads"
    $chPath   = Join-Path $root "chroma_db"

    if ($autoCleanup) {
        Write-Host ""
        Write-Step "Auto-cleanup is on (Settings) -- wiping local data without prompts."
        Wipe-Path "conversations"            $convPath
        Wipe-Path "uploaded documents"       $upPath
        Wipe-Path "vector index + summary cache" $chPath
    } else {
        Write-Host ""
        Write-Host "  --[ cleanup ]------------------------------------" -ForegroundColor DarkGray
        Write-Host "    answer y to wipe, anything else keeps" -ForegroundColor Gray
        Write-Host "    (toggle 'Auto-cleanup on exit' in Settings to skip)" -ForegroundColor DarkGray
        Write-Host "  -------------------------------------------------" -ForegroundColor DarkGray

        Confirm-Cleanup "conversations"            $convPath
        Confirm-Cleanup "uploaded documents"       $upPath
        Confirm-Cleanup "vector index + summary cache" $chPath
    }

    Write-Host ""
    Write-Ok "goodbye."
    # Auto-cleanup users want the window gone the second cleanup is
    # done -- no "press a key" gate, no final pause. For the prompt
    # path we wait briefly so users launched via double-click can
    # actually read the final messages before the window auto-closes.
    if (-not $autoCleanup) {
        Start-Sleep -Seconds 1
    }
}
