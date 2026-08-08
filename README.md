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
| `PROD` | Stable production releases | Lead/owner only (via PR from TEST, 2 approvals + release sign-off) |
| `TEST` | Integration and validation before release | Senior developers (via PR from DEV, 1 approval) |
| `DEV` | Shared integration branch for tested feature/fix work | Senior developers (via PR from `feature/*` or `fix/*`) |
| `BACKUP` | Immutable production recovery snapshots | Lead/owner only (via PR from PROD snapshot update) |
| `feature/<name>` | Individual feature work | Merged to DEV via PR |
| `fix/<name>` | Bug-fix work | Merged to DEV via PR |

**Never commit directly to DEV, TEST, PROD, or BACKUP.** All changes go through pull requests.

### Required PR Rules

1. **Allowed PR paths only**
   - `feature/*` or `fix/*` → `DEV`
   - `DEV` → `TEST`
   - `TEST` → `PROD`
   - `PROD` → `BACKUP`
2. **Required checks must pass** before merge:
   - CI syntax/build checks
   - Data-integrity checks (required CSV schema, ShiftId consistency, KPI tolerance)
3. **Required approvals**
   - To `DEV`: at least 1 approval
   - To `TEST`: at least 1 approval
   - To `PROD`: at least 2 approvals plus release sign-off
   - To `BACKUP`: lead/owner approval only
4. **PR evidence is mandatory**
   - Completed PR template
   - ShiftIds validated
   - Key KPI outputs recorded
   - Dashboard render confirmation
   - Linked issue (`Closes #...`)
5. **Protection controls**
   - No force push, no branch deletion, and no bypass of required checks on `DEV`/`TEST`/`PROD`/`BACKUP`
6. **Release traceability**
   - For each `TEST` → `PROD` merge: create release/tag, then update `BACKUP` from the previous `PROD` state.

---

## Versioning

Releases follow [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

- `MAJOR` — new tab or significant new feature
- `MINOR` — calculation methodology change
- `PATCH` — bug fix or minor improvement

After each TEST → PROD merge, a GitHub Release is created and the built `Haulage_Dashboard.html` is attached as a release artifact.

---

## Contributing

1. Create a branch from `DEV` (or from latest branch tip agreed by team): `git checkout -b feature/my-change DEV`
2. Make your changes and test locally with `python build_dashboard.py`
3. If you changed any calculation, update `Dashboard_Methodology.md`
4. Open a pull request targeting `DEV`
5. Fill in the PR template — include which shifts were used to validate

### Step-by-step: move tested changes from a new branch to `DEV`

1. Confirm you are on your feature/fix branch:
   - `git checkout feature/my-change`
2. Confirm everything is committed:
   - `git status`
   - If needed: `git add .` then `git commit -m "Describe tested change"`
3. Push your branch:
   - `git push -u origin feature/my-change`
4. Open a PR in GitHub:
   - Base: `DEV`
   - Compare: `feature/my-change`
5. Complete the PR template:
   - Data used to test
   - ShiftIds validated
   - Key KPI outputs
   - Dashboard render confirmation
   - Linked issue (`Closes #...`)
6. Wait for required checks (CI + governance/data-integrity gates) to pass.
7. Get required approval(s) for `DEV`.
8. Merge the PR into `DEV`.
9. Optionally delete the source branch after merge.

### Step-by-step: use the GitHub web UI to complete PR checks

1. Open the pull request and review the Checks panel.
2. If **Governance / Enforce branch promotion flow** fails:
   - Click **Edit** on the PR.
   - Make sure the PR path matches one of the allowed paths:
     - `feature/*` or `fix/*` → `DEV`
     - `DEV` → `TEST`
     - `TEST` → `PROD`
     - `PROD` → `BACKUP`
   - If the branch name or target branch is wrong, close the PR and open a new one with an allowed base/compare pair.
3. If **Governance / Require PR validation evidence** fails:
   - Click **Edit** on the PR description.
   - Fill in all required PR template fields:
     - **Branch being merged into**
     - **PR path**
     - **Data used to test**
     - **Dashboard renders without errors:** change to `[x] Yes`
     - **Key outputs validated:** include KPI percentages such as `64%`
     - **Shifts validated:** include at least one 9-digit ShiftId
     - **Linked to issue:** use `Closes #<issue-number>`
4. Click **Save** to update the PR description.
5. Wait for GitHub to rerun the checks automatically.
6. Open **Details** for any remaining failed check and correct the specific issue shown in the log.
7. Do not merge until CI, governance, and data-integrity checks are all green.

### Step-by-step: promote `DEV` to `TEST`

1. Open a new pull request in GitHub.
2. Set:
   - Base: `TEST`
   - Compare: `DEV`
3. Complete the PR template with the same validation evidence used to approve the DEV change set.
4. Wait for all required checks to pass.
5. Get the required approval for `TEST`.
6. Merge the PR into `TEST`.

See [Dashboard_Methodology.md](Dashboard_Methodology.md) for the full calculation specification.
