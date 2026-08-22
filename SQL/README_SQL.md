# Albian iSTAR — SQL Mode Package

This folder is a **self-contained copy** of the Albian Mine Haulage Dashboard configured to run in **SQL mode** — i.e. it pulls live operational data directly from the Dispatch database instead of reading CSV exports.

Copy this entire folder to any machine on the secured network that has ODBC access to the Dispatch SQL database.

---

## Folder Contents

```
SQL/
├── README_SQL.md                 ← this file
├── Run_SQL_Mode.bat              ← Windows launcher  (double-click to run)
├── run_sql_mode.sh               ← Linux / macOS launcher
├── build_dashboard.py            ← main builder script
├── build_dashboard_windows.py    ← Windows-compatible builder script
├── lib_chartjs.js                ← bundled Chart.js (no internet required)
├── istarlogov2.png               ← logo
├── scripts/
│   ├── istar_sql_backend.py      ← lightweight REST API backend (optional)
│   └── validate_data_integrity.py← data-integrity validation helper
├── Data/
│   └── RDLs/                     ← SSRS report definitions (contain SQL queries)
│       ├── AllLoadsDumps_git.rdl
│       ├── Statusevents_git.rdl
│       ├── TruckBalance_git.rdl
│       ├── TruckatShovel_git.rdl
│       ├── trucksatdump_git.rdl
│       ├── TruckAtLubeLand_git.rdl
│       ├── SystemVsManualFuelAssignments_git.rdl
│       ├── SystemVsManualAssignments_git.rdl
│       ├── ShovelLoadingSideTagLog_git.rdl
│       ├── ShovelCoverageFactors_git.rdl
│       ├── Dispatcher Dashboard_MRM_Git.rdl
│       ├── Dispatcher Dashboard_JPM_Git.rdl
│       └── 1weekdata/            ← same queries scoped to last 7 days
└── Budget/
    ├── MRM 2026 Budget.csv
    ├── MRM Haul Curve.csv
    ├── MRM_Fixed_Times.csv
    ├── JPM 2026 Budget.csv
    ├── JPM Haul Curve.csv
    └── JPM_Fixed_Times.csv
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.9+** | Must be on `PATH` (`python`, `python3`, or `py` launcher on Windows) |
| **pyodbc** | `pip install pyodbc` — required for SQL connectivity |
| **ODBC Driver 17 (or 18) for SQL Server** | Install from Microsoft if not already present |
| **Network access** | The machine must be able to reach the Dispatch SQL database server |

### Install pyodbc

```bash
pip install pyodbc
```

On a machine without internet access, install from a local wheel:

```bash
pip install --no-index --find-links=<path-to-wheels> pyodbc
```

---

## Quick Start — Windows

1. Open `Run_SQL_Mode.bat` in a text editor.
2. Find the line:
   ```
   set "DASH_SQL_CONNECTION_STRING="
   ```
3. Fill in your ODBC connection string between the quotes, for example:
   ```
   set "DASH_SQL_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=DBSERVER\DISPATCH;Database=DispatchDB;Trusted_Connection=Yes;"
   ```
4. Save the file.
5. Double-click `Run_SQL_Mode.bat`.
6. Open the generated `Haulage_Dashboard.html` in any browser.

> **Tip:** If Python is not detected automatically, create a file named `python_path.txt` in this folder containing the full path to `python.exe` (e.g. `C:\Python312\python.exe`).

---

## Quick Start — Linux / macOS

1. Open `run_sql_mode.sh` in a text editor.
2. Set the `DASH_SQL_CONNECTION_STRING` variable, for example:
   ```bash
   : "${DASH_SQL_CONNECTION_STRING:=Driver={ODBC Driver 17 for SQL Server};Server=DBSERVER\DISPATCH;Database=DispatchDB;Trusted_Connection=Yes;}"
   ```
3. Save the file.
4. Make it executable and run:
   ```bash
   chmod +x run_sql_mode.sh
   ./run_sql_mode.sh
   ```
5. Open the generated `Haulage_Dashboard.html` in any browser.

---

## Running Manually (any platform)

Set environment variables and run the builder directly:

### Windows (Command Prompt)
```cmd
set DASH_SQL_MODE=1
set DASH_SQL_CONNECTION_STRING=Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\<INSTANCE>;Database=<DB>;Trusted_Connection=Yes;
python build_dashboard_windows.py
```

### Linux / macOS (bash)
```bash
export DASH_SQL_MODE=1
export DASH_SQL_CONNECTION_STRING="Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\\<INSTANCE>;Database=<DB>;Trusted_Connection=Yes;"
python3 build_dashboard.py
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DASH_SQL_MODE` | Yes | `0` | Set to `1` to enable SQL mode |
| `DASH_SQL_CONNECTION_STRING` | Yes (SQL mode) | — | Full ODBC connection string |
| `DASH_SQL_LOOKBACK_DAYS` | No | `21` | Days of shift history to pull |
| `DASH_SQL_START_SHIFT` | No | — | Override start shift ID (9-digit, e.g. `260801001`) |
| `DASH_SQL_END_SHIFT` | No | — | Override end shift ID |

---

## How SQL Mode Works

When `DASH_SQL_MODE=1`, the builder:

1. Reads the SQL `CommandText` from each `.rdl` file in `Data/RDLs/`.
2. Substitutes `@StartShift` / `@EndShift` parameters with the computed shift range.
3. Executes each query against the Dispatch database via `pyodbc`.
4. Processes the result rows exactly the same as CSV input.
5. Produces `Haulage_Dashboard.html` in this folder.

The **RDL files are read-only reference files** — the same reports deployed to SSRS — so the SQL queries used here are always in sync with SSRS.

---

## Optional: Live API Backend

For a continuously-updating web dashboard (instead of a static HTML file), run the lightweight backend:

```bash
# Set connection string so the backend can query SQL directly
export ISTAR_SQL_CONNECTION_STRING="<your connection string>"
export ISTAR_SQL_JSON_QUERY="EXEC dbo.GetDashboardJSON"   # adjust to your proc/view

python scripts/istar_sql_backend.py
```

Then open `iSTAR_dashboard.html` (generated by `build_dashboard.py`) in a browser.  
The dashboard will automatically refresh data from `http://localhost:8765/api/dashboard-data`.

Backend environment variables:

| Variable | Default | Description |
|---|---|---|
| `ISTAR_SQL_CONNECTION_STRING` | — | ODBC connection string |
| `ISTAR_SQL_JSON_QUERY` | — | SQL query returning dashboard JSON as a single column |
| `ISTAR_BACKEND_HOST` | `0.0.0.0` | Bind address |
| `ISTAR_BACKEND_PORT` | `8765` | Listen port |
| `ISTAR_DASHBOARD_DATA_FILE` | `dashboard_data.json` | Fallback JSON file if no SQL query is set |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'pyodbc'` | Run `pip install pyodbc` |
| `pyodbc.Error: Data source name not found` | Check the `Driver={}` name matches an installed ODBC driver (run `odbcinst -q -d` on Linux, or open ODBC Data Sources on Windows) |
| `Login failed for user` | Verify credentials / Windows Authentication / firewall rules |
| `No common ShiftId` | The shift range may be outside the available data — increase `DASH_SQL_LOOKBACK_DAYS` |
| Python not found on Windows | Create `python_path.txt` containing the full path to `python.exe` |
