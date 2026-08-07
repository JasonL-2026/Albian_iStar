# Albian Mine Haulage Dashboard — Methodology

Reference for the calculations behind `Haulage_Dashboard.html`. Re-running `build_dashboard.py`
reproduces every number below from the source CSVs. This document is the spec; the script is the
implementation.

---

## 1. Scope & shift navigation (multi-shift)

The dashboard is **multi-shift**. The build reads every shift present in `AllLoadsDumps.csv`, computes the
full dashboard for each, and embeds them all. A **shift selector** (dropdown + ‹ older / newer › arrows)
sits in the top bar and **defaults to the most recent shift**.

- **Available shifts** are the distinct `ShiftId` values in `AllLoadsDumps.csv`, sorted most-recent-first
  (the `ShiftId` `YYMMDDsss` encodes date + sequence, so it sorts chronologically).
- Each shift's **date, month and base hour** are derived from `ShiftId` + `FullShiftName`
  (Day → 06:00 base, Night → 18:00 base) — the `ShiftStartTimestamp` column is unreliable (Excel mangles it).
- **Budgets** are looked up per shift by month/date/shift; haul curves and fixed times are keyed by month.
- **Nominal shift length** 12 h; NOH proration uses `elapsed/12` (elapsed defaults to 12 h for completed shifts).

Within a shift, three views come from the same logic: **MRM**, **JPM**, **Combined** — a view is just a
`LoadPit` filter. Both the shift selector and the MRM/JPM/Combined toggle sit in the top bar and apply to
whichever tab is open.

**Page layout (left-side tabs):**
1. **Truck / Shovel Balance** — a **separate last-hour Truck Balance card** sits at the top (LP Trucks
   Required vs Actual — from TruckBalance.csv, most-recent hour, see §6), above the gauge row. The gauge row
   is **Haulage Score · Truck Match · Loading Score**. Both gauges use the **same Potential as their
   waterfalls** (§7): Haulage from the **Truck waterfall**, Loading from the **Shovel Waterfall 2** — i.e.
   `Score = Actual ÷ Potential` where **Potential is the top-of-bridge value (Sched. Potential, which
   includes the PA/UA/OE availability gap)**. Because availability is inside the denominator, these read
   lower than the old cycle-only scores (e.g. Combined Haulage ≈ 64 %, Loading ≈ 50 %). Gauges are
   colour-coded by value (**< 90 % red, 90–100 % yellow, ≥ 100 % green**). The **centre Truck Match card**
   reads `Loading − Haulage` — a **function of the two scores**: negative = Under-Trucked, positive =
   Over-Trucked, within ±3 = Balanced. The **Haulage / Loading breakdowns** follow.
   Both are **grouped by material** (Ore, then Waste headers — the header swatch is the ore/waste
   colour), but each **bar is coloured by its own score** using the same legend as the gauges:
   **< 90 % red, 90–100 % yellow, ≥ 100 % green**. Within each material group: a **total** bar
   (availability-inclusive, matches the gauge), then the individual **haulage** (Load→Dump) or **shovels**
   (loading) under it — both **sorted lowest score first** (worst offenders on top), showing up to six.
   Loading shovels are placed under their **dominant material** and use each unit's own PA/UA/OE
   (availability-inclusive, matching the gauge and the Shovel Waterfall 2 drill-down). Haulage bars stay
   **cycle-only** and read higher — truck availability can't be attributed to an individual load→dump
   route (labelled as such). Between the balance card and the breakdowns, the tab carries **three balance
   graphs**: **(a) Bottleneck — trucks vs shovels** (above the balance graph): three horizontal bars —
   **Loading capacity** (shovel Sched. Potential) vs **Haulage capacity** (truck Sched. Potential) vs
   **Actual moved**; the **lower capacity is the binding constraint** (Loading > Haulage ⇒ trucks limit,
   adding trucks can lift output; Haulage > Loading ⇒ shovels limit; each fleet's own cycle basis, so
   directional). **(b) Truck Balance — over the shift**: the Required-vs-Actual truck time-series (15-min)
   with **under-trucked shaded red / over-trucked blue**, the **actual tonnage rate vs target** (green, right
   axis — 3-pt moving-average smoothed Catmull-Rom spline), and an **estimate of tonnes lost while
   under-trucked** (per-15-min linear truck-scaling: shortfall × interval tonnage ÷ actual trucks). The
   **last hour** (the window the balance card scores) is **very lightly shaded** with a small **status pill**
   — *Balanced* (green) / *Under-* / *Over-Trucked* (amber, with %).
   **(c) Wait-time balance over the shift**: a diverging area — shovel **hang** above budget fills up (red =
   shovels starved → under-trucked), truck **queue** above budget fills down (blue = over-trucked), flat =
   balanced; shares the time axis with (b) for correlation. (Equipment availability detail lives on the
   **KPI** tab — §2b.) *(The former standalone Fleet Match tab was merged into this tab.)*
2. **Truck Waterfall** — the Trucks Losses & Gains waterfall (topped by **PA / UA / OE availability rows**
   for Cat 797 vs budget, each bar embedding its main Down/Standby/Delay reason — see §7), the **Ore/Waste
   — Waterfall** (one waterfall each for ore and waste), and a **Trucks-at-dump** timeline showing **crusher dump locations
   only** (names starting "CR"; stockpiles/roads/berms excluded) — a stepped `TrucksAtDump` line per
   crusher over the shift, from `TruckAtDump.csv`, filtered by dominant loading pit. Each crusher is
   labelled with its **average dump-queue time per load** (`QueueTimeDmp` averaged over the loads dumping
   there in the current view, in minutes/load). Each crusher is drawn as a tall band with **thin
   horizontal guide lines at every integer truck count** (0–max) so the number of trucks queued at the
   crusher can be read directly off the stepped line. A **crusher-status strip sits at the base** of each
   band, colour-coded by the crusher's `ASEStatus` (Ready green / Delay amber / Down red / Standby blue,
   from the `Crusher`-type equipment whose `Eqmt` matches the dump-location name, e.g. `CR1_MRM`) — the
   same status colouring as the shovel timeline, but as a thin strip at the bottom rather than filling the row.
3. **Shovel Waterfall** — the shovel losses & gains bridge (availability + cycle), the hidden Ore/Waste
   breakdown, and the per-shovel drill-down with its filtered single-shovel status timeline (see §7b). *(This
   is the former "Shovel Waterfall 2"; the original cycle-only "Shovel Waterfall" tab and its Shovel-placement
   Sankey were removed — the Sankey and both status timelines now live on the Shift Analytics tab, §5.)*
