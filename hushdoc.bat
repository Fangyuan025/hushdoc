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

REM NOTE: we deliberately do NOT `chcp 65001` here. Switching the
REM cmd codepage in legacy conhost.exe forces a font swap (raster ->
REM TrueType) which makes every glyph render visibly smaller -- the
REM 'why did my terminal text shrink?' complaint. The .ps1 below
REM still sets [Console]::OutputEncoding = UTF8 so banner emoji
REM bytes flow correctly into modern terminals (Windows Terminal /
REM PS 7+ / Linux / macOS). Legacy conhost falls back to '?' for
REM the 🤫 emoji, which is a much better failure mode than tiny text.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%hushdoc.ps1" %*
set "EC=%ERRORLEVEL%"

popd
endlocal & exit /b %EC%
