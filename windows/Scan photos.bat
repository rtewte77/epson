@echo off
rem Double-click to start scanning. Edit these two lines once:
set IMMICH_URL=http://192.168.1.10:2283
set IMMICH_API_KEY=paste-your-api-key-here

rem Photos are also kept here on this PC:
set OUT=%USERPROFILE%\Pictures\Scanned photos

rem Close Epson Scan before starting: only one program can use the scanner at a time.
py -3 -m photopilot scan --out "%OUT%" --dpi 600
pause
