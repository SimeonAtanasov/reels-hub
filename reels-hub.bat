@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 reels_hub.py %* & goto :end)
where python >nul 2>nul && (python reels_hub.py %* & goto :end)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" reels_hub.py %* & goto :end)
echo Python was not found. Run install.bat first.
:end
pause
