@echo off
REM Trading Strategy Layer — install dependencies
REM Usage: scripts\install.bat

echo [Trading Strategy Layer] Installing dependencies...
pip install -r "%~dp0..\requirements.txt"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.
echo Next: scripts\run.bat
pause