2b. **KPI** (tab, formerly "Availability") — home for **Performance KPI — actual vs budget** (PA / UA / OE),
   computed from `ASEStatus` durations (Parked folded into Standby): PA = (Ready+Delay+Standby)/(+Down),
   UA = (Ready+Delay)/(+Standby), OE = Ready/(Ready+Delay). Top row: three **comparison cards** (Cat 797
   truck, cable BE 495B, hydraulic HIT 800), each listing PA/UA/OE as **Actual · Budget · Δ**, with Δ
   (actual − budget) coloured green at/above budget and red below. Budget PA/UA/OE come from the budget CSV
   (PA797/UA797/OE797, PA495/UA495, PA8000/UA8000), NOH-weighted across pits; cable &amp; hydraulic shovels
   have no OE budget (shown as "—"). Below the cards, a matching 3-column **Top-15 lost-time reasons**
   panel: for each equipment type, the 15 largest `Reason` contributors to **Down / Standby / Delay** time
   (hours, this shift/view), each row a horizontal bar coloured by status (Down red, Standby blue, Delay
   amber) so the biggest availability hitters line up directly under the KPI card they explain. (The old
   PA/UA/OE grouped-bar and time-usage-stacked-bar charts were removed.)
4. **Haulage drill-down** — per-lane waterfalls, grouped by loading shovel (formerly "Lane drill-down").
   A **summary card** at the top groups every shovel by its **dominant loss category** (the waterfall row
   with the largest tonnage loss — Payload, Load, Queue, Spot, Dump Idle, Dumping, Full Haul or Empty
   Haul); groups are ordered worst-score-first and shovels are sorted low → high score within each group,
   with each shovel's score and that category's lost tonnes shown. **Clicking a shovel** in the summary
   filters the lane table to that shovel and renders its **aggregate waterfall** below (load-weighted
   across its lanes; closes with no residual). A "← show all shovels" link clears the filter; clicking an
   individual lane still drills into that single lane's waterfall.
