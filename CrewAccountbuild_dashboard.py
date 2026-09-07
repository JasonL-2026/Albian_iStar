#!/usr/bin/env python3
"""
Albian Mine - Crew Accountability Report builder + HTML generator.

Re-run whenever the source files change:  python3 CrewAccountbuild_dashboard.py
Output: CrewAccountabilityReport.html  (self-contained, open in any browser)

Data source
-----------
This builder relies ONLY on the rendered SSRS export
`Data/CrewAccountabilityReport.csv`. (The matching .rdl is used solely by SSRS
subscriptions to produce that CSV; it is not read here.) The CSV is a set of
self-describing blocks separated by blank rows:
    * a title block (reporting period + selected mines),
    * one structured breakdown table (Category / DumpLoc / Crew / tonnage),
    * per-crew chart series ("<Metric>_..._label" / "..._Value_Y"),
    * KPI textbox blocks - a header row of "Textbox###" ids followed by one value
      row; a block may carry a SINGLE value or a WHOLE ROW of values (e.g. the
      Equipment and Cycle-time grids), so every column is captured, not just the
      first.

The generated HTML mirrors the production SSRS report, split into tabs for
single-page viewing (mirrors haulage_dashboard.html's sidebar layout):

  * Production (Mt)          - the full Category x Dump Loc x Crew tonnage table
                               (with per-row / per-crew / grand totals, avg crusher
                               tonnes/hr, avg # ore shovel, # of shifts, avg release
                               duration) plus the Productive-tonnage-by-crew chart.
  * Equipment - Cat 797 Trucks - TPGOH / TPNOH / PA / UA / OE / UA*OE / GOH /
                               Tonnes / POE per crew (+ Crew Avg) with the TPNOH chart.
  * Cycle Time              - Oilsand & Waste grids (Full Haul km, Loading, Full/Empty
                               Haul, Dumping, Spot, Queue, Idle, Cycle) with both charts.
  * Source data             - raw chart series + every KPI textbox, for transparency.

Every displayed cell is reconstructed from the CSV. The cell -> textbox / series
mapping (PROD_EXTRA_TILES / EQUIP_* / CYCLE_*) is keyed on SSRS textbox names and
chart-series keys, which are stable across data refreshes, so the report keeps
reconstructing correctly on new exports.

The CSV file ships with a literal space in its name
("CrewAccountabilityReport .csv"); `_resolve_file` resolves that fuzzily so the
builder works regardless of the exact on-disk spelling.
"""
import csv, json, os, re, sys
from collections import OrderedDict
from datetime import datetime, timezone

# Anchor all paths to THIS script's folder so it runs from any working directory
# (mirrors build_dashboard.py behaviour).
BASE = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
DATADIR = os.environ.get('DASH_DATADIR') or f'{BASE}/Data'   # override with DASH_DATADIR
REPORT_CSV = 'CrewAccountabilityReport.csv'
OUT_HTML = f'{BASE}/CrewAccountabilityReport.html'


def num(x):
    try:
        return float(str(x).replace(',', ''))
    except Exception:
        return None


# ---- resilient file resolution: re-exports rename/recase files (and this CSV
# even carries a stray space). Resolve requested names to whatever is actually
# in the folder, case-insensitively and by fuzzy stem match.
try:
    _DATA_FILES = [f for f in os.listdir(DATADIR) if f.lower().endswith('.csv')]
except Exception:
    _DATA_FILES = []


def _canon(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _resolve_file(name):
    p = f'{DATADIR}/{name}'
    if os.path.exists(p):
        return p
    want = _canon(name.rsplit('.', 1)[0])
    for f in _DATA_FILES:                       # case-insensitive exact
        if f.lower() == name.lower():
            return f'{DATADIR}/{f}'
    for f in _DATA_FILES:                       # canonical stem equal (ignores the stray space)
        if _canon(f.rsplit('.', 1)[0]) == want:
            return f'{DATADIR}/{f}'
    cands = [f for f in _DATA_FILES
             if want and (want in _canon(f.rsplit('.', 1)[0]) or _canon(f.rsplit('.', 1)[0]) in want)]
    if cands:
        cands.sort(key=lambda f: abs(len(_canon(f.rsplit('.', 1)[0])) - len(want)))
        return f'{DATADIR}/{cands[0]}'
    return p                                     # give up -> original (raises if truly missing)


# ============================ CSV block parsing ============================
def read_blocks(path):
    """Split the rendered CSV into blocks separated by fully-blank rows."""
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))
    blocks, cur = [], []
    for r in rows:
        if all((c or '').strip() == '' for c in r):
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(r)
    if cur:
        blocks.append(cur)
    return blocks


def _cells(row):
    return [(c or '').strip() for c in row]


_SERIES_SUFFIX = re.compile(r'^(?P<metric>.*?)_(?P<grp>Chart\d+_CategoryGroup\d*)_label$')


def parse_report(path):
    """Classify every CSV block. Returns dict with title/category/series/tiles."""
    blocks = read_blocks(path)
    title = {'period': '', 'mines': ''}
    category = None            # {'header':[...], 'rows':[[...]]}
    series = []                # [{'label','metric','crews':[...],'values':[...]}]
    tiles = []                 # [{'id','value'}]

    for i, b in enumerate(blocks):
        hdr = _cells(b[0])
        h0 = hdr[0] if hdr else ''

        # --- title block: first block, two Textbox headers -> period + mines
        if i == 0 and h0.startswith('Textbox') and len(b) >= 2:
            vals = _cells(b[1])
            title['period'] = vals[0] if len(vals) > 0 else ''
            title['mines'] = vals[1] if len(vals) > 1 else ''
            continue

        # --- per-crew chart series: header ends with "_label", rows are metric,crew,value
        if h0.endswith('_label'):
            metric = _cells(b[1])[0] if len(b) > 1 else h0[:-6]
            grp = ''
            if len(hdr) > 1:
                mm = _SERIES_SUFFIX.match(hdr[1])
                if mm:
                    grp = mm.group('grp')
            crews, values = [], []
            for r in b[1:]:
                c = _cells(r)
                if len(c) >= 3 and c[1]:
                    crews.append(c[1])
                    values.append(num(c[2]))
            label = metric + (f' ({grp.replace("_", " ")})' if grp else '')
            series.append({'label': label, 'metric': metric, 'key': hdr[1] if len(hdr) > 1 else h0,
                           'crews': crews, 'values': values})
            continue

        # --- KPI textbox block: a header row of Textbox ids + one value row. A block
        #     may hold a single value OR a whole row (Equipment / Cycle-time grids),
        #     so pair every id column with its value column, not just the first.
        if h0.startswith('Textbox') and len(b) == 2:
            vals = _cells(b[1])
            for j, tid in enumerate(hdr):
                if tid.startswith('Textbox'):
                    tiles.append({'id': tid, 'value': vals[j] if j < len(vals) else ''})
            continue

        # --- structured breakdown table (Category / DumpLoc / Crew / ...)
        if category is None and h0 and not h0.startswith('Textbox'):
            width = max(len(_cells(r)) for r in b)
            header = (_cells(b[0]) + [''] * width)[:width]
            data = [(_cells(r) + [''] * width)[:width] for r in b[1:]]
            category = {'header': header, 'rows': data}
            continue

    # index tiles by their (stable) SSRS textbox id, and series by their raw header key
    tiles_by_id = {t['id']: t['value'] for t in tiles}
    series_by_key = {s['key']: s for s in series}

    return {'title': title, 'category': category, 'series': series, 'tiles': tiles,
            'tiles_by_id': tiles_by_id, 'series_by_key': series_by_key}


# ============================ production-layout mapping ============================
# The rendered CrewAccountabilityReport is reproduced below by mapping each cell of
# the production report to its source in the CSV export. SSRS textbox names and chart
# series keys are STABLE across data refreshes, so these maps keep working on new
# exports. EVERY displayed value comes from the CSV (the .rdl is not read).
CREWS = ['Crew A', 'Crew B', 'Crew C', 'Crew D']

