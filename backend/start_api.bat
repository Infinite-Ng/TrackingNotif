@echo off
chcp 65001 >nul
set FLASK_DEBUG=0
set FLASK_ENV=production
set FLASK_APP=
cd /d "%~dp0"
if not exist cert.pem (
    echo cert.pem not found - generating SSL certificate...
    python generate_cert.py
    echo.
)
echo Starting ITU Notice Tracking API Server on port 5001 (HTTPS)...
echo.
python api.py
pause
