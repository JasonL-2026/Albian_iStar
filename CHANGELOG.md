# Changelog

All notable changes to the Albian iSTAR Haulage Dashboard are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

- **MAJOR** — new tab or significant new feature
- **MINOR** — calculation methodology change
- **PATCH** — bug fix, data-schema fix, or minor improvement

---

## [Unreleased]
<!-- Add changes here as they are merged to DEV/TEST, before tagging a release -->

### Added
- Added a **Recommendations** tab that surfaces below-baseline measures, sorts them by priority, and links back to the source tab for drill-down.

### Changed
- Updated repository governance documentation to formalize PR-only promotion flow (`feature|fix -> DEV -> TEST -> PROD -> BACKUP`) and required PR evidence/approvals.
- Added GitHub web UI instructions for resolving branch-promotion and PR-evidence check failures before merge.

### Fixed
- `trucksatdump_git.rdl`: changed `FROM [CommonASE].[TrucksAtDump]` to `FROM [ASEOperational].[CommonASE].[TrucksAtDump]` (fully-qualified 3-part name) so the query works regardless of the default database set in the DSN or connection string.

---

## 🗓️ Next Steps — Target: ~2 weeks (September 2026)

### Live-Query Web Server Architecture (replaces embedded-data HTML)

**Problem:** The current static build bakes all shift data into `Haulage_Dashboard.html`, resulting in a ~12 MB file that must be fully rebuilt to refresh data.

**Proposed solution:** Split into a lightweight Python web server + thin HTML frontend:

1. **Python backend** (`dashboard_server.py`) — runs locally on the mine PC or server.
   - Uses Python's built-in `http.server` (zero new dependencies) or `Flask`.
   - Exposes `/api/<dataset>` endpoints that query SQL live on each request.
   - Datasets: `loads`, `status`, `truckatshovel`, `truckatdump`, `balance`, `lube`.

2. **HTML frontend** — becomes a thin shell (~50 KB).
   - Loads `lib_chartjs.js` for charts (already in repo).
   - Uses `fetch('/api/...')` to pull data on page load and on a configurable auto-refresh timer.
   - No data embedded; file stays small regardless of shift history.

3. **Launcher** (`Run_Live_Server.bat`) — starts the Python server, opens the browser automatically, keeps running in the background.

4. **Keep existing static build** (`build_dashboard_windows.py`) as a fallback for offline snapshots and emailed reports.

**Decisions needed before implementation:**
- Flask (cleaner routing, requires `pip install flask`) vs stdlib `http.server` (no install, offline-safe)?
- Default port (`8050` suggested)?
- Auto-refresh interval (5 min default)?

---

## [1.0.0] — Initial Release
### Added
- Multi-shift dashboard with shift selector (dropdown + older/newer arrows), defaulting to the most recent shift
- Three views per shift: MRM, JPM, Combined (filtered by `LoadPit`)
- **Truck/Shovel Balance tab** — Haulage Score, Loading Score, Truck Match card, bottleneck analysis, truck balance time-series, wait-time diverging chart
- **Truck Waterfall tab** — availability rows (PA/UA/OE vs budget), cycle-loss waterfall, payload row, crusher queue timelines
- **Shovel Waterfall tab** — availability + cycle losses bridge, per-shovel drill-down with status timeline
- **KPI tab** — PA/UA/OE actuals vs budget (Cat 797, BE 495B, HIT 800); top-15 lost-time reasons per fleet
- **Haulage Drill-down tab** — per-lane waterfalls grouped by shovel, dominant-loss-category summary card
- **Shift Analytics tab** — cumulative/hourly production charts, delay paretos, payload box plots, equipment Gantt timelines (shovels + trucks), shovel placement Sankey
- **Shift Stats tab** — loaded haul lanes, empty haul legs, and shovel hang/queue/payload tables
- Self-contained HTML output (`Haulage_Dashboard.html`) — no server or internet connection required
- One-click updaters: `update_dashboard.sh` (Linux/macOS), `Update Dashboard.bat` (Windows)
- `Dashboard_Methodology.md` — full specification of every KPI and calculation