# Production (Mt) "extra" rows below the category table (one textbox per crew, A..D)
PROD_EXTRA_TILES = OrderedDict([
    ('Avg # Ore Shovel',        ['Textbox360', 'Textbox361', 'Textbox362', 'Textbox363']),
    ('# of Shifts',             ['Textbox251', 'Textbox253', 'Textbox254', 'Textbox255']),
    ('Avg Release Duration (hr)', ['Textbox365', 'Textbox366', 'Textbox367', 'Textbox368']),
])

# Equipment - Cat 797 Trucks. Every crew is a wide textbox row in the CSV whose 8
# columns are (in order) the metric rows below, plus a separate POE textbox. The maps
# are column-oriented: crew/avg -> list of textbox ids aligned with EQUIP_METRICS.
EQUIP_METRICS = ['TPGOH', 'TPNOH', 'PA', 'UA', 'OE', 'UA*OE', 'GOH', 'Tonnes (Mt)']
EQUIP_IDS = OrderedDict([
    ('Crew A',  ['Textbox179', 'Textbox182', 'Textbox185', 'Textbox188', 'Textbox191', 'Textbox162', 'Textbox194', 'Textbox196']),
    ('Crew B',  ['Textbox189', 'Textbox199', 'Textbox201', 'Textbox203', 'Textbox205', 'Textbox170', 'Textbox207', 'Textbox209']),
    ('Crew C',  ['Textbox200', 'Textbox204', 'Textbox208', 'Textbox212', 'Textbox214', 'Textbox171', 'Textbox216', 'Textbox218']),
    ('Crew D',  ['Textbox206', 'Textbox213', 'Textbox217', 'Textbox221', 'Textbox223', 'Textbox174', 'Textbox225', 'Textbox227']),
    ('Crew Avg', ['Textbox215', 'Textbox222', 'Textbox226', 'Textbox230', 'Textbox232', 'Textbox175', 'Textbox234', 'Textbox236']),
])
EQUIP_POE_IDS = OrderedDict([
    ('Crew A', 'Textbox340'), ('Crew B', 'Textbox341'), ('Crew C', 'Textbox342'),
    ('Crew D', 'Textbox343'), ('Crew Avg', 'Textbox346'),
])

# Oilsand / Waste cycle-time grids. Each crew is a wide textbox row whose 9 columns
# align with CYCLE_METRICS.
CYCLE_METRICS = ['Full Haul (km)', 'Loading Time (min)', 'Full Haul (min)', 'Empty Haul (min)',
                 'Dumping Time (min)', 'Spot Time (min)', 'Queue Time (min)', 'Idle time (min)',
                 'Cycle Time (min)']
CYCLE_OIL_IDS = OrderedDict([
    ('Crew A', ['Textbox395', 'Textbox397', 'Textbox451', 'Textbox401', 'Textbox403', 'Textbox405', 'Textbox407', 'Textbox409', 'Textbox453']),
    ('Crew B', ['Textbox420', 'Textbox422', 'Textbox454', 'Textbox425', 'Textbox427', 'Textbox429', 'Textbox431', 'Textbox433', 'Textbox456']),
    ('Crew C', ['Textbox437', 'Textbox439', 'Textbox457', 'Textbox442', 'Textbox444', 'Textbox446', 'Textbox448', 'Textbox450', 'Textbox459']),
    ('Crew D', ['Textbox463', 'Textbox465', 'Textbox467', 'Textbox469', 'Textbox471', 'Textbox473', 'Textbox475', 'Textbox477', 'Textbox479']),
])
CYCLE_WASTE_IDS = OrderedDict([
    ('Crew A', ['Textbox423', 'Textbox426', 'Textbox455', 'Textbox432', 'Textbox436', 'Textbox440', 'Textbox443', 'Textbox447', 'Textbox460']),
    ('Crew B', ['Textbox462', 'Textbox464', 'Textbox466', 'Textbox468', 'Textbox470', 'Textbox472', 'Textbox474', 'Textbox476', 'Textbox478']),
    ('Crew C', ['Textbox481', 'Textbox482', 'Textbox483', 'Textbox484', 'Textbox485', 'Textbox486', 'Textbox487', 'Textbox488', 'Textbox489']),
    ('Crew D', ['Textbox491', 'Textbox492', 'Textbox493', 'Textbox494', 'Textbox495', 'Textbox496', 'Textbox497', 'Textbox498', 'Textbox499']),
])

# Cycle-time chart series (Cycle Time row) — kept for the per-crew charts.
CYCLE_OIL_CHART = 'Truck Idle Time_Chart4_CategoryGroup_label'
CYCLE_WASTE_CHART = 'Truck Idle Time_Chart4_CategoryGroup2_label'
EQUIP_TPNOH_CHART = 'Rdy Duration_Chart3_CategoryGroup_label'
PROD_CHART_SERIES = 'Tonnage_Chart1_CategoryGroup_label'   # Productive tonnage by crew


def _T(*nums):
    """Expand a list of textbox numbers into full 'Textbox<n>' ids."""
    return ['Textbox%s' % n for n in nums]


# ---- lower report sections (below Waste), each a metric-row x crew-column grid ----
# Every value comes from the CSV export. Section titles / row labels / column headers
# are taken verbatim from the RDL layout (the RDL itself is NOT read at runtime).
HYDRO_ROWS = ['TPGOH', 'TPNOH', 'PA', 'UA', 'OE', 'GOH', 'NOH', 'Tonnes (Mt)',
              'TotalLoads', 'Avg Hang(min/ld)']
_ABCD_AVG = ['Crew A', 'Crew B', 'Crew C', 'Crew D', 'Crew Avg']
_ABCD = ['Crew A', 'Crew B', 'Crew C', 'Crew D']

