@echo off
rem Use this instead of "Scan photos.bat" if you prefer scanning with Epson Scan itself.
rem In Epson Scan: Document Type = Reflective / Photo, Document Source = Scanner Glass,
rem select the WHOLE glass (no auto crop), 600 dpi, save as TIFF or JPEG into the folder below.
set IMMICH_URL=http://192.168.1.10:2283
set IMMICH_API_KEY=paste-your-api-key-here
set WATCH=%USERPROFILE%\Pictures\Epson Scan
set OUT=%USERPROFILE%\Pictures\Scanned photos

if not exist "%WATCH%" mkdir "%WATCH%"
py -3 -m photopilot watch "%WATCH%" --out "%OUT%"
pause