5. **Shift Analytics** — the analytics charts (cumulative, hourly, delay pareto, delay variance, payload box plot), the two **Equipment status timelines** — **shovels** (S0/S8, with the trucks-at-shovel queue overlay) and **trucks** (Cat 797 haul trucks that hauled this view, top 40 by Ready time; status segments only) — both Gantt-style, one row per unit, status coloured Ready/Delay/Standby/Down and placed by real timestamp, plus a copy of the **Shovel placement** Sankey (also on the Shovel Waterfall tab). *(The shovel status timeline was moved here from the Shovel Waterfall tab; the Shovel Waterfall 2 tab keeps its own single-shovel filtered version.)*
5b. **Shift Stats** — consolidated into **three wide tables** (with column headers) plus the shift-level
   **Σ empty ÷ Σ full haul distance** ratio at the top. **(A) Loaded haul lanes** (`Load → Dump`, top 12 by
   cycle time): Full dist · Full time · Cycle time. **(B) Empty haul legs** (`prev-dump → dig`, top 12 by
   empty time): Empty dist · Empty time. **(C) Shovels**: Hang · Queue · Payload. Within each table the rows
   are **split into Ore and Waste sections** (each entity assigned by its dominant material, ranked and
   top-N'd per material). Every value is the average over loads where the metric is present (≥5 loads), with
   the load count shown, and is **colour-shaded against its budget** — green = better, red = worse (hover for
   the target). Budgets: full/empty distance vs
   `FullExpectedDistance`/`EmptyExpectedDistance`; full/empty/cycle time vs
   `FullExpectedDuration`/`EmptyExpectedDuration` (cycle also adds the `Fixed_Times` shovel/dump budgets);
   shovel hang vs the 361 t-cycle hang budget (`361×3600/TPNOH − Spot_b − Load_b`); queue vs
   `Fixed_Times.Queue`; payload vs the 361 t target. **Empty haul** is the leg **previous-dump → this-load**:
   the origin is the same truck's **row-above** `DumpLocation` (`PREV_DUMP`, file order); the record's
   `EmptyHaulDuration`/`EmptyHaullDistance` already measure that leg (no recompute). The layout is
   **data-driven** (a `tables` list of column defs + rows) so new metrics/columns are easy to add. Each table
   carries a `data-kpi` tag; the **▮▮▮ button beside the matching KPI row on the Truck / Shovel Waterfalls**
   calls `gotoStat(kpi)`, which switches here and highlights the related table (Full Haul & Cycle → A, Empty
   Haul → B, Hang/Queue/Payload → C).
6b. **Delays & Standby** — a table of every Cat 797 **Delay** and **Standby** reason for the selected
   shift/view (styled after the reference sheet): columns **Status (`ASEStatus`: Delay/Standby, colour-coded)
   · reason · % · Target · Actual · +/− tonnes**.
   `%` is the reason's time as a share of total accounted time (Ready+Delay+Standby+Down); **Actual %** =
   actual share, **Target %** = the `ExpectedDuration` share (blank where no standard exists). **+/− tonnes**
   = (target − actual) time × `TPNOH797`, colour-shaded by magnitude — **green = under target (gain)**, **red
   = over target (loss)**; reasons with no target count fully as loss. Rows are sorted by actual % (biggest
   first). This is the reason-level companion to the KPI tab's Down/Standby/Delay hitters and the waterfall's
   OE/UA rows.
6d. **Shovel Productivity** — a per-shovel KPI scorecard (styled after the reference sheet): columns are
   **All Shovels** plus one column per **active shovel unit** that shift (e.g. S011, S807), each split
   **Target · Actual · +/− tonnes**, with a header **score badge = Actual ÷ Target loaded**
   (≥90 % green · 70–90 % amber · <70 % red). Rows: **Total Loaded** (tonnes) whose target is the
   **scheduled potential** = total/calendar hours (from per-unit `Statusevents`) × budget **PA** × **UA** ×
   **OE** × budget **Dig Rate** (blended TPNOH). Availability chain (from that unit's status events, with
   total/calendar hours TH = Ready+Delay+Standby+Down, **GOH** = Ready+Delay, **NOH** = Ready):
   **PA** = (Ready+Delay+Standby)/TH, **UA** = (Ready+Delay)/(Ready+Delay+Standby), **OE** =
   Ready/(Ready+Delay). The table lists **PA · UA · OE** (%, each act vs budget, +/− tonnes from the
   sequential decomposition — PA + UA + OE + Dig Rate +/− tonnes **sum to the Total Loaded gap**), then
   **NOH** (net operating hrs; actual = Ready, target = TH × budget PA·UA·OE; its +/− tonnes is the combined
   availability subtotal) and **NOH %** = NOH ÷ TH = PA·UA·OE (net operating hours as a share of calendar
   time). Then the
   **cycle block** (mm:ss) — **Shovel Cycle Time**, **Shovel Hang**, **Shovel Wait %** (hang ÷ cycle),
   **Spot at Shovel**, **Load Time** — with the Hang/Spot/Load +/− tonnes taken from the cycle-time
   waterfall (a separate impact from the availability bridge). Then **Payload — CAT 797** (wTons): the
   average weighed payload per load vs the 361 t target, its +/− tonnes being the payload cycle impact
   (Σ actual − 361). Finally the **payload-quality block** vs the
   361 t target using the **10-10-20 rule**: **Overloads (>120 %)** target 0 %, **110–120 %** target ≤10 %,
   **Underloads (70–90 %)** target 5 % (from `MeasuredTon`). +/− tonnes cells are shaded green = gain,
   red = loss. Availability/dig-rate budgets are the fleet-type values (`PA495/UA495/NOH495/GOH495`,
   `PA8000/UA8000/NOH8000/GOH8000`, `TPNOH*`) applied to each unit's own operating hours.
6c. **Hourly Production** — a wide **KPI × hour** table (styled after the reference "Hourly Performance"
   sheet): columns **KPI · UOM · Total · then one column per shift-hour** (`HH:00`, 12 buckets from the
   shift start). Loads are bucketed by **dump time**. Rows: production totals (**Loaded, Ore Moved, Waste
   Moved** in tonnes; **Load Count**) then per-load averages (**Payload — CAT 797** in wTons; and the
   cycle segments **Truck Cycle Time, Spot at Shovel, Load Time, Shovel Hang, Truck Wait at Shovel, Wait at
   Dump, Dumping Time** in mm:ss). **Total** = shift sum for tonnes/count, shift average for times/payload.
   Each hourly cell is colour-shaded vs a target — **green = better, red = worse**: production is
   higher-is-better vs plan÷12; cycle times are lower-is-better vs the **load-weighted budget** (fixed times
   + curve travel); Payload vs the 361 t target; Shovel Hang (no fixed-time budget) vs the **shift average**.
   Load Count is left unshaded. Shade intensity scales with the % deviation.
7. **Appendix** — a reference tab listing **all target/budget numbers** the dashboard uses for the selected
   shift: global constants (payload target 361 t, NOH floor, score/balance bands), per-pit availability &
   rate budgets (PA/UA/OE for Cat 797 / BE 495B / HIT 800 — shovel OE derived NOH÷GOH — with NOH/GOH/TPNOH),
   truck cycle fixed-time budgets (Fixed_Times), the derived shovel cycle budgets (TPNOH, Spot_b, Load_b,
   constant Hang_b, cycle), plan tonnes per pit, and the haul-curve TPNOH/cycle/travel for the HD buckets
   hauled this shift. Pulls from the budget CSVs; updates with the shift selector.

6. **Fuel and Lube** — lube-land delay analysis from `TruckAtLubeLand.csv`, **entirely for the selected
   shift + MRM/JPM view** (no cross-shift content): an **Hourly fuel delay** chart (12 hourly buckets from
   shift start, x-axis = clock time of shift formatted `HH:00` e.g. `18:00`, **y-axis in hours**, stacked
   actual delay by reason — FUEL&LUBE / WAIT FOR FUEL BAY / FUEL BREAK — vs an expected line; each bar is
   annotated with **total delay in hours** on top, **total occurrence count** at its base, and — when any
   WAIT FOR FUEL BAY events occur that hour — the **wait-for-bay occurrence count** in orange just below
   the axis). A fuel-level-at-refuel histogram (custom bin edges **0·8·16·24·32·40·60·80·100 %**; faulty > 100 % reads and zero/missing
   excluded) with each bar labelled **% of the day's refuels** on top; plus an actual-vs-expected-by-reason
   table (where **% over** = share of that reason's events whose actual duration exceeded expected), an
   overrun leaderboard, and average lube time by truck class. A **Faulty fuel-level sensors** table lists,
   grouped by truck class, every truck whose `FuelLevel` read **> 100 %** (the impossible ~163/164 %
   value that flags a broken level sensor), with its bad-read count and reported level. **FUEL&LUBE / FUEL
   BREAK events under 20 s are excluded from all metrics but counted** ("short" occurrences). Hourly
   buckets index events by minutes from shift start (`int(mfs/60)`, 0–11).

Chart.js charts (Shift Analytics tab) are rendered only when that tab is opened, so hidden canvases
never size to zero.

---

## 2. Equipment mapping (prefix-based)

| Asset | Prefix | Class | In scope |
|---|---|---|---|
| Truck | `T1…` | CAT 797 | Yes (haulage) |
| Shovel | `S0…` | BE 495 (cable) | Yes (loading) |
| Shovel | `S8…` | HIT 8000 (hydraulic) | Yes (loading) |
| Everything else (T4xx, S2xx, S3xx) | — | — | **Ignored for now** |

- **Material** comes from `MaterialGroupName` (`Ore` / `Waste`).
- **Pit** comes from `LoadPit` (for T1 loads `LoadPit` always equals `DumpPit`).

---

## 3. Data sources

> **SSRS-export compatibility.** The Data CSVs may come from SSRS reports, which name every column
> `Dtl_<Name>_<pos>` (e.g. `Dtl_Excav_5`), add a UTF-8 BOM, and write dates as `M/D/YYYY h:MM:SS AM/PM`.
> On load, `load_csv()` strips the `Dtl_` prefix and `_<pos>` suffix (plain headers pass through unchanged),
> and `_dtp()` parses the AM/PM format alongside the older ones — so both the SSRS and the earlier exports
> work. Two SSRS columns can be **blank**: `Statusevents.StartTime` and `TruckAtDump.Moment`. Availability,
> scores, waterfalls, delays, hourly and payload all still work (they use `Duration`/tonnage, not the
> timestamp); the **equipment status timelines** fall back to **cumulative `Duration`** placement, but the
> **trucks-at-dump / crusher timeline needs `Moment`** and stays empty until that column is populated.
> Point the build at a different data folder with the `DASH_DATADIR` env var (defaults to `Data`).

| File | Used for | Key fields |
|---|---|---|
| `Data/AllLoadsDumps.csv` | Haulage, Loading, Trucks waterfall | `Truck`, `Excav`, `LoadPit`, `MaterialGroupName`, `LoadLocation`, `DumpLocation`, `Tonnage`, cycle-component seconds, `FullHaulDistance`, `EmptyHaullDistance` |
| `Data/Statusevents.csv` | Availability (PA/UA/OE), status timelines, delay pareto/variance | `ShiftId`, `Eqmt`, `TimeStamp`, `Reason`, `Duration` (s), `ExpectedDuration` (s, may be NULL), `Timecat`, `ASEStatus`, `Eqmttype`, `Pit` — column names re-cased in the latest export; aliased at load |
| `Data/TruckatShovel.csv` | Trucks-at-shovel overlay (shovel status timeline) | `shiftId`, `Excav`, `LogTime`, `TrucksAtShovel`, `TrucksInQueue`, `QStatus` |
| `Data/TruckAtDump.csv` | Trucks-at-crusher timeline | `shiftId`, `DumpLocation`, `LogTime`, `TrucksAtDump`, `TrucksInQueue`, `QStatus` — **same schema as TruckatShovel** (latest export; previously had `Moment`/`TrucksAtDump` only) |
| `Data/TruckBalance.csv` | Truck Balance gauge & shift time-series | `Shiftid`, `Pit`, `TotalRequired`, `LPActual`, `Short`, `Timestamp` — **now carries `Shiftid`**, so rows are matched **by shift** (was timestamp-window matched) |
| `Budget/MRM Haul Curve.csv`, `JPM Haul Curve.csv` | Haulage Potential, truck cycle budgets | `Material`, `HD (km)`, `Month`, `TPNOH (t/h)`, `Cycle (min)`, `Travel (min)` |
| `Budget/MRM_Fixed_Times.csv`, `JPM_Fixed_Times.csv` | Truck fixed-time budgets (min) | `month_f`, `Material2`, `Queue_Time`, `Spot_Time`, `Load_Time`, `Dump_Idle`, `Dumping_Time` — **seasonal**: summer (May–Nov) and winter (Dec–Apr) each carry one value set per pit × material; every month in a season shares that set |
| `Budget/MRM 2026 Budget.csv`, `JPM 2026 Budget.csv` | Loading TPNOH, truck NOH/payload | `Month/Date/Shift`, `NOH797`, `TPNOH797`, `TPNOHO495`, `TPNOHW495`, `TPNOH8000`, `TPNOHW8000` |

**Cycle-component columns (seconds) in AllLoadsDumps:**
`SpotTime`, `LoadingTime`, `QueueTimeShvl`, `HangTime`, `EmptyHaulDuration`, `FullHaulDuration`,
`QueueTimeDmp`, `DumpSpotTime`, `DumpingTime`.

> **Data quirks (handled in code):**
> 1. The **JPM Haul Curve** file's internal `Pit` column is mislabeled `MRM` — curves are matched **by filename**, not by that column.
> 2. `EmptyHaullDistance` is spelled with a double-l in the header.
> 3. The Ore-8000 budget column is `TPNOH8000` (missing the "O" of the `TPNOHO…` pattern).
> 4. `DumpSpotTime` is `0` for all records → "dump idle" actual is taken from `QueueTimeDmp`.
> 5. **AllLoadsDumps timestamps** are valid in the current (Jul 1–25) export — `DumpingTimestamp` and
>    `ShiftStartTimestamp` carry full `M/D/YY H:MM` datetimes, so the **time-of-day production charts
>    (cumulative & hourly)** work. A minority (~28 %) of rows have a missing/unparseable timestamp; those are
>    simply excluded from time-of-day **bucketing** (their tonnage still counts in totals/scores). (An earlier
>    export had these reduced to `MM:SS`, disabling the two charts — no longer the case.)
> 6. `Statusevents.csv` was re-exported with **renamed columns** (`StartTime→TimeStamp`, `EqmtType→Eqmttype`,
>    `TimeCat→Timecat`); these are **aliased at load** so all code keeps working. `TimeStamp` now carries
>    full `M/D/YY H:MM` datetimes, so the **shovel and crusher status timelines are placed by real time** —
>    segments sit at their true offset from shift start (a shovel that started mid-shift shows the leading
>    gap). (An earlier export had lost the date on `TimeStamp`, forcing a cumulative-`Duration` fallback;
>    that reconstruction has been removed now that the timestamps are clean.)
> 7. `TruckBalance.csv` **now carries `Shiftid`**, so rows are matched **by shift id** (all 49 shifts covered)
>    instead of the old timestamp-window heuristic — this fixed the earlier gap where the balance graph only
>    appeared on a couple of shifts. The gauge still reflects only the
>    **most recent hour** of readings inside that shift window (not the whole-shift average).

---

## 4. Haulage Score (trucks)

Filter to **T1** trucks. Per load record:

**Payload-normalized basis (adopted).** Potential is built on a **payload-normalized rate** so that a
load's budget-cycle output equals the **361 t** payload target, which keeps the Haulage-Score Potential
identical to the truck-waterfall Potential and lets the waterfall close with no residual (see §7a).

```
cycle_seconds_actual = EmptyHaulDuration + SpotTime + LoadingTime + QueueTimeShvl
                     + FullHaulDuration + QueueTimeDmp + DumpSpotTime + DumpingTime      # NULL → 0

HD_km  = clamp( round(FullHaulDistance / 1000, 1), 0.5, 15.5 )
budget_cycle_seconds = ( Queue_Time + Spot_Time + Load_Time + Dump_Idle + Dumping_Time  # fixed times (min)
                       + Travel ) × 60                                                   # + haul-curve travel (min)
rate*          = 361 t / budget_cycle_seconds                 # payload-normalized (t/s); 0 if no budget
Potential_load = rate* × cycle_seconds_actual                # tonnes
```

The published haul-curve `TPNOH` still drives the distance bucket lookup (it sets `Travel`), but the
rate that converts time to tonnes is `rate*`, not `TPNOH/3600`. Roll up: **LoadLocation × DumpLocation →
Ore/Waste → total.**

```
Potential = Σ Potential_load
Actual    = Σ Tonnage
Haulage Score = Actual / Potential
```

**Zero-distance rule:** if `FullHaulDistance = 0`, use the average distance of other loads on the same
LoadLocation→DumpLocation lane; if the lane has no other loads, default to **5 km**.

> **Note — basis change:** this replaces the earlier `Potential = TPNOH × actual cycle-hours`. Because
> `rate*` is ≈ 361/343.5 ≈ 5% higher than `TPNOH/3600`, Potential rises ~5% and the Haulage Score drops
> ~5% (e.g. shift 260708002: Potential 335,365 → 352,891 t, Score 91.0 % → 86.4 %). In exchange, the
> waterfall Payload row is measured against the true 361 t target and the bridge carries no residual.

---

## 5. Loading Score (shovels)

Filter to **S0 (BE 495)** and **S8 (HIT 8000)** shovels. Per load record:

```
NOH_hours = max(150 s, SpotTime + LoadingTime + HangTime) / 3600     # touch-time, floored at 2.5 min
TPNOH     = ShovelBudget[pit][shovel_type][material]                 # t/h, from 2026 Budget csv
Potential_load = NOH_hours × TPNOH
```

TPNOH column map (2026 Budget, matched on Month/Date/Shift):

| Shovel type | Ore | Waste |
|---|---|---|
| BE 495 (S0) | `TPNOHO495` | `TPNOHW495` |
| HIT 8000 (S8) | `TPNOH8000` | `TPNOHW8000` |

Roll up by **pit × shovel type × material**:

```
Potential = Σ Potential_load
Actual    = Σ Tonnage
Loading Score = Actual / Potential
```

Result (Combined): Potential 391,420 t · Actual 409,372 t · **Score 104.6 %**.

> **Known characteristic:** because NOH is *touch-time only* (no idle/waiting), Potential ≈ Actual and the
> score sits near/just above 100 %. It measures actual loading rate vs budget rate, **not** capacity
> utilisation. Switching NOH to a full operating-time base (from ShiftStatusEvents) would make Potential a
> capacity ceiling and pull the score below 100 % — deferred.

---

## 6. Truck Balance

From `TruckBalance.csv` (columns `Shiftid`, `Pit`, `TotalRequired`, `LPActual`, `Short`, `Timestamp`). Rows
are matched to the selected shift **by `Shiftid`** (the latest export added this column; previously matched
by timestamp window). The gauge then uses the **most recent hour** of readings in that shift (per pit, the
latest timestamp and everything within 60 min before it):

```
Required = Σ_pit mean(TotalRequired over most-recent hour)
Actual   = Σ_pit mean(LPActual   over most-recent hour)
% under  = (Required − Actual) / Required
```

The gauge header shows an **"as of HH:MM"** stamp (latest reading used). Label band: `|%| ≤ 5` →
**Balanced**; `> 5` → **Under-Trucked**; `< −5` → **Over-Trucked**. Only shifts whose window overlaps
the CSV timestamps show data (others show "No data").

**Truck Balance — over the shift** (v2, below the gauge): a time-series SVG of **Trucks Required vs Actual
across the whole shift** (x-axis = clock time from shift start). Each pit's readings are bucketed to 15-min
ticks and **forward-filled** (so a pit that starts reporting mid-shift doesn't create a false step), then
summed across the pits in the view. Required is a dashed amber line, Actual (LP) a solid blue line, and the
**region between them is shaded** — Actual below Required = under-trucked, above = over-trucked. Point
tooltips give the time and both values.

---

## 7. Trucks Losses & Gains waterfall (Option 1)

Bridges **Potential (realized-cycle, = Haulage Potential) → Actual**. Every row is valued in tonnes;
green = gain (beat target), red = loss.

Rendered as a **classic floating-bar waterfall** with a tabular header: a dark header bar carries the
columns **KPI · UOM · Target · Actual**, and every row shows its name, unit (mm:ss / wTons / % / t/h), and
target-vs-actual values in those columns. The plot area has a **light-grey background with white vertical
gridlines** at round tick values and an x-axis of tonnage labels. A Potential start anchor (indigo, value
centred in the bar) steps up (green gain) / down (red loss) through each KPI with dashed connectors to the
Actual anchor; the signed delta is labelled at the moving end of each bar. The shovel bridge and every lane
drill-down use the same renderer.

**Per load**, with the **payload-normalized rate** `rate* = 361 t / budget_cycle_seconds` (t/s), where
`budget_cycle_seconds = (Queue_Time + Spot_Time + Load_Time + Dump_Idle + Dumping_Time + Travel) × 60`:

| Row | Actual (sec) | Budget (sec) | Tonnes |
|---|---|---|---|
| Load | `LoadingTime` | `Load_Time × 60` | `rate* × (budget − actual)` |
| Queue at Shovel | `QueueTimeShvl` | `Queue_Time × 60` | `rate* × (budget − actual)` |
| Spot at Shovel | `SpotTime` | `Spot_Time × 60` | `rate* × (budget − actual)` |
| Dump Idle | `QueueTimeDmp` | `Dump_Idle × 60` | `rate* × (budget − actual)` |
| Dumping | `DumpingTime` | `Dumping_Time × 60` | `rate* × (budget − actual)` |
| Full Haul | `FullHaulDuration` | `Travel × 60 × FFULL` | `rate* × (budget − actual)` |
| Empty Haul | `EmptyHaulDuration` (+`DumpSpotTime`) | `Travel × 60 × (1−FFULL)` (+0) | `rate* × (budget − actual)` |
| **Payload** | `Tonnage` | **361 t (hard target)** | `Actual − 361` (tonnes, not time) |

```
Potential + Σ rows = Actual   (residual = 0, no residual bar)
```

**No residual — payload-normalized basis.** Because `rate* × budget_cycle_seconds = 361` by construction,
`Potential + Σ time rows` lands exactly on `361 × loads`; adding the Payload row `Actual − 361` closes to
Actual with nothing left over. The Payload row is therefore measured against the true **361 t target**,
and the Potential anchor equals the Haulage-Score gauge (both use `rate*`).

> **Evolution of this bridge.** (1) The original waterfall anchored on `TPNOH × actual cycle time` and
> charged Payload against a flat 361 t; the ≈346-vs-361 mismatch left a residual bar. (2) It was then put
> on a single haul-curve basis (Payload vs `TPNOH × budget cycle time` ≈346 t), which removed the residual
> but the Payload row no longer read against 361 t. (3) **Current:** the payload-normalized `rate*` gives a
> residual-free bridge *and* a Payload row measured against the real 361 t target — at the cost of Potential
> (and the Haulage Score) shifting ~5% vs the published-TPNOH basis. The 361 t compliance detail also lives
> in the payload-compliance box plot (Shift Analytics).

**FFULL (full-leg share of budget travel time)** is derived from actual speeds:

```
v_full  = Σ FullHaulDistance  / Σ FullHaulDuration     # ≈ 19.1 km/h (loaded)
v_empty = Σ EmptyHaullDistance / Σ EmptyHaulDuration   # ≈ 23.9 km/h (empty)
FFULL   = v_empty / (v_empty + v_full)                 # = 0.555
```

**Leading measure** shown next to each row = load-weighted average **actual → budget** (mm:ss for time
rows, tonnes for Payload); the actual figure is coloured green/red to match the row, the budget stays muted.

**PA / UA / OE rows & Scheduled Potential** (main trucks waterfall only): above the realized Potential the
bridge shows the **Cat 797 availability breakdown vs budget**, replacing the old single NOH row. The top
anchor is **Sched. Potential = realized Potential − (PA + UA + OE tonnage effects)** — i.e. the potential
the fleet would reach at *budget* PA/UA/OE — and three rows (**PA → UA → OE**) step from it to the realized
Potential.

PA/UA/OE are computed exactly as on the **KPI tab** (Cat 797, `ASEStatus`, Parked folded into Standby):
`PA = (R+De+S)/(R+De+S+Dn)`, `UA = (R+De)/(R+De+S)`, `OE = R/(R+De)`. Budget PA/UA/OE come from the budget
CSV (`PA797`, `UA797`, `OE797`), NOH-weighted for the display %. The **tonnage effect** of each is a
sequential decomposition of net operating hours `NOH = TH × PA × UA × OE` (TH = total accounted hours),
converted at `TPNOH797`, done **per pit then summed**:

```
PA_t = Σ_pit  TH × (PAa − PAb) × UAb × OEb × TPNOH797
UA_t = Σ_pit  TH × PAa × (UAa − UAb) × OEb × TPNOH797
OE_t = Σ_pit  TH × PAa × UAa × (OEa − OEb) × TPNOH797
```

so `PA_t + UA_t + OE_t = realized Potential − Sched. Potential`, and the bridge closes exactly. Each PA/UA/OE
bar has its **actual → budget %** as the leading measure (actual coloured). **Red (loss) bars are
subdivided by reason** — thin white lines mark each Down / Standby / Delay code's share of that category's
hours (PA↔Down, UA↔Standby, OE↔Delay), with the major reasons labelled inside the wide-enough segments so
they stand out; full reason + hours are in each segment's hover tooltip. **Green (gain) bars carry no
dividers and no text** — a plain bar. The header indicator badge shows PA/UA/OE actual/budget. The
**Ore/Waste split waterfalls also carry PA/UA/OE**: availability is fleet-level, so its tonnage effect is
**allocated to ore vs waste by each material's share of Potential** (the act/bud % shown are the fleet
values — identical on ore and waste — only the tonnage bars differ; reason segments carry over at the same
proportions). The **per-lane waterfalls (Haulage drill-down) still start at Potential with no availability
rows** (a lane is too granular to attribute fleet availability to). Every truck/haulage waterfall closes
exactly (`Potential + Σ rows = Actual`, **no residual bar**), and the availability rows add on top with
`Sched. Potential + PA + UA + OE + cycle rows = Actual`.

### Indicators (shown, not blended into tonnes)

```
Truck NOH%  = actual_NOH / (NOH797 × elapsed/12)
              actual_NOH = Σ Duration where Eqmttype='Cat 797' AND Timecat='Operating', per Pit
Empty/Full distance ratio = Σ EmptyHaullDistance / Σ FullHaulDistance     # target = 1.000
```

NOH% is **not** a tonnage bar: the realized-cycle Potential already reflects actual operating time, so
there is no headroom for an availability loss (trucks ran +4 % over budget NOH this shift). Making NOH%
a tonnage row would require re-anchoring Potential to `budget_NOH × TPNOH797` — deferred.

### Lane drill-down

The identical waterfall is computed for every **LoadLocation → DumpLocation** lane. Lanes are ranked by
Potential and colour-flagged by score (green ≥ 95 %, amber 85–95 %, red < 85 %), with the single biggest
loss row called out. Clicking a lane renders its own waterfall.

**Lane filters (drill-down list only — totals unaffected):**
1. Lanes where `LoadLocation = DumpLocation` are excluded (data artifacts — these are the zero-distance records).
2. Lanes with **fewer than 5 loads** are excluded (low-volume noise).

These filters apply only to the lane drill-down display; the section-level Potential, Actual, and Score
still include every load.

**Lane list layout:**
- Lanes are **grouped and ordered by loading shovel** (the dominant `Excav` on that lane), then by Potential within each shovel.
- Each shovel group has a **subtotal header row**: summed Loads, Potential, Actual, and the group Score (colour-flagged green ≥ 95 %, amber 85–95 %, red < 85 %).
- Each lane row shows **average Full / Empty haul distance (km)** — mean `FullHaulDistance` and `EmptyHaullDistance` over the lane's loads (zero values excluded from the average).
- Clicking a lane renders its own waterfall; KPI row labels are colour-matched to their bar (green gain, red loss). The bridge closes on Actual with no residual bar.

---

## 7b. Shovels Losses & Gains (productivity bridge)

The shovel TPNOH is a **blended rate already calibrated over the full touch-time (including normal
hang)**. So the shovel side cannot be split into Spot/Load/Hang against the truck Fixed_Times — those
budgets don't reconcile with the shovel TPNOH (implied payload swings 240–336 t across shovel types, vs
the 361 t target, producing a meaningless ~88 kt residual). A detailed KPI-time shovel waterfall would
require **shovel-specific** budget times (budget hang, budget load per truck class) that are not in the
current budget files.

Instead the shovels use a **productivity bridge** that reconciles exactly:

```
Potential = Σ (touch-time NOH × budget TPNOH)        # = Loading Potential
Actual    = Σ Tonnage
For each segment (shovel type × material):
    variance (t) = Actual_segment − Potential_segment
                 = (actual loading rate − budget TPNOH) × segment NOH hours
Potential + Σ segment variances = Actual              # exact
```

Each row's leading measure is **budget TPNOH → actual rate** (t/h). Rows: BE 495 · Ore, BE 495 · Waste,
HIT 8000 · Ore, HIT 8000 · Waste. A green bar = the segment loaded faster than its budget rate.

**Shovel drill-down:** per individual shovel unit (Excav), ranked by Potential, showing loads,
Potential, Actual, Score, and budget → actual rate — colour-flagged (green ≥ 100 %, amber 92–100 %,
red < 92 %) to surface which shovels are productive vs lagging.

### 7b(new). Shovel Losses & Gains — availability + cycle (tab: **Shovel Waterfall**)

The **shovel waterfall** (formerly "Shovel Waterfall 2"; the original cycle-only tab was removed) mirrors the
trucks method:
**Sched. Potential → PA · UA · OE → Potential → Payload · Spot · Load · Hang → Actual.** The tab also has a
**hidden Ore/Waste breakdown** (toggle) which carries the **full bridge including PA/UA/OE** — availability
is fleet-level, so its tonnage effect is **allocated to ore vs waste by each material's Potential share**
(act/bud % are the fleet values on both). There is also a **shovel drill-down**: a per-unit table (Excav,
type, loads, Potential/Actual/Score) where **clicking a shovel renders that unit's own full waterfall**
below — **Sched. Potential → PA · UA · OE → Potential → Payload/Spot/Load/Hang → Actual**. Availability is
now **attributed per unit**: each shovel's PA/UA/OE come from its own `Statusevents` (Eqmt), decomposed the
same sequential way (TH × PA × UA × OE), so the per-unit bridge closes exactly (schedPotential + PA/UA/OE
losses = cycle Potential) and the drill-down **Potential/Score in the list use the top-anchor schedule
potential** — so a unit's drill-down score matches its Shovel Productivity score (e.g. S008 ≈ 19.5 %, vs a
cycle-only 39 %). Every breakdown reuses the same renderer and closes with no residual. The tab also carries an
**Equipment status timeline — shovels** that is **filtered to the selected shovel** (mirroring the waterfall
selection): for a single shovel it renders as a **tall band** — trucks-at-shovel count (line, 0–qmax, with
horizontal guides) over a **status strip** (Ready/Delay/Standby/Down) at the base, plus that unit's average
queue and hang min/load — the same enlarged style as the trucks-at-dump graph. (The all-shovels overview
timeline remains on the Shovel Waterfall tab.)

