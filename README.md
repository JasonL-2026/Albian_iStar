# Albian iSTAR — Mine Haulage Dashboard

Near-real-time shift IQ for the Albian Mine haulage operation.
Processes truck and shovel operational data and produces a single self-contained HTML dashboard — open it in any browser, no server required.

---

## What It Does

- Reads shift CSV exports from mine dispatch/SSRS for two pits: **MRM** (Muskeg River Mine) and **JPM** (Jackpine Mine)
- Computes Haulage Score, Loading Score, Truck Match, PA/UA/OE availability KPIs, and waterfall analyses
- Embeds all shifts found in the data; defaults to the most recent shift on load
- Output: `Haulage_Dashboard.html` — fully self-contained, shareable by email or network share

See [`Dashboard_Methodology.md`](Dashboard_Methodology.md) for a full specification of every calculation.

---

## How to Update the Dashboard

### Prerequisites
- Python 3 installed and on PATH

### Steps

1. **Refresh the source CSVs** in `Data/` from the mine's SSRS reporting system:
   - `AllLoadsDumps.csv`
   - `Statusevents.csv`
   - `TruckBalance.csv`
   - `TruckatShovel.csv`
   - `TruckAtDump.csv`
   - `TruckAtLubeLand.csv`

2. **Run the builder:**

   **Linux / macOS:**
   ```bash
   ./update_dashboard.sh
   ```

   **Windows:**
   Double-click `Update Dashboard.bat`

   Or directly:
   ```bash
   python build_dashboard.py
   ```

3. **Open** `Haulage_Dashboard.html` in your browser.

> **Tip:** Set the `DASH_DATADIR` environment variable to point the builder at a different data folder (e.g., a network share) without editing the script.

---

## Repository Structure

```
Albian_iSTAR/
├── build_dashboard.py          # Main builder — produces Haulage_Dashboard.html
├── Haulage_Dashboard.html      # Built output (open in browser)
├── Dashboard_Methodology.md    # Specification for every KPI and calculation
├── CHANGELOG.md                # Version history
├── update_dashboard.sh         # One-click updater (Linux/macOS)
├── Update Dashboard.bat        # One-click updater (Windows)
├── lib_chartjs.js              # Bundled Chart.js (offline capable)
├── istarlogov2.png             # Logo
├── Data/                       # Live operational CSVs (NOT committed — see below)
│   └── samples/                # Sample/anonymized CSVs for CI testing
├── Budget/                     # Budget CSVs by pit (MRM, JPM)
│   ├── MRM 2026 Budget.csv
│   ├── MRM Haul Curve.csv
│   ├── MRM_Fixed_Times.csv
│   ├── JPM 2026 Budget.csv
│   ├── JPM Haul Curve.csv
│   └── JPM_Fixed_Times.csv
└── .github/
    ├── pull_request_template.md
    └── workflows/
        └── ci.yml              # Syntax check + sample build on push/PR
```

---

## Data Hygiene

**Live operational CSVs in `Data/` are not committed to this repository.**
They contain sensitive production data and change every shift.

- Store live CSVs on a shared network drive or SharePoint folder
- Copy/refresh them into `Data/` locally before running the builder
- For CI testing, place anonymized sample CSVs in `Data/samples/`

---

## Branch Strategy

| Branch | Purpose | Who can merge |
|--------|---------|---------------|
| `PROD` | Stable production releases | Lead/owner only (via PR from TEST, 2 approvals) |
| `TEST` | Validated before release | Senior developers (via PR from DEV, 1 approval) |
| `DEV` | Integration branch | All developers (via PR from feature branches, 1 approval) |
| `feature/<name>` | Individual feature work | Cut from DEV; merge back to DEV via PR |
| `fix/<name>` | Bug fixes | Cut from DEV (or TEST for hotfixes); merge via PR |

**Never commit directly to DEV, TEST, or PROD.** All changes go through a pull request.

---

## Versioning

Releases follow [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

- `MAJOR` — new tab or significant new feature
- `MINOR` — calculation methodology change
- `PATCH` — bug fix or minor improvement

After each TEST → PROD merge, a GitHub Release is created and the built `Haulage_Dashboard.html` is attached as a release artifact.

---

## Contributing

1. Create a branch from `DEV`: `git checkout -b feature/my-feature DEV`
2. Make your changes and test locally with `python build_dashboard.py`
3. If you changed any calculation, update `Dashboard_Methodology.md`
4. Open a pull request targeting `DEV`
5. Fill in the PR template — include which shifts were used to validate

See [Dashboard_Methodology.md](Dashboard_Methodology.md) for the full calculation specification.
