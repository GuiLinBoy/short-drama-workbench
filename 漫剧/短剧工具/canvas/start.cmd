@echo off
setlocal
cd /d "%~dp0"
if not exist "dist\index.html" (
  echo Build missing. Run npm ci --ignore-scripts and npm run build first.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8778/ in your browser.
echo Keep this window open. Press Ctrl+C to stop.
python -I -X utf8 server.py --port 8778 %*
if errorlevel 1 pause
