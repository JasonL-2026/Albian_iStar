#!/bin/bash
# ============================================================
#  Albian Mine Haulage Dashboard - one-click updater (macOS / Linux)
#  Run after refreshing the CSVs in Data/ and Budget/:
#      ./update_dashboard.sh          # build once and exit
#      ./update_dashboard.sh --watch  # rebuild every 5 minutes
#  (or double-click on macOS if marked executable). No admin rights needed.
# ============================================================
# Resolve this script's own folder so it works from any location / after being copied.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
echo
echo "Updating Albian Mine Haulage Dashboard..."
echo "Project folder: $DIR"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: Could not run the update."
  echo "Python 3 does not appear to be installed or on PATH."
  echo "Ask IT to install Python 3, or run this on a machine that has it."
  exit 1
fi

if [ "$1" = "--watch" ]; then
  echo "Watch mode: rebuilding every 5 minutes. Press Ctrl-C to stop."
  echo
  while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S')  Building dashboard..."
    python3 "$DIR/build_dashboard.py" && echo "  Done. Haulage_Dashboard.html updated." || echo "  Build FAILED - see above."
    sleep 300
  done
else
  python3 "$DIR/build_dashboard.py" && {
    echo
    echo "Done. Open Haulage_Dashboard.html to view the updated dashboard."
    exit 0
  }
  echo
  echo "ERROR: Build failed - see messages above."
  exit 1
fi
