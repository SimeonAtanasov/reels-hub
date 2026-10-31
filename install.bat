@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Reels Hub - install
echo.
echo  ============================================
echo   Reels Hub - one-time install
echo  ============================================
echo  This installs Python, ffmpeg and Deno (if missing), the Python packages,
echo  and puts a "Reels Hub" shortcut on your desktop. It can take 10-20 minutes.
echo.

where winget >nul 2>nul
if errorlevel 1 (
  echo  winget is missing. Update "App Installer" from the Microsoft Store, then run this again.
  pause & exit /b 1
)

rem ---- Python ------------------------------------------------------------
call :findpy
if not defined PY (
  echo  [1/5] Installing Python 3.12 ...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  call :findpy
)
if not defined PY (
  echo.
  echo  Python was installed but Windows needs a fresh window to find it.
  echo  Close this window and double-click install.bat again.
  pause & exit /b 1
)
echo  [1/5] Python: !PY!

rem ---- ffmpeg ------------------------------------------------------------
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo  [2/5] Installing ffmpeg ...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
) else (
  echo  [2/5] ffmpeg: already installed
)

rem ---- Deno (YouTube needs a JavaScript runtime) -------------------------
where deno >nul 2>nul
if errorlevel 1 (
  echo  [3/5] Installing Deno ...
  winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements
) else (
  echo  [3/5] Deno: already installed
)

rem ---- Python packages ---------------------------------------------------
echo  [4/5] Installing Python packages (the biggest step) ...
!PY! -m pip install --upgrade pip
!PY! -m pip install --upgrade yt-dlp faster-whisper easyocr
if errorlevel 1 (
  echo  Package install failed - check the internet connection and run install.bat again.
  pause & exit /b 1
)

rem ---- Desktop shortcut --------------------------------------------------
echo  [5/5] Creating the desktop shortcut ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Reels Hub.lnk')); $s.TargetPath='%~dp0reels-hub.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\imageres.dll,18'; $s.Save()"

echo.
echo  Done! Double-click "Reels Hub" on the desktop to start.
echo  The first start asks for your name and folders. The first video takes
echo  a few extra minutes while the speech and text models download.
echo.
pause
exit /b 0

:findpy
set "PY="
where py >nul 2>nul && (py -3 -c "import sys" >nul 2>nul && set "PY=py -3")
if defined PY exit /b 0
where python >nul 2>nul && (python -c "import sys; assert sys.version_info>=(3,10)" >nul 2>nul && set "PY=python")
if defined PY exit /b 0
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe") do (
  if exist %%P set PY="%%~P"
)
exit /b 0