*Availability rows (PA/UA/OE)* — same calc as the KPI tab, but for **BE 495B (cable) + HIT 800
(hydraulic)** combined. Budget PA/UA come from `PA495/UA495`, `PA8000/UA8000`; **budget OE is derived as
`NOH ÷ GOH`** (validated: `NOH797/GOH797 = OE797 = 0.925` exactly), giving cable ≈ 0.905, hydraulic ≈ 0.85.
Tonnage effect is the same per-pit sequential decomposition (`TH·ΔPA·UAb·OEb`, etc.) × the shovel rate
(`TPNOHCable` / `TPNOHHydro`), summed. Red bars split by Down/Standby/Delay reason (green plain), as on the
trucks waterfall.

*Payload + cycle rows (Payload/Spot/Load/Hang)* — built from a **budget cycle for the 361 t payload target**.
Per group (**shovel fleet · pit · month · material**):

```
Spot_b, Load_b = Fixed_Times (× 60, seconds)               # constant per pit·month·material
c_b    = 361 × 3600 ÷ TPNOH                                  # budget cycle to load 361 t at budget rate
Hang_b = c_b − Spot_b − Load_b                              # constant hang target per group
rate   = 361 ÷ c_b  ( = TPNOH ÷ 3600 )                     # the published budget rate
per load:  Spot/Load/Hang = rate × (seg_b − seg_a) ;  Payload = Tonnage − 361
```

