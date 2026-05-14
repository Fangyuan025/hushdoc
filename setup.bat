@echo off
REM Hushdoc one-time setup. Double-click this once after cloning the repo.
REM
REM Walks through the four things you'd otherwise have to do by hand:
REM   1. Create the Python venv and install dependencies
REM   2. npm install for the React frontend
REM   3. Download llama-server.exe (the local LLM runtime)
REM   4. Download a default model (Qwen3-1.7B Q4_K_M, ~1 GB)
REM
REM Safe to re-run: every step skips itself if the work is already done.
REM After this finishes, run hushdoc.bat to start the app.

setlocal
set "ROOT=%~dp0"
pushd "%ROOT%"

REM Switch cmd's codepage to UTF-8 so the banner's 🤫 emoji renders.
chcp 65001 >nul 2>&1

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup.ps1" %*
set "EC=%ERRORLEVEL%"

popd
endlocal & exit /b %EC%