SECTIONS = [
    # ---- Equipment - Shovels (umbrella heading) ----
    {'title': 'Cable Shovel', 'group': 'Equipment - Shovels', 'rows': HYDRO_ROWS,
     'cols': _ABCD_AVG, 'ids': {
        'Crew A':   _T(224, 231, 235, 239, 241, 243, 4, 245, 6, 247),
        'Crew B':   _T(503, 505, 507, 509, 511, 513, 8, 515, 10, 517),
        'Crew C':   _T(506, 510, 516, 518, 520, 522, 9, 524, 12, 526),
        'Crew D':   _T(508, 512, 519, 521, 523, 525, 11, 527, 13, 528),
        'Crew Avg': _T(276, 277, 278, 279, 280, 281, 282, 283, 83, 82)}},
    {'title': 'Big Hydraulic', 'group': 'Equipment - Shovels', 'rows': HYDRO_ROWS,
     'cols': _ABCD_AVG, 'ids': {
        'Crew A':   _T(302, 304, 318, 320, 322, 324, 34, 326, 36, 328),
        'Crew B':   _T(583, 584, 585, 586, 587, 588, 37, 589, 38, 590),
        'Crew C':   _T(592, 593, 594, 595, 596, 597, 39, 598, 40, 599),
        'Crew D':   _T(601, 602, 603, 604, 605, 606, 42, 607, 44, 608),
        'Crew Avg': _T(330, 331, 332, 333, 334, 335, 336, 337, 88, 89)}},
    {'title': 'Small Hydraulic', 'group': 'Equipment - Shovels', 'rows': HYDRO_ROWS,
     'cols': _ABCD_AVG, 'ids': {
        'Crew A':   _T(269, 271, 273, 293, 295, 297, 24, 305, 26, 307),
        'Crew B':   _T(556, 557, 558, 559, 560, 561, 27, 562, 28, 563),
        'Crew C':   _T(565, 566, 567, 568, 569, 570, 29, 571, 30, 572),
        'Crew D':   _T(574, 575, 576, 577, 578, 579, 31, 580, 32, 581),
        'Crew Avg': _T(309, 310, 311, 312, 313, 314, 315, 316, 86, 87)}},
    # ---- standalone tables ----
    {'title': 'Equipment - D11 Dozers', 'rows': ['PA', 'UA', 'OE'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(530, 532, 544), 'Crew B': _T(616, 618, 545),
        'Crew C': _T(630, 631, 546), 'Crew D': _T(634, 635, 547),
        'Crew Avg': _T(638, 639, 548)}},
    {'title': 'Loading Profile (10-10-20 Rule)',
     'rows': ['Avg. Tonnes (VIMS)', 'Count 20%', 'Overload percentage'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(412, 414, 416), 'Crew B': _T(611, 613, 615),
        'Crew C': _T(614, 617, 619), 'Crew D': _T(623, 625, 627),
        'Crew Avg': _T(624, 626, 628)}},
    {'title': 'Hot Coffee Duration', 'rows': ['Cat 797 avg. (mins)'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(79), 'Crew B': _T(81), 'Crew C': _T(84), 'Crew D': _T(85),
        'Crew Avg': _T(90)}},
    {'title': 'Shovel Loading Sides', 'rows': ['Double Side', 'Single Side'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(93, 94), 'Crew B': _T(98, 99), 'Crew C': _T(100, 101),
        'Crew D': _T(103, 104), 'Crew Avg': _T(106, 107)}},
    {'title': 'Shift Change Duration',
     'rows': ['Cat 797 (mins)', 'BE 495B (mins)', 'HIT 8000 (mins)'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(112, 2, 14), 'Crew B': _T(122, 16, 17), 'Crew C': _T(116, 18, 19),
        'Crew D': _T(118, 20, 21), 'Crew Avg': _T(120, 22, 45)}},
    {'title': 'Shift Change Tonnage (t)', 'rows': ['Shift Change Tonnage (t)'], 'cols': _ABCD, 'ids': {
        'Crew A': _T(345), 'Crew B': _T(347), 'Crew C': _T(348), 'Crew D': _T(349)}},
    {'title': 'Fuel Duration (incl. Fuel & Lube, Wait for Fuel Bay)',
     'rows': ['FUEL & LUBE', 'Wait for Fuel', 'Totall Fuel', 'OE Impact (%)'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(154, 262, 351, 370), 'Crew B': _T(160, 263, 352, 372),
        'Crew C': _T(167, 264, 353, 373), 'Crew D': _T(173, 288, 354, 374),
        'Crew Avg': _T(198, 289, 355, 375)}},
    {'title': 'Fueling %', 'rows': ['Fueling %'], 'cols': _ABCD, 'ids': {
        'Crew A': _T(265), 'Crew B': _T(284), 'Crew C': _T(285), 'Crew D': _T(286)}},
    {'title': 'Waste Capture', 'rows': ['Ore', 'Waste', 'Waste Capture'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(125, 49, 57), 'Crew B': _T(128, 58, 59), 'Crew C': _T(130, 60, 61),
        'Crew D': _T(132, 62, 133), 'Crew Avg': _T(135, 136, 137)}},
    {'title': 'Reject', 'rows': ['Reject Recycle %', 'Total Reject %'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(158, 260), 'Crew B': _T(220, 291), 'Crew C': _T(248, 292),
        'Crew D': _T(252, 338), 'Crew Avg': _T(257, 339)}},
    {'title': 'Long Empty Haul ( >10 km ) occurances/shift', 'rows': ['MRM'], 'cols': _ABCD_AVG, 'ids': {
        'Crew A': _T(141), 'Crew B': _T(143), 'Crew C': _T(145), 'Crew D': _T(147),
        'Crew Avg': _T(149)}},
]

# Per-section bar chart beside each lower table (crews A-D). Each key is a CSV chart
# series verified to match the section's headline row; matches the top-page chart style.
SECTION_CHARTS = {
    'Cable Shovel':      {'key': 'Delay Duration_Chart6_CategoryGroup_label',  'title': 'TPGOH', 'dec': 0},
    'Big Hydraulic':     {'key': 'Delay Duration_Chart6_CategoryGroup2_label', 'title': 'TPGOH', 'dec': 0},
    'Small Hydraulic':   {'key': 'Delay Duration_Chart6_CategoryGroup3_label', 'title': 'TPGOH', 'dec': 0},
    'Equipment - D11 Dozers':          {'key': 'Standby_Chart9_CategoryGroup2_label', 'title': 'UA', 'dec': 2},
    'Loading Profile (10-10-20 Rule)': {'key': 'Tonnage_Chart9_CategoryGroup_label', 'title': 'Overload %', 'dec': 2},
    'Hot Coffee Duration':             {'key': 'Duration_Chart2_CategoryGroup_label', 'title': 'Cat 797 avg. (mins)', 'dec': 1},
    'Shovel Loading Sides':            {'key': 'RS_Chart10_CategoryGroup_label', 'title': 'Double Side', 'dec': 2},
    'Shift Change Duration':           {'key': 'Duration_Chart2_CategoryGroup2_label', 'title': 'Cat 797 (mins)', 'dec': 1},
    'Shift Change Tonnage (t)':        {'key': 'Tonnage_Chart2_CategoryGroup7_label', 'title': 'Tonnage (t)', 'dec': 0},
    'Fuel Duration (incl. Fuel & Lube, Wait for Fuel Bay)': {'key': 'Reason_Chart2_CategoryGroup5_label', 'title': 'Totall Fuel', 'dec': 1},
    'Waste Capture':                   {'key': 'Ore_Chart2_CategoryGroup3_label', 'title': 'Waste Capture', 'dec': 2},
    'Reject':                          {'key': 'Reject Crusher_Chart2_CategoryGroup6_label', 'title': 'Reject Recycle %', 'dec': 2},
    'Long Empty Haul ( >10 km ) occurances/shift': {'key': 'Shift ID_Chart2_CategoryGroup4_label', 'title': 'occurances/shift', 'dec': 0},
}


def _series_map(report, key):
    """crew -> float for a chart series key (or {} if missing)."""
    s = report['series_by_key'].get(key)
    if not s:
        return {}
    return {c: v for c, v in zip(s['crews'], s['values'])}


def build_sections(report):
    """Render every lower report section from the CSV textbox values."""
    tiles = report['tiles_by_id']
    out = []
    for sec in SECTIONS:
        cols = sec['cols']
        rows = []
        for mi, label in enumerate(sec['rows']):
            vals = []
            for c in cols:
                ids = sec['ids'].get(c, [])
                tid = ids[mi] if mi < len(ids) else None
                raw = tiles.get(tid, '') if tid else ''
                vals.append(raw if raw not in ('', 'NaN') else None)
            rows.append({'label': label, 'vals': vals})
        chart = None
        cinfo = SECTION_CHARTS.get(sec['title'])
        if cinfo:
            m = _series_map(report, cinfo['key'])
            crews = ['Crew A', 'Crew B', 'Crew C', 'Crew D']
            chart = {'crews': crews, 'title': cinfo['title'], 'dec': cinfo['dec'],
                     'values': [m.get(c) for c in crews]}
        out.append({'title': sec['title'], 'group': sec.get('group'),
                    'cols': cols, 'rows': rows, 'chart': chart})
    return out


def _kpi_grid(report, metrics, ids_by_col, poe_ids=None):
    """Build display rows (metric x crew) from a column-oriented textbox-id map.

    ids_by_col: OrderedDict col-label -> [textbox id per metric]. Returns
    {'cols':[...], 'rows':[{'label','vals':[...]}]} with every value pulled from the CSV.
    """
    tiles = report['tiles_by_id']
    cols = list(ids_by_col.keys())
    rows = []
    for mi, metric in enumerate(metrics):
        vals = []
        for c in cols:
            tid = ids_by_col[c][mi]
            raw = tiles.get(tid, '')
            vals.append(raw if raw != '' else None)
        rows.append({'label': metric, 'vals': vals})
    if poe_ids:
        rows.append({'label': 'POE',
                     'vals': [tiles.get(poe_ids.get(c), '') or None for c in cols]})
    return {'cols': cols, 'rows': rows}



