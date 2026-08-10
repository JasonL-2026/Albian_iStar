# Albian Mine Haulage Dashboard — Methodology

Reference for the calculations behind `Haulage_Dashboard.html`. Re-running `build_dashboard.py`
reproduces every number below from the source CSVs. This document is the spec; the script is the
implementation.

---

## 1. Scope & shift navigation (multi-shift)

The dashboard is **multi-shift**. The build reads every shift present in `AllLoadsDumps.csv`, computes the
full dashboard for each, and embeds them all. A **shift selector** (dropdown + ‹ older / newer › arrows)
sits in the top bar and **defaults to the most recent shift**. Each dropdown entry appends the operating
**crew** as a suffix (e.g. "07-AUG-2026 Day Shift · Crew A"), read from the `Crew` column of `AllLoadsDumps.csv`.

- **Available shifts** are the distinct `ShiftId` values in `AllLoadsDumps.csv`, sorted most-recent-first
  (the `ShiftId` `YYMMDDsss` encodes date + sequence, so it sorts chronologically).
- Each shift's **date, month and base hour** are derived from `ShiftId` + `FullShiftName`
  (Day → 06:00 base, Night → 18:00 base) — the `ShiftStartTimestamp` column is unreliable (Excel mangles it).
- **Budgets** are looked up per shift by month/date/shift; haul curves and fixed times are keyed by month.
- **Nominal shift length** 12 h; NOH proration uses `elapsed/12` (elapsed defaults to 12 h for completed shifts).

Within a shift, three views come from the same logic: **MRM**, **JPM**, **Combined** — a view is just a
`LoadPit` filter. Both the shift selector and the MRM/JPM/Combined toggle sit in the top bar and apply to
whichever tab is open.

**State persistence & deep-linking.** The current **shift, view, tab, and the productivity-table toggles**
are remembered across reloads (saved to `localStorage`) and mirrored into the **URL hash**
(`#tab=…&shift=…&view=…`), so a given view can be **bookmarked or shared** — opening that URL restores it
(the hash takes precedence over the saved state; invalid ids fall back to defaults). Browser back/forward
re-applies the state.

**Print / PDF.** A **Print** button in the top bar (or Ctrl/Cmd-P) prints **just the tab currently open** via
a dedicated print stylesheet: the sidenav, shift selector, view toggle and the button itself are hidden,
colours are preserved (score shading, heatmaps), and cards/table rows avoid breaking across pages — so each
tab prints as a clean one- or two-page shift report.

A **Back ‹ / Forward ›** bar sits at the top-left of every page (above the first card, showing `n / total ·
<page name>`) and steps through the tabs in sidenav order (disabled at the first/last tab).

