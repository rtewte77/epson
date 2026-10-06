@echo off
rem One-time setup for photo-pilot on Windows.
rem Needs Python 3.9+ from python.org (tick "Add python.exe to PATH") or: winget install Python.Python.3.12
cd /d "%~dp0\.."
py -3 --version >nul 2>&1 || (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and run this again.
  pause & exit /b 1
)
py -3 -m pip install --upgrade pip
py -3 -m pip install . || (echo Install failed. & pause & exit /b 1)
echo.
echo Installed. Now edit "windows\Scan photos.bat" once: put in your Immich address and API key.
echo Then make sure the scanner shows up here:
py -3 -m photopilot devices
pause