def build_production(report):
    """Reconstruct the Production (Mt) table from the self-describing category block.

    Block columns (positional): Category, DumpLoc, Crew, AlbianTonnage,
      Textbox53 (DumpLoc row total across crews), Textbox51 (category total for crew),
      Textbox54 (category grand total), Textbox48 (crew grand total),
      Textbox55 (report grand total), Textbox358 (avg crusher tonnes/hr for crew).
    """
    cat = report['category']
    if not cat:
        return None
    hdr = cat['header']

    def col(name):
        return hdr.index(name) if name in hdr else -1
    ci, di, cr = col('Category'), col('DumpLoc'), col('Crew')
    ta = col('AlbianTonnage')
    c_rowtot, c_cattot, c_catgrand = col('Textbox53'), col('Textbox51'), col('Textbox54')
    c_crewgrand, c_grand, c_avgcru = col('Textbox48'), col('Textbox55'), col('Textbox358')

    categories, dumplocs, crews = [], [], []
    cell = {}
    rowtot = {}          # (cat, dumploc) -> total across crews
    cattot = {}          # (cat, crew) -> category total
    catgrand = {}        # cat -> grand total
    crewgrand = {}       # crew -> grand total
    grand = None
    avgcru = {}          # crew -> avg crusher tonnes/hr
    for r in cat['rows']:
        c = r[ci] if ci >= 0 else ''
        d = r[di] if di >= 0 else ''
        w = r[cr] if cr >= 0 else ''
        if c and c not in categories:
            categories.append(c)
        if d and d not in dumplocs:
            dumplocs.append(d)
        if w and w not in crews:
            crews.append(w)
        cell[(c, d, w)] = r[ta] if ta >= 0 else ''
        if c_rowtot >= 0:
            rowtot[(c, d)] = r[c_rowtot]
        if c_cattot >= 0:
            cattot[(c, w)] = r[c_cattot]
        if c_catgrand >= 0:
            catgrand[c] = r[c_catgrand]
        if c_crewgrand >= 0:
            crewgrand[w] = r[c_crewgrand]
        if c_grand >= 0:
            grand = r[c_grand]
        if c_avgcru >= 0:
            avgcru[w] = r[c_avgcru]
    if not crews:
        crews = CREWS

    tiles = report['tiles_by_id']
    extras = []
    for label, ids in PROD_EXTRA_TILES.items():
        extras.append({'label': label, 'vals': [tiles.get(t, '') or None for t in ids]})

    prod_series = _series_map(report, PROD_CHART_SERIES)
    chart = {'crews': crews,
             'values': [round(prod_series.get(c), 3) if prod_series.get(c) is not None
                        else num(cattot.get(('Productive', c))) for c in crews]}

    return {
        'categories': categories, 'dumplocs': dumplocs, 'crews': crews,
        'cell': {f'{c}|{d}|{w}': v for (c, d, w), v in cell.items()},
        'rowtot': {f'{c}|{d}': v for (c, d), v in rowtot.items()},
        'cattot': {f'{c}|{w}': v for (c, w), v in cattot.items()},
        'catgrand': catgrand, 'crewgrand': crewgrand, 'grand': grand,
        'avgcru': avgcru, 'extras': extras, 'chart': chart,
    }


# ============================ build payload ============================
def build_mine(csv_path):
    """Parse one per-mine CSV export into a self-contained dashboard payload."""
    report = parse_report(csv_path)

    production = build_production(report)
    equip_grid = _kpi_grid(report, EQUIP_METRICS, EQUIP_IDS, EQUIP_POE_IDS)
    equipment = {
        'cols': equip_grid['cols'],
        'rows': equip_grid['rows'],
        'chart': {'crews': CREWS,
                  'title': 'TPNOH',
                  'values': [_series_map(report, EQUIP_TPNOH_CHART).get(c) for c in CREWS]},
    }
    cycle = {
        'oil': {
            'cols': list(CYCLE_OIL_IDS.keys()),
            'rows': _kpi_grid(report, CYCLE_METRICS, CYCLE_OIL_IDS)['rows'],
            'chart': {'crews': CREWS, 'title': 'CycleTime (ore)',
                      'values': [_series_map(report, CYCLE_OIL_CHART).get(c) for c in CREWS]},
        },
        'waste': {
            'cols': list(CYCLE_WASTE_IDS.keys()),
            'rows': _kpi_grid(report, CYCLE_METRICS, CYCLE_WASTE_IDS)['rows'],
            'chart': {'crews': CREWS, 'title': 'CycleTime (waste)',
                      'values': [_series_map(report, CYCLE_WASTE_CHART).get(c) for c in CREWS]},
        },
    }

    # Mine(s) named in this export's title (e.g. "JPM" or, for a combined file, "JPM , MRM").
    mines = [m.strip() for m in re.split(r'[;,/]', report['title'].get('mines', '')) if m.strip()]

    return {
        'source_csv': os.path.basename(csv_path),
        'title': report['title'],
        'mines': mines,
        'production': production,
        'equipment': equipment,
        'cycle': cycle,
        'sections': build_sections(report),
        'series': report['series'],
        'tiles': report['tiles'],
        'counts': {
            'series': len(report['series']),
            'tiles': len(report['tiles']),
            'category_rows': len(report['category']['rows']) if report['category'] else 0,
        },
    }


def _discover_mine_files():
    """Find per-mine CSV exports like CrewAccountabilityReport_JPM.csv -> {'JPM': path}.

    Falls back to the single combined CrewAccountabilityReport.csv (whose title may
    list several mines) when no per-mine files are present.
    """
    found = {}
    for f in _DATA_FILES:
        m = re.match(r'(?i)^crewaccountabilityreport[ _-]+([a-z0-9]+)\.csv$', f.strip())
        if m:
            found[m.group(1).upper()] = f'{DATADIR}/{f}'
    return found


def _order_mines(by_mine):
    """Order mines for the selector: MRM first, JPM second, then any others A-Z."""
    preferred = ['MRM', 'JPM']
    keys = list(by_mine)
    ordered = [m for m in preferred if m in by_mine]
    ordered += sorted(k for k in keys if k not in preferred)
    return ordered


def build():
    """Build the multi-mine payload: one dataset per mine so the picker can switch data."""
    mine_files = _discover_mine_files()
    by_mine = {}
    warning = ''
    if mine_files:
        for mine in sorted(mine_files):
            by_mine[mine] = build_mine(mine_files[mine])
        mines = _order_mines(by_mine)
        mode = 'per-mine'
    else:
        # No per-mine files: fall back to the combined export as a single dataset,
        # keyed by the (possibly multi-mine) title so the page still renders.
        payload = build_mine(_resolve_file(REPORT_CSV))
        key = ' , '.join(payload['mines']) or 'All mines'
        by_mine[key] = payload
        mines = [key]
        mode = 'combined-fallback'
        warning = (
            'Per-mine CSV files were not found in "%s", so the dashboard is showing the '
            'combined export and cannot split JPM vs MRM. Add CrewAccountabilityReport_JPM.csv '
            'and CrewAccountabilityReport_MRM.csv to that folder (or set DASH_DATADIR) and re-run.'
            % DATADIR)
        sys.stderr.write('\n' + '!' * 78 + '\n')
        sys.stderr.write('WARNING: ' + warning + '\n')
        sys.stderr.write('  CSV files seen in folder: %s\n'
                         % (', '.join(sorted(_DATA_FILES)) or '(none)'))
        sys.stderr.write('!' * 78 + '\n\n')

    first = by_mine[mines[0]]
    return {
        'generated': datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M %Z'),
        'mines': mines,
        'byMine': by_mine,
        'counts': first['counts'],
        'mode': mode,
        'warning': warning,
    }


