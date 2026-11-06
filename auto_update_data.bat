@echo off
chcp 65001 > nul

set "PYTHON_EXE_PATH=C:\Users\xuzhi\AppData\Local\anaconda3\envs\tracking_env\python.exe"
set "PYTHON_SCRIPT_PATH=C:\Users\xuzhi\TrackingNotif\dataTransfer\data2Json.py"
set "JSON_FILE_PATH=C:\Users\xuzhi\TrackingNotif\frontend\data\data.json"
set "SERVER_TARGET_PATH=\\intweb.itu.int\intwebroot\ITU-R\space\css\tracking\data"
set "LOG_FILE_PATH=C:\Users\xuzhi\TrackingNotif\dataTransfer\update_log.txt"

echo ===================================== >> "%LOG_FILE_PATH%"
echo %date% %time% - start working on updating the data >> "%LOG_FILE_PATH%"

echo %date% %time% - Start to run Python... >> "%LOG_FILE_PATH%"
"%PYTHON_EXE_PATH%" "%PYTHON_SCRIPT_PATH%" >> "%LOG_FILE_PATH%" 2>&1

echo %date% %time% - Start to run Python... >> "%LOG_FILE_PATH%"
python "%PYTHON_SCRIPT_PATH%" >> "%LOG_FILE_PATH%" 2>&1
if errorlevel 1 (
    echo %date% %time% - Running Python failed... >> "%LOG_FILE_PATH%"
    pause
    exit /b 1
)

if not exist "%JSON_FILE_PATH%" (
    echo %date% %time% - Did not find the generated data.json file... >> "%LOG_FILE_PATH%"
    pause
    exit /b 1
)

echo %date% %time% - Start to upload data.json to server... >> "%LOG_FILE_PATH%"
xcopy "%JSON_FILE_PATH%" "%SERVER_TARGET_PATH%" /y /d >> "%LOG_FILE_PATH%" 2>&1
if errorlevel 1 (
    echo %date% %time% - Files uploading failed... >> "%LOG_FILE_PATH%"
    pause
    exit /b 1
)


echo %date% %time% - data updated and uploaded! >> "%LOG_FILE_PATH%"
echo ===================================== >> "%LOG_FILE_PATH%"
echo. >> "%LOG_FILE_PATH%"

exit /b 0