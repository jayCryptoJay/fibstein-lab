@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 launch.py
) else (
  python launch.py
)
if errorlevel 1 (
  echo.
  echo Startup failed. Install Python 3.12 or newer from python.org, then try again.
  pause
)
