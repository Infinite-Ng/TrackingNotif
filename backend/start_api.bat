@echo off

:: ── Auto-elevate to Administrator if needed ──────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo =====================================================
echo  ITU Notice Tracking API Server (HTTPS port 5001)
echo  Running as Administrator
echo =====================================================
cd /d "%~dp0"
if not exist cert.pem python generate_cert.py

:: Add firewall rule for port 5001 if not already present
netsh advfirewall firewall show rule name="ITU TrackingNotif API Port 5001" >nul 2>&1
if errorlevel 1 (
    echo Adding firewall rule for port 5001...
    netsh advfirewall firewall add rule name="ITU TrackingNotif API Port 5001" dir=in action=allow protocol=TCP localport=5001
)

echo.
echo Starting HTTPS API on https://156.106.168.185:5001
echo Open that URL in browser once and click Advanced - Proceed to trust the cert.
echo.

:: ── Kill any process already holding port 5001 ───────────────────────────
echo Checking if port 5001 is in use...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R "\<5001\>" ^| findstr LISTENING') do (
    echo Port 5001 is occupied by PID %%P. Terminating...
    taskkill /PID %%P /F >nul 2>&1
    if errorlevel 1 (
        echo   Warning: Could not kill PID %%P. It may have already exited.
    ) else (
        echo   PID %%P terminated successfully.
    )
)
echo Port 5001 is now free. Starting API server...
echo.

python api.py
pause