The budget cycle uses the **constant 361 t target** (not the group's mean payload), so `rate` collapses to
the published `TPNOH/3600`. Because the cycle rows land on `rate × c_b = 361` and the Payload row carries
`Tonnage − 361`, `Potential + Payload + Spot + Load + Hang = Σ Tonnage = Actual` **closes exactly, no
residual**. The **Payload row** compares actual truck payload to the **361 t target** (flat, matching the
trucks waterfall and the payload-compliance box plot — note the shovels load ~4 % smaller trucks that read
below 361). The **Hang target is a stable per-group constant** from budget TPNOH + Spot + Load (mean payload
is shown in the Appendix for reference only). If a group's `Hang_b` comes out negative it is clamped to 0
(badge notes the load count); in the current data no groups clamp.

**Sanity-check notes:** (1) Because shovel TPNOH is very high (~3,400–4,500 t/h), the **availability rows
dwarf the cycle rows** — Sched. Potential can be ~2× Actual — so the waterfall is availability-dominated by
design. (2) Shovel **Delay reasons are operational** (Weather–Operator On Board, moves, pit prep), *not*
"waiting for trucks", and status delays have no load records, so the OE row and the Hang cycle row are
**temporally disjoint — no double counting**. (3) This graph's Score differs from the productivity-bridge
Loading Score because it is built on a different basis. The existing productivity bridge is unchanged.

---

## 7c. Shovel placement — Sankey (shovel → dump)

A "Shovel placement" section (on the **Shovels** tab) showing a **Sankey** of tonnage routed from each
shovel (LoadLocation) to each dump (DumpLocation), built from T1-truck loads and following the
MRM / JPM / Combined toggle.
Ore = teal, waste = amber. (The spatial flow map and full-vs-empty scatter were prototyped but dropped —
without an actual haul-road network model, straight-line geometry misrepresents real cycle distance.)

- **Left nodes (shovels):** label = shovel id + **actual average TPNOH** (t/h) = total tonnage ÷ total
  loading hours at that location, where loading hours = max(2.5 min, Spot+Load+Hang) per load ÷ 3600
  (the realized loading rate, not the budget standard).
- **Right nodes (dumps):** label = dump id + **total tonnes received**.
- **Ribbons:** width ∝ tonnage, coloured by material; node heights ∝ total tonnage loaded / received.
- **Small-ribbon filter:** flows below ~1.5 % of the column total are hidden so labels stay legible.
- Locations are still validity-filtered by the median-GPS bounding box (drops garbage points); self-flows
  (load = dump) are excluded. Combined view concatenates both pits (location ids are unique across pits).

---

## 7d. Shift Analytics (6 charts)

A "Shift Analytics" section (6 charts), per view (MRM / JPM / Combined), rendered with **Chart.js 4.4.1** (inlined
into the HTML from `lib_chartjs.js` — no CDN, fully offline) except the timeline and the payload box plot which are hand-drawn SVG.
The plan/target tonnage is taken **straight from the budget CSV per shift**: `planTotal = POre + PWst`
(productive ore + waste plan) — MRM 276,708 t, JPM 140,588 t, ≈ 417 kt combined. (This matches
`NOH797 × TPNOH797` to within ~1 %.) Production series use T1-truck loads.

1. **Shift progress — cumulative tonnes vs target.** Line chart: actual cumulative tonnage in 15-min
   buckets (from `DumpingTimestamp`) vs a linear target ramp 0 → planTotal over the 12 h shift.
2. **Hourly tonnes vs target.** Stacked bars per hour: productive ore, productive waste, non-productive
   (`Category = Non-Productive`). Target lines come from the budget ÷ 12 but are **stacked cumulatively in
   the same order as the bars**, so each dashed line marks the top the stack should reach at that level:
   **ore** target `POre/12` (teal), **+waste** cumulative `(POre+PWst)/12` (amber), **+non-prod**
   cumulative `(POre+PWst+NPOre+NPWst)/12` (red = grand total). This lets each stacked segment's actual top
   be compared directly against its budgeted top; the tooltip shows both the cumulative value and that
   segment's own per-hour target.
3. **Equipment status timeline — shovels** (shown on the **Shovels** tab). SVG Gantt: one row per S0/S8
   shovel, segments coloured by **`ASEStatus`** — Ready (green), Delay (yellow), Down (red), Standby
   (blue; Parked folded in), over the 12 h shift; the **segment tooltip shows the delay `Reason`**. Each
   row overlays a **stepped black line** of `TrucksInQueue` (from `TruckAtShovel.csv`), scaled 0–max
   queue, for shovels with queue data (the overlay uses **`TrucksAtShovel`**, from `TruckatShovel.csv`).
   The **shovel-ID label is coloured by dominant material** (ore teal / waste amber, matching Shovel
   placement), with the shovel's **average queue-at-shovel and hang time per load** (`QueueTimeShvl` and
   `HangTime` averaged over its loads in the current view, min/load) shown beneath the ID.
