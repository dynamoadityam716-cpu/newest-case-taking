@echo off
rem SIH26047 "new" (redesign) — starts the local server on port 8001 and opens the app.
cd /d "%~dp0"
powershell -Command "Start-Sleep -Seconds 1; Start-Process 'http://localhost:8001'"
node server.js
echo.
echo If the browser did not open, go to http://localhost:8001
echo Close this black window to stop the app.
pause