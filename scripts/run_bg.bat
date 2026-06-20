@echo off
REM Trading Strategy Layer -- start server in BACKGROUND (detached)
REM Usage: scripts\run_bg.bat [port] [host]
REM Port defaults to config.yaml -> server.port
REM Logs:  server.log / server.err

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

REM -- Locate python --
set "PYTHON_EXE="
for /f "tokens=*" %%P in ('where python 2^>nul') do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)
if "%PYTHON_EXE%"=="" (
    echo [ERROR] python.exe not found
    pause
    exit /b 1
)

REM -- Kill existing uvicorn on port --
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:":%PORT% " ^| findstr /c:"LISTENING"') do (
    echo [INFO] Killing old process PID %%P on port %PORT% ...
    taskkill /f /pid %%P >nul 2>&1
    timeout /t 1 /nobreak >nul
)

set PYTHONPATH=%PYTHONPATH%;%ROOT%

echo.
echo ========================================
echo   Trading Strategy Layer
echo   Host: %HOST%:%PORT%
echo   API:  http://%HOST%:%PORT%/docs
echo   Mode: BACKGROUND (detached)
echo   Log:  server.log
echo   Err:  server.err
echo ========================================
echo.

REM -- Start uvicorn as hidden background process --
REM PowerShell Start-Process -WindowStyle Hidden creates a process that
REM survives the cmd window being closed.
powershell -NoProfile -Command "Start-Process -FilePath '%PYTHON_EXE%' -WindowStyle Hidden -ArgumentList '-m uvicorn strategy_layer.main:app --host %HOST% --port %PORT%' -RedirectStandardOutput '%ROOT%\server.log' -RedirectStandardError '%ROOT%\server.err'"

REM -- Health check (poll /api/health every 1s, max 15s) --
echo [INFO] Waiting for backend to start ...
set "HEALTH_URL=http://%HOST%:%PORT%/api/health"
for /l %%i in (1,1,15) do (
    timeout /t 1 /nobreak >nul
    powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%HEALTH_URL%' -UseBasicParsing -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
    if not errorlevel 1 goto :READY
)

echo [ERROR] Backend failed to respond within 15s
echo         Check server.err for details
goto :SHOW_ERR

:READY
REM -- Show PID --
set "FINAL_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:":%PORT% " ^| findstr /c:"LISTENING"') do set "FINAL_PID=%%P"
echo [OK] Backend is running (PID !FINAL_PID!)
echo.
echo   URL:  http://%HOST%:%PORT%/
echo   API:  http://%HOST%:%PORT%/docs
echo.
echo To stop:  taskkill /pid !FINAL_PID! /f
echo Or run:   scripts\run_bg.bat (kills old first)
goto :END

:SHOW_ERR
echo.
echo Last 20 lines of server.err:
echo --- server.err ---
powershell -NoProfile -Command "Get-Content -LiteralPath '%ROOT%\server.err' -Tail 20 -Encoding UTF8"
echo.

:END
