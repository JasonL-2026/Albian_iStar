@echo off
REM ============================================================
REM  Albian Mine Haulage Dashboard - one-click updater (Windows)
REM  Double-click after refreshing the CSVs in Data\ and Budget\.
REM  Regenerates Haulage_Dashboard.html. No admin rights needed.
REM
REM  Usage:
REM    Double-click (or run with no args) — build once and exit.
REM    Run with /watch                    — rebuild every 5 minutes.
REM    Run with /auto                     — build once, no "press any key" pause
REM                                         (used by Windows Task Scheduler).
REM
REM  Cron-equivalent via Task Scheduler:
REM    schtasks /create /tn "AlbianDashboard" /tr "\"<path>\Update Dashboard.bat\" /auto" /sc MINUTE /mo 5 /f
REM
REM  If it can't find Python: open build_dashboard_windows.py in
REM  VS Code, run   import sys; print(sys.executable)   copy the
REM  path it prints, and paste it into a text file named
REM  python_path.txt in this same folder. Then run this again.
REM ============================================================
setlocal
cd /d "%~dp0"
REM Pass /auto (used by Task Scheduler) to skip the "press any key" prompts.
REM Pass /watch to rebuild every 5 minutes in a loop.
set "NOPAUSE="
set "WATCHMODE="
if /i "%~1"=="/auto" set "NOPAUSE=1"
if /i "%~1"=="/watch" set "WATCHMODE=1"

echo.
echo Updating Albian Mine Haulage Dashboard...
echo Folder: %~dp0
echo.

set "PYEXE="

REM 1) Manual override: full path to python.exe saved in python_path.txt
if exist "%~dp0python_path.txt" set /p PYEXE=<"%~dp0python_path.txt"
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="

REM 2) Windows 'py' launcher (works even when 'python' is not on PATH)
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py"

REM 3) 'python3' on PATH
if not defined PYEXE where python3 >nul 2>nul && set "PYEXE=python3"

REM 4) Common install locations
if not defined PYEXE (
  for %%P in (
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "%UserProfile%\Anaconda3\python.exe"
    "%UserProfile%\Miniconda3\python.exe"
    "%ProgramData%\Anaconda3\python.exe"
  ) do if not defined PYEXE if exist %%P set "PYEXE=%%~P"
)

if not defined PYEXE (
  echo ERROR: Could not find Python automatically.
  echo.
  echo   1^) Open build_dashboard_windows.py in VS Code
  echo   2^) In the terminal / a cell run:  import sys; print^(sys.executable^)
  echo   3^) Copy the full path it prints ^(ends in python.exe^)
  echo   4^) Paste it into a new text file named  python_path.txt  in THIS folder
  echo   5^) Double-click this updater again
  echo.
  if not defined NOPAUSE pause
  exit /b 1
)

echo Using Python: %PYEXE%
echo.

if defined WATCHMODE (
  echo Watch mode: rebuilding every 5 minutes. Close this window to stop.
  echo.
  :watchloop
  echo %DATE% %TIME%  Building dashboard...
  "%PYEXE%" "%~dp0build_dashboard_windows.py"
  if errorlevel 1 (
    echo   Build FAILED - see the messages above.
  ) else (
    echo   Done. Haulage_Dashboard.html updated.
  )
  TIMEOUT /T 300 /NOBREAK >nul
  goto watchloop
)

"%PYEXE%" "%~dp0build_dashboard_windows.py"
if errorlevel 1 (
  echo.
  echo Update FAILED - see the messages above.
  if not defined NOPAUSE pause
  exit /b 1
)

echo.
echo Done. Haulage_Dashboard.html has been updated.
echo.
if not defined NOPAUSE pause
exit /b 0
