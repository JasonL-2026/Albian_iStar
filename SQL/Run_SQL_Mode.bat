@echo off
REM ============================================================
REM  Albian Mine Haulage Dashboard — SQL Mode Launcher (Windows)
REM
REM  Use this script on a machine that has direct access to the
REM  Dispatch SQL database.  It sets the required environment
REM  variables and calls build_dashboard.py to pull live data
REM  and regenerate Haulage_Dashboard.html.
REM
REM  BEFORE FIRST RUN:
REM    1. Edit the DASH_SQL_CONNECTION_STRING value below so it
REM       matches your database server, credentials, and driver.
REM    2. Optionally adjust DASH_SQL_LOOKBACK_DAYS (default 21).
REM    3. Double-click (or run from a Command Prompt / Task Scheduler).
REM
REM  EXAMPLE CONNECTION STRING (Windows Authentication):
REM    Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\<INSTANCE>;Database=<DB>;Trusted_Connection=Yes;
REM
REM  EXAMPLE CONNECTION STRING (SQL Login):
REM    Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\<INSTANCE>;Database=<DB>;UID=<user>;******;
REM ============================================================
setlocal
cd /d "%~dp0"

REM -------------------------------------------------------
REM  REQUIRED: set your ODBC connection string here.
REM  Leave as-is to be prompted at runtime instead.
REM -------------------------------------------------------
set "DASH_SQL_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\<INSTANCE>;Database=<DB>;UID=<user>;******;"

REM -------------------------------------------------------
REM  OPTIONAL overrides (leave blank to use defaults)
REM -------------------------------------------------------
REM  Number of calendar days to look back when fetching shifts.
set "DASH_SQL_LOOKBACK_DAYS=21"

REM  Explicit shift range (9-digit integer IDs, e.g. 260801001).
REM  If set, DASH_SQL_LOOKBACK_DAYS is ignored.
set "DASH_SQL_START_SHIFT="
set "DASH_SQL_END_SHIFT="

REM -------------------------------------------------------
REM  Runtime — do not edit below this line
REM -------------------------------------------------------
if not defined DASH_SQL_CONNECTION_STRING (
    echo.
    echo No connection string found in Run_SQL_Mode.bat.
    echo.
    set /p DASH_SQL_CONNECTION_STRING="Enter ODBC connection string: "
    if not defined DASH_SQL_CONNECTION_STRING (
        echo ERROR: A connection string is required.
        pause
        exit /b 1
    )
)

set "DASH_SQL_MODE=1"

echo.
echo Albian iSTAR — SQL Mode
echo Lookback: %DASH_SQL_LOOKBACK_DAYS% days
if defined DASH_SQL_START_SHIFT echo Shift range: %DASH_SQL_START_SHIFT% to %DASH_SQL_END_SHIFT%
echo.

REM Locate Python
set "PYEXE="
if exist "%~dp0python_path.txt" set /p PYEXE=<"%~dp0python_path.txt"
REM If python_path.txt contains a folder path (not the .exe), append python.exe automatically
if defined PYEXE if exist "%PYEXE%\" set "PYEXE=%PYEXE%\python.exe"
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python3 >nul 2>nul && set "PYEXE=python3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
    for %%P in (
        "%LocalAppData%\Programs\Python\Python313\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%LocalAppData%\Programs\Python\Python310\python.exe"
        "%LocalAppData%\Programs\Python\Python39\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Program Files\Python312\python.exe"
        "C:\Program Files\Python311\python.exe"
        "C:\Program Files\Python39\python.exe"
        "%UserProfile%\Anaconda3\python.exe"
        "%UserProfile%\Miniconda3\python.exe"
        "%ProgramData%\Anaconda3\python.exe"
    ) do if not defined PYEXE if exist %%P set "PYEXE=%%~P"
)

if not defined PYEXE (
    echo ERROR: Python not found.
    echo Install Python 3 or create python_path.txt with the full path to python.exe.
    pause
    exit /b 1
)

echo Using Python: %PYEXE%
echo.
echo Connecting to database and building dashboard...
echo.

"%PYEXE%" "%~dp0build_dashboard_windows.py"
if errorlevel 1 (
    echo.
    echo Build FAILED — check the messages above.
    pause
    exit /b 1
)

echo.
echo Done.  Haulage_Dashboard.html has been updated.
echo Open it in any browser.
echo.
pause
exit /b 0
