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

### Changed
- Updated repository governance documentation to formalize PR-only promotion flow (`feature|fix -> DEV -> TEST -> PROD -> BACKUP`) and required PR evidence/approvals.
- Added GitHub web UI instructions for resolving branch-promotion and PR-evidence check failures before merge.

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
