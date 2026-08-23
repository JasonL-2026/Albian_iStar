#!/usr/bin/env bash
# =============================================================
#  Albian Mine Haulage Dashboard — SQL Mode Launcher (Linux/macOS)
#
#  Use this script on a machine that has direct access to the
#  Dispatch SQL database.  It sets the required environment
#  variables and calls build_dashboard.py to pull live data
#  and regenerate Haulage_Dashboard.html.
#
#  BEFORE FIRST RUN:
#    1. Set DASH_SQL_CONNECTION_STRING below (or export it in
#       your shell before running this script).
#    2. Ensure pyodbc is installed:  pip install pyodbc
#    3. Make this script executable:  chmod +x run_sql_mode.sh
#    4. Run:  ./run_sql_mode.sh
#
#  EXAMPLE CONNECTION STRING (Kerberos / AD):
#    Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\<INSTANCE>;Database=<DB>;Trusted_Connection=Yes;
#
#  EXAMPLE CONNECTION STRING (SQL Login):
#    Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\<INSTANCE>;Database=<DB>;UID=<user>;******;
# =============================================================
set -euo pipefail
cd "$(dirname "$0")"

# -------------------------------------------------------
#  REQUIRED: set your ODBC connection string here,
#  or export DASH_SQL_CONNECTION_STRING before running.
# -------------------------------------------------------
: "${DASH_SQL_CONNECTION_STRING:=Driver={ODBC Driver 17 for SQL Server};Server=<SERVER>\<INSTANCE>;Database=<DB>;UID=<user>;******;}"

# -------------------------------------------------------
#  OPTIONAL overrides (leave blank to use defaults)
# -------------------------------------------------------
# Number of calendar days to look back when fetching shifts (default 21).
: "${DASH_SQL_LOOKBACK_DAYS:=21}"

# Explicit shift range (9-digit integers, e.g. 260801001).
# If set, DASH_SQL_LOOKBACK_DAYS is ignored.
: "${DASH_SQL_START_SHIFT:=}"
: "${DASH_SQL_END_SHIFT:=}"

# -------------------------------------------------------
#  Runtime — do not edit below this line
# -------------------------------------------------------
if [ -z "$DASH_SQL_CONNECTION_STRING" ]; then
    read -rp "Enter ODBC connection string: " DASH_SQL_CONNECTION_STRING
    if [ -z "$DASH_SQL_CONNECTION_STRING" ]; then
        echo "ERROR: A connection string is required." >&2
        exit 1
    fi
fi

export DASH_SQL_MODE=1
export DASH_SQL_CONNECTION_STRING
export DASH_SQL_LOOKBACK_DAYS
[ -n "$DASH_SQL_START_SHIFT" ] && export DASH_SQL_START_SHIFT
[ -n "$DASH_SQL_END_SHIFT" ]   && export DASH_SQL_END_SHIFT

echo
echo "Albian iSTAR — SQL Mode"
echo "Lookback: ${DASH_SQL_LOOKBACK_DAYS} days"
[ -n "$DASH_SQL_START_SHIFT" ] && echo "Shift range: ${DASH_SQL_START_SHIFT} to ${DASH_SQL_END_SHIFT}"
echo

PYEXE=
for candidate in python3 python py; do
    if command -v "$candidate" &>/dev/null; then
        PYEXE="$candidate"
        break
    fi
done
if [ -z "$PYEXE" ]; then
    echo "ERROR: Python 3 not found on PATH. Install Python 3 and try again." >&2
    exit 1
fi

echo "Using Python: $(command -v "$PYEXE")"
echo "Connecting to database and building dashboard..."
echo

"$PYEXE" "$(dirname "$0")/build_dashboard.py"

echo
echo "Done.  Haulage_Dashboard.html has been updated."
echo "Open it in any browser."