4. **Lost time — delay pareto.** Bar + cumulative-% line of delay hours by `Reason`, filtered to
   `ASEStatus = 'Delay'` (operational delays only — excludes Down/maintenance and Standby), top 20 +
   Other, per pit. Each bar is labelled with its **total lost hours** on top and its **occurrence count**
   (`n×`) at the base (on the x-axis line); tooltip shows hours and event count per reason.
5. **Delay variance — actual vs expected.** Signed bar chart of **actual − expected** delay hours by
   `Reason` (`ASEStatus = 'Delay'`), ranked by |variance| descending. The **y-axis spans negative and
   positive** (zero line emphasised) so the direction is plotted directly: bars go **up and red where
   actual > expected** (over-run) and **down and green where actual < expected** (under). Each bar is
   labelled with its **signed variance (actual − expected, h)** at the bar end and its **occurrence count**
   (`n×`) at the zero baseline. Built from the same delay
   rows as the pareto but **only where `ExpectedDuration` is present** — rows with a NULL/blank expected
   are dropped entirely (so unplanned delays such as OPERATOR ON BOARD, which carry no standard, don't
   appear). Top 20 reasons by |variance| + Other, per pit; tooltip shows signed variance, actual,
   expected and event count. Sits between the lost-time pareto and the payload chart.
