@echo off
REM ============================================================
REM  Albian Mine Haulage Dashboard - one-click updater (Windows)
REM  Double-click this file after refreshing the CSVs in Data\ and Budget\.
REM  It regenerates Haulage_Dashboard.html. No admin rights needed.
REM ============================================================
REM %~dp0 is this .bat file's own folder, so it works from any location / after being copied.
cd /d "%~dp0"
echo.
echo Updating Albian Mine Haulage Dashboard...
echo Project folder: %~dp0
echo.

REM Try the standard 'python' command, then the Windows 'py' launcher as a fallback.
python "%~dp0build_dashboard_windows.py"
if %errorlevel%==0 goto ok
py "%~dp0build_dashboard_windows.py"
if %errorlevel%==0 goto ok

echo.
echo ERROR: Could not run the update.
echo Python 3 does not appear to be installed or on PATH.
echo Ask IT to install Python 3, or run this on a machine that has it.
echo.
pause
exit /b 1

:ok
echo.
echo Done. Open Haulage_Dashboard.html to view the updated dashboard.
echo.
pause