**Page layout (left-side tabs):** the sidenav is **two levels deep** — a handful of tabs are **indented
children** of the tab above them: **Loading drill-down** and **Shovel Productivity** sit under **Shovel
Waterfall**, and **Haulage drill-down**, **Delays & Standby**, and **Truck Productivity** sit under **Truck
Waterfall**. The parents are still their own clickable tabs (each has its own content); the nesting is
organisational only. Current top-level order: **Shift Overview · Truck / Shovel Balance · Shovel Waterfall**
(→ Loading drill-down, Shovel Productivity) **· Truck Waterfall** (→ Haulage drill-down, Delays & Standby,
Truck Productivity) **· Hourly Production · Fuel and Lube · Shift Stats · Cross-Shift Trends · Appendix ·
Sandbox**. *(A **Pulse — Shift State** tab built on `ShovelCoverageFactors.csv` was prototyped and is preserved
in `build_dashboard_v2.py` / `Haulage_Dashboard_v2.html`; it is **not** in the current main build.)* *(The former standalone **Shift Analytics** tab was removed — its **delay pareto** and **delay
variance** moved to **Delays & Standby** (fleet-filtered by the Truck/Shovel toggle), the **payload-compliance
box plot** moved to the **Shovel Waterfall** tab's toggles, and the **equipment status timelines** are now
reached via the **Timeline** hover-popup on each Equipment Hours card and the single-shovel timeline on the
Loading tab.)*
0. **Shift Overview** — the landing tab (first, and the default view). At the very top an **executive
   one-liner** summarises the shift: **Haulage X%** and **Loading Y%** (same Sched.-Potential ratios as the
   Balance-tab gauges, §2b), each **colour-coded** (< 90 red / 90–100 amber / ≥ 100 green) and **clickable —
   the Haulage score opens the Truck Waterfall, the Loading score the Shovel Waterfall**. Each score is
   followed by its **biggest pro (▲, largest positive tonnage factor) and biggest con (▼, largest negative)**
   drawn from that fleet's waterfall rows plus PA/UA/OE. It closes with **Truck match ±Z%
   (Balanced/Under-/Over-Trucked)**. Below it: the **Shift progress —
   cumulative tonnes vs target** line (with a **data label above the tip of the actual line** showing the latest
   cumulative tonnes, and a **red dashed "Future-shift pace" cumulative line** rising 0 → the tonnage a future
   shift must reach to hold the month run-rate). A **production projection** — an **amber dashed line + shaded
   area** — extrapolates shift-to-date productivity to shift end (linear from origin through the last actual
   point), with a **"proj … t" label below its tip** giving the projected shift-total tonnes (actual label sits
   above, projection below, so they don't collide). The pace = `(month ore+waste budget − month-to-date actual) ÷
   remaining budgeted shifts` (month budget and shift count from the pit Budget CSVs; actual-to-date sums the
   truck-waterfall actual of this month's shifts up to and including the current one). A **foot note under the
   chart** spells it out — the month budget, tonnes done so far, remaining shifts, the required per-shift
   average, and its % vs the plan and the **Hourly tonnes vs target** stacked bars (both moved here from Shift Analytics).
   The hourly bars are drawn **slightly lightened (≈80 % opacity) so the target lines read through**; **clicking
   an hour** (a bar or the axis) makes that hour **fully opaque** and opens a **side panel** next to the chart showing that
   hour's full **Hourly Performance** column (§6c) as **Actual + Δ-vs-target** columns — the Actual value is
   colour-shaded and the Δ is a **▲/▼ chip** (green ▲ = better than target, red ▼ = worse). Rows are **sorted by
   how far each metric is from its target (largest deviation first)**; metrics with no target sit at the bottom. The panel **defaults to the most recent hour with production**, and
   its header shows the **hour span** (e.g. `16:00 – 17:00`). A **Details** toggle on the hourly card
   shows/hides the panel (**hidden by default** — the bars use the full card width until it's turned on). The cumulative chart fills its whole card, and both
   cards **shrink responsively** with the page.
   Below them, the **Equipment Hours and Performance** card set (one per fleet, titled
   **Cable Shovel - BE 495B**, **Hydraulic Shovel - HIT 8000**, **Large Truck - Cat 797**). Each card headlines
   the **effective number of units deployed** = **GOH ÷ elapsed shift-hours**, where **GOH (gross operating
   hours) = Ready + Delay = NOH ÷ OE** — i.e. the time-average count of units that were switched on and operating
   (delay counts as deployed, so this is "deployed", not "producing"). The divisor is the shift's **elapsed**
   hours (so an in-progress shift isn't understated by a fixed 12); for a full day it would be ÷24, for an hour
   window ÷1. Each fleet card reports its **own** deployed count as-is; when an **overall** equipment count is
   needed the shovels are combined at the official **HIT 800 = 0.7 × BE 495** capacity ratio (equivalent BE 495
   units = cable + 0.7 × hydraulic), but that combined total is not shown on the per-type cards. The **Cat 797**
   card also appends the overall **average full-haul distance** (`@x.xkm`, from `FullHaulDistance`). Each card has
   a **Timeline** button that, on hover (or click), pops a floating panel with that fleet's **equipment status
   timeline** (filtered to its type) plus a **per-hour "deployed / hr" row across the top** and the shift-average
   deployed in the title. Below the
   headline is a single
   Actual · Budget · Δ table whose rows are **TPNOH** (t/h) on top, then **PA · UA · OE · POE** (their Δ shown
   with a **% sign**). TPNOH actual = tonnage ÷ Ready-hours vs budget t/h. **Every row is click-to-expand**:
   PA/UA/OE show their top reasons, POE the non-productive minutes/load, and **TPNOH expands to a per-unit
   leaderboard**. On the **two shovel cards** it lists every **shovel** as `unit (O/W)` with **actual · budget ·
   Δ columns aligned to the card's overall TPNOH row** (Δ colour-coded, sorted **Δ lowest → highest**), grouped
   into **Cable** and **Hydraulic** sections — the `(O)`/`(W)` tag is the
   shovel's **majority material** (ore/waste by tonnage) and the budget is the type's `TPNOHCable`/`TPNOHHydro`
   for its dominant pit. On the **Large Truck card** it instead shows **one row per loading shovel = the
   average TPNOH of the trucks that shovel loaded** (each truck assigned to the shovel that loaded most of its
   tonnage) vs the `TPNOH797` budget — grouped into Cable/Hydraulic sections. **Under each shovel, the unique
   dump locations it served are listed** (one row each as `<loads> - <dump> (<km>)`, sorted by **Δ tonnes,
   lowest → highest**; internal-road dumps `IN_*` and any load where dump = dig location are excluded), each
   showing **actual vs haul-curve-target TPNOH (actual colour-coded green/red vs target) and Δ total tonnes**.
   **Clicking a dump row** expands a small **line graph of that path's average truck cycle time per hour**
   (mm:ss, points bucketed by dump hour; hover a point for the value). Actual TPNOH = tonnes ÷ truck cycle-hours; the target is the
   **haul-curve `TPNOH` for that load's haul distance** (cycle-weighted); Δ tonnes = actual − target×cycle-hours
   (green = above the curve, red = below). *(The
   per-fleet Ready/Delay/Standby/Down hours line and the card's formula footnote were removed.)*
   **POE** (productive operating efficiency) strips the non-productive time out of Ready: **shovels** =
   (Ready − hang) ÷ (Ready+Delay); **trucks** = (Ready − queue-at-shovel − dump-idle) ÷ (Ready+Delay). Hang
   comes from `HangTime`, truck queue/idle from `QueueTimeShvl`+`QueueTimeDmp` (loads data); POE has no budget.
   Each metric row is **click-to-expand** — the reason breakdown is folded into the card: **PA** drops the
   top-5 **Down** reasons + Other, **UA** the top-5 **Standby**, **OE** the top-5 **Delay**, and **POE** shows
   the average non-productive minutes per load (hang for shovels; queue-at-shovel + dump-idle for trucks).
   Each reason names the **equipment unit** it came from (top contributor, "+N" if others also logged it) —
   **except on the Cat 797 (trucks) card, where the unit IDs are hidden** (there are too many trucks for a unit
   to be meaningful); shovel cards keep their unit IDs. On the shovel cards, **a reason whose "+N" hides more
   than one shovel is itself click-to-expand**, revealing each contributing shovel and its hours. A
   **Expand all / Contract all** toggle at the **top-right of the card** opens or closes every dropdown (metric
   rows and the nested reason breakdowns) at once; it also **tracks manual clicks** — expanding any single item
   flips it to "Contract all" so it doubles as a one-click collapse-everything shortcut. Each reason also shows its
   **impact on the metric in points** rather than a share of category hours: a reason of `x` hours costs
   the metric `x ÷ (metric denominator) × 100` points (PA denom = R+D+S+Dn, UA = R+D+S, OE = R+D). These
   per-reason points **sum to the metric's shortfall** (e.g. the Down impacts add up to 100 − PA), so they
   read as a decomposition of what each reason is costing. Below the card set, the **Shovel placement** Sankey
   (tonnage routed shovel → dump; moved here from Shift Analytics, §7c) closes the tab.
0a. **Cross-Shift Trends** — the **last tab** (bottom of the sidenav). Where every other tab is a single-shift snapshot, this one plots
   one point per shift (**oldest → newest**, left → right, for the currently selected MRM/JPM/Combined view) so
   a one-off is easy to tell from a real trend; the **point for the shift you're currently viewing is
   highlighted**. Four line charts, all read from the shifts already embedded in the file: **(a) Haulage &
   Loading Score %** (same Sched.-Potential ratios as the Balance gauges); **(b) Cat 797 availability — PA · UA
   · OE %**; **(c) Production — actual dumped vs plan tonnes**; **(d) Truck Match** (Loading − Haulage points).
   Points are joined with gaps where a shift has no data for the selected view.
1. **Truck / Shovel Balance** — a **separate last-hour Truck Balance card** sits at the top (LP Trucks
   Required vs Actual — from TruckBalance.csv, most-recent hour, see §6). *(The **Haulage Score · Truck Match ·
   Loading Score** gauge row and the **Haulage / Loading Score breakdowns** were **moved to the Sandbox** tab,
   §2b.)* Below the card, the tab carries **two balance
   graphs**: **(a) Truck Balance — over the shift**: the Required-vs-Actual truck time-series (15-min)
   with **under-trucked shaded red / over-trucked blue** and an **estimate of tonnes lost while
   under-trucked** (per-15-min linear truck-scaling: shortfall × interval tonnage ÷ actual trucks). The
   **last hour** (the window the balance card scores) is **very lightly shaded** with a small **status pill**
   — *Balanced* (green) / *Under-* / *Over-Trucked* (amber, with %). *(On this tab the actual-tonnage-rate /
   target t/h line is **omitted** — the full version with the rate line lives on the **Sandbox** tab, §2b.
   The **Bottleneck — trucks vs shovels** graph was also **moved to the Sandbox** tab.)*
   **(b) Wait-time balance over the shift**: a diverging area — shovel **hang** above budget fills up (red =
   shovels starved → under-trucked), truck **queue** above budget fills down (blue = over-trucked), flat =
   balanced; shares the time axis with (a) for correlation. **Both graphs seat a per-hour readout in a light text
   box at the base of the plot** — two rows labelled **Truck#** (blue) and **Shovel#** (teal) to the left of the
   y-axis — giving the effective units operating each hour, bucketed from Statusevents ÷ 1 h. A single
   **Showing: Deployed / Productive** switch (mirrored on both graphs and kept in sync) flips the whole readout
   between **Deployed = gross operating hours (Ready+Delay) ÷ 1 h** (its 12-hour average matches the Equipment
   Hours "deployed" headline, §0) and **Productive = net operating hours (Ready only) ÷ 1 h**. Shovels are the
   **HIT 800 = 0.7 × BE 495 equivalent**. It follows the shift + MRM/JPM/Combined view. (Equipment availability
   detail lives on the
   **KPI** tab — §2b.) *(The former standalone Fleet Match tab was merged into this tab.)*
   **Internal-road/berm/pad paths excluded:** every load whose **`DumpLocation` starts with `IN`** (the
   `IN_*` internal roads, berms, and pads — e.g. `IN_ROADS_MRM`, `IN_806PIT_BERMS`) is **dropped from the
   Truck Waterfall computation**. Because these loads are removed at source, they are absent from the top
   bridge, the Ore/Waste waterfalls, and the per-path list on the Haulage drill-down (§4); the waterfall
   Potential/Actual — and therefore the **balance-tab Haulage Score and Truck Match**, which read the same
   Truck-waterfall Potential (§1) — also exclude them. Loading (shovel) figures are unaffected.
2. **Truck Waterfall** — the Trucks Losses & Gains waterfall (topped by **PA / UA / OE availability rows**
   for Cat 797 vs budget, each bar embedding its main Down/Standby/Delay reason — see §7). Below the waterfall,
   beside the **Ore/Waste Details** toggle, are **four show/hide buttons that each reveal a per-shovel box plot —
   Queue at Shovel, Dump Idle, Dumping, Full Haul** (the truck-cycle time components). Each is **grouped by the
   loading shovel** (one box per shovel, the same idea as the Cat 797 dump breakdown on the Equipment Hours
   card), drawn in the same style as the shovel-side time boxes — **minutes, outliers hidden, dashed per-shovel
   budget tick** (Queue/Dump-Idle/Dumping budgets from Fixed_Times, Full-Haul budget from the haul-curve Travel ×
   0.555 loaded share) — and the tooltip reports the share of loads within ±10 % of budget. There is **no payload
   box for trucks** (payload compliance lives on the Shovel Waterfall tab). The tab also has the **Ore/Waste
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
3. **Shovel Waterfall** — the shovel losses & gains bridge (availability + cycle) and the hidden Ore/Waste
   breakdown. The **per-shovel drill-down and its status timeline moved to the child _Loading drill-down_ tab**
   (§3a). *(This is the former "Shovel Waterfall 2"; the original cycle-only "Shovel Waterfall" tab and its
   Shovel-placement Sankey were removed — the Sankey and the multi-shovel status timeline now live on the Shift
   Overview and Shift Analytics tabs respectively.)*
3a. **Loading drill-down** (child of Shovel Waterfall) — opens with a **top-3 "Fix first" band** (see §4) and a
   **single merged Shovel Loss Matrix** that folds together what used to be two separate panels (the shovel ×
   factor loss heatmap and the per-shovel drill-down list). Rows are **grouped by shovel type** — **BE 495
   (Cable)** and **HIT 8000 (Hydraulic)** — each group carrying a subtotal row, and the groups are ordered by
   tonnes lost. Each shovel row reads: **Shovel ID with its load count in "( )"**, then **Actual** (bold) and **Potential**
   tonnes, then **Score %** (Actual ÷ Potential, colour-coded ≥95 green / ≥85 amber / else red), then the
   **seven heat-shaded loss-factor cells** (PA/UA/OE availability + Payload/Spot Time/Load Time/Hang Time cycle;
   redder = more tonnes lost to that factor), then a **Total lost** chip. A **Total (all)** footer sums
   Actual/Potential and every factor column across all shovels. **Clicking a shovel row** (the whole table is
   interactive, biggest-loss shovel auto-selected) drives the two panels beneath it, laid out in this order:
   first that shovel's own **cycle waterfall** (see §7b), then the **Shovel Status Timeline** (the single-shovel
   status band). The status band carries a **TPNOH trend line**
   (purple, on its own right-hand axis): per hour it plots **tonnes loaded ÷ net operating (Ready) hours** for
   the selected shovel, so cycle/availability losses can be read against the shovel's productivity through the
   shift. The table and timeline were moved here out of the Shovel Waterfall tab.
2b. **Availability KPIs — PA/UA/OE/POE** — computed from `ASEStatus` durations. **Parked**
   (`PARKED - AVAILABLE` and `PARKED - MAINTAINING`) is its own state and is **excluded from every KPI** — its
   hours drop out of the ratios entirely. PA =
   (Ready+Delay+Standby)/(+Down), UA = (Ready+Delay)/(+Standby), OE = Ready/(Ready+Delay), plus **POE**
   (see §0). Budget PA/UA/OE come from the budget CSV (PA797/UA797/OE797, PA495/UA495, PA8000/UA8000),
   NOH-weighted across pits; cable &amp; hydraulic shovels have no OE budget (shown as "—"). These live on the
   **Equipment Hours and Performance** card on the **Shift Overview** tab (§0) — the standalone "KPI" tab was
   removed. The **Sandbox** tab (at the bottom of the list) now carries, top to bottom:
   **(1) the score gauge row — Haulage Score · Truck Match · Loading Score** (moved from the Balance tab).
   Both gauges use the **same Potential as their waterfalls** (§7): Haulage from the **Truck waterfall**,
   Loading from the **Shovel Waterfall 2** — `Score = Actual ÷ Potential` where Potential is the top-of-bridge
   **Sched. Potential** (includes the PA/UA/OE availability gap), so they read lower than the old cycle-only
   scores; gauges are colour-coded **< 90 % red, 90–100 % yellow, ≥ 100 % green**. The centre **Truck Match**
   card reads `Loading − Haulage` (negative = Under-Trucked, positive = Over-Trucked, within ±3 = Balanced).
   **(2) the Haulage / Loading Score breakdowns** (also moved from the Balance tab), each **grouped by
   material** (Ore then Waste), every bar coloured by its own score (same legend): within each group a
   **total** bar (availability-inclusive, matches the gauge), then the individual **haulage** (Load→Dump) or
   **shovels** (loading) below it, sorted lowest score first, up to six. Loading shovels sit under their
   dominant material and use each unit's own PA/UA/OE; haulage bars stay **cycle-only** (availability can't be
   attributed to a single route, so they read higher — labelled as such).
   **(3) Bottleneck — trucks vs shovels** (moved from the Balance tab): three horizontal bars — **Loading
   capacity** (shovel Sched. Potential) vs **Haulage capacity** (truck Sched. Potential) vs **Actual moved**;
   the **lower capacity is the binding constraint** (Loading > Haulage ⇒ trucks limit, adding trucks can lift
   output; Haulage > Loading ⇒ shovels limit; each fleet's own cycle basis, so directional).
   **(4) Truck Balance — over the shift** (a full copy of the Balance-tab graph, **with** the
   actual-tonnage-rate vs target t/h line — green, right axis, 3-pt moving-average smoothed Catmull-Rom spline
   — which is omitted from the Balance-tab version). **(5) the Top-15 lost-time reasons** panel (per equipment
   type, the 15 largest `Reason` contributors to Down / Standby / Delay time, each a bar coloured by status).
4. **Haulage drill-down** (child of Truck Waterfall) — per-path waterfalls, grouped by loading shovel (formerly "Path drill-down").
   Paths whose **`DumpLocation` starts with `IN`** (internal roads/berms/pads) are **excluded** — same
   source filter as the Truck Waterfall (§2), so they never appear in the path list.
   The tab opens with the **Haulage Loss Matrix** (which replaced the old loss heatmap + path table — both
   removed). It mirrors the Shovel Loss Matrix on the Loading tab: **paths grouped under each loading shovel**
   (group subtotal row, groups ordered by tonnes lost), each path row reading the **Dump** location with its
   **load count and average full-haul distance** in the `(n) @x.xkm` format (the dig/load
   origin is dropped since rows are already grouped by shovel; full "Load → Dump" is in the row tooltip) with its
   dominant material, then **Actual** (bold) and **Potential** tonnes, **Score %** (colour-coded), the **eight
   heat-shaded loss-factor cells** (Payload · Load · Queue · Spot · Dump Idle · Dumping · Full Haul · Empty Haul;
   redder = more tonnes lost), and a **Total lost** chip, with a **Total (all paths)** footer. Each path row
   shows its **load count and average full-haul distance** as `(n) @x.xkm`. **Clicking a path row loads that
   path's waterfall** in the **Path Waterfall** card below (defaulting to the **biggest-loss path**); **hovering a
   path row** pops a floating **Full-haul-time-per-hour** chart for that path — actual (blue line) vs haul-curve
   target (dashed) on a time x-axis, points green at/under and red over target, y-axis zoomed to the data range.
   Above the matrix a **"Fix first" band** — built from the same per-component waterfall tonnage — gives a
   one-line plain-language summary naming the three costliest factors, the tonnes each lost, the shovels driving
   them, and the share of total loss they represent. **The Loading drill-down (§3a) likewise uses a single merged, type-grouped, interactive
   Shovel Loss Matrix** in place of a separate heatmap + drill-down list (PA/UA/OE availability added to the
   factor list; see §3a). *(The earlier per-path/per-shovel loss heatmaps, the path and shovel drill-down tables,
   the per-factor Pareto, and the "shovel inefficiency summary" card were all removed in favour of the two
   interactive matrices.)*
5. **Shift Analytics — REMOVED.** The tab is gone; see §7d for where its charts went. On the **Delays & Standby**
   tab the **Lost-Time Pareto** and **Delay Variance** charts are **hidden by default behind two show/hide
   buttons** at the bottom of the delay/standby table (they only render when opened, and refresh with the
   Truck / Shovel toggle). The equipment status timelines are reached via the Equipment Hours **Timeline**
   hover-popup (§0) and the single-shovel timeline on the Loading tab (§3a).
5b. **Shift Stats** — consolidated into **three wide tables** (with column headers) plus the shift-level
   **Σ empty ÷ Σ full haul distance** ratio at the top. **(A) Loaded haul paths** (`Load → Dump`, top 12 by
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
6b. **Delays & Standby** — a table of every **Delay** and **Standby** reason for the selected
   shift/view, **grouped by status** with the **UA (Standby) group on top**, then OE (Delay), each sorted by
   **actual impact, biggest first**. Each group header carries the fleet's **metric actual · budget · Δ**
   (UA and OE, act vs the NOH-weighted budget; shovels have no OE budget → "—") so the reason rows sit under
   their headline metric. A **Truck / Shovel** toggle at the top-right switches
   the fleet — **Cat 797** trucks (tonnage impact × `TPNOH797`) or **all shovels** (BE 495B + HIT 8000; tonnage
   impact × each event's `TPNOHCable`/`TPNOHHydro`). Each group header is centred and reads `<metric>
   <b>actual%</b> budget% ▲/▼ Δ` (black text, grey budget, green ▲ / red ▼ delta). Columns: **reason · Actual % · Target % · +/− tonnes**
   (Actual before Target). **+/− tonnes** is shown as a colour-coded **▲/▼ chip** (green ▲ = gain/under target,
   red ▼ = loss/over target) — the same chip style used in the group headers. **Actual %** and **Target %** are each reason's actual (and budgeted
   `ExpectedDuration`) time as its **impact on the affected metric** — Delays on **OE** (÷ Ready+Delay),
   Standbys on **UA** (÷ Ready+Delay+Standby); Target is blank where no standard exists. **+/− tonnes** =
   (target − actual) time × `TPNOH797`, colour-shaded by magnitude — **green = under target (gain)**, **red =
   over target (loss)**; reasons with no target count fully as loss. This is the reason-level companion to the
   Overview card's Down/Standby/Delay reasons and the waterfall's OE/UA rows.
6d. **Shovel Productivity** — a per-shovel KPI scorecard (styled after the reference sheet): columns are
   **All Shovels** plus one column per **active shovel unit** that shift (e.g. S011, S807), each split
   **Target · Actual · +/− tonnes**, with a header **score badge = Actual ÷ Target loaded**
   (≥90 % green · 70–90 % amber · <70 % red). Rows: **Total Dumped** (tonnes; source tonnage is dumped tonnes) whose target is the
   **scheduled potential** = total/calendar hours (from per-unit `Statusevents`) × budget **PA** × **UA** ×
   **OE** × budget **Dig Rate** (blended TPNOH). Availability chain (from that unit's status events, with
   total/calendar hours TH = Ready+Delay+Standby+Down, **GOH** = Ready+Delay, **NOH** = Ready):
   **PA** = (Ready+Delay+Standby)/TH, **UA** = (Ready+Delay)/(Ready+Delay+Standby), **OE** =
   Ready/(Ready+Delay). The table lists **PA · UA · OE** (%, each act vs budget, +/− tonnes from the
   sequential decomposition — PA + UA + OE + Dig Rate +/− tonnes **sum to the Total Dumped gap**), then
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
6e. **Truck Productivity** (child of Truck Waterfall) — a per-**shovel** KPI scorecard for the Cat 797 haul
   trucks (loads grouped by the shovel that loaded them): columns are **All Trucks** plus one column per
   active shovel, each split **Target · Actual · +/− tonnes** with a header score badge (Actual ÷ Potential).
   Rows: **Total Moved**, **Potential**, **NOH** and **NOH %**, **Truck Productivity** (T/NOH — the row
   formerly labelled *Dig Rate*), **Truck Cycle Time** and its cycle components (Queue, Spot, Load, Wait at
   Dump, Dumping, Full/Empty Haul), **Payload**, **Empty/Full Distance Ratio**, and the payload-quality block
   (Overloads/110–120 %/Underloads). *(The standalone **PA · UA · OE** availability rows were **removed** from
   this table — availability is still reflected in the **NOH** and **Truck Productivity** +/− tonnes, which
   carry the combined PA/UA/OE tonnage impact.)* Two **toggles directly following the table title**
   independently **show/hide the Target column** and the **+/− t column** (both on by default). Hiding a column
   **condenses the table** — shovel columns shrink and their group headers wrap onto multiple lines (numbers
   never wrap), so **more shovel columns fit in the page width** when Target and/or +/− t are hidden; the table
   still stretches to the full card width. The **Actual** column is **colour-shaded with the same green-gain /
   red-loss scale as the +/− t column** (shading scaled by each column's largest tonnage swing), so
   productivity reads at a glance even with the other two columns hidden. **The Shovel Productivity table (§6d)
   has the identical toggles, condensing layout, and Actual-column shading.**
6c. **Hourly Production** — a wide **KPI × hour** table (styled after the reference "Hourly Performance"
   sheet): columns **KPI · UOM · Total · then one column per shift-hour** (`HH:00`, 12 buckets from the
   shift start). Loads are bucketed by **dump time**. Rows: production totals (**Dumped, Ore Moved, Waste
   Moved** in tonnes — source tonnage is dumped tonnes; **Load Count**) then per-load averages (**Payload — CAT 797** in wTons; and the
   cycle segments **Cycle Time - Ore** and **Cycle Time - Waste** (the total truck cycle split by
   the load's material, each vs its own material's budget cycle), **Spot at Shovel, Load Time, Shovel Hang,
   Truck Wait at Shovel, Wait at Dump, Dumping Time** in mm:ss). **Total** = shift sum for tonnes/count, shift average for times/payload.
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
| `Data/AllLoadsDumps.csv` | Haulage, Loading, Trucks waterfall | `Truck`, `Excav`, `LoadPit`, `MaterialGroupName`, `LoadLocation`, `DumpLocation`, `Tonnage`, cycle-component seconds, `FullHaulDistance` / `FullExpectedDistance` (actual/expected loaded-haul, m), `EmptyHaullDistance`, `FieldElock` / `FieldDlock` (locked-load flags — non-`NONE` = forced, not optimizer-chosen; drive the Sankey hatching), `Crew` (appended to the shift-selector label). **The non-existent unit `S8810` is dropped at load** (from loads and status events). |
| `Data/Statusevents.csv` | Availability (PA/UA/OE), status timelines, delay pareto/variance | `ShiftId`, `Eqmt`, `TimeStamp`, `Reason`, `Duration` (s), `ExpectedDuration` (s, may be NULL), `Timecat`, `ASEStatus`, `Eqmttype`, `Pit` — column names re-cased in the latest export; aliased at load |
| `Data/TruckatShovel.csv` | Trucks-at-shovel overlay (shovel status timeline) | `shiftId`, `Excav`, `LogTime`, `TrucksAtShovel`, `TrucksInQueue`, `QStatus` |
| `Data/TruckAtDump.csv` | Trucks-at-crusher timeline | `shiftId`, `DumpLocation`, `LogTime`, `TrucksAtDump`, `TrucksInQueue`, `QStatus` — **same schema as TruckatShovel** (latest export; previously had `Moment`/`TrucksAtDump` only) |
| `Data/TruckBalance.csv` | Truck Balance gauge & shift time-series | `Shiftid`, `Pit`, `TotalRequired`, `LPActual`, `Short`, `Timestamp` — **now carries `Shiftid`**, so rows are matched **by shift** (was timestamp-window matched) |
| `Data/ShovelCoverageFactors.csv` | **Not wired into the main build** (used only by the v2 Pulse prototype). LP dispatch-optimizer per-shovel snapshots (~1-min cadence): `LPCalcId`, `LPTime`, `ShiftId`, `pit`, `Excav`, `DigLoc`, `GradeMaterialType`, `CurrentGrade`, `Bit`/`Fines`/`D50` (ore quality), **`LoadRate` (node rate = dig capacity)** / **`PathRate` (path rate = coverage-limited, 0 if disabled)**, `LPMode` (Normal / Under trucked / Disabled / Unused), **`LPCoverage` (0–1 truck-coverage factor)** |
| `Budget/MRM Haul Curve.csv`, `JPM Haul Curve.csv` | Haulage Potential, truck cycle budgets | `Material`, `HD (km)`, `Month`, `TPNOH (t/h)`, `Cycle (min)`, `Travel (min)` |
| `Budget/MRM_Fixed_Times.csv`, `JPM_Fixed_Times.csv` | Truck fixed-time budgets (min) | `month_f`, `Material2`, **`ShovelType`** (`Average`/`Cable`/`Hydraulic`), `Queue_Time`, `Spot_Time`, `Load_Time`, `Dump_Idle`, `Dumping_Time` — **seasonal**: summer (May–Nov) and winter (Dec–Apr) each carry one value set per pit × material; every month in a season shares that set. **`Load_Time` is split by shovel type** (Cable = 0.94 ×, Hydraulic = 1.24 × the Average row); other times are identical across types. Loader: `load_fx` keys by month → material → ShovelType. |
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
LoadLocation→DumpLocation path; if the path has no other loads, default to **5 km**.

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
Actual anchor; the signed delta is labelled at the moving end of each bar. The shovel bridge and every path
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

PA/UA/OE are computed exactly as on the **KPI tab** (Cat 797, `ASEStatus`, Parked excluded from the KPI):
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
proportions). The **per-path waterfalls (Haulage drill-down) still start at Potential with no availability
rows** (a path is too granular to attribute fleet availability to). Every truck/haulage waterfall closes
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

### Path drill-down

The identical waterfall is computed for every **LoadLocation → DumpLocation** path. Paths are ranked by
Potential and colour-flagged by score (green ≥ 95 %, amber 85–95 %, red < 85 %), with the single biggest
loss row called out. Clicking a path renders its own waterfall.

**Path filters (drill-down list only — totals unaffected):**
1. Paths where `LoadLocation = DumpLocation` are excluded (data artifacts — these are the zero-distance records).
2. Paths with **fewer than 5 loads** are excluded (low-volume noise).

These filters apply only to the path drill-down display; the section-level Potential, Actual, and Score
still include every load.

**Path list layout:**
- Paths are **grouped and ordered by loading shovel** (the dominant `Excav` on that path), then by Potential within each shovel.
- Each shovel group has a **subtotal header row**: summed Loads, Potential, Actual, and the group Score (colour-flagged green ≥ 95 %, amber 85–95 %, red < 85 %).
- Each path row shows **average Full / Empty haul distance (km)** — mean `FullHaulDistance` and `EmptyHaullDistance` over the path's loads (zero values excluded from the average).
- Clicking a path renders its own waterfall; KPI row labels are colour-matched to their bar (green gain, red loss). The bridge closes on Actual with no residual bar.

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
(act/bud % are the fleet values on both). Below the waterfall an **Expand all / Contract all** button opens or closes all of the box plots at once (same
idea as the Equipment Hours card), alongside **four show/hide buttons — ordered Payload · Spot · Load · Hang to
match the waterfall rows — that each reveal a per-shovel box plot**: **Payload Compliance** (the same measured-payload box plot as
Shift Analytics, with a dashed 361 t target) and **Spot Time**, **Load Time**, **Hang Time** (box plots of the
actual per-load cycle time per shovel **shown in minutes**, each drawn with the same Tukey Q1–median–Q3 box and
mean diamond as the payload chart — **outliers are hidden** on the three time charts — plus a **dashed budget
tick per shovel** = that shovel's loads-weighted budget — Spot and Load from Fixed_Times by shovel type, Hang
derived as 361 t × 3600 ÷ TPNOH − Spot − Load; the tooltip also reports the share of loads within ±10 % of
budget). All three follow the current shift/view and re-render when it
changes. There is also a **shovel drill-down**: a per-unit table (Excav,
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
queue and hang min/load, and a **purple TPNOH trend line on its own right-hand axis** (hourly tonnes ÷ net
operating (Ready) hours for that shovel) — the same enlarged style as the trucks-at-dump graph. (The interactive
per-shovel loss table and this filtered timeline now live on the **Loading drill-down** tab, §3a; the all-shovels
overview timeline remains on the Shovel Waterfall / Shift Analytics tabs.)

*Availability rows (PA/UA/OE)* — same calc as the KPI tab, but for **BE 495B (cable) + HIT 800
(hydraulic)** combined. Budget PA/UA come from `PA495/UA495`, `PA8000/UA8000`; **budget OE is derived as
`NOH ÷ GOH`** (validated: `NOH797/GOH797 = OE797 = 0.925` exactly), giving cable ≈ 0.905, hydraulic ≈ 0.85.
Tonnage effect is the same per-pit sequential decomposition (`TH·ΔPA·UAb·OEb`, etc.) × the shovel rate
(`TPNOHCable` / `TPNOHHydro`), summed. Red bars split by Down/Standby/Delay reason (green plain), as on the
trucks waterfall.

*Payload + cycle rows (Payload/Spot/Load/Hang)* — built from a **budget cycle for the 361 t payload target**.
Per group (**shovel fleet · pit · month · material**):

```
Spot_b         = Fixed_Times.Spot_Time (× 60, seconds)      # constant per pit·month·material
Load_b         = Fixed_Times.Load_Time × type-factor (× 60) # BY SHOVEL TYPE: Cable ×0.94, Hydraulic ×1.24
c_b    = 361 × 3600 ÷ TPNOH                                  # budget cycle to load 361 t at budget rate
Hang_b = c_b − Spot_b − Load_b                              # hang target per group (shifts with Load_b)
rate   = 361 ÷ c_b  ( = TPNOH ÷ 3600 )                     # the published budget rate
per load:  Spot/Load/Hang = rate × (seg_b − seg_a) ;  Payload = Tonnage − 361
```

**Load-time target is split by shovel type.** The `Load_Time` in Fixed_Times is a **fleet-averaged** value;
because cable (rope) shovels load faster than hydraulics, one target for both is wrong. The CSV now carries a
**`ShovelType` column** with **Average / Cable / Hydraulic** rows, where `Load_time_Cable = 0.94 × average`
and `Load_time_Hydraulic = 1.24 × average` (only `Load_Time` is split; Queue/Spot/Dump times are unchanged).
Every place the Load budget is used — the **shovel cycle waterfall & Loading drill-down, the Truck waterfall
& Haulage drill-down's truck-Load row, Shovel/Truck Productivity, Hourly Production, and Shift Stats** — looks
up the target for the **loading shovel's own type** (BE 495B → Cable, HIT 800 → Hydraulic; unknown →
Average). So `Hang_b` (= c_b − Spot_b − Load_b) also shifts per type. The Appendix's Fixed_Times table shows
all three Load values.

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

A "Shovel placement" section (on the **Shift Overview** tab, §0) showing a **Sankey** of tonnage routed from each
shovel (**`Excav`** — the actual loading unit) to each dump (DumpLocation), built from T1-truck loads and
following the MRM / JPM / Combined toggle. Keying the left nodes by the shovel rather than the dig `LoadLocation`
means each shovel's loads aggregate into one node regardless of whether it was digging a numbered bench block or
re-handling a named stockpile / ore-prep pad / reject dump (which is why earlier some nodes read as
`THUNDER_N`, `BLAZER`, etc. — those were dig-location names, not the shovel). If `Excav` is blank the
`LoadLocation` is used as a fallback.
Ore = teal, waste = amber. (The spatial flow map and full-vs-empty scatter were prototyped but dropped —
without an actual haul-road network model, straight-line geometry misrepresents real cycle distance.)

- **Left nodes (shovels):** label = shovel id + **actual average TPNOH** (t/h) = total tonnage ÷ total
  loading hours at that location, where loading hours = max(2.5 min, Spot+Load+Hang) per load ÷ 3600
  (the realized loading rate, not the budget standard).
- **Right nodes (dumps):** label = dump id + **total tonnes received**.
- **Ribbons:** width ∝ tonnage, coloured by material; node heights ∝ total tonnage loaded / received. Each
  ribbon prints its **average full-haul distance** (`x.xkm`) just right of the shovel node. *(Section titled
  **Shovel Placement**.)*
- **Locked (un-optimized) loads:** a load is **locked** when either **`FieldElock`** or **`FieldDlock`** in
  AllLoadsDumps holds a shovel/dump id (i.e. is not `NONE`) — meaning the dispatch was forced rather than chosen
  by the optimizer; fewer locked loads = the system running more open, so material movement is maximised. Each
  ribbon's **locked share (by load count) is hatched** (diagonal hashing on the top slice of the band), and the
  **% of locked loads is printed under every shovel and dump node** (red when ≥ 25 %). Node and ribbon
  percentages are computed over the flows shown (self-flows and sub-threshold ribbons excluded).
- **Small-ribbon filter:** flows below ~1.5 % of the column total are hidden so labels stay legible.
- Locations are still validity-filtered by the median-GPS bounding box (drops garbage points); self-flows
  (load = dump) are excluded. Combined view concatenates both pits (location ids are unique across pits).

---

## 7d. Shift Analytics — REMOVED (charts redistributed)

The standalone **Shift Analytics** tab has been **deleted**. Its charts now live elsewhere: the **delay pareto**
and **delay variance** are on the **Delays & Standby** tab (§6b), both **filtered by fleet and following that
tab's Truck / Shovel toggle**; the **payload-compliance box plot** is one of the toggle charts on the **Shovel
Waterfall** tab (§7b); the **equipment status timelines** (shovels / trucks) are reached from the **Timeline**
hover-popup on each Equipment Hours card (§0, filtered per fleet with a per-hour deployed row) and the
single-shovel filtered timeline on the **Loading drill-down** tab (§3a). The cumulative and hourly tonnage
charts had already moved to the **Shift Overview** (§0). Plan/target tonnage is still taken straight from the
budget CSV per shift: `planTotal = POre + PWst` (productive ore + waste plan). Charts render with **Chart.js
4.4.1** inlined from `lib_chartjs.js` (offline, no CDN); timelines and box plots are hand-drawn SVG.

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
   (blue), Parked (purple, its own state), over the 12 h shift; the **segment tooltip shows the delay
   `Reason`**. Each
   row overlays a **stepped black line** of `TrucksInQueue` (from `TruckAtShovel.csv`), scaled 0–max
   queue, for shovels with queue data (the overlay uses **`TrucksAtShovel`**, from `TruckatShovel.csv`).
   The **shovel-ID label is coloured by dominant material** (ore teal / waste amber, matching Shovel
   placement), with the shovel's **average queue-at-shovel and hang time per load** (`QueueTimeShvl` and
   `HangTime` averaged over its loads in the current view, min/load) shown beneath the ID.
4. **Lost time — delay pareto.** *(Moved to the **Delays & Standby** tab, §6b.)* Bar + cumulative-% line of
   delay hours by `Reason`, filtered to `ASEStatus = 'Delay'` (operational delays only — excludes
   Down/maintenance and Standby), top 20 + Other. It is now **filtered by fleet and follows the tab's
   Truck / Shovel toggle** (trucks = Cat 797, shovels = BE 495B + HIT 800). Each bar is labelled with its
   **total lost hours** on top and its **occurrence count** (`n×`) at the base; tooltip shows hours and event
   count per reason.
5. **Delay variance — actual vs expected.** *(Moved to the **Delays & Standby** tab, §6b, and — like the pareto —
   **filtered by fleet, following the Truck / Shovel toggle**.)* Signed bar chart of **actual − expected** delay hours by
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
4. Zero haul distance → same-path average → else 5 km default.
5. NULL `EmptyHaulDuration` treated as 0 in cycle time.
6. Shovel NOH = touch-time (spot+load+hang), floored at **2.5 min** (removes physically-impossible sub-minute loads).
7. "Dump idle" actual = `QueueTimeDmp` (`DumpSpotTime` is all zeros).
8. Budget travel split 0.555 full / 0.445 empty, derived from actual loaded vs empty speeds.
9. Potential & waterfall use a **payload-normalized rate** `rate* = 361 t ÷ budget cycle time`, so budget-cycle tonnes = 361/load, the Payload row is `Actual − 361`, and the bridge closes with **no residual**. This raises Potential ~5% and lowers the Haulage Score ~5% vs the published-TPNOH basis; the Haulage-Score gauge uses the same `rate*` so it matches the waterfall Potential.
10. Trucks waterfall anchored on **realized-cycle** Potential (`rate* × actual cycle time`); above it, **PA/UA/OE availability rows** (Cat 797 vs budget CSV) step down from Sched. Potential — each bar embeds its top Down/Standby/Delay reason. Empty/Full is an indicator; no residual.
11. Budget NOH prorated by elapsed/12 (= 11.23 / 12).
12. Truck Balance from the most-recent hour of the selected shift, ±5 % "Balanced" band.
13. Path drill-down excludes (a) paths where LoadLocation = DumpLocation and (b) paths with < 5 loads. These filters affect the path list only, not the section totals.

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