6. **Payload compliance per shovel.** Uses **`MeasuredTon`** (actual weighed payload, ≥ 50 t to drop
   zero/erroneous readings) — **not** the nominal `Tonnage` field (which is a rated payload, constant per
   truck class). SVG **box plot** per S0/S8 shovel (T1 loads): box = Q1–Q3
   (25th–75th percentile), median line, and mean marker (◆); points beyond 1.5 × IQR of the quartiles are
   drawn as outlier circles. **No whiskers** (removed — box only). Dashed target line at 361 t. Boxes are
   coloured by the shovel's dominant material (ore teal / waste amber, matching the Shovel-placement
   palette). Compliance % (within ±5 % of target) is in the tooltip. Hand-drawn SVG (no Chart.js), so it
   works offline.

> Chart.js is **inlined** (from `lib_chartjs.js`), so all charts work fully offline with no external
> calls. Rendering is wrapped in try/catch so a problem in one chart never breaks the rest of the page.
> If `lib_chartjs.js` is ever missing, the generator falls back to the jsdelivr CDN automatically.

---

## 8. Assumptions register

1. Only T1 / S0 / S8 assets are in scope; all other equipment is excluded.
2. Material = `MaterialGroupName`; Pit = `LoadPit`.
3. Haul curves matched by **filename** (JPM file's internal Pit label is wrong).
4. Zero haul distance → same-lane average → else 5 km default.
5. NULL `EmptyHaulDuration` treated as 0 in cycle time.
6. Shovel NOH = touch-time (spot+load+hang), floored at **2.5 min** (removes physically-impossible sub-minute loads).
7. "Dump idle" actual = `QueueTimeDmp` (`DumpSpotTime` is all zeros).
8. Budget travel split 0.555 full / 0.445 empty, derived from actual loaded vs empty speeds.
9. Potential & waterfall use a **payload-normalized rate** `rate* = 361 t ÷ budget cycle time`, so budget-cycle tonnes = 361/load, the Payload row is `Actual − 361`, and the bridge closes with **no residual**. This raises Potential ~5% and lowers the Haulage Score ~5% vs the published-TPNOH basis; the Haulage-Score gauge uses the same `rate*` so it matches the waterfall Potential.
10. Trucks waterfall anchored on **realized-cycle** Potential (`rate* × actual cycle time`); above it, **PA/UA/OE availability rows** (Cat 797 vs budget CSV) step down from Sched. Potential — each bar embeds its top Down/Standby/Delay reason. Empty/Full is an indicator; no residual.
11. Budget NOH prorated by elapsed/12 (= 11.23 / 12).
12. Truck Balance from the most-recent hour of the selected shift, ±5 % "Balanced" band.
13. Lane drill-down excludes (a) lanes where LoadLocation = DumpLocation and (b) lanes with < 5 loads. These filters affect the lane list only, not the section totals.

---

## 9. Open / fine-tune items

- **Shovels waterfall — detailed KPI rows** (hang, spot, load-time & payload by truck class): blocked on shovel-specific budget times not in the current budget files. Current version is the productivity bridge (section 7b).
- **NOH% as tonnes** and **Loading Score base** (touch-time vs full operating time): re-anchor decisions deferred.
- **Residual**: eliminated — Potential and the waterfall use the payload-normalized `rate*` (361 ÷ budget cycle time), so every truck/haulage waterfall closes exactly on Actual with the Payload row measured vs 361 t.
- **Empty/full speed split**: currently data-derived; can be replaced with a budgeted split.

---

## 10. How to regenerate / deploy

**Update (one-click):** double-click **`Update Dashboard.bat`** (Windows) or run
**`./update_dashboard.sh`** (macOS/Linux) after refreshing the CSVs. Either runs `build_dashboard.py`,
which regenerates `Haulage_Dashboard.html`. From a terminal: `python3 build_dashboard.py`.

**Fully portable — self-locating paths.** `build_dashboard.py` anchors every file path to **its own folder**
(`BASE = folder of build_dashboard.py`), and the updater scripts invoke it by its own path. So the whole
folder can be **copied or moved to any location or another computer** and the updater still works — it no
longer depends on the current working directory. Just keep the folder intact (`Data/`, `Budget/`,
`lib_chartjs.js`, and the scripts together). On macOS/Linux, if the copy loses the executable bit, run
`chmod +x update_dashboard.sh` once (or run it as `bash update_dashboard.sh`).

**For a new shift:** update the constants at the top of `build_dashboard.py` (`SHIFT_ID`, `SHIFT_MONTH`,
`SHIFT_DATE`, `SHIFT_NUM`, `SHIFT_NAME`, `ELAPSED_H`) and ensure the source CSVs cover that shift.

### Corporate deployment notes
- **No special access / no admin rights** needed to run the updater — it only runs a Python script.
- **No third-party packages.** The generator uses the Python **standard library only**
  (`csv, json, datetime, collections, statistics`). The only requirement is Python 3 installed. If IT
  won't install Python, the script can be packaged into a standalone `.exe` (PyInstaller).
- **Fully offline / no CDN.** Chart.js is inlined from `lib_chartjs.js`, so the HTML makes no external
  network calls — safe behind strict corporate firewalls.
- **The real gate is data access**, not the script: whoever can export the source CSVs into `Data/` and
  `Budget/` can refresh the dashboard.
- **Publishing:** the output is a single `Haulage_Dashboard.html`; drop it on a shared drive / SharePoint /
  intranet and any viewer can open it — nothing to install on their end.
- **Automation:** point Windows Task Scheduler / cron at the updater to rebuild each shift unattended.

### Files in the project
| File | Purpose |
|---|---|
| `build_dashboard.py` | The generator (all formulas, the single source of truth). Cross-platform. |
| `build_dashboard_windows.py` | Identical copy used as the Windows entry point (run by the .bat). Keep in sync with `build_dashboard.py` if either is edited. |
| `Haulage_Dashboard.html` | The deliverable — self-contained, open in any browser. |
| `Update Dashboard.bat` | Windows one-click updater → runs `build_dashboard_windows.py`. |
| `update_dashboard.sh` | macOS/Linux one-click updater → runs `build_dashboard.py`. |
| `lib_chartjs.js` | Local Chart.js library, inlined at build time (keep alongside the script). |
| `Dashboard_Methodology.md` | This document. |
| `Data/`, `Budget/` | Source CSVs. |
| `dashboard_data.json` | Intermediate data dump (optional/diagnostic). |
