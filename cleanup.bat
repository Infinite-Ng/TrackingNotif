@echo off
echo ============================================
echo   TrackingNotif - Port 5001 Cleanup
echo ============================================
echo.

set PORT=5001

echo [1] Looking for processes on port %PORT%...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%.*LISTENING"') do (
    set PID=%%a
    echo     Found PID: %%a
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo     [OK] Killed PID: %%a
    ) else (
        echo     [FAIL] Could not kill PID: %%a
    )
)

echo.
echo [2] Cleaning up remaining python/waitress processes...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM waitress-serve.exe >nul 2>&1

echo.
echo [3] Verifying port %PORT% is free...
netstat -ano | findstr ":%PORT%.*LISTENING" >nul 2>&1
if errorlevel 1 (
    echo     [OK] Port %PORT% is now free.
) else (
    echo     [WARN] Port %PORT% may still be in use.
)

echo.
echo Done. You can now restart the server.
pause
