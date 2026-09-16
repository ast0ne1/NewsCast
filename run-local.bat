@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PORT=8080"
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="PORT" set "PORT=%%B"
    )
)
set "PORT=%PORT: =%"

if not exist ".venv\Scripts\python.exe" (
    echo Create the virtualenv first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

set "FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        echo Stopping PID %%P on port %PORT%...
        taskkill /PID %%P /F /T >nul 2>&1
        set "FOUND=1"
    )
)

if "!FOUND!"=="1" (
    echo Waiting for the port to free...
    timeout /t 2 /nobreak >nul
) else (
    echo No existing listener on port %PORT%.
)

echo Starting NewsCast at http://127.0.0.1:%PORT% (HTTPS if enabled in Settings)
".venv\Scripts\python.exe" -m app.serve
endlocal
