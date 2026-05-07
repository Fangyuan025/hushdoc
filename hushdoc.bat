@echo off
REM Hushdoc one-click launcher.
REM
REM Double-click this file (or run it from Explorer / cmd) to start the
REM full local stack: FastAPI backend on :8000, Vite frontend on :5173,
REM and llama-server.exe on :8765 (auto-spawned on first chat).
REM
REM When you press Ctrl+C or close the window, every spawned process is
REM stopped cleanly and you'll be asked whether to wipe local data
REM (conversations, uploads, vector index).

setlocal
set "ROOT=%~dp0"
pushd "%ROOT%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%hushdoc.ps1" %*
set "EC=%ERRORLEVEL%"

popd
endlocal & exit /b %EC%