out = build()
print('parsed %d mine dataset(s) [%s]: %s'
      % (len(out['mines']), out['mode'], ', '.join(out['mines'])))
for _m in out['mines']:
    _c = out['byMine'][_m]['counts']
    print('  %-9s %d chart series, %d KPI textboxes, %d breakdown rows'
          % (_m, _c['series'], _c['tiles'], _c['category_rows']))


# ============================ HTML ============================
HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crew Accountability Report</title>
__CHARTJS__
<style>
:root{--bg:#eef0f4;--card:#fff;--ink:#2b2f36;--muted:#7c828c;--line:#d7dbe2;--blue:#3f51b5;--teal:#159a8f;--head:#5c6470}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.layout{display:flex;gap:18px;align-items:flex-start;padding:16px clamp(10px,1.5vw,28px);width:100%;margin:0 auto}
.sub{color:var(--muted);font-size:12px}
h1{margin:0;font-size:19px}
h2{font-size:16px;color:var(--head);margin:0 0 12px}
h3{margin:0 0 8px;font-size:13px;color:var(--muted);font-weight:600}

.sidenav{flex:0 0 clamp(200px,15vw,300px);position:sticky;top:16px;display:flex;flex-direction:column;gap:6px}
.sidenav-logo{width:100%;padding:8px 6px 20px;text-align:center}
.sidenav-logo img{width:100%;max-width:312px;height:auto;display:block;margin:0 auto}
.sidenav-logo-label{margin-top:6px;font:400 24px/1.05 "Vineta BT","Bookman Old Style",Georgia,serif;letter-spacing:.8px;color:#5f6f7c;text-transform:uppercase;white-space:nowrap;display:inline-block}
.sidenav-logo .brand{font:600 22px/1.05 "Segoe UI",Roboto,Arial,sans-serif;letter-spacing:.5px;color:#5f6f7c;text-transform:uppercase}
.sidenav-logo .tag{font-size:11px;color:var(--muted);margin-top:2px}
.backbtn{position:fixed;top:9px;left:16px;z-index:65;border:1px solid #cfd4dd;background:#fff;color:#566;border-radius:8px;padding:7px 12px;font-weight:600;font-size:12.5px;line-height:1;cursor:pointer;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.12);text-decoration:none}
.backbtn:hover{background:#f2f5fa;border-color:var(--blue);color:var(--blue)}
.sidenav-controls{display:flex;flex-direction:column;gap:8px;padding:0 4px 12px;margin-bottom:4px;border-bottom:1px solid var(--line)}
.sidenav-controls .lbl{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
.minesel{display:flex;gap:8px}
.minesel button{flex:1 1 0;border:2px solid #cfd4dd;background:#fff;border-radius:8px;padding:9px 10px;font-weight:700;color:#566;cursor:pointer;font-size:13px;letter-spacing:.03em;transition:background .12s,border-color .12s,color .12s}
.minesel button:hover{border-color:var(--blue);color:var(--blue)}
.minesel button.on{background:var(--blue);color:#fff;border-color:var(--blue);box-shadow:0 1px 4px rgba(63,81,181,.35)}
/* prominent mine toggle in the report header */
.minetoggle{display:inline-flex;gap:0;margin:10px 0 4px;border:2px solid var(--blue);border-radius:10px;overflow:hidden}
.minetoggle button{border:0;background:#fff;color:var(--blue);cursor:pointer;font:700 15px/1 "Segoe UI",Arial,sans-serif;padding:11px 30px;letter-spacing:.04em}
.minetoggle button+button{border-left:2px solid var(--blue)}
.minetoggle button.on{background:var(--blue);color:#fff}
.minetoggle button:not(.on):hover{background:#eef1fb}
.buildstamp{margin-top:6px;font-size:11px;color:var(--muted)}
.warnbanner{background:#fdecea;border:1px solid #f5c6cb;color:#8a1c1c;border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:12.5px;font-weight:600}
.sidenav .navbtn{text-align:left;border:1px solid #cfd4dd;background:#fff;border-radius:8px;padding:10px 14px;font-weight:600;color:#566;cursor:pointer;font-size:16px;line-height:1.2;white-space:nowrap}
.sidenav .navbtn.on{background:var(--blue);color:#fff;border-color:var(--blue)}
/* ── Collapsible / hide-able sidebar (matches haulage_dashboard) ── */
#sbEdge{position:fixed;top:0;left:0;width:18px;height:100vh;z-index:55;display:none}
#sbShow,#sbArrow{position:fixed;top:50%;transform:translateY(-50%);z-index:56;display:none;align-items:center;justify-content:center;width:22px;height:64px;border:1px solid #cfd4dd;border-left:0;border-radius:0 10px 10px 0;background:#fff;color:#566;font-size:16px;line-height:1;cursor:pointer;box-shadow:2px 0 7px rgba(0,0,0,.12);padding:0}
#sbShow{left:0}
#sbShow:hover,#sbArrow:hover{background:var(--blue);color:#fff;border-color:var(--blue)}
body.sb-auto #sbEdge{display:block}
body.sb-auto #sbShow{display:flex}
body:not(.sb-auto) #sbArrow{display:flex}
body.sb-auto.sb-show #sbShow{display:none}
body.sb-auto .layout{gap:0}
body.sb-auto .sidenav{position:fixed;left:0;top:0;height:100vh;width:clamp(240px,22vw,320px);margin:0;padding:16px 14px 16px 12px;background:var(--bg);box-shadow:2px 0 14px rgba(0,0,0,.16);overflow-y:auto;transform:translateX(-100%);transition:transform .22s ease;z-index:60}
body.sb-auto.sb-show .sidenav{transform:translateX(0)}

.content{flex:1;min-width:0}
.topbar{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:16px}
.topbar .minebadge{color:var(--blue);font-weight:700}
.topbar .meta{margin-top:6px;font-size:13px}
.topbar .meta .ml{font-weight:700;color:var(--head)}
.topbar .meta .mv{color:var(--blue);font-weight:600}
.page{display:none}
.page.on{display:block}
.block{margin-bottom:26px}
.split{display:flex;gap:clamp(12px,1.4vw,22px);flex-wrap:wrap;align-items:flex-start}
.split .tblwrap{flex:1 1 460px;min-width:min(340px,100%);overflow-x:auto}
.split .chartwrap{flex:1 1 360px;min-width:min(300px,100%);background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.tblwrap.cyc{display:flex;align-items:stretch;gap:0}
.tblwrap.cyc .vlabel{writing-mode:vertical-rl;transform:rotate(180deg);text-align:center;font-weight:700;color:var(--head);
  padding:2px 3px;border:1px solid var(--line);border-right:none;background:#fafbfd;display:flex;align-items:center;
  justify-content:center;font-size:12px;margin-bottom:14px}
.tblwrap.cyc table.rpt{margin-bottom:14px}

table.rpt{border-collapse:collapse;background:var(--card);border:1px solid var(--line);font-size:clamp(11px,.95vw,13px);margin-bottom:14px}
table.rpt caption{caption-side:top;text-align:left;font-weight:700;color:var(--head);padding:0 0 6px;font-size:clamp(12px,1vw,13.5px)}
table.rpt th,table.rpt td{border:1px solid var(--line);padding:4px clamp(5px,.7vw,9px);text-align:right;white-space:nowrap}
table.rpt th{background:#f4f6fa;color:var(--muted);font-weight:600}
table.rpt td.lbl,table.rpt th.lbl{text-align:left;color:var(--ink)}
table.rpt td.cat{text-align:left;font-weight:600;color:var(--head);vertical-align:middle;background:#fafbfd}
table.rpt tr.tot td{font-weight:700;background:#f7f9fc}
table.rpt tr.grand td{font-weight:700;background:#eef1f8}
table.rpt td.na{color:#b9bfc8}
.rowhdr{font-weight:700;color:var(--head)}
.secwrap{display:inline-block;vertical-align:top;margin:0 18px 14px 0}
table.rpt.sec{min-width:280px}
.note{color:var(--muted);font-size:11.5px;margin:6px 0 0}
.note b{color:#9a6a00}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:150px}
.card .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:22px;font-weight:700;margin-top:2px;color:var(--head)}
.ds{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px}
.ds b{color:var(--blue)}
.ds .fields{color:var(--muted);font-size:12px;margin-top:3px;word-spacing:2px}
.tiles{display:flex;flex-wrap:wrap;gap:8px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:92px}
.tile .id{color:var(--muted);font-size:11px}
.tile .val{font-size:15px;font-weight:700;color:var(--head)}
details{margin-top:8px}summary{cursor:pointer;color:var(--blue)}
canvas{max-width:100%}
/* chart holder: height is set in JS to match its table (then shrunk 20%) */
.chartwrap .cholder{position:relative;width:100%;height:220px}
.chartwrap .cholder canvas{position:absolute;inset:0;width:100%!important;height:100%!important}
/* green/red conditional formatting vs the crew average (target) */
table.rpt td.good{background:#e6f4ea;color:#137333;font-weight:600}
table.rpt td.bad{background:#fce8e6;color:#b3261e;font-weight:600}
footer{color:var(--muted);font-size:12px;padding:8px clamp(10px,1.5vw,28px) 24px;width:100%;margin:0 auto}
@media(max-width:900px){.layout{gap:10px}.sidenav{flex:0 1 clamp(120px,22vw,210px);min-width:0}}
@media print{.sidenav,.backbtn,#sbShow,#sbArrow,#sbEdge{display:none!important}.layout{display:block}.content{min-width:0}}
</style>
</head>
<body>
<a class="backbtn" href="Albian_Mine_Operations_Portal.html" title="Back to Albian Mine Operations Portal">&#8249; Portal</a>
<button id="sbShow" onclick="toggleSidebar()" title="Show sidebar">&#8250;</button>
<button id="sbArrow" onclick="toggleSidebar()" title="Hide sidebar">&#8249;</button>
<div id="sbEdge"></div>
<div class="layout">
  <nav class="sidenav" id="sidenav">
    <div class="sidenav-logo">
      <img src="https://github.com/user-attachments/assets/d258c096-68f1-4f87-8919-c0d0cab5eba6" alt="CNRL iSTAR logo" onerror="this.onerror=null;this.src='istarlogov2.png';">
      <div class="sidenav-logo-label">Crew Accountability</div>
    </div>
    <div class="sidenav-controls">
      <div class="lbl">Mine Selection</div>
      <div class="minesel" id="mineSel"></div>
    </div>
    <button class="navbtn on" data-tab="report">Report</button>
  </nav>

  <main class="content">
    <div id="warnBanner" class="warnbanner" style="display:none"></div>
    <div class="topbar">
      <h1>Crew Accountability Report</h1>
      <div class="meta"><span class="ml">Shift Range:</span> <span class="mv" id="shiftRange"></span></div>
      <div class="meta"><span class="ml">Pit:</span> <span class="mv" id="pit"></span></div>
      <div class="buildstamp" id="buildStamp"></div>
    </div>

    <section class="page on" id="report">
      <div class="block">
        <h2>Production (Mt)</h2>
        <div class="split">
          <div class="tblwrap">
            <table class="rpt" id="prodTable"></table>
            <table class="rpt" id="prodExtra"></table>
          </div>
          <div class="chartwrap"><h3>Productive tonnage by crew</h3><div class="cholder"><canvas id="prodChart"></canvas></div></div>
        </div>
      </div>

      <div class="block">
        <h2>Equipment - Cat 797 Trucks</h2>
        <div class="split">
          <div class="tblwrap"><table class="rpt" id="equipTable"></table></div>
          <div class="chartwrap"><h3>TPNOH</h3><div class="cholder"><canvas id="equipChart"></canvas></div></div>
        </div>
      </div>

      <div class="block">
        <div class="split">
          <div class="tblwrap cyc"><div class="vlabel">Oilsand</div><table class="rpt" id="oilTable"></table></div>
          <div class="chartwrap"><h3>CycleTime (ore)</h3><div class="cholder"><canvas id="oilChart"></canvas></div></div>
        </div>
        <div class="split" style="margin-top:16px">
          <div class="tblwrap cyc"><div class="vlabel">Waste</div><table class="rpt" id="wstTable"></table></div>
          <div class="chartwrap"><h3>CycleTime (waste)</h3><div class="cholder"><canvas id="wstChart"></canvas></div></div>
        </div>
      </div>

      <div id="moreSections"></div>
    </section>

    <section class="page" id="source">
      <h2>Source data</h2>
      <div class="sub" style="margin-bottom:10px">Raw values parsed from <code id="csvName"></code> — chart series and single-value textboxes.</div>
      <h3>Chart series</h3>
      <div id="serList" style="margin-bottom:16px"></div>
      <h3>KPI textboxes (<span id="tileCount"></span>)</h3>
      <div class="tiles" id="tiles"></div>
    </section>
  </main>
</div>
<footer id="foot"></footer>

<script>
const DATA = __DATA__;
const TEAL='#159a8f', SKY='#7fbfe0', PAL=['#3f51b5','#4caf50','#e0952a','#7a4fd0'];
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function cell(v){return v==null||v===''?'<td class="na">&mdash;</td>':`<td>${esc(v)}</td>`;}

// mine selection (main page) — each mine is a full dataset; switching re-renders everything
let selMine=(DATA.mines&&DATA.mines[0])||'';
function curData(){return DATA.byMine[selMine]||{};}
function mineLabel(){const D=curData();return (D.title&&D.title.mines)||selMine||'All mines';}
function renderSub(){
  const D=curData();
  document.getElementById('shiftRange').textContent = (D.title&&D.title.period)||'';
  document.getElementById('pit').textContent = mineLabel();
  const bs=document.getElementById('buildStamp');
  if(bs) bs.textContent='Showing '+(selMine||'')+' only · source: '+(D.source_csv||'')+' · built '+(DATA.generated||'');
}
function renderMineSel(){
  const mines=DATA.mines.slice();
  const paint=(wrap)=>{
    if(!wrap) return;
    wrap.innerHTML=mines.map(m=>`<button data-m="${esc(m)}" class="${m===selMine?'on':''}">${esc(m)}</button>`).join('');
    wrap.querySelectorAll('button').forEach(b=>b.onclick=()=>{selMine=b.getAttribute('data-m');renderMineSel();renderAll();});
  };
  paint(document.getElementById('mineSel'));
}

// tab switching
function setTab(id){
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on',p.id===id));
  document.querySelectorAll('.navbtn').forEach(b=>b.classList.toggle('on',b.getAttribute('data-tab')===id));
}
document.querySelectorAll('.navbtn').forEach(b=>b.onclick=()=>setTab(b.getAttribute('data-tab')));

// ---------- collapsible / hide-able sidebar (matches haulage_dashboard) ----------
let sbAuto=false;   // sidebar visible by default; hide button slides it off-screen
try{sbAuto=localStorage.getItem('crewDashSb')==='1';}catch(e){}
function applySidebar(){
  document.body.classList.toggle('sb-auto',sbAuto);
  if(!sbAuto) document.body.classList.remove('sb-show');
  posHideTab();
}
function posHideTab(){
  const sn=document.getElementById('sidenav'), b=document.getElementById('sbArrow');
  if(sn&&b&&!sbAuto) b.style.left=Math.round(sn.getBoundingClientRect().right)+'px';
}
function toggleSidebar(){
  sbAuto=!sbAuto; applySidebar();
  try{localStorage.setItem('crewDashSb',sbAuto?'1':'0');}catch(e){}
}
function initSidebarHover(){
  const sn=document.getElementById('sidenav');
  const show=()=>{if(sbAuto)document.body.classList.add('sb-show');};
  const hide=()=>document.body.classList.remove('sb-show');
  const edge=document.getElementById('sbEdge'); if(edge) edge.addEventListener('mouseenter',show);
  if(sn){sn.addEventListener('mouseenter',show);sn.addEventListener('mouseleave',hide);}
}
window.addEventListener('resize',posHideTab);

// ---------- fit the logo label to the logo width on a single line ----------
function fitLogoLabel(){
  const label=document.querySelector('.sidenav-logo-label');
  const logo=document.querySelector('.sidenav-logo img');
  if(!label||!logo) return;
  const avail=(logo.getBoundingClientRect().width)||label.parentElement.clientWidth;
  if(!avail) return;
  const MAX=24, MIN=9;
  label.style.letterSpacing='.8px';
  label.style.fontSize=MAX+'px';
  // shrink until the single-line text fits within the logo width (or hit the min)
  let fs=MAX;
  while(fs>MIN && label.scrollWidth>avail){ fs-=0.5; label.style.fontSize=fs+'px'; }
  // if still too wide at the smallest font, drop the letter-spacing to reclaim width
  if(label.scrollWidth>avail) label.style.letterSpacing='0';
}
let _fitReq=null;
function scheduleFitLogoLabel(){
  cancelAnimationFrame(_fitReq);
  _fitReq=requestAnimationFrame(()=>requestAnimationFrame(fitLogoLabel));
}
window.addEventListener('resize',scheduleFitLogoLabel);
(function(){
  const logo=document.querySelector('.sidenav-logo img');
  if(logo){ if(logo.complete) scheduleFitLogoLabel(); else logo.addEventListener('load',scheduleFitLogoLabel); logo.addEventListener('error',scheduleFitLogoLabel); }
  if(document.fonts&&document.fonts.ready){ document.fonts.ready.then(scheduleFitLogoLabel); }
})();

// ---------- bar chart with value labels + red average line (SSRS look) ----------
function chartPlugins(dec){
  return [{
    id:'valLabels',
    afterDatasetsDraw(chart){
      const ctx=chart.ctx, meta=chart.getDatasetMeta(0), ds=chart.data.datasets[0].data;
      ctx.save();ctx.font='bold 12px Segoe UI,Arial';ctx.fillStyle='#555';ctx.textAlign='center';
      meta.data.forEach((b,i)=>{ if(ds[i]!=null) ctx.fillText(Number(ds[i]).toFixed(dec), b.x, b.y-6); });
      ctx.restore();
    }
  },{
    id:'avgLine',
    afterDraw(chart){
      const vals=chart.data.datasets[0].data.filter(v=>v!=null).map(Number);
      if(!vals.length)return;
      const avg=vals.reduce((a,b)=>a+b,0)/vals.length;
      const ctx=chart.ctx, ca=chart.chartArea, y=chart.scales.y.getPixelForValue(avg);
      ctx.save();ctx.strokeStyle='#d33';ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(ca.left,y);ctx.lineTo(ca.right,y);ctx.stroke();ctx.restore();
    }
  }];
}
function crewChart(el,cfg){
  const dec=cfg.dec==null?1:cfg.dec;
  const cv=document.getElementById(el); if(!cv) return;
  const ex=(window.Chart&&Chart.getChart)?Chart.getChart(cv):null; if(ex) ex.destroy();
  new Chart(cv,{type:'bar',
    data:{labels:cfg.crews,datasets:[{data:cfg.values,backgroundColor:cfg.color||TEAL,maxBarThickness:46}]},
    options:{maintainAspectRatio:false,responsive:true,layout:{padding:{top:22}},plugins:{legend:{display:false}},
      scales:{y:{beginAtZero:false,ticks:{color:'#7c828c'},
                 title:{display:!!cfg.ytitle,text:cfg.ytitle||'',color:'#7c828c'}},
              x:{ticks:{color:'#7c828c'},grid:{display:false}}}},
    plugins:chartPlugins(dec)});
}

// ---------- Production (Mt) ----------
function renderProduction(D){
  const P=D.production; if(!P){document.getElementById('prodTable').innerHTML='<caption>No production data.</caption>';document.getElementById('prodExtra').innerHTML='';return;}
  const crews=P.crews, span=P.dumplocs.length+1;
  let h='<caption>Production (Mt)</caption><thead><tr><th class="lbl">Category</th><th class="lbl">Dump Loc</th>'+
        crews.map(c=>`<th>${esc(c)}</th>`).join('')+'<th>Total</th></tr></thead><tbody>';
  P.categories.forEach(c=>{
    P.dumplocs.forEach((d,j)=>{
      h+='<tr>';
      if(j===0) h+=`<td class="cat" rowspan="${span}">${esc(c)}</td>`;
      h+=`<td class="lbl">${esc(d)}</td>`;
      h+=crews.map(cr=>cell(P.cell[c+'|'+d+'|'+cr])).join('');
      h+=cell(P.rowtot[c+'|'+d])+'</tr>';
    });
    h+='<tr class="tot"><td class="lbl">Total</td>'+crews.map(cr=>cell(P.cattot[c+'|'+cr])).join('')+cell(P.catgrand[c])+'</tr>';
  });
  h+='<tr class="grand"><td class="lbl" colspan="2">Total</td>'+crews.map(cr=>cell(P.crewgrand[cr])).join('')+cell(P.grand)+'</tr>';
  h+='<tr><td class="lbl" colspan="2">Avg. crusher tonnes/hr</td>'+crews.map(cr=>cell(P.avgcru[cr])).join('')+'<td class="na">&mdash;</td></tr>';
  h+='</tbody>';
  document.getElementById('prodTable').innerHTML=h;

  let e='<tbody>';
  P.extras.forEach(row=>{
    e+='<tr><td class="lbl rowhdr">'+esc(row.label)+'</td>'+row.vals.map(cell).join('')+'</tr>';
  });
  e+='</tbody>';
  document.getElementById('prodExtra').innerHTML=e;

  crewChart('prodChart',{crews:P.chart.crews,values:P.chart.values,color:TEAL,dec:2,ytitle:'Productive (Mt)'});
}

// ---------- generic crew KPI table ----------
function kpiTable(el,title,rows,cols){
  let h=`<caption>${esc(title)}</caption><thead><tr><th class="lbl"></th>`+
        cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr></thead><tbody>';
  rows.forEach(r=>{
    h+='<tr><td class="lbl rowhdr">'+esc(r.label)+'</td>'+r.vals.map(cell).join('')+'</tr>';
  });
  h+='</tbody>';
  document.getElementById(el).innerHTML=h;
}

// ---------- Equipment ----------
function renderEquipment(D){
  const E=D.equipment;
  kpiTable('equipTable','Equipment - Cat 797 Trucks',E.rows,E.cols);
  crewChart('equipChart',{crews:E.chart.crews,values:E.chart.values,color:SKY,dec:0,ytitle:'TPNOH'});
}

// ---------- Cycle Time ----------
function renderCycle(D){
  const C=D.cycle;
  kpiTable('oilTable','Oilsand cycle time',C.oil.rows,C.oil.cols);
  kpiTable('wstTable','Waste cycle time',C.waste.rows,C.waste.cols);
  crewChart('oilChart',{crews:C.oil.chart.crews,values:C.oil.chart.values,color:TEAL,dec:1,ytitle:'CycleTime (ore)'});
  crewChart('wstChart',{crews:C.waste.chart.crews,values:C.waste.chart.values,color:TEAL,dec:1,ytitle:'CycleTime (waste)'});
}

// ---------- additional lower report sections ----------
function renderSections(D){
  const secs=D.sections||[];
  const host=document.getElementById('moreSections');
  if(!secs.length){host.innerHTML='';return;}
  let html='', lastGroup=null; const charts=[];
  secs.forEach((s,idx)=>{
    if(s.group && s.group!==lastGroup){ html+=`<h2 style="margin-top:22px">${esc(s.group)}</h2>`; lastGroup=s.group; }
    else if(!s.group){ lastGroup=null; }
    let t=`<table class="rpt sec"><caption>${esc(s.title)}</caption><thead><tr><th class="lbl"></th>`+
          s.cols.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr></thead><tbody>';
    s.rows.forEach(r=>{
      t+='<tr><td class="lbl rowhdr">'+esc(r.label)+'</td>'+r.vals.map(cell).join('')+'</tr>';
    });
    t+='</tbody></table>';
    let chartHtml='';
    if(s.chart){
      const cid='secChart'+idx;
      chartHtml=`<div class="chartwrap"><h3>${esc(s.chart.title)}</h3><div class="cholder"><canvas id="${cid}"></canvas></div></div>`;
      charts.push({id:cid,c:s.chart});
    }
    html+=`<div class="block"><div class="split"><div class="tblwrap">${t}</div>${chartHtml}</div></div>`;
  });
  host.innerHTML=html;
  charts.forEach(o=>crewChart(o.id,{crews:o.c.crews,values:o.c.values,color:TEAL,dec:o.c.dec,ytitle:o.c.title}));
}

// ---------- Source data ----------
function renderSource(D){
  document.getElementById('csvName').textContent=D.source_csv;
  document.getElementById('serList').innerHTML=D.series.map(s=>
    `<div class="ds"><b>${esc(s.label)}</b><div class="fields">${s.crews.map((c,i)=>esc(c)+': '+esc(s.values[i])).join(' · ')}</div></div>`).join('');
  document.getElementById('tileCount').textContent=D.tiles.length;
  document.getElementById('tiles').innerHTML=D.tiles.map(t=>
    `<div class="tile"><div class="id">${esc(t.id)}</div><div class="val">${esc(t.value)}</div></div>`).join('');
  document.getElementById('foot').textContent=
    'Generated '+DATA.generated+' from '+D.source_csv+
    '  ·  '+D.counts.series+' chart series, '+D.counts.tiles+' KPI textboxes.';
}

// ---------- green/red conditional formatting ----------
// Metrics where a LOWER value is the better outcome (time/delay/loss style).
// Keep these specific so sibling sections where higher is better are not caught,
// e.g. "Shift Change Tonnage (t)" and "Fueling %" must stay higher-is-better.
const LOWER_BETTER=['delay','cycle time','empty haul','idle','queue','spot time',
  'loading time','dumping','hang','overload','reject','fuel duration','fuel & lube',
  'shift change duration','standby','hot coffee','non-productive','wait','release duration',
  'long empty haul'];
function _num(t){const n=parseFloat(String(t==null?'':t).replace(/[, %]/g,''));return isNaN(n)?null:n;}
function colorTargets(tbl){
  const ths=[...tbl.querySelectorAll('thead th')];
  if(!ths.length) return;
  const heads=ths.map(h=>h.textContent.trim().toLowerCase());
  if(heads.includes('category')||heads.includes('dump loc')) return; // skip complex production grid
  const crewIdx=[]; heads.forEach((h,i)=>{ if(/^crew [a-d]$/.test(h)) crewIdx.push(i); });
  if(crewIdx.length<2) return;
  const avgIdx=heads.indexOf('crew avg');
  const capEl=tbl.querySelector('caption');
  const caption=(capEl?capEl.textContent:'').toLowerCase();
  tbl.querySelectorAll('tbody tr').forEach(tr=>{
    const tds=[...tr.children];
    if(tds.length!==heads.length) return; // alignment guard
    const lblEl=tr.querySelector('td.lbl');
    // Section tables carry the metric name in the caption (e.g. "Hot Coffee Duration");
    // per-metric grids carry it in the row label. Check both.
    const label=((lblEl?lblEl.textContent:'')+' '+caption).toLowerCase();
    const lower=LOWER_BETTER.some(k=>label.includes(k));
    // Special fixed target: Double Side loading — >=60% is good (green), below is bad (red).
    const dblSide=caption.includes('loading sides') &&
                  (lblEl?lblEl.textContent.toLowerCase().includes('double'):false);
    let target=avgIdx>=0?_num(tds[avgIdx].textContent):null;
    const vals=crewIdx.map(i=>_num(tds[i].textContent));
    if(dblSide){
      crewIdx.forEach((i,k)=>{
        const v=vals[k]; if(v==null) return;
        tds[i].classList.remove('good','bad');
        tds[i].classList.add(v>=60?'good':'bad');
      });
      return;
    }
    if(target==null){
      const nn=vals.filter(v=>v!=null);
      if(nn.length<2) return;
      target=nn.reduce((a,b)=>a+b,0)/nn.length;
    }
    crewIdx.forEach((i,k)=>{
      const v=vals[k]; if(v==null) return;
      const good=lower?(v<=target):(v>=target);
      tds[i].classList.remove('good','bad');
      tds[i].classList.add(good?'good':'bad');
    });
  });
}
function applyTargets(){
  document.querySelectorAll('#report table.rpt').forEach(colorTargets);
}

// ---------- match each chart's height to its table, then shrink 20% ----------
function sizeCharts(){
  document.querySelectorAll('#report .split').forEach(sp=>{
    const tw=sp.querySelector('.tblwrap'); if(!tw) return;
    const h=Math.max(90,Math.round(tw.offsetHeight*0.8)); // graph = table height, shrunk 20%
    sp.querySelectorAll('.cholder').forEach(ch=>{ ch.style.height=h+'px'; });
  });
  if(window.Chart){
    document.querySelectorAll('#report canvas').forEach(cv=>{
      const c=Chart.getChart(cv); if(c) c.resize();
    });
  }
}
let _sizeReq=null;
function scheduleSizeCharts(){
  cancelAnimationFrame(_sizeReq);
  _sizeReq=requestAnimationFrame(()=>requestAnimationFrame(sizeCharts));
}
window.addEventListener('resize',scheduleSizeCharts);

// ---------- render everything for the selected mine ----------
function renderAll(){
  const D=curData();
  renderSub();
  renderProduction(D);
  renderEquipment(D);
  renderCycle(D);
  renderSections(D);
  renderSource(D);
  applyTargets();
  scheduleSizeCharts();
}

// ---------- fallback warning banner ----------
(function(){
  const b=document.getElementById('warnBanner');
  if(b && DATA.warning){ b.textContent='⚠ '+DATA.warning; b.style.display='block'; }
})();

renderMineSel();renderAll();
applySidebar();initSidebarHover();scheduleFitLogoLabel();
</script>
</body>
</html>'''

HTML = HTML.replace('__DATA__', json.dumps(out))

# Inline Chart.js for a fully self-contained, offline / no-CDN file. Falls back to CDN if the lib is absent.
try:
    _cjs = open(f'{BASE}/lib_chartjs.js', encoding='utf-8').read()
    chart_tag = '<script>\n' + _cjs + '\n</script>'
    _mode = 'inlined (offline-ready, no CDN)'
except FileNotFoundError:
    chart_tag = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>'
    _mode = 'CDN fallback (lib_chartjs.js not found)'
HTML = HTML.replace('__CHARTJS__', chart_tag)

open(OUT_HTML, 'w', encoding='utf-8').write(HTML)
print('HTML written: %s (%d KB)  Chart.js: %s' % (OUT_HTML, len(HTML) // 1024, _mode))
