@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

set "OUT=%cd%\dist\NewsCast-pi"
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%\app"
mkdir "%OUT%\deploy"

xcopy /e /i /q /y "app" "%OUT%\app" >nul
xcopy /e /i /q /y "deploy" "%OUT%\deploy" >nul
if exist "%OUT%\deploy\export-pi.bat" del /q "%OUT%\deploy\export-pi.bat"
copy /y "requirements.txt" "%OUT%\" >nul
copy /y ".env.example" "%OUT%\" >nul
copy /y "README.md" "%OUT%\" >nul
copy /y "INSTALL.md" "%OUT%\" >nul

echo Ready to copy to the Pi:
echo   %OUT%
echo.
echo On the Pi:
echo   cd /path/to/NewsCast-pi
echo   sudo ./deploy/install.sh --hostname newscast
endlocal
