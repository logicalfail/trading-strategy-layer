@echo off
REM Trading Strategy Layer -- start server (foreground)
REM Usage: scripts\run.bat [port] [host]
REM Port defaults to config.yaml -> server.port

setlocal enabledelayedexpansion
cd /d "%~dp0\.."
set "ROOT=%CD%"

REM -- Read port from config.yaml --
set "PORT="
for /f "tokens=*" %%a in ('python "%~dp0get_port.py" 2^>nul') do set "PORT=%%a"
if "%PORT%"=="" set "PORT=8004"

REM -- Override from CLI args --
if not "%1"=="" set "PORT=%1"

REM -- Host --
set "HOST=127.0.0.1"
if not "%2"=="" set "HOST=%2"

set PYTHONPATH=%PYTHONPATH%;%ROOT%

REM -- Kill existing uvicorn on port --
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:":%PORT% " ^| findstr /c:"LISTENING"') do (
    echo [INFO] Killing old process PID %%P on port %PORT% ...
    taskkill /f /pid %%P >nul 2>&1
    timeout /t 1 /nobreak >nul
)

echo.
echo ========================================
echo   Trading Strategy Layer
echo   Host: %HOST%:%PORT%
echo   API:  http://%HOST%:%PORT%/docs
echo ========================================
echo.

python -m uvicorn strategy_layer.main:app --host %HOST% --port %PORT%
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Server failed to start
    pause
    exit /b 1
)
