#!/usr/bin/env python3
"""
Albian Mine Haulage Dashboard - data builder + HTML generator.
Re-run this whenever the source CSVs change:  python3 build_dashboard.py
Outputs: Haulage_Dashboard.html  (self-contained, open in any browser)

Locked definitions (agreed section-by-section):
  HAULAGE SCORE  : T1 trucks. Payload-normalized basis: rate* = 361t / budget-cycle-seconds (so budget-cycle
                   tonnes = 361/load). Potential = sum(rate* x actual cycle-seconds); Actual = sum(Tonnage);
                   Score = Actual/Potential. (Gauge Potential equals the truck-waterfall Potential.)
  LOADING SCORE  : S0=BE495, S8=HIT8000 shovels. Potential = sum(NOH x budget TPNOH per pit/type/material),
                   NOH=(Spot+Load+Hang)/3600 floored at 2.5min; Actual=sum(Tonnage); Score=Actual/Potential.
  TRUCK BALANCE  : TruckBalance.csv, Required vs LP-Actual, shift average, %under=(Req-Act)/Req.
  TRUCKS WATERFALL: Potential(rate* x actual cycle) -> time-variance rows (rate* x (budget-actual) per segment)
                   -> Payload row (actual - 361) -> Actual. Payload-normalized rate* makes budget-cycle tonnes
                   = 361/load, so the bridge closes with NO residual and Payload is measured vs the 361 t target.
                   full/empty travel split by actual speed (0.555 full); Empty/Full shown as indicator.
                   Above Potential: Sched. Potential → PA → UA → OE rows (Cat 797 availability vs budget
                   CSV, same calc as KPI tab), each bar embeds the top Down/Standby/Delay reason.
"""
import csv, json, datetime, os, re, zipfile
from collections import defaultdict
from xml.etree import ElementTree as ET

# Anchor all paths to THIS script's folder, so the project works from any working directory
# or after being copied/moved to another computer (no dependency on the current directory).
BASE=os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
MONTHS={'1':'Jan','2':'Feb','3':'Mar','4':'Apr','5':'May','6':'Jun','7':'Jul','8':'Aug','9':'Sep','10':'Oct','11':'Nov','12':'Dec'}
PAYLOAD_TARGET=361.0
SAND_HAUL_TARGET=None         # PLACEHOLDER — per-shift sand-haul target (tonnes); wire to budget later
NOH_FLOOR_S=150.0            # 2.5 min shovel-load floor
PITS=['MRM','JPM']
WF_ROWS=['Payload','Load','Queue','Spot','DumpIdle','Dumping','FullHaul','EmptyHaul']
LM_KEY={'Load':'Load','Queue':'Queue','Spot':'Spot','DumpIdle':'DumpIdle','Dumping':'Dumping','FullHaul':'Full','EmptyHaul':'Empty'}
CYC=['Queue','Spot','Load','Empty','Full','DumpIdle','Dumping']
import statistics as _st
from datetime import datetime as _dtm, timedelta as _td

def num(x):
    try: return float(x)
    except: return 0.0
_DTFMTS=('%m/%d/%Y %I:%M:%S %p','%m/%d/%y %I:%M:%S %p','%m/%d/%Y %H:%M:%S',   # SSRS 12-hour AM/PM + others
         '%Y-%m-%d %H:%M:%S','%m/%d/%y %H:%M','%m/%d/%Y %H:%M','%m/%d/%y %H:%M:%S','%Y-%m-%d %H:%M')
def _dtp(s):
    s=str(s).strip()
    if not s or s=='NULL': return None
    if 'T' in s and '/' not in s: s=s.replace('T',' ')
    for f in _DTFMTS:
        try: return _dtm.strptime(s,f)
        except: pass
    try: return _dtm.strptime(s[:19],'%Y-%m-%d %H:%M:%S')   # ISO with trailing junk
    except: return None
def _dtp_tb(s):
    return _dtp(s)

# ---------- budgets (all months, keyed for per-shift lookup) ----------
def load_curve(fn):
    d=defaultdict(dict)
    for r in csv.DictReader(open(fn,encoding='utf-8-sig')):
        d[r['Month']][(r['Material'],round(float(r['HD (km)']),1))]={
            'TPNOH':float(r['TPNOH (t/h)']),'Cycle':float(r['Cycle (min)']),'Travel':float(r['Travel (min)'])}
    return d
CURVE={'MRM':load_curve(f'{BASE}/Budget/MRM Haul Curve.csv'),'JPM':load_curve(f'{BASE}/Budget/JPM Haul Curve.csv')}
def load_fx(fn):
    # month -> material -> shoveltype -> {times}. ShovelType splits Load_Time (Average/Cable/Hydraulic);
    # the non-Load times are identical across types. Falls back to a single 'Average' row for old files.
    d=defaultdict(lambda:defaultdict(dict))
    for r in csv.DictReader(open(fn,encoding='utf-8-sig')):
        st=(r.get('ShovelType') or 'Average').strip() or 'Average'
        d[r['month_f']][r['Material2']][st]={'Queue':float(r['Queue_Time']),'Spot':float(r['Spot_Time']),
            'Load':float(r['Load_Time']),'DumpIdle':float(r['Dump_Idle']),'Dumping':float(r['Dumping_Time'])}
    return d
FX={'MRM':load_fx(f'{BASE}/Budget/MRM_Fixed_Times.csv'),'JPM':load_fx(f'{BASE}/Budget/JPM_Fixed_Times.csv')}
def _fxmerge(bytype):
    # collapse the per-shoveltype rows into one rec: shared times from Average, plus LoadT = per-type Load minutes
    base=dict(bytype.get('Average') or next(iter(bytype.values())))
    base['LoadT']={st:bytype[st]['Load'] for st in bytype}
    return base
def load_pitbud(fn):
    d={}
    for r in csv.DictReader(open(fn,encoding='utf-8-sig')):
        d[(r['Month'],r['Date'],r['Shift'])]=r
    return d
PITBUD={'MRM':load_pitbud(f'{BASE}/Budget/MRM 2026 Budget.csv'),'JPM':load_pitbud(f'{BASE}/Budget/JPM 2026 Budget.csv')}

# ---------- data (all shifts, loaded once) ----------
DATADIR=os.environ.get('DASH_DATADIR') or f'{BASE}/Data'   # override with DASH_DATADIR to point at another folder
DEFAULT_DATADIR=f'{BASE}/Data'
PLAYBOOK_GAP_LIBRARY_DEFAULT={
    'FULL_HAUL_DURATION':{
        'measure':'Full Haul Duration','area':'Haulage (Trucks)','tab':'haulage',
        'detail':'Loaded travel running over haul-curve target. Check road conditions, speed compliance, routing.'
    },
    'EMPTY_HAUL_DURATION':{
        'measure':'Empty Haul Duration','area':'Haulage (Trucks)','tab':'haulage',
        'detail':'Empty return travel running over expected. Check road surface, haul road obstructions.'
    },
    'SHOVEL_HANG_TIME':{
        'measure':'Shovel Hang Time','area':'Loading (Shovels)','tab':'loading',
        'detail':'Shovels idling waiting for trucks. Fleet is under-trucked or truck assignment gaps exist.'
    },
    'DUMP_QUEUE_TIME':{
        'measure':'Dump Queue Time','area':'Dump / Crusher','tab':'trucks',
        'detail':'Trucks queuing at dump longer than budget. Check crusher availability or truck bunching.'
    },
    'LOADING_TIME':{
        'measure':'Loading Time','area':'Loading (Shovels)','tab':'loading',
        'detail':'Average loading time exceeds budget by >10 %. Check dig face conditions and bucket fill factor.'
    },
    'SPOT_TIME':{
        'measure':'Spot Time','area':'Loading (Shovels)','tab':'loading',
        'detail':'Trucks taking longer than budget to position at shovel. Coaching on approach / face geometry.'
    },
    'HANG_QUEUE_RATIO':{
        'measure':'Hang/Queue Ratio (Under-Trucked)','area':'Truck / Shovel Balance','tab':'balance',
        'detail':'Shovels idling far more than trucks queuing. Add truck(s) or re-assign to this shovel area.'
    }
}
PLAYBOOK_GAP_LIBRARY_CANDIDATES=[
    f'{DATADIR}/playbook_gap_library.json',
    f'{DEFAULT_DATADIR}/playbook_gap_library.json',
    f'{BASE}/playbook_gap_library.json',
]
MASTER_TRACKING_ACTIONS_CSV_CANDIDATES=[
    f'{DATADIR}/master_tracking_actions.csv',
    f'{DEFAULT_DATADIR}/master_tracking_actions.csv',
    f'{BASE}/master_tracking_actions.csv',
]
MASTER_TRACKING_ACTION_FIELDS=[
    'mine','shiftId','intervalId','assetId','deviation','corrective','owner',
    'support','slaDl','status','statusChangedAt','rootCause','impactVal','impactUnit','dateCreated'
]
SUGGESTION_FIELDS=[
    'mine','shiftId','category','area','suggestion','benefit',
    'owner','priority','status','dateCreated','notes'
]

def _xlsx_col_index(ref):
    letters=''.join(ch for ch in str(ref or '') if ch.isalpha()).upper()
    n=0
    for ch in letters:
        n=n*26+(ord(ch)-64)
    return max(0,n-1)

def _xlsx_cell_text(cell, ns, shared):
    ctype=cell.attrib.get('t') or ''
    if ctype=='inlineStr':
        node=cell.find(f'{{{ns}}}is')
        return ''.join(node.itertext()) if node is not None else ''
    val=cell.find(f'{{{ns}}}v')
    if val is None or val.text is None:
        return ''
    txt=str(val.text)
    if ctype=='s':
        try: return shared[int(txt)]
        except Exception: return ''
    return txt

def _load_xlsx_rows(path):
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(path) as zf:
        shared=[]
        if 'xl/sharedStrings.xml' in zf.namelist():
            sroot=ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in sroot.findall(f'{{{ns}}}si'):
                shared.append(''.join(si.itertext()))
        root=ET.fromstring(zf.read('xl/worksheets/sheet1.xml'))
        rows=[]
        for row in root.findall(f'.//{{{ns}}}sheetData/{{{ns}}}row'):
            vals=[]
            for cell in row.findall(f'{{{ns}}}c'):
                idx=_xlsx_col_index(cell.attrib.get('r'))
                while len(vals)<=idx:
                    vals.append('')
                vals[idx]=_xlsx_cell_text(cell,ns,shared).strip()
            rows.append(vals)
        return rows

def load_suggestions():
    for path in [f'{DATADIR}/suggestions.xlsx', f'{DEFAULT_DATADIR}/suggestions.xlsx', f'{BASE}/suggestions.xlsx']:
        if not os.path.exists(path):
            continue
        try:
            rows=_load_xlsx_rows(path)
            if not rows:
                return []
            header_idx={}
            for i,h in enumerate(rows[0]):
                key=str(h or '').strip().lower()
                if key:
                    header_idx[key]=i
            items=[]
            for row in rows[1:]:
                item={}
                has_data=False
                for key in SUGGESTION_FIELDS:
                    idx=header_idx.get(key.lower())
                    val=(row[idx] if idx is not None and idx < len(row) else '').strip()
                    item[key]=val
                    has_data=has_data or bool(val)
                if has_data:
                    items.append(item)
            return items
        except Exception:
            pass
    return []

def load_master_tracking_actions():
    items=[]
    seen=set()
    for path in MASTER_TRACKING_ACTIONS_CSV_CANDIDATES:
        if path in seen:
            continue
        seen.add(path)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if not any((row.get(k) or '').strip() for k in MASTER_TRACKING_ACTION_FIELDS):
                    continue
                items.append({k:(row.get(k,'') or '').strip() for k in MASTER_TRACKING_ACTION_FIELDS})
        break
    return items

def load_playbook_gap_library():
    lib={k:dict(v) for k,v in PLAYBOOK_GAP_LIBRARY_DEFAULT.items()}
    for path in PLAYBOOK_GAP_LIBRARY_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            with open(path,encoding='utf-8-sig') as f:
                raw=json.load(f)
            if not isinstance(raw,dict):
                continue
            for key,val in raw.items():
                if not isinstance(val,dict):
                    continue
                base=lib.get(key,{}).copy()
                for field in ('measure','area','tab','detail'):
                    if field in val and val[field] is not None:
                        base[field]=str(val[field])
                if base:
                    lib[key]=base
        except Exception:
            pass
        break
    return lib

PLAYBOOK_GAP_LIBRARY=load_playbook_gap_library()

def gap_msg(key):
    g=PLAYBOOK_GAP_LIBRARY.get(key) or PLAYBOOK_GAP_LIBRARY_DEFAULT.get(key) or {}
    return (g.get('measure',key), g.get('area','Shift Overview'), g.get('tab','overview'), g.get('detail',''))
_COLRE=re.compile(r'^(?:Dtl|Data)_(.*?)(?:_\d+)?$')
def _normcol(h):   # SSRS exports name columns "Dtl_<Name>_<pos>" (AllLoadsDumps' latest export uses "Data_" instead
                    # of "Dtl_" for the same prefix+position scheme); strip that (and BOM) → plain <Name>. Leaves
                    # plain headers, and the separate lowercase 'd<Name>' exports (aliased at load, not here), unchanged.
    h=h.strip().lstrip('﻿'); m=_COLRE.match(h); return m.group(1) if m else h
# ---- resilient file resolution: re-exports rename/recase files (e.g. ShovelCoverageFactors.csv ->
# Shovelcoveragefactor.csv, ShovelLoadingSide.csv -> ShovelLoadingSideTagLog.csv). Resolve requested
# names to whatever's actually in the folder, case-insensitively and by fuzzy stem match.
try: _DATA_FILES=[f for f in os.listdir(DATADIR) if f.lower().endswith('.csv')]
except Exception: _DATA_FILES=[]
def _canon(s): return re.sub(r'[^a-z0-9]','',s.lower())
_RESOLVED={}
def _resolve_file(name):
    p=f'{DATADIR}/{name}'
    if os.path.exists(p): return p
    want=_canon(name.rsplit('.',1)[0])
    for f in _DATA_FILES:                       # case-insensitive exact
        if f.lower()==name.lower(): _RESOLVED[name]=f; return f'{DATADIR}/{f}'
    for f in _DATA_FILES:                       # canonical stem equal
        if _canon(f.rsplit('.',1)[0])==want: _RESOLVED[name]=f; return f'{DATADIR}/{f}'
    cands=[f for f in _DATA_FILES if (want and (want in _canon(f.rsplit('.',1)[0]) or _canon(f.rsplit('.',1)[0]) in want))]
    if cands:
        cands.sort(key=lambda f:abs(len(_canon(f.rsplit('.',1)[0]))-len(want)))
        _RESOLVED[name]=cands[0]; return f'{DATADIR}/{cands[0]}'
    return p                                     # give up → original (will raise if truly missing)
def load_csv(name):
    with open(_resolve_file(name),encoding='utf-8-sig',newline='') as f:
        rdr=csv.reader(f)
        try: hdr=[_normcol(c) for c in next(rdr)]
        except StopIteration: return []
        return [dict(zip(hdr,row)) for row in rdr if row]
loads_all=load_csv('AllLoadsDumps.csv')
BAD_EQ={'S8810'}   # non-existent equipment IDs to drop at source
loads_all=[r for r in loads_all if (r.get('Excav') or '') not in BAD_EQ and (r.get('Truck') or '') not in BAD_EQ]
# Empty-haul leg origin = the same truck's PREVIOUS row's DumpLocation (row order = chronological; the empty
# haul time/distance columns already measure prev-dump → this-load, so only the origin label is derived here).
PREV_DUMP={}; _lastdump={}
for _r in loads_all:
    _tk=_r.get('Truck')
    if _tk in _lastdump: PREV_DUMP[id(_r)]=_lastdump[_tk]
    _lastdump[_tk]=_r.get('DumpLocation') or '?'
status_all=load_csv('Statusevents.csv')
status_all=[e for e in status_all if (e.get('Eqmt') or '') not in BAD_EQ]
for _r in status_all:   # tolerate either schema (StartTime/TimeStamp, EqmtType/Eqmttype, TimeCat/Timecat)
    if 'EqmtType' not in _r: _r['EqmtType']=_r.get('Eqmttype','')
    if 'StartTime' not in _r: _r['StartTime']=_r.get('TimeStamp','')
    if 'TimeCat' not in _r: _r['TimeCat']=_r.get('Timecat','')
tas_all=load_csv('TruckatShovel.csv')
tad_all=load_csv('TruckAtDump.csv')
for _r in tad_all:   # tolerate the 'd'-prefixed export schema (dShiftId, dDumpLocation, dLogTime, dTrucksAtDump, ...) → add unprefixed aliases
    for _k in list(_r.keys()):
        if len(_k)>=2 and _k[0]=='d' and _k[1].isupper(): _r.setdefault(_k[1:], _r[_k])
lube_all=load_csv('TruckAtLubeLand.csv')
fuel_assign_all=load_csv('SystemVsManualFuelAssignments.csv')
truck_assign_all=load_csv('SystemVsManualAssignments.csv')
# Real x/y positions for the Cycle Map, auto-derived from AllLoadsDumps GPS: the truck's field GPS at
# load (FieldGpsxtkl/ytkl) gives each shovel's position, at dump (FieldGpsxtkd/ytkd) each dump's — taken
# as the MEDIAN over all loads/dumps for that location (robust to GPS jitter; zeros/blanks are missing).
# UTM metres (easting/northing). An optional Data/LocationCoordinates.csv (Location,X,Y) overrides these.
def _cf(v):
    try: return float(str(v).replace(',','').strip())
    except: return None
def _median(a):
    s=sorted(a); n=len(s)
    return None if not n else (s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2.0)
_shx=defaultdict(list); _shy=defaultdict(list); _dux=defaultdict(list); _duy=defaultdict(list)
for _r in loads_all:
    _e=(_r.get('Excav') or '').strip(); _lx=_cf(_r.get('FieldGpsxtkl')); _ly=_cf(_r.get('FieldGpsytkl'))
    if _e and _lx and _ly: _shx[_e].append(_lx); _shy[_e].append(_ly)
    _dl=(_r.get('DumpLocation') or '').strip(); _dx=_cf(_r.get('FieldGpsxtkd')); _dy=_cf(_r.get('FieldGpsytkd'))
    if _dl and _dx and _dy: _dux[_dl].append(_dx); _duy[_dl].append(_dy)
loc_coords={}
for _k in _shx: loc_coords[_k]=[_median(_shx[_k]), _median(_shy[_k])]
for _k in _dux: loc_coords[_k]=[_median(_dux[_k]), _median(_duy[_k])]   # dump keys distinct from shovels
try:   # optional surveyed-coordinate override
    for _r in load_csv('LocationCoordinates.csv'):
        _nm=(_r.get('Location') or _r.get('Name') or _r.get('Loc') or _r.get('Node') or _r.get('Id') or '').strip()
        _x=_cf(_r.get('X') if (_r.get('X') not in (None,'')) else _r.get('Easting'))
        _y=_cf(_r.get('Y') if (_r.get('Y') not in (None,'')) else _r.get('Northing'))
        if _nm and _x is not None and _y is not None: loc_coords[_nm]=[_x,_y]
except Exception:
    pass
# Optional: truck GPS breadcrumb traces → approximate haul-road network for the Cycle Map. Data/TruckTraces.csv
# with columns X,Y (same UTM grid as the load/dump GPS; extra columns ignored). Points are binned to a grid and
# the busiest cells kept as a faint density underlay — the roads emerge from where trucks actually drive. The
# builder aggregates so the raw trace file can be arbitrarily large; only compact cell centres ship. File absent
# → no road layer, no change.
ROAD_CELL=20.0   # metres per grid cell (finer = sharper road profile)
def _firstval(r,keys):
    for _k in keys:
        _v=r.get(_k)
        if _v not in (None,''): return _v
    return None
_XKEYS=('dFieldXloc','FieldXloc','X','Easting','GpsX','Xloc','FieldX')
_YKEYS=('dFieldYloc','FieldYloc','Y','Northing','GpsY','Yloc','FieldY')
import math as _mth
road_cells=[]
try:
    _rows=[]
    for _fn in ('TruckDotsWithLoad.csv','TruckTraces.csv','TruckDots.csv','TruckGps.csv'):
        try: _rows=load_csv(_fn)
        except Exception: _rows=[]
        if _rows: break
    _acc={}
    def _bump(x,y):
        _gk=(int(x//ROAD_CELL),int(y//ROAD_CELL)); _acc[_gk]=_acc.get(_gk,0)+1
    _tr=defaultdict(list)
    for _r in _rows:
        _x=_cf(_firstval(_r,_XKEYS)); _y=_cf(_firstval(_r,_YKEYS))
        if _x is None or _y is None or (_x==0 and _y==0): continue
        _bump(_x,_y)   # raw ping density
        _tt=_dtp(_r.get('dGPSTime') or _r.get('GPSTime') or _r.get('dLogtime') or '')
        _tk=(_r.get('dTruck') or _r.get('Truck') or ''); _sd=(_r.get('dShiftID') or _r.get('ShiftID') or '')
        _tr[(_sd,_tk)].append((_tt,_x,_y))
    # interpolate the driven path between consecutive close-in-time pings so corridors build up
    for _pts in _tr.values():
        _p=[q for q in _pts if q[0] is not None]; _p.sort(key=lambda q:q[0])
        _prev=None
        for _t,_x,_y in _p:
            if _prev is not None:
                _pt,_px,_py=_prev; _dt=(_t-_pt).total_seconds(); _dd=_mth.hypot(_x-_px,_y-_py)
                if 0<_dd<=1600 and 0<_dt<=1500:
                    _n=int(_dd//ROAD_CELL)
                    for _i in range(1,_n): _bump(_px+(_x-_px)*_i/_n, _py+(_y-_py)*_i/_n)
            _prev=(_t,_x,_y)
    if _acc:
        # keep every cell visited ≥2× (drops one-off GPS noise) — the full road profile, not just the busiest lanes
        _items=[it for it in sorted(_acc.items(),key=lambda kv:-kv[1]) if it[1]>=2][:40000]
        _wmax=max((c for _,c in _items),default=1)
        road_cells=[[round((gx+0.5)*ROAD_CELL,1),round((gy+0.5)*ROAD_CELL,1),round((c/_wmax)**0.5,3)] for (gx,gy),c in _items]
except Exception:
    road_cells=[]
# Optional: georeferenced GIS basemap under the Cycle Map. Drop an image named basemap.png/.jpg in Data/ plus its
# georeference — either a world file (basemap.pgw/.jgw/.wld, as exported by QGIS/ArcGIS) or a plain
# basemap_extent.txt containing "minX,minY,maxX,maxY" in the same UTM grid. The image is embedded (base64) and
# placed by its world extent, so it lines up with the GPS-positioned circles. Absent → no basemap.
import struct as _struct, base64 as _b64
def _png_dims(b):
    try:
        if b[:8]==b'\x89PNG\r\n\x1a\n' and b[12:16]==b'IHDR':
            w,h=_struct.unpack('>II',b[16:24]); return (w,h)
    except Exception: pass
    return None
def _jpg_dims(b):
    try:
        i=2; n=len(b)
        while i<n-1:
            if b[i]!=0xFF: i+=1; continue
            m=b[i+1]; i+=2
            if 0xD0<=m<=0xD9: continue
            if 0xC0<=m<=0xCF and m not in (0xC4,0xC8,0xCC):
                h,w=_struct.unpack('>HH',b[i+3:i+7]); return (w,h)
            if i+2<=n: i+=_struct.unpack('>H',b[i:i+2])[0]
            else: break
    except Exception: pass
    return None
def _basemap_extent(base,dims):
    for _en in (base+'_extent.txt', base+'.extent', base+'_extent.csv', base+'_bounds.txt'):
        _p=f'{DATADIR}/{_en}'
        if os.path.exists(_p):
            _nums=[_cf(x) for x in re.split(r'[,\s]+', open(_p).read().strip()) if _cf(x) is not None]
            if len(_nums)>=4: return _nums[:4]
    if dims:
        _W,_H=dims
        for _wf in (base+'.pgw', base+'.jgw', base+'.wld', base+'.pngw'):
            _p=f'{DATADIR}/{_wf}'
            if os.path.exists(_p):
                _v=[_cf(x) for x in open(_p).read().split() if _cf(x) is not None]
                if len(_v)>=6:
                    _A,_D,_B,_E,_C,_F=_v[:6]
                    _x0=_C-_A/2.0; _x1=_C+(_W-0.5)*_A
                    _yt=_F-_E/2.0; _yb=_F+(_H-0.5)*_E
                    return [min(_x0,_x1),min(_yt,_yb),max(_x0,_x1),max(_yt,_yb)]
    return None
base_map=None
try:
    for _bn in ('basemap','Basemap','BaseMap','gis','GIS'):
        for _ext,_mime in (('png','image/png'),('jpg','image/jpeg'),('jpeg','image/jpeg')):
            _p=f'{DATADIR}/{_bn}.{_ext}'
            if os.path.exists(_p):
                _b=open(_p,'rb').read()
                _dims=_png_dims(_b) if _ext=='png' else _jpg_dims(_b)
                _extent=_basemap_extent(_bn,_dims)
                if _extent:
                    base_map={'img':'data:%s;base64,%s'%(_mime,_b64.b64encode(_b).decode('ascii')),'ext':_extent}
                    break
        if base_map: break
except Exception:
    base_map=None
# Shovel loading-side snapshots (~5-min intervals): LEFT/RIGHT/DOUBLE SIDE LOADING (+ BELONGS TO TAILING).
# Per (shift, shovel): count single-sided (LEFT|RIGHT) vs double-sided (DOUBLE); ignore TAILING/blank.
LOADSIDE=defaultdict(lambda:defaultdict(lambda:[0,0]))   # sid -> excav -> [single, double]
for _r in load_csv('ShovelLoadingSide.csv'):
    _ls=(_r.get('Loadside') or '').upper()
    if 'DOUBLE SIDE' in _ls: LOADSIDE[_r.get('ShiftID')][_r.get('Excav')][1]+=1
    elif 'LEFT SIDE' in _ls or 'RIGHT SIDE' in _ls: LOADSIDE[_r.get('ShiftID')][_r.get('Excav')][0]+=1
def _lube_ok(r):   # ignore FUEL&LUBE / FUEL BREAK events under 20 s (counted separately)
    return not (r['Reason'] in ('FUEL&LUBE','FUEL BREAK') and num(r['Duration'])<20)
def lube_trend(pits):   # cross-shift (all shifts) lube minutes by reason + expected, filtered by pit
    sh=defaultdict(lambda:defaultdict(float)); shexp=defaultdict(float)
    for r in lube_all:
        if r['Pit'] not in pits or not _lube_ok(r): continue
        s=_sid(r); sh[s][r['Reason']]+=num(r['Duration'])/60; shexp[s]+=num(r['ExpectedDuration'])/60
    return [{'id':s,'fuel':round(sh[s]['FUEL&LUBE']),'wait':round(sh[s]['WAIT FOR FUEL BAY']),
             'brk':round(sh[s]['FUEL BREAK']),'exp':round(shexp[s])} for s in sorted(sh)]
tb_all=[]   # (shiftId, pit, required, actual, timestamp) — ShiftId may be absent (SSRS) → matched by timestamp window
for r in load_csv('TruckBalance.csv'):
    try: req,act=float(r['TotalRequired']),float(r['LPActual'])
    except (KeyError,TypeError,ValueError): continue
    tb_all.append((r.get('Shiftid') or r.get('ShiftId') or r.get('ShiftID') or '',
                   r.get('Pit') or '', req, act, _dtp_tb(r.get('Timestamp'))))

def stype(ex): return 'BE495' if ex.startswith('S0') else ('HIT8000' if ex.startswith('S8') else None)
SHVTYPE={'BE495':'Cable','HIT8000':'Hydraulic'}          # fleet -> Fixed_Times ShovelType
def styp_of(ex): return SHVTYPE.get(stype(ex),'Average')  # unknown/None -> Average load target
def LMIN(f,styp='Average'):                               # type-specific Load-time (minutes); avg fallback
    if not f: return 0.0
    return (f.get('LoadT') or {}).get(styp, f.get('Load',0.0))
def _med(vals):
    v=[x for x in vals if x>0]; return _st.median(v) if v else 0.0
def _statgrp(s):
    if s=='Ready': return 'Ready'
    if s=='Delay': return 'Delay'
    if s=='Down': return 'Down'
    if s=='Standby': return 'Standby'
    if s=='Parked': return 'Parked'
    return 'Other'
def _sid(r): return r.get('ShiftId') or r.get('ShiftID')

# ---------- shifts available in the production data (AllLoadsDumps) ----------
_seen={}
for r in loads_all:
    sid=_sid(r)
    if sid and sid not in _seen:
        _seen[sid]={'id':sid,'name':r['FullShiftName'],'start':r['ShiftStartTimestamp'],'crew':(r.get('Crew') or '').strip()}
SHIFTS=sorted(_seen.values(), key=lambda s:s['id'], reverse=True)   # ShiftId encodes YYMMDD+seq -> chronological; most recent first

# ============================ per-shift build ============================
def build_shift(sm):
    sid=sm['id']; sname=sm['name']
    # Derive shift date/time from ShiftId (YYMMDD+seq); ShiftStartTimestamp column is mangled by Excel export.
    yy=int(sid[0:2]); mm=int(sid[2:4]); dd=int(sid[4:6]); seq=int(sid[6:9])
    base=18 if 'Night' in sname else 6
    start=_dtm(2000+yy,mm,dd,base,0,0)
    monthnum=str(mm); mabbr=MONTHS[monthnum]; date=str(dd); snum=str(seq)
    curve={p:CURVE[p].get(mabbr,{}) for p in PITS}
    fx={p:{m:_fxmerge(FX[p][mabbr][m]) for m in FX[p].get(mabbr,{})} for p in PITS}
    pitbud={p:PITBUD[p].get((monthnum,date,snum)) for p in PITS}
    def bud(p,c):
        r=pitbud[p]; return num(r[c]) if r else 0.0
    shTPNOH={p:{('BE495','Ore'):bud(p,'TPNOHO495'),('BE495','Waste'):bud(p,'TPNOHW495'),
                ('HIT8000','Ore'):bud(p,'TPNOH8000'),('HIT8000','Waste'):bud(p,'TPNOHW8000')} for p in PITS}
    loads=[r for r in loads_all if _sid(r)==sid]
    # Truck-productivity and truck-waterfall graphs should include all haul trucks in the shift,
    # not just the T1 fleet. Keep the broader Cat 797 availability metrics elsewhere,
    # but the two truck graphs should aggregate every truck record in the shift.
    t1=[r for r in loads if str(r.get('Truck') or '').startswith('T')]
    dts=[d for d in (_dtp(r['DumpingTimestamp']) for r in loads) if d]
    elapsed=min(12.0,max((max(dts)-start).total_seconds()/3600.0,0.1)) if dts else 12.0
    def mfs(dt): return (dt-start).total_seconds()/60.0 if dt else None
    lane_avg=defaultdict(lambda:[0.0,0])
    for r in t1:
        d=num(r['FullHaulDistance'])
        if d>0: k=(r['LoadLocation'],r['DumpLocation']); lane_avg[k][0]+=d; lane_avg[k][1]+=1
    def rdist(r):
        d=num(r['FullHaulDistance'])
        if d>0: return d
        k=(r['LoadLocation'],r['DumpLocation']); return lane_avg[k][0]/lane_avg[k][1] if lane_avg[k][1] else 5000.0
    def hd_of(r): return min(15.5,max(0.5,round(rdist(r)/1000,1)))
    def cv(pit,mat,hd): return curve[pit].get((mat,hd)) or {'TPNOH':0.0,'Cycle':0.0,'Travel':0.0}
    fd=fdur=ed=edur=0.0
    for r in t1:
        if num(r['FullHaulDuration'])>0: fd+=num(r['FullHaulDistance']); fdur+=num(r['FullHaulDuration'])
        if num(r['EmptyHaulDuration'])>0: ed+=num(r['EmptyHaullDistance']); edur+=num(r['EmptyHaulDuration'])
    v_full=fd/fdur if fdur else 0.0; v_empty=ed/edur if edur else 0.0
    FFULL=v_empty/(v_empty+v_full) if (v_empty+v_full) else 0.5
    # budget cycle seconds per load = Σ fixed times + travel (FFULL cancels in the total).
    # Payload-normalized rate = PAYLOAD_TARGET / budget-cycle-seconds, so budget-cycle tonnes = 361/load
    # and the waterfall closes with the Payload row measured against 361 t (no residual).
    def budsecs(pit,mat,c,styp='Average'):
        f=fx[pit].get(mat) or {'Queue':0,'Spot':0,'Load':0,'DumpIdle':0,'Dumping':0}
        return (f['Spot']+LMIN(f,styp)+f['Queue']+f['DumpIdle']+f['Dumping']+c['Travel'])*60.0
    def rate_star(pit,mat,c,styp='Average'):
        Tb=budsecs(pit,mat,c,styp); return (PAYLOAD_TARGET/Tb if Tb>0 else 0.0)

    # ---- HAULAGE ----
    haul=defaultdict(lambda:{'pot':0.0,'act':0.0,'mat':defaultdict(lambda:[0.0,0.0]),'lane':defaultdict(lambda:[0.0,0.0,0])})
    for r in t1:
        pit=r['LoadPit']; mat=r['MaterialGroupName']; c=cv(pit,mat,hd_of(r))
        cyc_h=sum(num(r[x]) for x in ['EmptyHaulDuration','SpotTime','LoadingTime','QueueTimeShvl',
                  'FullHaulDuration','QueueTimeDmp','DumpSpotTime','DumpingTime'])/3600.0
        pot=rate_star(pit,mat,c,styp_of(r['Excav']))*cyc_h*3600.0; act=num(r['Tonnage'])   # payload-normalized Potential (Load budget per loading-shovel type)
        H=haul[pit]; H['pot']+=pot; H['act']+=act; H['mat'][mat][0]+=pot; H['mat'][mat][1]+=act
        L=H['lane'][(mat,r['LoadLocation'],r['DumpLocation'])]; L[0]+=pot; L[1]+=act; L[2]+=1
    # ---- LOADING ----
    shovel_type_of={}
    load=defaultdict(lambda:{'pot':0.0,'act':0.0,'mat':defaultdict(lambda:[0.0,0.0]),'shovel':defaultdict(lambda:[0.0,0.0]),
        'grp':defaultdict(lambda:[0.0,0.0,0.0]),'unit':defaultdict(lambda:[0.0,0.0,0.0,0])})
    for r in loads:
        s=stype(r['Excav'])
        if not s: continue
        pit=r['LoadPit']; mat=r['MaterialGroupName']; shovel_type_of[r['Excav']]=s
        noh=max(NOH_FLOOR_S,num(r['SpotTime'])+num(r['LoadingTime'])+num(r['HangTime']))/3600.0
        tp=shTPNOH[pit].get((s,mat),0.0); pot=noh*tp; act=num(r['Tonnage'])
        Lo=load[pit]; Lo['pot']+=pot; Lo['act']+=act; Lo['mat'][mat][0]+=pot; Lo['mat'][mat][1]+=act
        Lo['shovel'][s][0]+=pot; Lo['shovel'][s][1]+=act
        g=Lo['grp'][(s,mat)]; g[0]+=pot; g[1]+=act; g[2]+=noh
        u=Lo['unit'][r['Excav']]; u[0]+=pot; u[1]+=act; u[2]+=noh; u[3]+=1
    # ---- TRUCK BALANCE (by ShiftId if present, else by shift timestamp window; graph uses most-recent hour) ----
    tb=defaultdict(list); _wend=start+_td(hours=12)
    for sid_,pit,req,act,dt in tb_all:
        if dt is None: continue
        if sid_:
            if sid_!=sid: continue
        elif not (start<=dt<_wend): continue
        tb[pit].append((dt,req,act))
    # ---- TRUCKS WATERFALL ----
    def new_wf(): return {'pot':0.0,'act':0.0,'n':0,'rows':defaultdict(float),'lm':defaultdict(lambda:[0.0,0.0])}
    truckswf=defaultdict(lambda:{'top':new_wf(),'mat':defaultdict(new_wf),'lane':defaultdict(new_wf)})
    lane_mat=defaultdict(lambda:defaultdict(int)); lane_exc=defaultdict(lambda:defaultdict(int)); lane_dist=defaultdict(lambda:[0.0,0.0,0,0])
    lane_fh=defaultdict(lambda:[[0.0,0.0,0] for _ in range(12)])   # per lane, per hour: [Σ actual full-haul sec, Σ target sec, n]
    ratio_n=defaultdict(float); ratio_d=defaultdict(float)
    for r in t1:
        if (r['DumpLocation'] or '').upper().startswith('IN'): continue   # hide internal roads/berms/pads (IN_*) from waterfall + haulage drill-down
        if not stype(r['Excav']): continue   # truck waterfall hierarchy = only BE495/HIT8000 shovels (+ Cat 797 trucks)
        pit=r['LoadPit']; mat=r['MaterialGroupName']; c=cv(pit,mat,hd_of(r))
        f=fx[pit].get(mat) or {'Queue':0,'Spot':0,'Load':0,'DumpIdle':0,'Dumping':0}
        lkey=(r['LoadLocation'],r['DumpLocation']); lane_mat[(pit,)+lkey][mat]+=1; lane_exc[(pit,)+lkey][r['Excav']]+=1
        ld_=lane_dist[(pit,)+lkey]
        if num(r['FullHaulDistance'])>0: ld_[0]+=num(r['FullHaulDistance']); ld_[2]+=1
        if num(r['EmptyHaullDistance'])>0: ld_[1]+=num(r['EmptyHaullDistance']); ld_[3]+=1
        a={'Spot':num(r['SpotTime']),'Load':num(r['LoadingTime']),'Queue':num(r['QueueTimeShvl']),
           'DumpIdle':num(r['QueueTimeDmp']),'Dumping':num(r['DumpingTime']),
           'Full':num(r['FullHaulDuration']),'Empty':num(r['EmptyHaulDuration']),'DumpSpot':num(r['DumpSpotTime'])}
        b={'Spot':f['Spot']*60,'Load':LMIN(f,styp_of(r['Excav']))*60,'Queue':f['Queue']*60,'DumpIdle':f['DumpIdle']*60,
           'Dumping':f['Dumping']*60,'Full':c['Travel']*60*FFULL,'Empty':c['Travel']*60*(1-FFULL),'DumpSpot':0.0}
        _d=_dtp(r['DumpingTimestamp']); _mn=mfs(_d) if _d else None   # bucket full-haul time by hour for the per-path line chart
        if _mn is not None and 0<=_mn<720:
            _hb=lane_fh[(pit,)+lkey][int(_mn//60)]; _hb[0]+=a['Full']; _hb[1]+=b['Full']; _hb[2]+=1
        Tb=sum(b.values()); rate=(PAYLOAD_TARGET/Tb) if Tb>0 else 0.0   # payload-normalized rate (t/s)
        pot=rate*sum(a.values()); act=num(r['Tonnage'])
        paybud=rate*Tb   # = 361 t when Tb>0 → Payload row is actual − 361, bridge closes with no residual
        for tgt in (truckswf[pit]['top'],truckswf[pit]['mat'][mat],truckswf[pit]['lane'][lkey]):
            tgt['pot']+=pot; tgt['act']+=act; tgt['n']+=1
            tgt['rows']['Payload']+=act-paybud
            tgt['lm']['Payload'][0]+=act; tgt['lm']['Payload'][1]+=paybud
            for comp in ['Load','Queue','Spot','DumpIdle','Dumping']: tgt['rows'][comp]+=rate*(b[comp]-a[comp])
            tgt['rows']['FullHaul']+=rate*(b['Full']-a['Full'])
            tgt['rows']['EmptyHaul']+=rate*((b['Empty']-a['Empty'])+(b['DumpSpot']-a['DumpSpot']))
            for comp,ak in LM_KEY.items(): tgt['lm'][comp][0]+=a[ak]; tgt['lm'][comp][1]+=b[ak]
        if num(r['FullHaulDistance'])>0: ratio_n[pit]+=num(r['EmptyHaullDistance']); ratio_d[pit]+=num(r['FullHaulDistance'])
    # ---- status events (this shift; renamed columns) ----
    sev=[e for e in status_all if _sid(e)==sid]
    # The live status export omits Duration while a state is active. Infer that
    # interval from the next state transition for the same unit.
    status_cutoff=min(start+_td(hours=12),max(dts+[_dtp(e.get('StartTime')) for e in sev if _dtp(e.get('StartTime'))] or [start+_td(hours=12)]))
    status_seconds={}
    by_eq=defaultdict(list)
    for e in sev:
      dt=_dtp(e.get('StartTime'))
      if dt: by_eq[e.get('Eqmt') or ''].append((dt,e))
    for events in by_eq.values():
      events.sort(key=lambda x:x[0])
      for index,(dt,e) in enumerate(events):
        raw=(e.get('Duration') or '').strip()
        if raw:
          status_seconds[id(e)]=num(raw)
        else:
          end=events[index+1][0] if index+1<len(events) else status_cutoff
          status_seconds[id(e)]=max(0.0,(end-dt).total_seconds())
    def status_hours(e): return status_seconds.get(id(e),num(e.get('Duration')))/3600.0
    anoh=defaultdict(float)   # actual net operating hours (Cat 797, ASEStatus=Ready only)
    for e in sev:
      if e.get('EqmtType')=='Cat 797' and e.get('ASEStatus')=='Ready': anoh[e['Pit']]+=status_hours(e)
    bnoh={p:(bud(p,'NOH797')*elapsed/12) for p in PITS}
    # ---- equipment availability PA/UA/OE (Cable / Hydraulic / Cat 797), by ASEStatus ----
    AVGRP={'BE 495B':'Cable shovel','HIT 800':'Hydraulic shovel','Cat 797':'Cat 797'}
    # group -> (budget PA col, UA col, OE col or None, NOH weight col)
    AVBUD={'Cable shovel':('PA495','UA495',None,'NOH495'),'Hydraulic shovel':('PA8000','UA8000',None,'NOH8000'),'Cat 797':('PA797','UA797','OE797','NOH797')}
    def _budavg(pits,col,wcol):
        w=sum(bud(p,wcol) for p in pits)
        return (sum(bud(p,col)*bud(p,wcol) for p in pits)/w*100) if (w and col) else None
    def agg_avail(pits):
        buck=defaultdict(lambda:defaultdict(float))
        rb=defaultdict(lambda:defaultdict(lambda:[0.0,0,defaultdict(float)]))   # g -> (status,reason) -> [hours,count,{eqmt:hours}]
        readyh=defaultdict(float)   # per-unit Ready (net operating) hours, for per-unit TPNOH
        for e in sev:
            if e['Pit'] not in pits: continue
            g=AVGRP.get(e['EqmtType'])
            if not g: continue
            st=e['ASEStatus']   # Parked is its own state, excluded from PA/UA/OE (not folded into Standby)
            h=status_hours(e)
            buck[g][st]+=h
            if st=='Ready': readyh[e['Eqmt']]+=h
            if st in ('Down','Standby','Delay'):
                k=(st,(e['Reason'] or 'Unspecified').title()); rb[g][k][0]+=h; rb[g][k][1]+=1; rb[g][k][2][e['Eqmt']]+=h
        # per-fleet non-productive time (POE), tonnage, load count, truck queue/idle; per-unit tonnage for TPNOH
        nonprod=defaultdict(float); ton=defaultdict(float); nld=defaultdict(int); trkq=trki=0.0
        shov_ton=defaultdict(float); shov_pit=defaultdict(lambda:defaultdict(float)); shov_mat=defaultdict(lambda:defaultdict(float))
        trk_ton=defaultdict(float); trk_shov=defaultdict(lambda:defaultdict(float)); trk_pit=defaultdict(lambda:defaultdict(float))
        shov_dump=defaultdict(lambda:defaultdict(lambda:[0.0,0,0.0,0.0,0.0,[0.0]*12,[0]*12,[0.0]*12]))   # shovel->dump->[Σ full-dist(m), n, Σ ton, Σ cyc_h, Σ target tonnes, cyc-sec/hr, loads/hr, Σ ton/hr]
        for r in loads:
            if r['LoadPit'] not in pits: continue
            s=stype(r['Excav'])
            g2='Cable shovel' if s=='BE495' else ('Hydraulic shovel' if s=='HIT8000' else None)
            if not g2: continue
            T=num(r['Tonnage'])
            nonprod[g2]+=num(r['HangTime'])/3600.0; ton[g2]+=T; nld[g2]+=1
            shov_ton[r['Excav']]+=T; shov_pit[r['Excav']][r['LoadPit']]+=T; shov_mat[r['Excav']][r['MaterialGroupName']]+=T
        for r in t1:
            if r['LoadPit'] not in pits: continue
            q=num(r['QueueTimeShvl']); i=num(r['QueueTimeDmp']); T=num(r['Tonnage'])
            nonprod['Cat 797']+=(q+i)/3600.0; ton['Cat 797']+=T; nld['Cat 797']+=1; trkq+=q; trki+=i
            trk_ton[r['Truck']]+=T; trk_shov[r['Truck']][r['Excav']]+=T; trk_pit[r['Truck']][r['LoadPit']]+=T
            fd=num(r['FullHaulDistance']); dl=(r['DumpLocation'] or '?')
            if fd>0 and not dl.upper().startswith('IN_') and dl!=(r['LoadLocation'] or ''):   # skip internal roads and dump==dig
                cyc_h=sum(num(r[x]) for x in ('EmptyHaulDuration','SpotTime','LoadingTime','QueueTimeShvl','FullHaulDuration','QueueTimeDmp','DumpSpotTime','DumpingTime'))/3600.0
                tgtTP=cv(r['LoadPit'],r['MaterialGroupName'],hd_of(r)).get('TPNOH',0.0)   # haul-curve target TPNOH for this load's haul distance
                dd=shov_dump[r['Excav']][dl]; dd[0]+=fd; dd[1]+=1; dd[2]+=T; dd[3]+=cyc_h; dd[4]+=tgtTP*cyc_h
                _dt=_dtp(r['DumpingTimestamp']); _mn=mfs(_dt) if _dt else None   # bucket cycle time by dump hour
                if _mn is not None and 0<=_mn<720: _hh=int(_mn//60); dd[5][_hh]+=cyc_h*3600.0; dd[6][_hh]+=1; dd[7][_hh]+=T
        def _shovunit(u,t):   # per-shovel TPNOH (actual = ton ÷ Ready hrs) + budget (by type·dominant pit) + majority material; grouped by type
            typ='Cable' if stype(u)=='BE495' else ('Hydraulic' if stype(u)=='HIT8000' else '?')
            dompit=max(shov_pit[u],key=shov_pit[u].get) if shov_pit[u] else pits[0]
            dommat=max(shov_mat[u],key=shov_mat[u].get) if shov_mat[u] else 'Ore'
            bt=bud(dompit,'TPNOHCable' if typ=='Cable' else 'TPNOHHydro')
            return {'unit':u,'grp':typ,'mat':('O' if dommat=='Ore' else 'W'),'tpnoh':round(t/readyh[u]),'btpnoh':(round(bt) if bt else None)}
        shovUnits=[_shovunit(u,t) for u,t in shov_ton.items() if readyh.get(u,0)>0 and t>0]
        shovUnits.sort(key=lambda x:(x['tpnoh']-x['btpnoh']) if x['btpnoh'] else x['tpnoh'])   # Δ (act−budget) lowest → highest
        # Truck TPNOH aggregated to ONE row per loading shovel = the average TPNOH of the trucks it loaded.
        _svacc=defaultdict(lambda:[0.0,0,0.0,0])   # shovel -> [Σ truck tpnoh, n trucks, Σ truck budget, n budget]
        for u,t in trk_ton.items():
            if readyh.get(u,0)<=0 or t<=0: continue
            domshov=max(trk_shov[u],key=trk_shov[u].get) if trk_shov[u] else '?'
            dompit=max(trk_pit[u],key=trk_pit[u].get) if trk_pit[u] else pits[0]
            bt=bud(dompit,'TPNOH797'); a=_svacc[domshov]
            a[0]+=t/readyh[u]; a[1]+=1
            if bt: a[2]+=bt; a[3]+=1
        def _svtype(sv): return 'Cable' if stype(sv)=='BE495' else ('Hydraulic' if stype(sv)=='HIT8000' else '?')
        def _svdumps(sv):   # per dump this shovel served: loads, actual full-haul km, actual vs haul-curve target TPNOH, Δ tonnes
            lst=[]
            for dl,v in shov_dump.get(sv,{}).items():
                if v[1]<=0: continue
                km=v[0]/v[1]/1000; cyc=v[3]
                actTP=(v[2]/cyc) if cyc>0 else 0.0     # actual tonnes ÷ cycle hours
                tgtTP=(v[4]/cyc) if cyc>0 else 0.0     # cycle-weighted haul-curve target TPNOH
                hourly=[round(v[5][hh]/v[6][hh]) if v[6][hh] else None for hh in range(12)]   # avg truck cycle sec/hr
                hourlyTP=[round(v[7][hh]/(v[5][hh]/3600.0)) if v[5][hh]>0 else None for hh in range(12)]   # TPNOH/hr = tonnes ÷ cycle-hours
                lst.append({'dump':dl,'n':v[1],'km':round(km,1),'actTP':round(actTP),'tgtTP':round(tgtTP),'dTon':round(v[2]-v[4]),'hourly':hourly,'hourlyTP':hourlyTP})
            lst.sort(key=lambda x:x['dTon']); return lst   # Δ tonnes lowest → highest
        trkUnits=[{'unit':sv,'grp':_svtype(sv),'mat':'','n':n,'tpnoh':round(stp/n),'btpnoh':(round(sbt/nb) if nb else None),'dumps':_svdumps(sv)}
                  for sv,(stp,n,sbt,nb) in _svacc.items() if n>0]
        trkUnits.sort(key=lambda x:-x['tpnoh'])
        TPB={'Cable shovel':'TPNOHCable','Hydraulic shovel':'TPNOHHydro','Cat 797':'TPNOH797'}
        def _wavg(col,wcol):
            w=sum(bud(p,wcol) for p in pits); return (sum(bud(p,col)*bud(p,wcol) for p in pits)/w) if w else None
        def _unit(um):   # dominant unit(s) for a reason: top by hours, "+N" if more contributed
            u=sorted(um.items(),key=lambda x:-x[1])
            return (u[0][0]+((' +'+str(len(u)-1)) if len(u)>1 else '')) if u else ''
        def _ulist(um):   # per-unit hours for a reason, highest first (for the expandable breakdown)
            return [{'unit':k,'hours':round(v,1)} for k,v in sorted(um.items(),key=lambda x:-x[1]) if v>0.05]
        def _reasons(g,status,denom,n=5):   # denom = the metric's denominator → 'eff' = pts the reason costs that metric
            items=sorted([(rsn,hv[0],hv[1],hv[2]) for (st,rsn),hv in rb[g].items() if st==status],key=lambda x:-x[1])
            ef=lambda hh:(round(hh/denom*100,1) if denom>0 else 0)
            out=[{'reason':rr,'unit':_unit(um),'hours':round(hh,1),'n':cc,'eff':ef(hh),'units':_ulist(um)} for rr,hh,cc,um in items[:n]]
            oh=sum(x[1] for x in items[n:]); on=sum(x[2] for x in items[n:])
            if oh>0.05: out.append({'reason':'Other','unit':'','hours':round(oh,1),'n':on,'eff':ef(oh),'units':[]})
            return out
        out=[]
        for g in ['Cable shovel','Hydraulic shovel','Cat 797']:
            a=buck[g]; R,D,S,Dn=a['Ready'],a['Delay'],a['Standby'],a['Down']
            pac,uac,oec,wc=AVBUD[g]
            bPA=_budavg(pits,pac,wc); bUA=_budavg(pits,uac,wc); bOE=_budavg(pits,oec,wc) if oec else None
            btp=_wavg(TPB[g],wc); n=nld[g]
            poe=({'kind':'trk','queue':round(trkq/60/n,2),'idle':round(trki/60/n,2),'perLoad':round(nonprod[g]*60/n,2),'n':n}
                 if g=='Cat 797' else {'kind':'hang','perLoad':round(nonprod[g]*60/n,2) if n else 0,'n':n})
            hitters=sorted(rb[g].items(),key=lambda x:-x[1][0])[:15]
            hitters=[{'status':st,'reason':rsn,'hours':round(hv[0],1),'n':hv[1]} for (st,rsn),hv in hitters]
            out.append({'group':g,'ready':round(R,1),'delay':round(D,1),'standby':round(S,1),'down':round(Dn,1),'parked':round(a['Parked'],1),
                'goh':round(R+D,1),'deployed':round((R+D)/elapsed,2),'elapsedH':round(elapsed,1),   # effective units deployed = GOH ÷ elapsed shift-hours
                'PA':round((R+D+S)/(R+D+S+Dn)*100,1) if (R+D+S+Dn) else 0,
                'UA':round((R+D)/(R+D+S)*100,1) if (R+D+S) else 0,
                'OE':round(R/(R+D)*100,1) if (R+D) else 0,
                'POE':round(max(0.0,R-nonprod[g])/(R+D)*100,1) if (R+D) else 0,
                'tpnoh':round(ton[g]/R) if R>0 else 0,'btpnoh':(round(btp) if btp else None),
                'tpUnits':(trkUnits if g=='Cat 797' else [u for u in shovUnits if u['grp']==('Cable' if g=='Cable shovel' else 'Hydraulic')]),   # trucks: per-truck TPNOH by loading shovel; each shovel card lists only its own type
                'tpBy':('trkavg' if g=='Cat 797' else 'type'),
                'reasons':{'PA':_reasons(g,'Down',R+D+S+Dn),'UA':_reasons(g,'Standby',R+D+S),'OE':_reasons(g,'Delay',R+D)},'poe':poe,
                'bPA':round(bPA,1) if bPA is not None else None,'bUA':round(bUA,1) if bUA is not None else None,
                'bOE':round(bOE,1) if bOE is not None else None,'hitters':hitters})
        return out
    # ---- trucks-at-shovel series (this shift) ----
    _qraw=defaultdict(list)
    for r in tas_all:
        if (r.get('shiftId') or r.get('ShiftId'))!=sid: continue
        d=_dtp(r['LogTime'])
        if not d: continue
        _qraw[r['Excav']].append((round(max(0.0,min(720.0,mfs(d))),1),int(num(r['TrucksAtShovel']))))
    qseries={k:sorted(v) for k,v in _qraw.items()}
    # ---- trucks-at-dump series (this shift), by dump location, pit via dominant LoadPit ----
    dpc=defaultdict(lambda:defaultdict(int))
    for r in t1: dpc[r['DumpLocation']][r['LoadPit']]+=1
    dumppit={k:max(c,key=c.get) for k,c in dpc.items()}
    digset={(r.get('LoadLocation') or '') for r in t1}   # dig/load locations — a dump equal to one of these is excluded
    _draw=defaultdict(list)
    for r in tad_all:
        if (r.get('shiftId') or r.get('ShiftId'))!=sid: continue   # TruckAtDump: SSRS uses 'Moment'; newer exports 'LogTime'
        dl=r['DumpLocation']
        if dl in ('NULL','',None): continue
        d=_dtp(r.get('LogTime') or r.get('Moment'))
        if not d: continue
        _draw[dl].append((round(max(0.0,min(720.0,mfs(d))),1),int(num(r.get('TrucksAtDump')))))
    dseries={k:sorted(v) for k,v in _draw.items()}
    # crusher status segments (EqmtType 'Crusher', Eqmt = dump-location name e.g. CR1_MRM) — placed by real TimeStamp
    cseg=defaultdict(list); _ccum=defaultdict(float)
    for e in sev:
        if e.get('EqmtType')!='Crusher': continue
        dur=num(e['Duration'])/60.0
        if dur<=0: continue
        d=_dtp(e['StartTime']); stm=mfs(d) if d else None   # SSRS StartTime may be blank → cumulative Duration from shift start
        if stm is None: stm=_ccum[e['Eqmt']]
        _ccum[e['Eqmt']]=max(0.0,stm)+dur
        cseg[e['Eqmt']].append([round(max(0.0,stm),1),round(dur,1),_statgrp(e['ASEStatus']),(e['Reason'] or '').title()])
    for k in cseg: cseg[k].sort()
    # avg queue-at-dump time per load, by dump location & loading pit (QueueTimeDmp)
    dqacc=defaultdict(lambda:defaultdict(lambda:[0.0,0]))
    for r in t1:
        dl=r['DumpLocation']
        if dl in ('NULL','',None): continue
        e=dqacc[dl][r['LoadPit']]; e[0]+=num(r['QueueTimeDmp']); e[1]+=1
    def agg_dumptl(pits):
        # all real dump locations except internal roads ('IN…') and dumps equal to a dig/load location
        eq=[k for k in dseries if not (k or '').upper().startswith('IN') and k not in digset and dumppit.get(k) in pits and any(c>0 for _,c in dseries[k])]
        eq.sort(key=lambda k:-max((c for _,c in dseries[k]),default=0))
        mx=max([1]+[c for k in eq for (_,c) in dseries[k]])
        avgq={}
        for k in eq:
            s=sum(dqacc[k][p][0] for p in pits); n=sum(dqacc[k][p][1] for p in pits)
            avgq[k]=round(s/n/60,1) if n else None
        return {'equip':eq,'seg':{k:dseries[k] for k in eq},'qmax':mx,'base':base,'avgq':avgq,
                'status':{k:cseg.get(k,[]) for k in eq}}
    # ---- lube-land delays (this shift) ----
    lube=[r for r in lube_all if _sid(r)==sid]
    # fuel-assignment index for this shift: truck -> sorted [(ts, isManual)]
    _fa_bytruck=defaultdict(list)
    for r in (x for x in fuel_assign_all if x.get('ShiftID')==sid):
        tk=r.get('messagebody',''); ts=_dtp(r.get('TIMESTAMP',''))
        if tk and ts: _fa_bytruck[tk].append((ts,'Dispatcher' in (r.get('AssignType') or '')))
    for _k in _fa_bytruck: _fa_bytruck[_k].sort()
    def _assign_manual(tk,ts):
        # True=manual (dispatcher), False=system, None=no matching assignment
        cand=_fa_bytruck.get(tk)
        if not cand or ts is None: return None
        pre=[c for c in cand if c[0]<=ts]           # nearest preceding assignment
        if pre: return pre[-1][1]
        return min(cand,key=lambda c:abs((c[0]-ts).total_seconds()))[1]
    def agg_lube(pits):
        L=[r for r in lube if r['Pit'] in pits]
        short=sum(1 for r in L if not _lube_ok(r))
        Lv=[r for r in L if _lube_ok(r)]
        rr=defaultdict(lambda:[0,0.0,0.0,0])
        for r in Lv:
            d,e=num(r['Duration']),num(r['ExpectedDuration']); k=r['Reason']
            rr[k][0]+=1; rr[k][1]+=d; rr[k][2]+=e; rr[k][3]+=1 if d>e else 0
        reasons=[{'reason':k,'n':v[0],'actual':round(v[1]/60,1),'exp':round(v[2]/60,1),
                  'avg':round(v[1]/v[0]) if v[0] else 0,'over':round(v[3]/v[0]*100) if v[0] else 0}
                 for k,v in sorted(rr.items(),key=lambda x:-x[1][1])]
        FEDGES=[0,8,16,24,32,40,60,80,100]      # custom (non-uniform) fuel-level bin edges
        hist=[0]*(len(FEDGES)-1); histMan=[0]*(len(FEDGES)-1); faulty=0; zero=0
        fsm=defaultdict(lambda:[0,0.0,''])   # eqmt -> [faulty-read count, sample value, type]
        for r in Lv:
            f=num(r['FuelLevel'])
            if f>100:
                faulty+=1; e=fsm[r['Eqmt']]; e[0]+=1; e[1]=f; e[2]=r['Eqmttype']
            elif f<=0: zero+=1
            else:
                bi=len(FEDGES)-2
                for j in range(len(FEDGES)-1):
                    if f<FEDGES[j+1]: bi=j; break
                hist[bi]+=1
                if _assign_manual(r['Eqmt'],_dtp(r['TimeStamp'])):   # manual-assigned refuel
                    histMan[bi]+=1
        faultySensor=[{'eqmt':k,'type':v[2],'reads':v[0],'value':round(v[1])}
                      for k,v in sorted(fsm.items(),key=lambda x:(x[1][2],x[0]))]
        lb=sorted(Lv,key=lambda r:-(num(r['Duration'])-num(r['ExpectedDuration'])))[:12]
        leaderboard=[{'eqmt':r['Eqmt'],'type':r['Eqmttype'],'reason':r['Reason'],
                      'actual':round(num(r['Duration'])),'exp':round(num(r['ExpectedDuration'])),
                      'over':round(num(r['Duration'])-num(r['ExpectedDuration'])),
                      'time':(r['TimeStamp'].split(' ')[-1] if ' ' in r['TimeStamp'] else r['TimeStamp'])} for r in lb]
        ec=defaultdict(lambda:[0,0.0])
        for r in Lv: ec[r['Eqmttype']][0]+=1; ec[r['Eqmttype']][1]+=num(r['Duration'])
        byClass=[{'type':k,'n':v[0],'avg':round(v[1]/v[0]) if v[0] else 0,'total':round(v[1]/60,1)}
                 for k,v in sorted(ec.items(),key=lambda x:-x[1][1])]
        # hourly breakdown (this shift), minutes by reason
        RMAP={'FUEL&LUBE':'fuel','WAIT FOR FUEL BAY':'wait','FUEL BREAK':'brk'}
        hb={'fuel':[0.0]*12,'wait':[0.0]*12,'brk':[0.0]*12,'exp':[0.0]*12}
        occ=[0]*12; occWait=[0]*12
        fuelMan=[0.0]*12; occFuel=[0]*12; occFuelMan=[0]*12   # manual-assigned fuel events per hour
        for r in Lv:
            d=_dtp(r['TimeStamp']); mn=mfs(d) if d else None
            if mn is None or mn<0 or mn>=720: continue
            i=int(mn//60); k=RMAP.get(r['Reason'])
            if k: hb[k][i]+=num(r['Duration'])/60
            hb['exp'][i]+=num(r['ExpectedDuration'])/60
            occ[i]+=1
            if r['Reason']=='WAIT FOR FUEL BAY': occWait[i]+=1
            if r['Reason']=='FUEL&LUBE':
                occFuel[i]+=1
                if _assign_manual(r['Eqmt'],d):     # matched to a dispatcher (manual) fuel assignment
                    fuelMan[i]+=num(r['Duration'])/60; occFuelMan[i]+=1
        hourly={'hours':[f"{(base+i)%24:02d}:00" for i in range(12)],'fuel':[round(x,1) for x in hb['fuel']],
                'wait':[round(x,1) for x in hb['wait']],'brk':[round(x,1) for x in hb['brk']],
                'exp':[round(x,1) for x in hb['exp']],'occ':occ,'occWait':occWait,
                'fuelMan':[round(x,1) for x in fuelMan],'occFuel':occFuel,'occFuelMan':occFuelMan}
        # ---- fuel assignment automation (System vs Manual) ----
        fa=[r for r in fuel_assign_all if r.get('ShiftID')==sid and any(p in (r.get('ToLocation') or '') for p in pits)]
        fa_sys=[r for r in fa if r.get('AssignType')=='System Fuel Assignment']
        fa_man_raw=[r for r in fa if r.get('AssignType')=='Dispatcher Fuel Assignment']
        fa_man_latest={}
        for r in fa_man_raw:
            tk=r.get('messagebody',''); ts=_dtp(r.get('TIMESTAMP',''))
            if tk and ts and (tk not in fa_man_latest or ts>fa_man_latest[tk][1]):
                fa_man_latest[tk]=(r,ts)
        fa_sys_n=len(fa_sys); fa_man_n=len(fa_man_latest); fa_tot=fa_sys_n+fa_man_n
        fuelAssign={'system':fa_sys_n,'manual':fa_man_n,'total':fa_tot,
                    'sysPct':round(fa_sys_n/fa_tot*100) if fa_tot else 0,
                    'manPct':round(fa_man_n/fa_tot*100) if fa_tot else 0}
        # ---- truck assignment automation (System vs Manual/Dispatcher, all pits) ----
        ta=[r for r in truck_assign_all if r.get('ShiftID')==sid]
        ta_sys=[r for r in ta if r.get('AssignType')=='System Assign']
        ta_man_raw=[r for r in ta if r.get('AssignType') in ('Dispatcher Assign','Reassign')]
        ta_man_latest={}
        for r in ta_man_raw:
            tk=r.get('Truck',''); ts=_dtp(r.get('TIMESTAMP',''))
            if tk and ts and (tk not in ta_man_latest or ts>ta_man_latest[tk][1]):
                ta_man_latest[tk]=(r,ts)
        ta_sys_n=len(ta_sys); ta_man_n=len(ta_man_latest); ta_tot=ta_sys_n+ta_man_n
        truckAssign={'system':ta_sys_n,'manual':ta_man_n,'total':ta_tot,
                     'sysPct':round(ta_sys_n/ta_tot*100) if ta_tot else 0,
                     'manPct':round(ta_man_n/ta_tot*100) if ta_tot else 0}
        return {'reasons':reasons,'fuelHist':hist,'fuelHistMan':histMan,'fuelEdges':FEDGES,'faulty':faulty,'zero':zero,'shortCount':short,
                'leaderboard':leaderboard,'byClass':byClass,'n':len(Lv),'hourly':hourly,
                'faultySensor':faultySensor,'fuelAssign':fuelAssign,'truckAssign':truckAssign}

    # ---- aggregators (close over the shift locals) ----
    def agg_haul(pits):
        pot=sum(haul[p]['pot'] for p in pits); act=sum(haul[p]['act'] for p in pits)
        mat={m:{'pot':sum(haul[p]['mat'][m][0] for p in pits),'act':sum(haul[p]['mat'][m][1] for p in pits)} for m in ['Ore','Waste']}
        lanes=defaultdict(lambda:[0.0,0.0,0])
        for p in pits:
            for k,v in haul[p]['lane'].items(): lanes[k][0]+=v[0]; lanes[k][1]+=v[1]; lanes[k][2]+=v[2]
        lane=[{'mat':k[0],'load':k[1],'dump':k[2],'pot':v[0],'act':v[1],'loads':v[2]} for k,v in sorted(lanes.items(),key=lambda x:-x[1][0])]
        return {'potential':pot,'actual':act,'score':act/pot*100 if pot else 0,'byMaterial':mat,'byLane':lane}
    def agg_load(pits):
        pot=sum(load[p]['pot'] for p in pits); act=sum(load[p]['act'] for p in pits)
        mat={m:{'pot':sum(load[p]['mat'][m][0] for p in pits),'act':sum(load[p]['mat'][m][1] for p in pits)} for m in ['Ore','Waste']}
        sh={s:{'pot':sum(load[p]['shovel'][s][0] for p in pits),'act':sum(load[p]['shovel'][s][1] for p in pits)} for s in ['BE495','HIT8000']}
        return {'potential':pot,'actual':act,'score':act/pot*100 if pot else 0,'byMaterial':mat,'byShovel':sh}
    def agg_tb(pits):
        # time series from shift start: bucket each pit to 15-min ticks, forward-fill, sum across pits
        pser={}; ticks=set()
        for p in pits:
            dd={}
            for (dt,rq,ac) in tb.get(p,[]):
                mn=mfs(dt)
                if mn is None or mn<0 or mn>720: continue
                t=int(round(mn/15.0)*15); dd[t]=(rq,ac); ticks.add(t)
            pser[p]=dd
        last={p:None for p in pits}; series=[]
        for t in sorted(ticks):
            for p in pits:
                if t in pser[p]: last[p]=pser[p][t]
            got=[last[p] for p in pits if last[p]]
            if not got: continue
            series.append({'m':t,'req':round(sum(v[0] for v in got),1),'act':round(sum(v[1] for v in got),1)})
        per={}
        for p in pits:
            rows=tb.get(p)
            if not rows: continue
            mx=max(r[0] for r in rows); cut=mx-_td(hours=1)
            recent=[r for r in rows if r[0]>=cut]
            per[p]=(_st.mean([r[1] for r in recent]),_st.mean([r[2] for r in recent]),mx)
        if not per: return {'required':0,'actual':0,'pct':0,'label':'No data','series':series,'base':base}
        req=sum(v[0] for v in per.values()); act=sum(v[1] for v in per.values())
        pct=(req-act)/req*100 if req else 0
        label='Balanced' if abs(pct)<=5 else ('Under-Trucked' if pct>0 else 'Over-Trucked')
        asof=max(v[2] for v in per.values())
        return {'required':req,'actual':act,'pct':pct,'label':label,'series':series,'base':base,
                'asOf':asof.strftime('%H:%M'),'asOfFull':'%d/%d %s'%(asof.month,asof.day,asof.strftime('%H:%M'))}   # cross-platform (Windows has no %-m/%-d)
    def agg_fleetMatch(pits,tw,sw,tbd):
        # Fleet-match view: wait-time balance (shovel hang vs truck queue-at-shovel), bottleneck capacities,
        # per-15-min tonnage rate + est. tonnes lost to under-trucking.
        hs=qs=0.0; n=0; qb=0.0; ton15=defaultdict(float); wb=defaultdict(lambda:[0.0,0.0,0]); loadWaits=[]
        for r in t1:
            if r['LoadPit'] not in pits: continue
            hh=num(r['HangTime']); qq=num(r['QueueTimeShvl'])
            hs+=hh; qs+=qq; n+=1
            f=fx[r['LoadPit']].get(r['MaterialGroupName']) or {'Queue':0}; qb+=f['Queue']*60
            d=_dtp(r['DumpingTimestamp']); mn=mfs(d) if d else None
            if mn is not None and 0<=mn<720: ton15[int(mn//15)*15]+=num(r['Tonnage'])
            dl=_dtp(r['LoadingTimestamp']); mnl=mfs(dl) if dl else None      # queue/hang bucketed by when they occur (loading)
            if mnl is not None and 0<=mnl<720:
                e=wb[int(mnl//15)*15]; e[0]+=qq; e[1]+=hh; e[2]+=1
                loadWaits.append([round(mnl,1),round(hh),round(qq)])          # per-load [loadMin, hang s, queue s] for the bar view
        if n==0: return None
        hangBud=(sw['lm']['Hang']['target'] if sw and 'lm' in sw else None)
        POre=sum(bud(p,'POre') for p in pits); PWst=sum(bud(p,'PWst') for p in pits)
        rate=[{'m':t,'tph':round(ton15[t]*4)} for t in sorted(ton15)]
        waitSeries=[{'m':b,'q':round(wb[b][0]/wb[b][2]),'h':round(wb[b][1]/wb[b][2]),'n':wb[b][2]} for b in sorted(wb) if wb[b][2]>0]
        haulCap=(tw['schedPotential'] if tw and tw.get('availDecomp') else (tw['potential'] if tw else 0))
        loadCap=(sw['schedPotential'] if sw and sw.get('availDecomp') else (sw['potential'] if sw else 0))
        actual=(tw['actual'] if tw else 0)
        lost=0.0; underMin=overMin=0.0
        for p in (tbd.get('series',[]) if tbd else []):
            req,act,tn=p['req'],p['act'],ton15.get(p['m'],0.0)
            if act>0 and req>act+0.05: lost+=tn*(req-act)/act; underMin+=15
            elif act>req+0.05: overMin+=15
        return {'hangAvg':round(hs/n),'hangBud':(round(hangBud) if hangBud is not None else None),
                'queueAvg':round(qs/n),'queueBud':round(qb/n),'nLoads':n,
                'haulCap':round(haulCap),'loadCap':round(loadCap),'actual':round(actual),
                'rate':rate,'targetTph':round((POre+PWst)/12.0),'lostTonnes':round(lost),
                'underMin':round(underMin),'overMin':round(overMin),'waitSeries':waitSeries,'loadWaits':loadWaits}
    def _lm(srcs):
        n=sum(s['n'] for s in srcs) or 1; out={}
        for comp in WF_ROWS:
            sa=sum(s['lm'][comp][0] for s in srcs); sb=sum(s['lm'][comp][1] for s in srcs)
            out[comp]={'actual':sa/n,'target':sb/n,'unit':'t' if comp=='Payload' else 'time'}
        return out
    def avail_decomp(pits):
        # Cat 797 PA/UA/OE (same calc as KPI tab) → tonnage impact vs budget, per-pit then summed.
        # Sequential decomposition of NOH hours: base TH × ΔPA×UAb×OEb, ×PAa×ΔUA×OEb, ×PAa×UAa×ΔOE, ×TPNOH797.
        rd=defaultdict(lambda:defaultdict(float))   # status -> reason -> hours
        pa_t=ua_t=oe_t=0.0; AR=ADe=AS=ADn=0.0
        for p in pits:
            R=De=S=Dn=0.0
            for e in sev:
                if e['Pit']!=p or e.get('EqmtType')!='Cat 797': continue
                st=e['ASEStatus']   # Parked is its own state, excluded from PA/UA/OE (not folded into Standby)
                h=num(e['Duration'])/3600.0
                if st=='Ready': R+=h
                elif st=='Delay': De+=h
                elif st=='Standby': S+=h
                elif st=='Down': Dn+=h
                else: continue
                if st in ('Down','Standby','Delay'): rd[st][(e['Reason'] or 'Unspecified').title()]+=h
            TH=R+De+S+Dn
            if TH<=0: continue
            PAa=(R+De+S)/TH; UAa=((R+De)/(R+De+S)) if (R+De+S)>0 else 0.0; OEa=(R/(R+De)) if (R+De)>0 else 0.0
            PAb=bud(p,'PA797'); UAb=bud(p,'UA797'); OEb=bud(p,'OE797'); rate=bud(p,'TPNOH797')
            pa_t+=TH*(PAa-PAb)*UAb*OEb*rate
            ua_t+=TH*PAa*(UAa-UAb)*OEb*rate
            oe_t+=TH*PAa*UAa*(OEa-OEb)*rate
            AR+=R; ADe+=De; AS+=S; ADn+=Dn
        TH=AR+ADe+AS+ADn
        if TH<=0: return None
        PAa=(AR+ADe+AS)/TH*100; UAa=((AR+ADe)/(AR+ADe+AS)*100) if (AR+ADe+AS)>0 else 0.0; OEa=(AR/(AR+ADe)*100) if (AR+ADe)>0 else 0.0
        w=sum(bud(p,'NOH797') for p in pits) or 1
        PAb=sum(bud(p,'PA797')*bud(p,'NOH797') for p in pits)/w*100
        UAb=sum(bud(p,'UA797')*bud(p,'NOH797') for p in pits)/w*100
        OEb=sum(bud(p,'OE797')*bud(p,'NOH797') for p in pits)/w*100
        def reasons_of(st,limit=8):
            d=sorted(rd[st].items(),key=lambda x:-x[1])
            out=[[k,round(v,1)] for k,v in d[:limit]]
            other=sum(v for k,v in d[limit:])
            if other>0.5: out.append(['Other',round(other,1)])
            return out
        return {'pa':{'t':round(pa_t),'act':round(PAa,1),'bud':round(PAb,1),'reasons':reasons_of('Down')},
                'ua':{'t':round(ua_t),'act':round(UAa,1),'bud':round(UAb,1),'reasons':reasons_of('Standby')},
                'oe':{'t':round(oe_t),'act':round(OEa,1),'bud':round(OEb,1),'reasons':reasons_of('Delay')}}
    def scale_av(av,share):
        # allocate fleet PA/UA/OE tonnage effects to a material by its potential share (%, reasons unchanged)
        if not av: return None
        return {k:{'t':round(av[k]['t']*share),'act':av[k]['act'],'bud':av[k]['bud'],'reasons':av[k]['reasons']} for k in ('pa','ua','oe')}
    def _attach_av(d,av):
        d['availDecomp']=av
        d['schedPotential']=d['potential']-((av['pa']['t']+av['ua']['t']+av['oe']['t']) if av else 0.0)
        return d
    def agg_wf(pits):
        tops=[truckswf[p]['top'] for p in pits]
        pot=sum(s['pot'] for s in tops); act=sum(s['act'] for s in tops)
        rows={k:sum(s['rows'][k] for s in tops) for k in WF_ROWS}; residual=act-(pot+sum(rows.values()))
        av=avail_decomp(pits)
        mat={}
        for m in ['Ore','Waste']:
            ms=[truckswf[p]['mat'][m] for p in pits]
            mp=sum(s['pot'] for s in ms); ma=sum(s['act'] for s in ms)
            mr={k:sum(s['rows'][k] for s in ms) for k in WF_ROWS}
            mat[m]=_attach_av({'potential':mp,'actual':ma,'rows':mr,'residual':ma-(mp+sum(mr.values())),'lm':_lm(ms)},
                              scale_av(av,mp/pot if pot else 0))
        an=sum(anoh[p] for p in pits); bn=sum(bnoh[p] for p in pits)
        noh_t=sum((anoh[p]-bnoh[p])*bud(p,'TPNOH797') for p in pits)   # NOH tonnage impact (fleet)
        lanes=[]
        schedDelta=(av['pa']['t']+av['ua']['t']+av['oe']['t']) if av else 0.0
        for p in pits:
            for (ld,dp),s in truckswf[p]['lane'].items():
                mc=lane_mat[(p,ld,dp)]; ec=lane_exc[(p,ld,dp)]; dd=lane_dist[(p,ld,dp)]
                lr={k:s['rows'][k] for k in WF_ROWS}
                lnoh=noh_t*(s['pot']/pot) if pot else 0.0   # allocate fleet NOH to lane by potential share
                fh=lane_fh[(p,ld,dp)]
                lav=scale_av(av,s['pot']/pot if pot else 0)
                lsched=s['pot']-schedDelta*(s['pot']/pot) if pot else 0.0
                lanes.append({'pit':p,'load':ld,'dump':dp,'mat':max(mc,key=mc.get),'shovel':max(ec,key=ec.get),'loads':s['n'],
                    'distFull':dd[0]/dd[2]/1000 if dd[2] else 0.0,'distEmpty':dd[1]/dd[3]/1000 if dd[3] else 0.0,
                  'pot':s['pot'],'schedPotential':lsched,'act':s['act'],'score':s['act']/lsched*100 if lsched else 0,
                    'rows':lr,'residual':s['act']-(s['pot']+sum(lr.values())),'lm':_lm([s]),
                  'availDecomp':lav,
                    'fhHourly':{'hours':[f"{(base+i)%24:02d}" for i in range(12)],
                        'act':[round(fh[i][0]/fh[i][2]) if fh[i][2] else None for i in range(12)],
                        'tgt':[round(fh[i][1]/fh[i][2]) if fh[i][2] else None for i in range(12)]},
                    'nohTonnes':lnoh,'nohBudget':bn,'nohActual':an})
        lanes.sort(key=lambda x:(x['shovel'],-x['pot']))
        rn=sum(ratio_n[p] for p in pits); rd=sum(ratio_d[p] for p in pits)
        return {'potential':pot,'actual':act,'rows':rows,'residual':residual,'byMaterial':mat,'lm':_lm(tops),
                'availDecomp':av,'schedPotential':pot-schedDelta,
                'nohTonnes':noh_t,'lanes':lanes,'nohPct':an/bn*100 if bn else 0,'nohActual':an,'nohBudget':bn,'emptyFullRatio':rn/rd if rd else 0}
    def shovel_seg_decomp(pits):
        # Cycle decomposition (Spot / Load / Hang) built from budget cycle TIME, not TPNOH directly.
        # Per group (shovel fleet s, pit, material): representative payload P_rep = mean Tonnage;
        # budget cycle c_b = P_rep*3600/TPNOH; Hang_b (constant) = c_b − Spot_b − Load_b (from Fixed_Times).
        # Per load rate = Tonnage / c_b, so Potential + Spot+Load+Hang = Σ Tonnage = Actual (no residual).
        # ---- pass 1: representative payload & constant budget cycle per (s,pit,mat) ----
        psum=defaultdict(lambda:[0.0,0])
        for r in loads:
            if r['LoadPit'] not in pits: continue
            s=stype(r['Excav'])
            if not s: continue
            mat=r['MaterialGroupName']; T=num(r['Tonnage'])
            if shTPNOH[r['LoadPit']].get((s,mat),0.0)<=0 or T<=0: continue
            psum[(s,r['LoadPit'],mat)][0]+=T; psum[(s,r['LoadPit'],mat)][1]+=1
        cbud={}
        for (s,pit,mat),(tsum,cnt) in psum.items():
            tp=shTPNOH[pit].get((s,mat),0.0)
            f=fx[pit].get(mat) or {'Spot':0,'Load':0}; spot_b=f['Spot']*60; load_b=LMIN(f,SHVTYPE.get(s,'Average'))*60
            c_b=PAYLOAD_TARGET*3600.0/tp; hang_b=c_b-spot_b-load_b; clamp=hang_b<0   # budget cycle for the 361 t target
            if clamp: hang_b=0.0; c_b=spot_b+load_b
            cbud[(s,pit,mat)]=(c_b,spot_b,load_b,hang_b,clamp)
        # ---- pass 2: decompose (accumulate total, by material, by shovel unit) ----
        # payload-normalized: rate = PAYLOAD_TARGET/c_b (so cycle rows land on 361t), Payload = Tonnage − 361.
        def newacc(): return {'pot':0.0,'act':0.0,'rows':{'Payload':0.0,'Spot':0.0,'Load':0.0,'Hang':0.0},
            'lm':{'Payload':[0.0,0.0],'Spot':[0.0,0.0],'Load':[0.0,0.0],'Hang':[0.0,0.0]},'n':0,'hbneg':0}
        tot=newacc(); bymat=defaultdict(newacc); byunit=defaultdict(newacc); umeta={}; umat=defaultdict(lambda:defaultdict(float))
        for r in loads:
            if r['LoadPit'] not in pits: continue
            s=stype(r['Excav'])
            if not s: continue
            mat=r['MaterialGroupName']; T=num(r['Tonnage']); key=(s,r['LoadPit'],mat)
            if key not in cbud or T<=0: continue
            c_b,spot_b,load_b,hang_b,clamp=cbud[key]; rate=PAYLOAD_TARGET/c_b
            spot_a=num(r['SpotTime']); load_a=num(r['LoadingTime']); hang_a=num(r['HangTime']); c_a=spot_a+load_a+hang_a
            umeta[r['Excav']]=(s,r['LoadPit']); umat[r['Excav']][mat]+=T
            for acc in (tot,bymat[mat],byunit[r['Excav']]):
                acc['pot']+=rate*c_a; acc['act']+=T
                acc['rows']['Payload']+=T-PAYLOAD_TARGET
                acc['rows']['Spot']+=rate*(spot_b-spot_a); acc['rows']['Load']+=rate*(load_b-load_a); acc['rows']['Hang']+=rate*(hang_b-hang_a)
                acc['lm']['Payload'][0]+=T; acc['lm']['Payload'][1]+=PAYLOAD_TARGET
                acc['lm']['Spot'][0]+=spot_a; acc['lm']['Spot'][1]+=spot_b
                acc['lm']['Load'][0]+=load_a; acc['lm']['Load'][1]+=load_b
                acc['lm']['Hang'][0]+=hang_a; acc['lm']['Hang'][1]+=hang_b
                acc['n']+=1
                if clamp: acc['hbneg']+=1
        if tot['n']==0: return None
        def fin(a): return {'potential':a['pot'],'actual':a['act'],'rows':a['rows'],'n':a['n'],'hbneg':a['hbneg'],
            'score':a['act']/a['pot']*100 if a['pot'] else 0,
            'lm':{k:{'actual':a['lm'][k][0]/a['n'],'target':a['lm'][k][1]/a['n'],'unit':'t' if k=='Payload' else 'time'} for k in a['lm']}}
        return {'total':fin(tot),'byMat':{m:fin(bymat[m]) for m in bymat},
                'units':[dict(fin(byunit[u]),unit=u,type=umeta[u][0],pit=umeta[u][1],mat=(max(umat[u],key=umat[u].get) if umat[u] else 'Ore')) for u in byunit]}
    def avail_decomp_shovel(pits):
        # PA/UA/OE for cable (BE 495B) + hydraulic (HIT 800); OE budget = NOH/GOH (validated vs OE797).
        SPEC=[('BE 495B','PA495','UA495','NOH495','GOH495','TPNOHCable'),
              ('HIT 800','PA8000','UA8000','NOH8000','GOH8000','TPNOHHydro')]
        rd=defaultdict(lambda:defaultdict(float))
        pa_t=ua_t=oe_t=0.0; AR=ADe=AS=ADn=0.0; PAbw=UAbw=OEbw=wsum=0.0
        for etype,pac,uac,nohc,gohc,ratec in SPEC:
            for p in pits:
                R=De=S=Dn=0.0
                for e in sev:
                    if e['Pit']!=p or e.get('EqmtType')!=etype: continue
                    st=e['ASEStatus']   # Parked is its own state, excluded from PA/UA/OE (not folded into Standby)
                    h=num(e['Duration'])/3600.0
                    if st=='Ready': R+=h
                    elif st=='Delay': De+=h
                    elif st=='Standby': S+=h
                    elif st=='Down': Dn+=h
                    else: continue
                    if st in ('Down','Standby','Delay'): rd[st][(e['Reason'] or 'Unspecified').title()]+=h
                TH=R+De+S+Dn
                gohv=bud(p,gohc); OEb=(bud(p,nohc)/gohv) if gohv>0 else 0.0
                PAb=bud(p,pac); UAb=bud(p,uac); rate=bud(p,ratec)
                wnoh=bud(p,nohc)
                if wnoh>0: PAbw+=PAb*wnoh; UAbw+=UAb*wnoh; OEbw+=OEb*wnoh; wsum+=wnoh
                if TH<=0: continue
                PAa=(R+De+S)/TH; UAa=((R+De)/(R+De+S)) if (R+De+S)>0 else 0.0; OEa=(R/(R+De)) if (R+De)>0 else 0.0
                pa_t+=TH*(PAa-PAb)*UAb*OEb*rate
                ua_t+=TH*PAa*(UAa-UAb)*OEb*rate
                oe_t+=TH*PAa*UAa*(OEa-OEb)*rate
                AR+=R; ADe+=De; AS+=S; ADn+=Dn
        TH=AR+ADe+AS+ADn
        if TH<=0 or wsum<=0: return None
        PAa=(AR+ADe+AS)/TH*100; UAa=((AR+ADe)/(AR+ADe+AS)*100) if (AR+ADe+AS)>0 else 0.0; OEa=(AR/(AR+ADe)*100) if (AR+ADe)>0 else 0.0
        PAb=PAbw/wsum*100; UAb=UAbw/wsum*100; OEb=OEbw/wsum*100
        def reasons_of(st,limit=8):
            d=sorted(rd[st].items(),key=lambda x:-x[1]); out=[[k,round(v,1)] for k,v in d[:limit]]
            other=sum(v for k,v in d[limit:])
            if other>0.5: out.append(['Other',round(other,1)])
            return out
        return {'pa':{'t':round(pa_t),'act':round(PAa,1),'bud':round(PAb,1),'reasons':reasons_of('Down')},
                'ua':{'t':round(ua_t),'act':round(UAa,1),'bud':round(UAb,1),'reasons':reasons_of('Standby')},
                'oe':{'t':round(oe_t),'act':round(OEa,1),'bud':round(OEb,1),'reasons':reasons_of('Delay')}}
    def avail_decomp_unit(u,pit):
        # Per-unit PA/UA/OE decomposition (same sequential method as the fleet aggregate, one Eqmt).
        # Total/calendar hours TH = Ready+Delay+Standby+Down; GOH=Ready+Delay; NOH=Ready.
        s=stype(u)
        if s=='BE495': pac,uac,nohc,gohc,ratec='PA495','UA495','NOH495','GOH495','TPNOHCable'
        elif s=='HIT8000': pac,uac,nohc,gohc,ratec='PA8000','UA8000','NOH8000','GOH8000','TPNOHHydro'
        else: return None
        rd={'Down':defaultdict(float),'Standby':defaultdict(float),'Delay':defaultdict(float)}
        R=De=S=Dn=0.0
        for e in sev:
            if e['Eqmt']!=u: continue
            st=e['ASEStatus']   # Parked is its own state, excluded from PA/UA/OE (not folded into Standby)
            h=num(e['Duration'])/3600.0
            if st=='Ready': R+=h
            elif st=='Delay': De+=h
            elif st=='Standby': S+=h
            elif st=='Down': Dn+=h
            else: continue
            if st in ('Down','Standby','Delay'): rd[st][(e['Reason'] or 'Unspecified').title()]+=h
        TH=R+De+S+Dn
        if TH<=0: return None
        avl=R+De+S
        PAb=bud(pit,pac); UAb=bud(pit,uac); gohv=bud(pit,gohc); OEb=(bud(pit,nohc)/gohv) if gohv>0 else 0.0; rate=bud(pit,ratec)
        PAa=avl/TH; UAa=((R+De)/avl) if avl>0 else 0.0; OEa=(R/(R+De)) if (R+De)>0 else 0.0
        pa_t=TH*(PAa-PAb)*UAb*OEb*rate; ua_t=TH*PAa*(UAa-UAb)*OEb*rate; oe_t=TH*PAa*UAa*(OEa-OEb)*rate
        def rsn(st,limit=6):
            d=sorted(rd[st].items(),key=lambda x:-x[1]); out=[[k,round(v,1)] for k,v in d[:limit]]
            o=sum(v for k,v in d[limit:])
            if o>0.3: out.append(['Other',round(o,1)])
            return out
        return {'pa':{'t':round(pa_t),'act':round(PAa*100,1),'bud':round(PAb*100,1),'reasons':rsn('Down')},
                'ua':{'t':round(ua_t),'act':round(UAa*100,1),'bud':round(UAb*100,1),'reasons':rsn('Standby')},
                'oe':{'t':round(oe_t),'act':round(OEa*100,1),'bud':round(OEb*100,1),'reasons':rsn('Delay')}}
    def agg_shov2(pits):
        seg=shovel_seg_decomp(pits)
        if not seg: return None
        av=avail_decomp_shovel(pits); t=seg['total']
        schedDelta=(av['pa']['t']+av['ua']['t']+av['oe']['t']) if av else 0.0
        units=sorted(seg['units'],key=lambda x:-x['potential'])
        tp=t['potential']
        for u in units:   # allocate fleet PA/UA/OE by potential share so units sum to the fleet schedPotential
            share=u['potential']/tp if tp else 0
            u['availDecomp']=scale_av(av,share)
            u['schedPotential']=u['potential']-schedDelta*share
        byMaterial={m:_attach_av(seg['byMat'][m],scale_av(av,seg['byMat'][m]['potential']/tp if tp else 0))
                    for m in ['Ore','Waste'] if m in seg['byMat']}
        return {**t,'availDecomp':av,'schedPotential':t['potential']-schedDelta,
                'byMaterial':byMaterial,'units':units}
    def agg_shov(pits):
        pot=sum(load[p]['pot'] for p in pits); act=sum(load[p]['act'] for p in pits)
        groups=[]
        for (s,m) in [('BE495','Ore'),('BE495','Waste'),('HIT8000','Ore'),('HIT8000','Waste')]:
            gp=sum(load[p]['grp'][(s,m)][0] for p in pits); ga=sum(load[p]['grp'][(s,m)][1] for p in pits); gn=sum(load[p]['grp'][(s,m)][2] for p in pits)
            if gn<=0: continue
            groups.append({'name':('BE 495' if s=='BE495' else 'HIT 8000')+' · '+m,'type':s,'mat':m,
                'pot':gp,'act':ga,'variance':ga-gp,'rateBudget':gp/gn,'rateActual':ga/gn,'noh':gn})
        units=[]
        for p in pits:
            for ex,v in load[p]['unit'].items():
                up,ua,un,ul=v
                if un<=0: continue
                units.append({'unit':ex,'pit':p,'type':shovel_type_of.get(ex,''),'loads':ul,'pot':up,'act':ua,
                    'variance':ua-up,'score':ua/up*100 if up else 0,'rateBudget':up/un,'rateActual':ua/un})
        units.sort(key=lambda x:-x['pot'])
        return {'potential':pot,'actual':act,'score':act/pot*100 if pot else 0,'groups':groups,'units':units}
    # ---- haul-cycles flow (sankey) ----
    flowcache={}
    for pit in PITS:
        rs=[r for r in t1 if r['LoadPit']==pit]
        lx=defaultdict(list);ly=defaultdict(list);dxx=defaultdict(list);dyy=defaultdict(list)
        ltons=defaultdict(lambda:defaultdict(float));dtons=defaultdict(float);ltp=defaultdict(lambda:[0.0,0.0])
        full=defaultdict(lambda:[0.0,0,defaultdict(int),0,0.0,0.0])   # tons, count, matcounts, lockedCount, Σ full-haul dist(m), Σ expected dist(m)
        prev_full=defaultdict(lambda:[0.0,0,defaultdict(int),0,0.0])      # (prevDump,shovel) → [tons, count, {mat:count}, lockedCount, Σ empty-haul dist(m)]
        for r in rs:
            # left node = the actual shovel (Excav) so the placement axis always reads as a shovel and each
            # shovel's digs (bench + stockpile re-handle) aggregate into one node; fall back to LoadLocation if blank
            L=(r.get('Excav') or '').strip() or r['LoadLocation']; D=r['DumpLocation']; t=num(r['Tonnage']); m=r['MaterialGroupName']
            lx[L].append(num(r['FieldGpsxtkl']));ly[L].append(num(r['FieldGpsytkl']));ltons[L][m]+=t
            dxx[D].append(num(r['FieldGpsxtkd']));dyy[D].append(num(r['FieldGpsytkd']));dtons[D]+=t
            ltp[L][0]+=t; ltp[L][1]+=max(NOH_FLOOR_S,num(r['SpotTime'])+num(r['LoadingTime'])+num(r['HangTime']))/3600.0
            # a "locked" load is not optimized by the system: FieldElock or FieldDlock holds a shovel/dump ID (not NONE)
            lk=(r.get('FieldElock','NONE') not in ('','NONE')) or (r.get('FieldDlock','NONE') not in ('','NONE'))
            if L!=D:
                f=full[(L,D)];f[0]+=t;f[1]+=1;f[2][m]+=1;f[4]+=num(r['FullHaulDistance']);f[5]+=num(r['FullExpectedDistance'])
                if lk: f[3]+=1
            # prev-dump: where the truck was before arriving at this shovel (from PREV_DUMP global dict)
            pd=PREV_DUMP.get(id(r))
            if pd and pd!='?':
                pf=prev_full[(pd,L)]; pf[0]+=t; pf[1]+=1; pf[2][m]+=1; pf[4]+=num(r['EmptyHaullDistance'])
                if lk: pf[3]+=1
        allx=sorted(z for vs in list(lx.values())+list(dxx.values()) for z in vs if z>0)
        ally=sorted(z for vs in list(ly.values())+list(dyy.values()) for z in vs if z>0)
        def pc(a,p): return a[min(len(a)-1,int(len(a)*p))] if a else 0
        x0,x1=pc(allx,.02),pc(allx,.98); y0,y1=pc(ally,.02),pc(ally,.98); padb=(x1-x0)*0.5 or 500
        ok=lambda x,y: x0-padb<=x<=x1+padb and y0-padb<=y<=y1+padb
        loadN=[];dumpN=[]
        for L in lx:
            x,y=_med(lx[L]),_med(ly[L])
            if ok(x,y): loadN.append({'id':L,'tons':round(sum(ltons[L].values())),'mat':max(ltons[L],key=ltons[L].get),
                'tpnoh':round(ltp[L][0]/ltp[L][1]) if ltp[L][1] else 0})
        for D in dxx:
            x,y=_med(dxx[D]),_med(dyy[D])
            if ok(x,y): dumpN.append({'id':D,'tons':round(dtons[D])})
        lset={n['id'] for n in loadN}; dset={n['id'] for n in dumpN}
        fF=[{'from':k[0],'to':k[1],'tons':round(v[0]),'mat':max(v[2],key=v[2].get),'n':v[1],'nlock':v[3],'km':round(v[4]/v[1]/1000,1) if v[1] else 0,'kmE':round(v[5]/v[1]/1000,1) if v[1] else 0} for k,v in full.items() if k[0] in lset and k[1] in dset]
        # prev-dump flows: (prevDump,shovel) pairs where shovel is a known load node (≥2 loads for signal)
        prevF=[{'from':k[0],'to':k[1],'tons':round(v[0]),'mat':max(v[2],key=v[2].get),'n':v[1],'nlock':v[3],'km':round(v[4]/v[1]/1000,1) if v[1] else 0}
               for k,v in prev_full.items() if k[1] in lset and v[1]>=2]
        flowcache[pit]={'loadNodes':loadN,'dumpNodes':dumpN,'fullFlows':fF,'prevFlows':prevF}
    def agg_flows(pits):
        if len(pits)==1: return flowcache[pits[0]]
        out={'loadNodes':[],'dumpNodes':[],'fullFlows':[],'prevFlows':[]}
        for p in pits:
            for k in out: out[k]+=flowcache[p][k]
        return out
    # ---- shift analytics ----
    def compute_analytics(pits):
        Lr=[r for r in t1 if r['LoadPit'] in pits]
        POre=sum(bud(p,'POre') for p in pits); PWst=sum(bud(p,'PWst') for p in pits)
        NPOre=sum(bud(p,'NPOre') for p in pits); NPWst=sum(bud(p,'NPWst') for p in pits)
        planTotal=POre+PWst
        bk=[0.0]*48   # 48 fifteen-minute windows across the 12-h shift
        for r in Lr:
            d=_dtp(r['DumpingTimestamp'])
            if not d: continue
            mn=mfs(d)
            if mn is None or mn<0: mn=0
            bk[min(47,int(mn//15))]+=num(r['Tonnage'])
        cum=[];s=0.0
        for i in range(48): s+=bk[i]; cum.append(s)
        last=max((i for i in range(48) if bk[i]>0),default=-1)   # last window with a dump
        # cumulative is plotted at each window's END: tick i (06:00..18:00, i=0..48) shows tonnes dumped *through* labels[i];
        # tick 0 = shift start = 0 t, tick i≥1 = cum through window i-1.
        _throughMin=int(round(elapsed*60))   # data captured this many minutes into the shift (snapshot cutoff)
        cumulative={'labels':[f"{(base+(i*15)//60)%24:02d}:{(i*15)%60:02d}" for i in range(49)],
            'actual':[0]+[ (round(cum[i]) if i<=last else None) for i in range(48) ],
            'target':[round(planTotal*i/48) for i in range(49)],'plan':round(planTotal),
            # 'complete' = data reaches ~shift end; used to decide flat-carry vs projection (NOT the last-dump window,
            # which mis-fires when a live shift is snapshotted in the final hour).
            'complete':(elapsed>=11.75),'throughMin':_throughMin,'base':base}
        # Sand Haul: MaterialType names sand/crush AND dumped to a DY* location (all trucks + shovels), same pit filter
        def _is_sand(r):
            mt=(r.get('MaterialType') or '').lower()
            return ('sand' in mt or 'crush' in mt) and (r.get('DumpLocation') or '').upper().startswith('DY')
        bkS=[0.0]*48; sandDump=defaultdict(float)
        for r in loads:
            if r['LoadPit'] not in pits or not _is_sand(r): continue
            sandDump[r['DumpLocation'] or '?']+=num(r['Tonnage'])
            d=_dtp(r['DumpingTimestamp'])
            if not d: continue
            mn=mfs(d)
            if mn is None or mn<0: mn=0
            bkS[min(47,int(mn//15))]+=num(r['Tonnage'])
        cumS=[];sS=0.0
        for i in range(48): sS+=bkS[i]; cumS.append(sS)
        lastS=max((i for i in range(48) if bkS[i]>0),default=-1)
        sandByDump=[{'dump':k,'tons':round(v)} for k,v in sorted(sandDump.items(),key=lambda x:-x[1])]
        cumulativeSand={'labels':cumulative['labels'],
            'actual':[0]+[ (round(cumS[i]) if i<=lastS else None) for i in range(48) ],
            'target':([round(SAND_HAUL_TARGET*i/48) for i in range(49)] if SAND_HAUL_TARGET else [None]*49),
            'plan':(round(SAND_HAUL_TARGET) if SAND_HAUL_TARGET else None),
            'complete':(elapsed>=11.75),'throughMin':_throughMin,'base':base}
        ho=[0.0]*12;hw=[0.0]*12;hn=[0.0]*12; hd=[0.0]*12;hdn=[0]*12; hdT=0.0;hdTn=0   # per-hour full-haul distance (m) + count, and overall
        for r in Lr:
            d=_dtp(r['DumpingTimestamp'])
            if not d: continue
            mn=mfs(d)
            if mn is None or mn<0 or mn>=720: continue
            i=int(mn//60); t=num(r['Tonnage'])
            if r['Category']=='Non-Productive': hn[i]+=t
            elif r['MaterialGroupName']=='Waste': hw[i]+=t
            else: ho[i]+=t
            fdist=num(r['FullHaulDistance'])
            if fdist>0: hd[i]+=fdist; hdn[i]+=1; hdT+=fdist; hdTn+=1
        haulKm=[round(hd[i]/hdn[i]/1000,1) if hdn[i] else 0 for i in range(12)]   # avg full-haul km per hour
        haulKmAvg=round(hdT/hdTn/1000,1) if hdTn else 0
        hourly={'hours':[f"{(base+i)%24:02d}" for i in range(12)],'ore':[round(x) for x in ho],
            'waste':[round(x) for x in hw],'nonprod':[round(x) for x in hn],'haulKm':haulKm,'haulKmAvg':haulKmAvg,
            'target':round(planTotal/12),'targetOre':round(POre/12),'targetWaste':round(PWst/12),'targetNonProd':round((NPOre+NPWst)/12)}
        # timeline (shovels S0/S8) — placed by real Statusevents TimeStamp
        ev=[e for e in sev if e['Pit'] in pits and (e['Eqmt'][:2] in ('S0','S8'))]
        seg=defaultdict(list); _scum=defaultdict(float)
        for e in ev:
            dur=status_hours(e)*60.0
            if dur<=0: continue
            d=_dtp(e['StartTime']); st=mfs(d) if d else None   # SSRS StartTime may be blank → cumulative Duration
            if st is None: st=_scum[e['Eqmt']]
            _scum[e['Eqmt']]=max(0.0,st)+dur
            seg[e['Eqmt']].append([round(max(0.0,st),1),round(dur,1),_statgrp(e['ASEStatus']),(e['Reason'] or '').title()])
        eq=sorted(seg, key=lambda k:-sum(x[1] for x in seg[k] if x[2]=='Ready'))
        shmat=defaultdict(lambda:defaultdict(float))
        for r in Lr:
            if r['Excav'][:2] in ('S0','S8'): shmat[r['Excav']][r['MaterialGroupName']]+=num(r['Tonnage'])
        smat={k:(max(shmat[k],key=shmat[k].get) if shmat[k] else '') for k in eq}
        q={k:qseries[k] for k in eq if qseries.get(k)}
        qmax=max([1]+[val for k in q for (_,val) in q[k]])
        # avg queue-at-shovel and hang time per load, by shovel (QueueTimeShvl / HangTime)
        qsh=defaultdict(lambda:[0.0,0]); hsh=defaultdict(lambda:[0.0,0])
        for r in t1:
            if r['LoadPit'] in pits:
                e=qsh[r['Excav']]; e[0]+=num(r['QueueTimeShvl']); e[1]+=1
                g=hsh[r['Excav']]; g[0]+=num(r['HangTime']); g[1]+=1
        avgq={k:(round(qsh[k][0]/qsh[k][1]/60,1) if qsh[k][1] else None) for k in eq}
        avgh={k:(round(hsh[k][0]/hsh[k][1]/60,1) if hsh[k][1] else None) for k in eq}
        # per-shovel hourly TPNOH = tonnes loaded ÷ net operating (Ready) hours, per hour bucket
        sh_ton_hr=defaultdict(lambda:[0.0]*12)
        sh_hang_hr=defaultdict(lambda:[0.0]*12); sh_queue_hr=defaultdict(lambda:[0.0]*12); sh_hang_n=defaultdict(lambda:[0]*12)   # hang/queue seconds + load count per hour
        sh_lw=defaultdict(list)   # per-shovel per-load [loadMin, hang s, queue s] for the shovel-band bar view
        for r in Lr:
            ex=r['Excav']
            if ex[:2] not in ('S0','S8'): continue
            dl=_dtp(r['LoadingTimestamp']); mnl=mfs(dl) if dl else None
            if mnl is not None and 0<=mnl<720:
                sh_lw[ex].append([round(mnl,1),round(num(r['HangTime'])),round(num(r['QueueTimeShvl']))])
            d=_dtp(r['DumpingTimestamp'])
            if not d: continue
            mn=mfs(d)
            if mn is None or mn<0 or mn>=720: continue
            _hb=int(mn//60)
            sh_ton_hr[ex][_hb]+=num(r['Tonnage'])
            sh_hang_hr[ex][_hb]+=num(r['HangTime']); sh_queue_hr[ex][_hb]+=num(r['QueueTimeShvl']); sh_hang_n[ex][_hb]+=1
        sh_op_hr=defaultdict(lambda:[0.0]*12)   # operating (Ready) minutes per hour bucket
        for k in eq:
            for s in seg[k]:
                if s[2]!='Ready': continue
                st=s[0]; en=st+s[1]
                b=int(st//60)
                while b<12 and st<en:
                    bend=(b+1)*60; segend=min(en,bend)
                    sh_op_hr[k][b]+=(segend-st); st=segend; b+=1
        tphr={k:[ (round(sh_ton_hr[k][i]/(sh_op_hr[k][i]/60.0)) if sh_op_hr[k][i]>3 else None) for i in range(12) ] for k in eq}
        tonhr={k:[ (round(sh_ton_hr[k][i]) if sh_ton_hr[k][i]>0 else None) for i in range(12) ] for k in eq}   # total tonnes/hr
        hanghr={k:[ (round(sh_hang_hr[k][i]/sh_hang_n[k][i]/60,1) if sh_hang_n[k][i] else None) for i in range(12) ] for k in eq}   # avg hang min/load per hr
        queuehr={k:[ (round(sh_queue_hr[k][i]/sh_hang_n[k][i]/60,1) if sh_hang_n[k][i] else None) for i in range(12) ] for k in eq}   # avg queue min/load per hr
        timeline={'equip':eq,'seg':{k:seg[k] for k in eq},'queue':q,'qmax':qmax,'mat':smat,'base':base,'avgq':avgq,'avgh':avgh,'tphr':tphr,'tonhr':tonhr,'hanghr':hanghr,'queuehr':queuehr,'loadWaits':{k:sh_lw[k] for k in eq}}
        # timeline (Cat 797 haul trucks that actually hauled this view) — status segments, real TimeStamp
        activeTrk={r['Truck'] for r in t1 if r['LoadPit'] in pits}
        tseg=defaultdict(list); _tcum=defaultdict(float)
        for e in sev:
            if e.get('EqmtType')!='Cat 797' or e['Eqmt'] not in activeTrk: continue
            dur=num(e['Duration'])/60.0
            if dur<=0: continue
            d=_dtp(e['StartTime']); st=mfs(d) if d else None   # SSRS StartTime may be blank → cumulative Duration
            if st is None: st=_tcum[e['Eqmt']]
            _tcum[e['Eqmt']]=max(0.0,st)+dur
            tseg[e['Eqmt']].append([round(max(0.0,st),1),round(dur,1),_statgrp(e['ASEStatus']),(e['Reason'] or '').title()])
        teq=sorted(tseg)   # order trucks by Truck ID
        truckTimeline={'equip':teq,'seg':{k:tseg[k] for k in teq},'base':base}
        # delay pareto — accumulated for all equipment, and split by fleet (trucks / shovels) for the Delays & Standby tab
        rs=defaultdict(float); rc=defaultdict(int)
        rsg={'trucks':[defaultdict(float),defaultdict(int)],'shovels':[defaultdict(float),defaultdict(int)]}
        # delay variance accumulators (only rows with a valid ExpectedDuration), all + per fleet
        va=defaultdict(float); ve=defaultdict(float); vc=defaultdict(int)
        vg={'trucks':[defaultdict(float),defaultdict(float),defaultdict(int)],'shovels':[defaultdict(float),defaultdict(float),defaultdict(int)]}
        for e in sev:
            if e['Pit'] in pits and e['ASEStatus']=='Delay':
                k=e['Reason'] or 'UNSPECIFIED'; dh=num(e['Duration'])/3600.0; rs[k]+=dh; rc[k]+=1
                et=e.get('EqmtType'); grp='trucks' if et=='Cat 797' else ('shovels' if et in ('BE 495B','HIT 800') else None)
                if grp: rsg[grp][0][k]+=dh; rsg[grp][1][k]+=1
                ed=str(e['ExpectedDuration']).strip()
                if ed and ed.upper()!='NULL':
                    try: exv=float(ed)
                    except ValueError: exv=None
                    if exv is not None:
                        va[k]+=dh; ve[k]+=exv/3600.0; vc[k]+=1
                        if grp: vg[grp][0][k]+=dh; vg[grp][1][k]+=exv/3600.0; vg[grp][2][k]+=1
        def _mkpareto(rsd,rcd):
            top=sorted(rsd.items(),key=lambda x:-x[1])[:20]; topk={k for k,v in top}
            other=sum(v for k,v in rsd.items() if k not in topk); otherc=sum(c for k,c in rcd.items() if k not in topk)
            rlist=[(k,v,rcd[k]) for k,v in top]+([('Other',other,otherc)] if other>1 else [])
            tot=sum(v for k,v,c in rlist) or 1; cu=0; p={'reasons':[],'hours':[],'cumpct':[],'counts':[]}
            for k,v,c in rlist:
                cu+=v; p['reasons'].append(k.title()); p['hours'].append(round(v,1)); p['cumpct'].append(round(cu/tot*100,1)); p['counts'].append(c)
            return p
        pareto=_mkpareto(rs,rc)
        paretoTrk=_mkpareto(rsg['trucks'][0],rsg['trucks'][1]); paretoShv=_mkpareto(rsg['shovels'][0],rsg['shovels'][1])
        # delay variance — signed (actual − expected), ranked by |variance| (null-expected rows excluded); all + per fleet
        def _mkdvar(vad,ved,vcd):
            items=[(k,vad[k]-ved[k],vad[k],ved[k],vcd[k]) for k in vad]
            items.sort(key=lambda x:-abs(x[1]))
            vtop=items[:20]; vtopk={x[0] for x in vtop}
            oA=sum(vad[k] for k in vad if k not in vtopk); oE=sum(ved[k] for k in vad if k not in vtopk); oC=sum(vcd[k] for k in vad if k not in vtopk)
            vlist=list(vtop)
            if oC>0 and abs(oA-oE)>0.05: vlist.append(('Other',oA-oE,oA,oE,oC))
            dv={'reasons':[],'actual':[],'expected':[],'variance':[],'counts':[]}
            for k,var,a,e,c in vlist:
                dv['reasons'].append(k.title()); dv['actual'].append(round(a,1)); dv['expected'].append(round(e,1))
                dv['variance'].append(round(var,1)); dv['counts'].append(c)
            return dv
        delayVar=_mkdvar(va,ve,vc)
        delayVarTrk=_mkdvar(vg['trucks'][0],vg['trucks'][1],vg['trucks'][2]); delayVarShv=_mkdvar(vg['shovels'][0],vg['shovels'][1],vg['shovels'][2])
        # payload compliance (Tukey)
        pl=defaultdict(list); plm=defaultdict(lambda:defaultdict(float))
        for r in Lr:
            if r['Excav'][:2] in ('S0','S8'):
                mt=num(r['MeasuredTon'])                     # actual weighed payload (not nominal Tonnage)
                if mt>=50: pl[r['Excav']].append(mt)         # drop zero/erroneous weigh readings
                plm[r['Excav']][r['MaterialGroupName']]+=num(r['Tonnage'])
        def _q(v,p):
            if not v: return 0.0
            idx=p*(len(v)-1); loi=int(idx); hii=min(loi+1,len(v)-1); return v[loi]+(v[hii]-v[loi])*(idx-loi)
        pay=[]
        for sh in pl:
            v=sorted(pl[sh]); n=len(v)
            if n<3: continue
            avg=sum(v)/n; q1=_q(v,0.25); med=_q(v,0.5); q3=_q(v,0.75); iqr=q3-q1
            lf=q1-1.5*iqr; uf=q3+1.5*iqr; inb=[x for x in v if lf<=x<=uf]
            wlo=min(inb) if inb else q1; whi=max(inb) if inb else q3
            outs=sorted({round(x,1) for x in v if x<lf or x>uf})
            comp=sum(1 for x in v if abs(x-PAYLOAD_TARGET)<=PAYLOAD_TARGET*0.05)/n*100
            pay.append({'shovel':sh,'type':'BE495' if sh[:2]=='S0' else 'HIT8000','mat':(max(plm[sh],key=plm[sh].get) if plm[sh] else 'Ore'),
                'n':n,'avg':round(avg,1),'median':round(med,1),'q1':round(q1,1),'q3':round(q3,1),'iqr':round(iqr,1),
                'wlo':round(wlo,1),'whi':round(whi,1),'outliers':outs,'compliance':round(comp)})
        pay.sort(key=lambda x:-x['avg'])
        # hang-time & load-time box plots per shovel (actual seconds vs budget seconds), same Tukey stats as payload
        def _box(vals,sh,mat,tgt,comp,isdump=False):
            v=sorted(vals); n=len(v)
            if n<3: return None
            avg=sum(v)/n; q1=_q(v,0.25); med=_q(v,0.5); q3=_q(v,0.75); iqr=q3-q1
            lf=q1-1.5*iqr; uf=q3+1.5*iqr; inb=[x for x in v if lf<=x<=uf]
            wlo=min(inb) if inb else q1; whi=max(inb) if inb else q3
            outs=sorted({round(x,1) for x in v if x<lf or x>uf})
            return {'shovel':sh,'type':('' if isdump else ('BE495' if sh[:2]=='S0' else 'HIT8000')),'mat':mat,
                'n':n,'avg':round(avg,1),'median':round(med,1),'q1':round(q1,1),'q3':round(q3,1),'iqr':round(iqr,1),
                'wlo':round(wlo,1),'whi':round(whi,1),'outliers':outs,'tgt':round(tgt,1),'compliance':comp}
        # per-loading-shovel box plots — shovel cycle rows (hang/load/spot) and truck cycle rows (queue/idle/dump/full)
        hg=defaultdict(list); ld=defaultdict(list); sp=defaultdict(list)
        qv=defaultdict(list); iv=defaultdict(list); dv=defaultdict(list); fv=defaultdict(list)
        fhd=defaultdict(list); ehd=defaultdict(list)   # full/empty haul DISTANCE (m) per shovel, for the "Haul Distance" box plot
        hgb=defaultdict(lambda:[0.0,0]); ldb=defaultdict(lambda:[0.0,0]); spb=defaultdict(lambda:[0.0,0])   # Σ budget seconds, count
        qb=defaultdict(lambda:[0.0,0]); ib=defaultdict(lambda:[0.0,0]); db=defaultdict(lambda:[0.0,0]); fb=defaultdict(lambda:[0.0,0])
        fhdb=defaultdict(lambda:[0.0,0]); ehdb=defaultdict(lambda:[0.0,0])   # Σ expected distance (m), count
        hgc=defaultdict(lambda:[0,0]); ldc=defaultdict(lambda:[0,0]); spc=defaultdict(lambda:[0,0])          # within ±10%, total
        qc=defaultdict(lambda:[0,0]); ic=defaultdict(lambda:[0,0]); dc=defaultdict(lambda:[0,0]); fc=defaultdict(lambda:[0,0])
        fhdc=defaultdict(lambda:[0,0]); ehdc=defaultdict(lambda:[0,0])
        hlmat=defaultdict(lambda:defaultdict(float)); dmat=defaultdict(lambda:defaultdict(float))   # shovel- and dump-keyed material shares
        def _acc(store,budstore,compstore,ex,act,bud):
            if act>0: store[ex].append(act)
            if bud>0:
                budstore[ex][0]+=bud; budstore[ex][1]+=1; compstore[ex][1]+=1
                if abs(act-bud)<=0.10*bud: compstore[ex][0]+=1
        for r in Lr:
            ex=r['Excav']; s=stype(ex)
            if not s or ex[:2] not in ('S0','S8'): continue
            pit=r['LoadPit']; mat=r['MaterialGroupName']
            ha=num(r['HangTime']); la=num(r['LoadingTime']); sa=num(r['SpotTime'])
            hlmat[ex][mat]+=num(r['Tonnage'])
            f=fx.get(pit,{}).get(mat)
            if f is not None:
                spot_b=f['Spot']*60; load_b=LMIN(f,SHVTYPE.get(s,'Average'))*60
                tp=shTPNOH.get(pit,{}).get((s,mat),0.0)
                hang_b=max(0.0,PAYLOAD_TARGET*3600.0/tp-spot_b-load_b) if tp>0 else 0.0
                hgb[ex][0]+=hang_b; hgb[ex][1]+=1; ldb[ex][0]+=load_b; ldb[ex][1]+=1; spb[ex][0]+=spot_b; spb[ex][1]+=1
                if hang_b>0:
                    hgc[ex][1]+=1
                    if abs(ha-hang_b)<=0.10*hang_b: hgc[ex][0]+=1
                if load_b>0:
                    ldc[ex][1]+=1
                    if abs(la-load_b)<=0.10*load_b: ldc[ex][0]+=1
                if spot_b>0:
                    spc[ex][1]+=1
                    if abs(sa-spot_b)<=0.10*spot_b: spc[ex][0]+=1
                # queue-at-shovel grouped by loading shovel; dump idle & dumping grouped by dump location
                _acc(qv,qb,qc,ex,num(r['QueueTimeShvl']),f['Queue']*60)
                _dlk=(r['DumpLocation'] or '')
                if _dlk and not _dlk.upper().startswith('IN') and _dlk!=(r['LoadLocation'] or ''):
                    dmat[_dlk][mat]+=num(r['Tonnage'])
                    _acc(iv,ib,ic,_dlk,num(r['QueueTimeDmp']),f['DumpIdle']*60)
                    _acc(dv,db,dc,_dlk,num(r['DumpingTime']),f['Dumping']*60)
            full_b=cv(pit,mat,hd_of(r))['Travel']*60*FFULL
            _acc(fv,fb,fc,ex,num(r['FullHaulDuration']),full_b)
            _acc(fhd,fhdb,fhdc,ex,num(r['FullHaulDistance']),num(r['FullExpectedDistance']))
            _acc(ehd,ehdb,ehdc,ex,num(r['EmptyHaullDistance']),num(r['EmptyExpectedDistance']))
            if ha>0: hg[ex].append(ha)
            if la>0: ld[ex].append(la)
            if sa>0: sp[ex].append(sa)
        def _boxset(src,buds,comps,matmap=None,isdump=False):
            mm=matmap if matmap is not None else hlmat
            out=[]
            for sh in src:
                mat=max(mm[sh],key=mm[sh].get) if mm[sh] else 'Ore'
                tgt=buds[sh][0]/buds[sh][1] if buds[sh][1] else 0.0
                comp=round(comps[sh][0]/comps[sh][1]*100) if comps[sh][1] else 0
                b=_box(src[sh],sh,mat,tgt,comp,isdump)
                if b: out.append(b)
            out.sort(key=lambda x:-x['avg']); return out
        hangbox=_boxset(hg,hgb,hgc); loadbox=_boxset(ld,ldb,ldc); spotbox=_boxset(sp,spb,spc)
        queuebox=_boxset(qv,qb,qc); fullbox=_boxset(fv,fb,fc)
        idlebox=_boxset(iv,ib,ic,dmat,True); dumpbox=_boxset(dv,db,dc,dmat,True)   # grouped by dump location
        # Haul Distance: full-haul + empty-haul distance (m) per shovel, two boxes per shovel (full blue, empty amber)
        fhdbox=_boxset(fhd,fhdb,fhdc); ehdbox=_boxset(ehd,ehdb,ehdc)
        _fm={b['shovel']:b for b in fhdbox}; _em={b['shovel']:b for b in ehdbox}
        _shk=sorted(set(list(_fm)+list(_em)),key=lambda u:[int(t) if t.isdigit() else t for t in re.split(r'(\d+)',str(u))])
        haulbox=[]
        for sh in _shk:
            if sh in _fm: bf=dict(_fm[sh]); bf['shovel']=sh+' · full'; bf['col']='#3f51b5'; haulbox.append(bf)
            if sh in _em: be=dict(_em[sh]); be['shovel']=sh+' · empty'; be['col']='#e0952a'; haulbox.append(be)
        # Shovel Waterfall box plots (payload / spot / load / hang) ordered by shovel ID (natural sort); truck-tab boxes keep their avg order
        _shk=lambda x:[int(t) if t.isdigit() else t for t in re.split(r'(\d+)',str(x['shovel']))]
        for _b in (pay,hangbox,loadbox,spotbox): _b.sort(key=_shk)
        # attach this shift's single/double-sided loading split to each spot-time box (from ShovelLoadingSide)
        _lside=LOADSIDE.get(sid,{})
        for _b in spotbox:
            _c=_lside.get(_b['shovel']); _t=(_c[0]+_c[1]) if _c else 0
            if _t>0: _b['side2']=round(_c[1]/_t*100); _b['side1']=100-_b['side2']
        return {'cumulative':cumulative,'cumulativeSand':cumulativeSand,'sandByDump':sandByDump,'hourly':hourly,'timeline':timeline,'truckTimeline':truckTimeline,'pareto':pareto,'paretoTrk':paretoTrk,'paretoShv':paretoShv,'delayVar':delayVar,'delayVarTrk':delayVarTrk,'delayVarShv':delayVarShv,'payload':pay,'payloadTarget':PAYLOAD_TARGET,'hangbox':hangbox,'loadbox':loadbox,'spotbox':spotbox,'queuebox':queuebox,'idlebox':idlebox,'dumpbox':dumpbox,'fullbox':fullbox,'haulbox':haulbox}

    def delaysStandby(pits):
        # Delay + Standby reasons for a fleet. Actual/Target expressed as impact on the affected metric:
        # Delay → OE (denominator Ready+Delay), Standby → UA (denominator Ready+Delay+Standby). Tonnage impact × TPNOH.
        def _compute(eqtypes,rate_of,uaBud,oeBud):
            agg=defaultdict(lambda:{'ah':0.0,'eh':0.0,'t':0.0,'st':'','hasexp':False}); R=D=S=Dn=0.0
            for e in sev:
                if e['Pit'] not in pits or e.get('EqmtType') not in eqtypes: continue
                st=e['ASEStatus']   # Parked is its own state, excluded from PA/UA/OE (not folded into Standby)
                h=num(e['Duration'])/3600.0
                if st=='Ready':R+=h
                elif st=='Delay':D+=h
                elif st=='Standby':S+=h
                elif st=='Down':Dn+=h
                if st not in ('Delay','Standby'): continue
                k=(e['Reason'] or 'Unspecified').title(); ed=str(e['ExpectedDuration']).strip()
                a=agg[k]; a['ah']+=h; a['st']=st
                if ed and ed.upper()!='NULL': exh=num(ed)/3600.0; a['eh']+=exh; a['hasexp']=True
                else: exh=0.0
                a['t']+=(exh-h)*rate_of(e)   # under target = gain (+), over = loss (−); no-target reasons all loss
            if (R+D+S)<=0: return {'rows':[],'th':0,'ua':None,'oe':None}
            oeDen=(R+D) or 1; uaDen=(R+D+S) or 1
            def den(st): return oeDen if st=='Delay' else uaDen
            rows=[{'reason':k,'status':a['st'],'metric':('OE' if a['st']=='Delay' else 'UA'),
                   'actPct':round(a['ah']/den(a['st'])*100,1),
                   'tgtPct':(round(a['eh']/den(a['st'])*100,1) if a['hasexp'] else None),'tonnes':round(a['t'])}
                  for k,a in agg.items()]
            _o={'Standby':0,'Delay':1}   # UA (Standby) group on top, then OE (Delay)
            rows.sort(key=lambda r:(_o.get(r['status'],9), -r['actPct']))   # group by status, actual-impact descending
            uaAct=(R+D)/(R+D+S)*100 if (R+D+S)>0 else 0; oeAct=R/(R+D)*100 if (R+D)>0 else 0
            return {'rows':rows,'th':round(R+D+S+Dn,1),
                    'ua':{'act':round(uaAct,1),'bud':(round(uaBud,1) if uaBud is not None else None)},
                    'oe':{'act':round(oeAct,1),'bud':(round(oeBud,1) if oeBud is not None else None)}}
        shv_types={k for k,v in AVGRP.items() if v in ('Cable shovel','Hydraulic shovel')}
        wt=sum(bud(p,'NOH797') for p in pits) or 1
        tUAb=sum(bud(p,'UA797')*bud(p,'NOH797') for p in pits)/wt*100
        tOEb=sum(bud(p,'OE797')*bud(p,'NOH797') for p in pits)/wt*100
        ws=sum(bud(p,'NOH495')+bud(p,'NOH8000') for p in pits) or 1
        sUAb=sum(bud(p,'UA495')*bud(p,'NOH495')+bud(p,'UA8000')*bud(p,'NOH8000') for p in pits)/ws*100
        trucks=_compute({'Cat 797'}, lambda e: bud(e['Pit'],'TPNOH797'), tUAb, tOEb)
        shovels=_compute(shv_types, lambda e: bud(e['Pit'],'TPNOHCable' if AVGRP.get(e['EqmtType'])=='Cable shovel' else 'TPNOHHydro'), sUAb, None)  # shovels have no OE budget
        return {'trucks':trucks,'shovels':shovels}
    def compute_hourlyPerf(pits):
        H=12
        ton=[0.0]*H; ore=[0.0]*H; waste=[0.0]*H; cnt=[0]*H
        ts={k:[0.0]*H for k in ('spot','load','hang','qshv','qdmp','dump','cyc','pay')}; tc=[0]*H
        bs={k:0.0 for k in ('spot','load','qshv','qdmp','dump','cyc')}; bn=0
        cyc_m={'Ore':[0.0]*H,'Waste':[0.0]*H}; cyc_mn={'Ore':[0]*H,'Waste':[0]*H}   # truck cycle time split by material
        bs_cyc_m={'Ore':0.0,'Waste':0.0}; bn_m={'Ore':0,'Waste':0}
        for r in t1:
            if r['LoadPit'] not in pits: continue
            d=_dtp(r['DumpingTimestamp']); mn=mfs(d) if d else None
            if mn is None or mn<0 or mn>=720: continue
            i=int(mn//60); T=num(r['Tonnage']); mat=r['MaterialGroupName']
            ton[i]+=T; cnt[i]+=1
            (waste if mat=='Waste' else ore)[i]+=T
            spot=num(r['SpotTime']);load=num(r['LoadingTime']);hang=num(r['HangTime'])
            qshv=num(r['QueueTimeShvl']);qdmp=num(r['QueueTimeDmp']);dump=num(r['DumpingTime'])
            emp=num(r['EmptyHaulDuration']);ful=num(r['FullHaulDuration']);dsp=num(r['DumpSpotTime'])
            cyc=spot+load+hang+qshv+qdmp+dump+emp+ful+dsp
            for k,v in (('spot',spot),('load',load),('hang',hang),('qshv',qshv),('qdmp',qdmp),('dump',dump),('cyc',cyc),('pay',T)): ts[k][i]+=v
            tc[i]+=1
            f=fx[r['LoadPit']].get(mat) or {'Queue':0,'Spot':0,'Load':0,'DumpIdle':0,'Dumping':0}; c=cv(r['LoadPit'],mat,hd_of(r))
            lmn=LMIN(f,styp_of(r['Excav']))   # Load budget for this load's shovel type
            bs['spot']+=f['Spot']*60; bs['load']+=lmn*60; bs['qshv']+=f['Queue']*60
            bs['qdmp']+=f['DumpIdle']*60; bs['dump']+=f['Dumping']*60
            cyc_bud=(f['Spot']+lmn+f['Queue']+f['DumpIdle']+f['Dumping'])*60+c['Travel']*60
            bs['cyc']+=cyc_bud; bn+=1
            mm='Waste' if mat=='Waste' else 'Ore'
            cyc_m[mm][i]+=cyc; cyc_mn[mm][i]+=1; bs_cyc_m[mm]+=cyc_bud; bn_m[mm]+=1
        if bn==0: return None
        def avgrow(key):
            vals=[round(ts[key][i]/tc[i],1) if tc[i] else None for i in range(H)]
            tot=round(sum(ts[key])/sum(tc),1) if sum(tc) else None
            return vals,tot
        POre=sum(bud(p,'POre') for p in pits); PWst=sum(bud(p,'PWst') for p in pits)
        rows=[]
        def add(label,uom,vals,total,budget,good): rows.append({'label':label,'uom':uom,'vals':vals,'total':total,'budget':budget,'good':good})
        add('Dumped','tonnes',[round(x) for x in ton],round(sum(ton)),round((POre+PWst)/12),'high')
        add('Ore Moved','tonnes',[round(x) for x in ore],round(sum(ore)),round(POre/12),'high')
        add('Waste Moved','tonnes',[round(x) for x in waste],round(sum(waste)),round(PWst/12),'high')
        add('Load Count','#',cnt,sum(cnt),None,None)
        pv,pt=avgrow('pay'); add('Payload — CAT 797','wTons',pv,pt,PAYLOAD_TARGET,'high')
        def avgrow_m(mm):
            vals=[round(cyc_m[mm][i]/cyc_mn[mm][i],1) if cyc_mn[mm][i] else None for i in range(H)]
            tot=round(sum(cyc_m[mm])/sum(cyc_mn[mm]),1) if sum(cyc_mn[mm]) else None
            return vals,tot
        for mm in ('Ore','Waste'):
            v,t=avgrow_m(mm); add('Cycle Time - '+mm,'mmss',v,t,(round(bs_cyc_m[mm]/bn_m[mm]) if bn_m[mm] else None),'low')
        sv,st=avgrow('spot'); add('Spot at Shovel','mmss',sv,st,round(bs['spot']/bn),'low')
        lv,lt=avgrow('load'); add('Load Time','mmss',lv,lt,round(bs['load']/bn),'low')
        hv,ht=avgrow('hang'); add('Shovel Hang','mmss',hv,ht,ht,'low')
        qv,qt=avgrow('qshv'); add('Truck Wait at Shovel','mmss',qv,qt,round(bs['qshv']/bn),'low')
        wv,wt=avgrow('qdmp'); add('Wait at Dump','mmss',wv,wt,round(bs['qdmp']/bn),'low')
        uv,ut=avgrow('dump'); add('Dumping Time','mmss',uv,ut,round(bs['dump']/bn),'low')
        return {'hours':[f"{(base+i)%24:02d}:00" for i in range(H)],'rows':rows}
    def compute_shovelProd(pits):
        # Shovel-level KPI table (sample "Shovel Productivity"): columns = All + each active unit.
        # Total Loaded target = scheduled potential GOH x budget PA x UA x OE x budget dig-rate.
        seg=shovel_seg_decomp(pits)
        if not seg: return None
        T0=PAYLOAD_TARGET; useg={u['unit']:u for u in seg['units']}
        tmat=defaultdict(lambda:defaultdict(float)); mt=defaultdict(list)
        for r in loads:
            if r['LoadPit'] not in pits: continue
            if not stype(r['Excav']): continue
            u=r['Excav']; tmat[u][r['MaterialGroupName']]+=num(r['Tonnage'])
            m=num(r['MeasuredTon'])
            if m>=50: mt[u].append(m)
        def avail_unit(u):
            R=De=S=Dn=0.0
            for e in sev:
                if e['Eqmt']!=u: continue
                st=e['ASEStatus']   # Parked is its own state, excluded from PA/UA/OE (not folded into Standby)
                h=num(e['Duration'])/3600.0
                if st=='Ready': R+=h
                elif st=='Delay': De+=h
                elif st=='Standby': S+=h
                elif st=='Down': Dn+=h
            return R,De,S,Dn
        def budrates(u,pit):
            s=stype(u)
            pac,uac,nohc,gohc=(('PA495','UA495','NOH495','GOH495') if s=='BE495' else ('PA8000','UA8000','NOH8000','GOH8000'))
            g=bud(pit,gohc); return bud(pit,pac),bud(pit,uac),(bud(pit,nohc)/g if g>0 else 0.0)
        def digtgt(u,pit):
            tn=tmat[u]; tot=sum(tn.values()) or 1; s=stype(u)
            return sum(tn[m]*shTPNOH[pit].get((s,m),0.0) for m in tn)/tot
        raw={}   # per unit raw metrics
        for u,us in useg.items():
            pit=us['pit']; R,De,S,Dn=avail_unit(u); GOH=R+De+S+Dn; NOH=R
            PAb,UAb,OEb=budrates(u,pit); rt=digtgt(u,pit); act=us['actual']
            PAa=(R+De+S)/GOH if GOH else 0; UAa=(R+De)/(R+De+S) if (R+De+S) else 0; OEa=R/(R+De) if (R+De) else 0
            ra=act/NOH if NOH>0 else 0; tgt=GOH*PAb*UAb*OEb*rt
            lm=us['lm']; row=us['rows']
            v=mt[u]; n=len(v) or 1
            raw[u]={'pit':pit,'type':stype(u),'R':R,'De':De,'S':S,'Dn':Dn,'GOH':GOH,'NOH':NOH,'act':act,'tgt':tgt,
                'PAa':PAa,'PAb':PAb,'UAa':UAa,'UAb':UAb,'OEa':OEa,'OEb':OEb,'ra':ra,'rt':rt,
                'pa_t':GOH*(PAa-PAb)*UAb*OEb*rt,'ua_t':GOH*PAa*(UAa-UAb)*OEb*rt,'oe_t':GOH*PAa*UAa*(OEa-OEb)*rt,'dr_t':NOH*(ra-rt),
                'sa':lm['Spot']['actual'],'sb':lm['Spot']['target'],'spot_t':row['Spot'],
                'la':lm['Load']['actual'],'lb':lm['Load']['target'],'load_t':row['Load'],
                'ha':lm['Hang']['actual'],'hb':lm['Hang']['target'],'hang_t':row['Hang'],
                'pay_a':lm['Payload']['actual'],'pay_t':row['Payload'],
                'over120':sum(1 for x in v if x>1.20*T0)/n*100,'band':sum(1 for x in v if 1.10*T0<=x<=1.20*T0)/n*100,
                'under':sum(1 for x in v if 0.70*T0<=x<0.90*T0)/n*100,'nmt':len(v)}
        order=sorted(raw.keys(),key=lambda u:[int(t) if t.isdigit() else t for t in re.split(r'(\d+)',str(u))])   # columns by shovel ID (natural)
        # ALL aggregate
        tt=seg['total']; slm=tt['lm']; srow=tt['rows']
        SR=sum(raw[u]['R'] for u in raw); SDe=sum(raw[u]['De'] for u in raw); SS=sum(raw[u]['S'] for u in raw); SDn=sum(raw[u]['Dn'] for u in raw)
        SGOH=SR+SDe+SS+SDn; SNOH=SR; Sact=sum(raw[u]['act'] for u in raw); Stgt=sum(raw[u]['tgt'] for u in raw)
        def wavg(key,wkey):
            wsum=sum(raw[u][wkey] for u in raw) or 1; return sum(raw[u][key]*raw[u][wkey] for u in raw)/wsum
        allmt=[x for u in raw for x in mt[u]]; n=len(allmt) or 1
        cols=[{'id':'ALL','label':'All Shovels','type':'','score':(Sact/Stgt*100 if Stgt>0 else None)}]
        for u in order:
            r=raw[u]; cols.append({'id':u,'label':u,'type':('BE 495' if r['type']=='BE495' else 'HIT 8000'),
                'score':(r['act']/r['tgt']*100 if r['tgt']>0 else None)})
        def rowdef(label,uom,fmt,tons,good,indent):
            return {'label':label,'uom':uom,'fmt':fmt,'tons':tons,'good':good,'indent':indent,'vals':{}}
        R_=[]
        def V(t,a,d): return {'t':t,'a':a,'d':d}
        # Total Loaded
        r=rowdef('Total Dumped','tonnes','tons',True,'high',0); r['vals']['ALL']=V(round(Stgt),round(Sact),round(Sact-Stgt))
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['tgt']),round(x['act']),round(x['act']-x['tgt']))
        R_.append(r)
        def pctrow(label,ak,bk,tk):
            r=rowdef(label,'%','pct',True,'high',0)
            PAa=None
            allA={'PAa':(SR+SDe+SS)/SGOH if SGOH else 0,'UAa':(SR+SDe)/(SR+SDe+SS) if (SR+SDe+SS) else 0,'OEa':SR/(SR+SDe) if (SR+SDe) else 0}[ak]
            allB=wavg(bk,'GOH')
            r['vals']['ALL']=V(round(allB*100,1),round(allA*100,1),round(sum(raw[u][tk] for u in raw)))
            for u in order: x=raw[u]; r['vals'][u]=V(round(x[bk]*100,1),round(x[ak]*100,1),round(x[tk]))
            return r
        R_.append(pctrow('PA','PAa','PAb','pa_t'))
        R_.append(pctrow('UA','UAa','UAb','ua_t'))
        R_.append(pctrow('OE','OEa','OEb','oe_t'))
        # NOH (net operating hours) — actual = Ready hrs; target = TH x budget PA x UA x OE
        r=rowdef('NOH','hrs','hrs',True,'high',0)
        allNOHt=sum(raw[u]['GOH']*raw[u]['PAb']*raw[u]['UAb']*raw[u]['OEb'] for u in raw)
        r['vals']['ALL']=V(round(allNOHt,1),round(SNOH,1),round(sum(raw[u]['pa_t']+raw[u]['ua_t']+raw[u]['oe_t'] for u in raw)))
        for u in order:
            x=raw[u]; nt=x['GOH']*x['PAb']*x['UAb']*x['OEb']
            r['vals'][u]=V(round(nt,1),round(x['NOH'],1),round(x['pa_t']+x['ua_t']+x['oe_t']))
        R_.append(r)
        # NOH % — net operating hours as % of total/calendar hours = PA x UA x OE
        r=rowdef('NOH %','%','pct',False,'high',0)
        r['vals']['ALL']=V(round(allNOHt/SGOH*100,1) if SGOH else 0,round(SNOH/SGOH*100,1) if SGOH else 0,None)
        for u in order:
            x=raw[u]; r['vals'][u]=V(round(x['PAb']*x['UAb']*x['OEb']*100,1),round(x['NOH']/x['GOH']*100,1) if x['GOH'] else 0,None)
        R_.append(r)
        # Dig Rate
        r=rowdef('Dig Rate','T/NOH','rate',True,'high',0)
        allrt=(Stgt/sum(raw[u]['GOH']*raw[u]['PAb']*raw[u]['UAb']*raw[u]['OEb'] for u in raw)) if sum(raw[u]['GOH']*raw[u]['PAb']*raw[u]['UAb']*raw[u]['OEb'] for u in raw)>0 else 0
        r['vals']['ALL']=V(round(allrt),round(Sact/SNOH if SNOH else 0),round(sum(raw[u]['dr_t'] for u in raw)))
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['rt']),round(x['ra']),round(x['dr_t']))
        R_.append(r)
        # Cycle block (seconds) — use seg total for ALL, seg units otherwise
        def cyc(label,ak,bk,tk,indent):
            r=rowdef(label,'mm:ss','mmss',(tk is not None),'low',indent)
            r['vals']['ALL']=V(round(slm[bk]['target']),round(slm[ak]['actual']),(round(srow[tk]) if tk else None))
            for u in order: x=raw[u]; r['vals'][u]=V(round(x[bk]),round(x[ak]),(round(x[tk]) if tk else None))
            return r
        # Shovel Cycle Time (sum of spot+load+hang)
        r=rowdef('Shovel Cycle Time','mm:ss','mmss',False,'low',0)
        r['vals']['ALL']=V(round(slm['Spot']['target']+slm['Load']['target']+slm['Hang']['target']),round(slm['Spot']['actual']+slm['Load']['actual']+slm['Hang']['actual']),None)
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['sb']+x['lb']+x['hb']),round(x['sa']+x['la']+x['ha']),None)
        R_.append(r)
        r=rowdef('Shovel Hang','mm:ss','mmss_',True,'low',1)
        r['vals']['ALL']=V(round(slm['Hang']['target']),round(slm['Hang']['actual']),round(srow['Hang']))
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['hb']),round(x['ha']),round(x['hang_t']))
        R_.append(r)
        # Shovel Wait % = hang / cycle
        r=rowdef('Shovel Wait %','%','pct1',False,'low',1)
        ca=slm['Spot']['actual']+slm['Load']['actual']+slm['Hang']['actual']; cb=slm['Spot']['target']+slm['Load']['target']+slm['Hang']['target']
        r['vals']['ALL']=V(round(slm['Hang']['target']/cb*100,1) if cb else 0,round(slm['Hang']['actual']/ca*100,1) if ca else 0,None)
        for u in order: x=raw[u]; cca=x['sa']+x['la']+x['ha']; ccb=x['sb']+x['lb']+x['hb']; r['vals'][u]=V(round(x['hb']/ccb*100,1) if ccb else 0,round(x['ha']/cca*100,1) if cca else 0,None)
        R_.append(r)
        r=rowdef('Spot at Shovel','mm:ss','mmss_',True,'low',1)
        r['vals']['ALL']=V(round(slm['Spot']['target']),round(slm['Spot']['actual']),round(srow['Spot']))
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['sb']),round(x['sa']),round(x['spot_t']))
        R_.append(r)
        r=rowdef('Load Time','mm:ss','mmss_',True,'low',1)
        r['vals']['ALL']=V(round(slm['Load']['target']),round(slm['Load']['actual']),round(srow['Load']))
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['lb']),round(x['la']),round(x['load_t']))
        R_.append(r)
        # Payload — average weighed payload per load vs the 361 t target (± tonnes = payload cycle impact)
        r=rowdef('Payload — CAT 797','wTons','tons',True,'high',0)
        r['vals']['ALL']=V(round(slm['Payload']['target']),round(slm['Payload']['actual']),round(srow['Payload']))
        for u in order: x=raw[u]; r['vals'][u]=V(round(PAYLOAD_TARGET),round(x['pay_a']),round(x['pay_t']))
        R_.append(r)
        # Payload quality (10-10-20 rule): >120% target 0, 110-120% target 10, underload 70-90% target 5
        def plrow(label,key,tgt,indent):
            r=rowdef(label,'%','pct1',False,'low',indent)
            allv=sum(1 for x in allmt if (x>1.20*T0 if key=='over120' else (1.10*T0<=x<=1.20*T0 if key=='band' else 0.70*T0<=x<0.90*T0)))/n*100
            r['vals']['ALL']=V(tgt,round(allv,1),None)
            for u in order: r['vals'][u]=V(tgt,round(raw[u][key],1),None)
            return r
        R_.append(plrow('Overloads (>120%)','over120',0.0,2))
        R_.append(plrow('110–120%','band',10.0,2))
        R_.append(plrow('Underloads (70–90%)','under',5.0,2))
        return {'cols':cols,'rows':R_}
    def compute_truckProd(pits):
        # Per-shovel KPI scorecard for Cat 797 haul trucks.
        # Columns = All Trucks + one column per active shovel (grouped by Excav), sorted by descending actual tonnes.
        # The payload-normalized waterfall basis (rate* = T0/Tb) is used throughout so the bridge closes.
        T0=PAYLOAD_TARGET
        # Group haul cycles by shovel (Excav) instead of by truck unit
        shv_loads=defaultdict(list)
        for r in t1:
            if r['LoadPit'] not in pits or not stype(r['Excav']): continue   # only BE495/HIT8000 shovels
            shv_loads[r['Excav']].append(r)
        if not shv_loads: return None
        # Also keep full list for all-trucks aggregate availability
        all_trk_loads=defaultdict(list)
        for r in t1:
            if r['LoadPit'] not in pits or not stype(r['Excav']): continue
            all_trk_loads[r['Truck']].append(r)
        order=sorted(shv_loads.keys(),key=lambda s:-sum(num(r['Tonnage']) for r in shv_loads[s]))
        def avail_trucks_for_shovel(shv_rows):
            # Aggregate Cat 797 availability for all trucks that loaded at this shovel
            trucks={r['Truck'] for r in shv_rows}
            R=De=S=Dn=0.0
            for e in sev:
                if e['Eqmt'] not in trucks or e.get('EqmtType')!='Cat 797': continue
                st=e['ASEStatus']   # Parked is its own state, excluded from PA/UA/OE (not folded into Standby)
                h=num(e['Duration'])/3600.0
                if st=='Ready': R+=h
                elif st=='Delay': De+=h
                elif st=='Standby': S+=h
                elif st=='Down': Dn+=h
            return R,De,S,Dn
        raw={}
        for s in order:
            rows_s=shv_loads[s]; n_loads=len(rows_s) or 1
            act=sum(num(r['Tonnage']) for r in rows_s)
            # payload-normalized potential + per-component waterfall tonnage impacts (same formula as agg_wf)
            pot=0.0; cyc_wf=defaultdict(float); cyc_a_sum=defaultdict(float); cyc_b_sum=defaultdict(float)
            cycle_hours=0.0; row_cycles=[]
            for r in rows_s:
                pit=r['LoadPit']; mat=r['MaterialGroupName']; c=cv(pit,mat,hd_of(r))
                f=fx[pit].get(mat) or {'Queue':0,'Spot':0,'Load':0,'DumpIdle':0,'Dumping':0}
                bmap={'Spot':f['Spot']*60,'Load':LMIN(f,styp_of(r['Excav']))*60,'Queue':f['Queue']*60,
                      'DumpIdle':f['DumpIdle']*60,'Dumping':f['Dumping']*60,
                      'Full':c['Travel']*60*FFULL,'Empty':c['Travel']*60*(1-FFULL),'DumpSpot':0.0}
                amap={'Spot':num(r['SpotTime']),'Load':num(r['LoadingTime']),'Queue':num(r['QueueTimeShvl']),
                      'DumpIdle':num(r['QueueTimeDmp']),'Dumping':num(r['DumpingTime']),
                      'Full':num(r['FullHaulDuration']),'Empty':num(r['EmptyHaulDuration']),'DumpSpot':num(r['DumpSpotTime'])}
                row_cycle = sum(amap.values())
                cycle_hours += row_cycle
                row_cycles.append((r,row_cycle,c.get('TPNOH',0.0)))
                Tb=sum(bmap.values()); rate=T0/Tb if Tb>0 else 0.0
                cyc_act=row_cycle; pot+=rate*cyc_act
                for comp in ('Spot','Load','Queue','DumpIdle','Dumping','Full'): cyc_wf[comp]+=rate*(bmap[comp]-amap[comp])
                cyc_wf['Empty']+=rate*((bmap['Empty']-amap['Empty'])+(bmap['DumpSpot']-amap['DumpSpot']))
                for comp in ('Spot','Load','Queue','DumpIdle','Dumping','Full','Empty'):
                    cyc_a_sum[comp]+=amap[comp] if comp!='Empty' else amap['Empty']+amap['DumpSpot']
                    cyc_b_sum[comp]+=bmap[comp] if comp!='Empty' else bmap['Empty']
            def avgc(comp,_a=cyc_a_sum,_n=n_loads): return _a[comp]/_n
            def budc(comp,_b=cyc_b_sum,_n=n_loads): return _b[comp]/_n
            R,De,S,Dn=avail_trucks_for_shovel(rows_s); GOH=R+De+S+Dn; NOH=cycle_hours/3600.0
            PAa=(R+De+S)/GOH if GOH else 0; UAa=(R+De)/(R+De+S) if (R+De+S) else 0; OEa=R/(R+De) if (R+De) else 0
            pitcnt=defaultdict(int)
            for r in rows_s: pitcnt[r['LoadPit']]+=1
            dom_pit=max(pitcnt,key=pitcnt.get) if pitcnt else pits[0]
            PAb=bud(dom_pit,'PA797'); UAb=bud(dom_pit,'UA797'); OEb=bud(dom_pit,'OE797'); rate_bud=bud(dom_pit,'TPNOH797')
            # TPNOH target = haul-curve TPNOH at the AVERAGE actual full-haul distance (tonnage-weighted over the mix)
            _fhd=[num(r['FullHaulDistance']) for r in rows_s if num(r['FullHaulDistance'])>0]
            _hdAvg=min(15.5,max(0.5,round((sum(_fhd)/len(_fhd)/1000.0),1))) if _fhd else 0.5
            _rn=0.0; _rt=0.0
            for r in rows_s:
                _T=num(r['Tonnage']); _rt+=cv(r['LoadPit'],r['MaterialGroupName'],_hdAvg).get('TPNOH',0.0)*_T; _rn+=_T
            rate_tgt=(_rt/_rn) if _rn>0 else 0.0
            tgt=GOH*PAb*UAb*OEb*rate_tgt
            pa_t=GOH*(PAa-PAb)*UAb*OEb*rate_bud
            ua_t=GOH*PAa*(UAa-UAb)*OEb*rate_bud
            oe_t=GOH*PAa*UAa*(OEa-OEb)*rate_bud
            payloads=[num(r['MeasuredTon']) for r in rows_s if num(r['MeasuredTon'])>=50]; np_=len(payloads) or 1
            avg_pay=sum(payloads)/np_ if payloads else 0.0
            over120=sum(1 for x in payloads if x>1.20*T0)/np_*100
            band_=sum(1 for x in payloads if 1.10*T0<=x<=1.20*T0)/np_*100
            under_=sum(1 for x in payloads if 0.70*T0<=x<0.90*T0)/np_*100
            ef_n=sum(num(r['EmptyHaullDistance']) for r in rows_s); ef_d=sum(num(r['FullHaulDistance']) for r in rows_s)
            shv_type='BE 495B' if stype(s)=='BE495' else ('HIT 800' if stype(s)=='HIT8000' else 'Shovel')
            raw[s]={'pit':dom_pit,'type':shv_type,'act':act,'pot':pot,'tgt':tgt,'n':n_loads,'R':R,'De':De,'S':S,'Dn':Dn,
                    'GOH':GOH,'NOH':NOH,'PAa':PAa,'PAb':PAb,'UAa':UAa,'UAb':UAb,'OEa':OEa,'OEb':OEb,
                    'pa_t':pa_t,'ua_t':ua_t,'oe_t':oe_t,'rate_bud':rate_bud,'rate_tgt':rate_tgt,
                    'cyc_wf':dict(cyc_wf),'avgc':avgc,'budc':budc,
                    'avg_pay':avg_pay,'over120':over120,'band':band_,'under':under_,'ef_n':ef_n,'ef_d':ef_d}
        Sact=sum(raw[s]['act'] for s in order); Spot_=sum(raw[s]['pot'] for s in order)
        Stgt=sum(raw[s]['tgt'] for s in order)
        SR=sum(raw[s]['R'] for s in order); SDe=sum(raw[s]['De'] for s in order)
        SS=sum(raw[s]['S'] for s in order); SDn=sum(raw[s]['Dn'] for s in order)
        SGOH=SR+SDe+SS+SDn; SNOH=sum(raw[s]['NOH'] for s in order)   # actual NOH = truck cycle time (Σ cycle hours)
        # Use the active truck cycle-hours in scope to blend the target TPNOH instead of the pit budget hours.
        # This keeps the Truck Productivity target aligned with the same haul-distance profile that generated the
        # actual row, while still falling back to the original pit-budget blend when there are no active loads.
        active_noh=sum(raw[s]['NOH'] for s in order)
        if active_noh:
            allPAb=sum(raw[s]['PAb']*raw[s]['NOH'] for s in order)/active_noh
            allUAb=sum(raw[s]['UAb']*raw[s]['NOH'] for s in order)/active_noh
            allOEb=sum(raw[s]['OEb']*raw[s]['NOH'] for s in order)/active_noh
            allRate=sum(raw[s]['rate_tgt']*raw[s]['NOH'] for s in order)/active_noh
        else:
            wnoh=sum(bud(p,'NOH797') for p in pits) or 1
            allPAb=sum(bud(p,'PA797')*bud(p,'NOH797') for p in pits)/wnoh
            allUAb=sum(bud(p,'UA797')*bud(p,'NOH797') for p in pits)/wnoh
            allOEb=sum(bud(p,'OE797')*bud(p,'NOH797') for p in pits)/wnoh
            allRate=sum(bud(p,'TPNOH797')*bud(p,'NOH797') for p in pits)/wnoh
        # Fleet TPNOH target = haul-curve TPNOH at the FLEET average actual full-haul distance (tonnage-weighted);
        # overrides the cycle-weighted blend above.
        _allfhd=[num(r['FullHaulDistance']) for s in order for r in shv_loads[s] if num(r['FullHaulDistance'])>0]
        _allhd=min(15.5,max(0.5,round((sum(_allfhd)/len(_allfhd)/1000.0),1))) if _allfhd else 0.5
        _arn=0.0; _art=0.0
        for s in order:
            for r in shv_loads[s]:
                _T=num(r['Tonnage']); _art+=cv(r['LoadPit'],r['MaterialGroupName'],_allhd).get('TPNOH',0.0)*_T; _arn+=_T
        if _arn>0: allRate=_art/_arn
        _bnohTot=sum(bnoh[p] for p in pits)   # budget NOH (NOH797) prorated to elapsed → NOH-row target
        allPAa=(SR+SDe+SS)/SGOH if SGOH else 0
        allUAa=(SR+SDe)/(SR+SDe+SS) if (SR+SDe+SS) else 0
        allOEa=SR/(SR+SDe) if (SR+SDe) else 0
        allNOHt=SGOH*allPAb*allUAb*allOEb
        allPA_t=sum(raw[s]['pa_t'] for s in order); allUA_t=sum(raw[s]['ua_t'] for s in order); allOE_t=sum(raw[s]['oe_t'] for s in order)
        allpay=[num(r['MeasuredTon']) for s in order for r in shv_loads[s] if num(r['MeasuredTon'])>=50]
        nap=len(allpay) or 1
        all_avg_pay=sum(allpay)/nap if allpay else 0.0
        all_over120=sum(1 for x in allpay if x>1.20*T0)/nap*100
        all_band=sum(1 for x in allpay if 1.10*T0<=x<=1.20*T0)/nap*100
        all_under=sum(1 for x in allpay if 0.70*T0<=x<0.90*T0)/nap*100
        allEFn=sum(raw[s]['ef_n'] for s in order); allEFd=sum(raw[s]['ef_d'] for s in order)
        Nall=sum(raw[s]['n'] for s in order) or 1
        def allAvgC(comp): return sum(raw[s]['avgc'](comp)*raw[s]['n'] for s in order)/Nall
        def allBudC(comp): return sum(raw[s]['budc'](comp)*raw[s]['n'] for s in order)/Nall
        def allCycWF(comp): return sum(raw[s]['cyc_wf'].get(comp,0) for s in order)
        cols=[{'id':'ALL','label':'All Trucks','type':'Cat 797','score':(Sact/Spot_*100 if Spot_>0 else None)}]
        for s in order:
            x=raw[s]; cols.append({'id':s,'label':s,'type':x['type'],'score':(x['act']/x['pot']*100 if x['pot']>0 else None)})
        def rowdef(label,uom,fmt,tons,good,indent):
            return {'label':label,'uom':uom,'fmt':fmt,'tons':tons,'good':good,'indent':indent,'vals':{}}
        def V(t,a,d): return {'t':t,'a':a,'d':d}
        R_=[]
        # Total Moved: Target = payload-normalised cycle potential, Actual = actual tonnes
        r=rowdef('Total Moved','tonnes','tons',True,'high',0)
        r['vals']['ALL']=V(round(Spot_),round(Sact),round(Sact-Spot_))
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['pot']),round(x['act']),round(x['act']-x['pot']))
        R_.append(r)
        # (PA / UA / OE availability display rows removed — availability tonnage impacts still feed NOH & Dig Rate)
        # Potential: Target = budget-derived potential, Actual = cycle potential (no +/-t)
        r=rowdef('Potential','tonnes','tons',False,'high',0)
        r['vals']['ALL']=V(round(Stgt),round(Spot_),None)
        for u in order: x=raw[u]; r['vals'][u]=V(round(x['tgt']),round(x['pot']),None)
        R_.append(r)
        # NOH: Target = budget NOH797 prorated to elapsed; Actual = truck cycle-time NOH; +/-t = NOH-gap × target rate
        r=rowdef('NOH','hrs','hrs',True,'high',0)
        r['vals']['ALL']=V(round(_bnohTot,1),round(SNOH,1),round(allRate*(SNOH-_bnohTot)))
        for u in order:
            x=raw[u]; nt=(_bnohTot*(x['NOH']/SNOH)) if SNOH>0 else 0.0
            r['vals'][u]=V(round(nt,1),round(x['NOH'],1),round(x['rate_tgt']*(x['NOH']-nt)))
        R_.append(r)
        # NOH %  (target = prorated budget NOH ÷ GOH, actual = cycle NOH ÷ GOH)
        r=rowdef('NOH %','%','pct',False,'high',0)
        r['vals']['ALL']=V(round(_bnohTot/SGOH*100,1) if SGOH else 0,round(SNOH/SGOH*100,1) if SGOH else 0,None)
        for u in order:
            x=raw[u]; nt=(_bnohTot*(x['NOH']/SNOH)) if SNOH>0 else 0.0
            r['vals'][u]=V(round(nt/x['GOH']*100,1) if x['GOH'] else 0,round(x['NOH']/x['GOH']*100,1) if x['GOH'] else 0,None)
        R_.append(r)
        # Truck Productivity (T/NOH) — target = haul-curve TPNOH at avg full-haul distance; actual = tonnes ÷ cycle-NOH; +/-t = NOH × rate-gap
        r=rowdef('Truck Productivity','T/NOH','rate',True,'high',0)
        r['vals']['ALL']=V(round(allRate),round(Sact/SNOH if SNOH else 0),round(Sact-SNOH*allRate))
        for u in order:
            x=raw[u]; r['vals'][u]=V(round(x['rate_tgt']),round(x['act']/x['NOH'] if x['NOH'] else 0),
                                      round(x['act']-x['NOH']*x['rate_tgt']))
        R_.append(r)
        # Truck Cycle Time (average per load, no +/-t)
        r=rowdef('Truck Cycle Time','mm:ss','mmss',False,'low',0)
        r['vals']['ALL']=V(round(allBudC('Spot')+allBudC('Load')+allBudC('Queue')+allBudC('DumpIdle')+allBudC('Dumping')+allBudC('Full')+allBudC('Empty')),
                           round(allAvgC('Spot')+allAvgC('Load')+allAvgC('Queue')+allAvgC('DumpIdle')+allAvgC('Dumping')+allAvgC('Full')+allAvgC('Empty')),None)
        for u in order:
            x=raw[u]; bc=x['budc']; ac=x['avgc']
            r['vals'][u]=V(round(bc('Spot')+bc('Load')+bc('Queue')+bc('DumpIdle')+bc('Dumping')+bc('Full')+bc('Empty')),
                           round(ac('Spot')+ac('Load')+ac('Queue')+ac('DumpIdle')+ac('Dumping')+ac('Full')+ac('Empty')),None)
        R_.append(r)
        # Cycle component rows (indented, with waterfall +/-t)
        def cycrow(label,uom,comp,indent):
            r=rowdef(label,uom,'mmss_',True,'low',indent)
            r['vals']['ALL']=V(round(allBudC(comp)),round(allAvgC(comp)),round(allCycWF(comp)))
            for u in order:
                x=raw[u]; r['vals'][u]=V(round(x['budc'](comp)),round(x['avgc'](comp)),round(x['cyc_wf'].get(comp,0)))
            return r
        R_.append(cycrow('Queue at Shovel','mm:ss','Queue',1))
        R_.append(cycrow('Spot at Shovel','mm:ss','Spot',1))
        R_.append(cycrow('Load Time','mm:ss','Load',1))
        R_.append(cycrow('Wait at Dump','mm:ss','DumpIdle',1))
        R_.append(cycrow('Dumping Time','mm:ss','Dumping',1))
        R_.append(cycrow('Full Haul','mm:ss','Full',1))
        R_.append(cycrow('Empty Haul','mm:ss','Empty',1))
        # Payload (average measured payload vs 361 t target)
        r=rowdef('Payload','wTons','tons',True,'high',0)
        r['vals']['ALL']=V(round(T0),round(all_avg_pay),round(sum((raw[u]['avg_pay']-T0)*raw[u]['n'] for u in order)))
        for u in order: x=raw[u]; r['vals'][u]=V(round(T0),round(x['avg_pay']),round((x['avg_pay']-T0)*x['n']))
        R_.append(r)
        # Empty/Full Distance Ratio
        r=rowdef('Empty/Full Distance Ratio','ratio','ratio',False,'low',0)
        r['vals']['ALL']=V(1.0,round(allEFn/allEFd,3) if allEFd else 0,None)
        for u in order: x=raw[u]; r['vals'][u]=V(1.0,round(x['ef_n']/x['ef_d'],3) if x['ef_d'] else 0,None)
        R_.append(r)
        # Payload quality (10-10-20 rule)
        def plrow(label,key,tgt,indent):
            r=rowdef(label,'%','pct1',False,'low',indent)
            aval={'over120':all_over120,'band':all_band,'under':all_under}[key]
            r['vals']['ALL']=V(tgt,round(aval,1),None)
            for u in order: r['vals'][u]=V(tgt,round(raw[u][key],1),None)
            return r
        R_.append(plrow('Overloads (>120%)','over120',0.0,2))
        R_.append(plrow('110–120%','band',10.0,2))
        R_.append(plrow('Underloads (70–90%)','under',5.0,2))
        return {'cols':cols,'rows':R_}
    def compute_shiftStats(pits):
        # Consolidated shift stats — 3 wide tables (loaded lanes, empty legs, shovels), each metric with its
        # budget for colour-coding. FULL haul & cycle by loaded lane (LoadLocation→DumpLocation); EMPTY haul by
        # the empty leg (previous row's DumpLocation → this LoadLocation, via PREV_DUMP); shovels by unit.
        def alane(): return {'n':0,'fd':[0.,0],'fdb':[0.,0],'ft':[0.,0],'ftb':[0.,0],'cyc':[0.,0],'cycb':[0.,0],'mt':defaultdict(float)}
        def ael(): return {'n':0,'ed':[0.,0],'edb':[0.,0],'et':[0.,0],'etb':[0.,0],'mt':defaultdict(float)}
        def ash(): return {'n':0,'hang':[0.,0],'hangb':[0.,0],'q':[0.,0],'qb':[0.,0],'pay':[0.,0],'mt':defaultdict(float)}
        lane=defaultdict(alane); elane=defaultdict(ael); shov=defaultdict(ash); Sfd=Sed=0.0
        def add(p,v):
            if v>0: p[0]+=v; p[1]+=1
        for r in t1:
            if r['LoadPit'] not in pits: continue
            pit=r['LoadPit']; mat=r['MaterialGroupName']; s=stype(r['Excav']); f=fx[pit].get(mat) or {}
            fd=num(r['FullHaulDistance']); fdb=num(r['FullExpectedDistance']); ft=num(r['FullHaulDuration']); ftb=num(r['FullExpectedDuration'])
            ed=num(r['EmptyHaullDistance']); edb=num(r['EmptyExpectedDistance']); et=num(r['EmptyHaulDuration']); etb=num(r['EmptyExpectedDuration'])
            Sfd+=fd; Sed+=ed
            fixed=(f.get('Spot',0)+LMIN(f,SHVTYPE.get(s,'Average'))+f.get('Queue',0)+f.get('DumpIdle',0)+f.get('Dumping',0))*60
            cyc=(num(r['SpotTime'])+num(r['LoadingTime'])+num(r['QueueTimeShvl'])+num(r['HangTime'])
                 +num(r['QueueTimeDmp'])+num(r['DumpSpotTime'])+num(r['DumpingTime'])+ft+et)
            T=num(r['Tonnage'])
            a=lane[(r['LoadLocation'] or '?')+' → '+(r['DumpLocation'] or '?')]; a['n']+=1; a['mt'][mat]+=T
            add(a['fd'],fd); add(a['fdb'],fdb); add(a['ft'],ft); add(a['ftb'],ftb); add(a['cyc'],cyc); add(a['cycb'],fixed+ftb+etb)
            pd=PREV_DUMP.get(id(r))   # empty leg: previous row's dump → this load
            if pd is not None:
                e=elane[pd+' → '+(r['LoadLocation'] or '?')]; e['n']+=1; e['mt'][mat]+=T
                add(e['ed'],ed); add(e['edb'],edb); add(e['et'],et); add(e['etb'],etb)
            if s:
                sh=shov[r['Excav']]; sh['n']+=1; sh['mt'][mat]+=T
                add(sh['hang'],num(r['HangTime'])); add(sh['q'],num(r['QueueTimeShvl'])); add(sh['pay'],T); add(sh['qb'],f.get('Queue',0)*60)
                tp=shTPNOH[pit].get((s,mat),0.0)
                if tp>0: add(sh['hangb'],max(0.0,PAYLOAD_TARGET*3600.0/tp-f.get('Spot',0)*60-f.get('Load',0)*60))
        MIN=5; av=lambda p:(p[0]/p[1] if p[1] else 0.0)
        L=[(k,v) for k,v in lane.items() if v['n']>=MIN]; E=[(k,v) for k,v in elane.items() if v['n']>=MIN]; S=[(k,v) for k,v in shov.items() if v['n']>=MIN]
        def C(val,bud): return {'v':round(val,1),'b':(round(bud,1) if bud else None)}
        def dom(v): m=v['mt']; return (max(m,key=m.get) if m else 'Ore')
        def grouped(items,keyfn,cellsfn,perMat):   # split by dominant material, Ore then Waste, top-N each
            rows=[]
            for mt in ('Ore','Waste'):
                for k,v in sorted([(k,v) for k,v in items if dom(v)==mt],key=keyfn)[:perMat]:
                    rows.append({'name':k,'n':v['n'],'mat':mt,'cells':cellsfn(v)})
            return rows
        laneCells=lambda v:[C(av(v['fd'])/1000,av(v['fdb'])/1000),C(av(v['ft']),av(v['ftb'])),C(av(v['cyc']),av(v['cycb']))]
        emptyCells=lambda v:[C(av(v['ed'])/1000,av(v['edb'])/1000),C(av(v['et']),av(v['etb']))]
        shovCells=lambda v:[C(av(v['hang']),av(v['hangb'])),C(av(v['q']),av(v['qb'])),C(av(v['pay']),PAYLOAD_TARGET)]
        tables=[
          {'id':'lanes','title':'Loaded haul paths  (Load → Dump)','kpis':'FullHaul Cycle',
           'cols':[{'name':'Path','t':'name'},{'name':'Loads','t':'n'},{'name':'Full dist','t':'km','good':'low'},{'name':'Full time','t':'mmss','good':'low'},{'name':'Cycle time','t':'mmss','good':'low'}],
           'rows':grouped(L,lambda kv:-av(kv[1]['cyc']),laneCells,8)},
          {'id':'empty','title':'Empty haul legs  (prev dump → dig)','kpis':'EmptyHaul',
           'cols':[{'name':'Empty leg','t':'name'},{'name':'Loads','t':'n'},{'name':'Empty dist','t':'km','good':'low'},{'name':'Empty time','t':'mmss','good':'low'}],
           'rows':grouped(E,lambda kv:-av(kv[1]['et']),emptyCells,8)},
          {'id':'shovels','title':'Shovels','kpis':'Hang Queue Payload',
           'cols':[{'name':'Shovel','t':'name'},{'name':'Loads','t':'n'},{'name':'Hang','t':'mmss','good':'low'},{'name':'Queue','t':'mmss','good':'low'},{'name':'Payload','t':'ton','good':'high'}],
           'rows':grouped(S,lambda kv:-av(kv[1]['hang']),shovCells,12)},
        ]
        return {'ratio':{'empty':round(Sed),'full':round(Sfd),'val':(round(Sed/Sfd,3) if Sfd else 0)},'tables':tables}
    def agg_ophourly(pits):
        # effective units OPERATING per hour = gross operating hours (Ready+Delay) bucketed by hour ÷ 1 h.
        # Uses Statusevents (consistent with the Equipment Hours "deployed" headline); shovels combined at HIT 800 = 0.7 × BE 495.
        gohHr=defaultdict(lambda:[0.0]*12); nohHr=defaultdict(lambda:[0.0]*12); _gc=defaultdict(float)   # GOH=Ready+Delay (deployed), NOH=Ready only (productive)
        for e in sev:
            if e['Pit'] not in pits: continue
            g=AVGRP.get(e['EqmtType'])
            if not g or e['ASEStatus'] not in ('Ready','Delay'): continue
            h=num(e['Duration'])/3600.0
            d=_dtp(e['StartTime']); smin=mfs(d) if d else None
            if smin is None: smin=_gc[e['Eqmt']]
            _gc[e['Eqmt']]=max(0.0,smin)+h*60
            ready=(e['ASEStatus']=='Ready')
            s0=max(0.0,smin); en=s0+h*60; b=int(s0//60)
            while b<12 and s0<en:
                be=(b+1)*60; seg=min(en,be); frac=(seg-s0)/60.0; gohHr[g][b]+=frac
                if ready: nohHr[g][b]+=frac
                s0=seg; b+=1
        def _tr(hr): return [round(hr['Cat 797'][h],1) for h in range(12)]
        def _sh(hr): return [round(hr['Cable shovel'][h]+0.7*hr['Hydraulic shovel'][h],1) for h in range(12)]
        def _g(hr,g): return [round(hr[g][h],1) for h in range(12)]
        return {'hours':[f"{(base+i)%24:02d}" for i in range(12)],
                'trucks':_tr(gohHr),'shovels':_sh(gohHr),'trucksP':_tr(nohHr),'shovelsP':_sh(nohHr),
                'cable':_g(gohHr,'Cable shovel'),'hydraulic':_g(gohHr,'Hydraulic shovel')}
    def compute_shift_recommendations(pits, fx_data, all_loads, t1_loads):
        """Compute cycle-component and balance gap recommendations for one view (pit subset).

        Returns a list of dicts sorted by estimated tonnes-at-risk (highest first). Each dict:
          {priority, area, measure, actual_s, baseline_s, actual_label, baseline_label,
           gap_label, tonnes_at_risk, tab, detail}
        priority: 1 (High) / 2 (Medium) / 3 (Low) matching JS colour coding.
        """
        # --- helpers ---
        def _avg(seq):
            s=[x for x in seq if x is not None]; return sum(s)/len(s) if s else 0.0
        def _fmts(sec):
            m=int(abs(sec))//60; s=int(abs(sec))%60
            return ('-' if sec<0 else '')+f"{m}:{s:02d}"
        def _priority(frac_cycle):
            if frac_cycle>0.10: return 1
            if frac_cycle>0.05: return 2
            return 3

        rs=[r for r in all_loads if r['LoadPit'] in pits and not r['DumpLocation'].startswith('IN')]
        t1r=[r for r in t1_loads if r['LoadPit'] in pits and not r['DumpLocation'].startswith('IN')]
        if not t1r: return []

        # avg full truck cycle (all 8 components)
        _cyc_keys=['EmptyHaulDuration','SpotTime','LoadingTime','QueueTimeShvl',
                   'FullHaulDuration','QueueTimeDmp','DumpSpotTime','DumpingTime']
        cyc_vals=[sum(num(r[k]) for k in _cyc_keys) for r in t1r]
        avg_cycle=_avg(cyc_vals) or 1.0
        shift_tonnes=sum(num(r['Tonnage']) for r in t1r)

        recs=[]
        def _add(gap_key, actual_s, baseline_s):
            gap_s=actual_s-baseline_s
            if gap_s<=0: return  # at or under budget — no recommendation
            tonnes_at_risk=round(gap_s/avg_cycle*shift_tonnes)
            frac=gap_s/avg_cycle
            measure, area, tab, detail=gap_msg(gap_key)
            recs.append({
                'message_key':gap_key,
                'priority':_priority(frac),
                'area':area,
                'measure':measure,
                'actual_s':round(actual_s,1),
                'baseline_s':round(baseline_s,1),
                'actual_label':_fmts(actual_s),
                'baseline_label':_fmts(baseline_s),
                'gap_label':'+'+_fmts(gap_s),
                'tonnes_at_risk':tonnes_at_risk,
                'tab':tab,
                'detail':detail,
            })

        # --- 1. Full Haul vs haul-curve expected ---
        fh_gaps=[num(r['FullHaulDuration'])-num(r['FullExpectedDuration'])
                 for r in t1r if num(r['FullHaulDuration'])>0 and num(r['FullExpectedDuration'])>0]
        avg_fh_actual=_avg([num(r['FullHaulDuration']) for r in t1r if num(r['FullHaulDuration'])>0])
        avg_fh_expected=_avg([num(r['FullExpectedDuration']) for r in t1r if num(r['FullExpectedDuration'])>0])
        if fh_gaps and _avg(fh_gaps)>30:
            _add('FULL_HAUL_DURATION',avg_fh_actual,avg_fh_expected)

        # --- 2. Empty Haul vs expected ---
        eh_gaps=[num(r['EmptyHaulDuration'])-num(r['EmptyExpectedDuration'])
                 for r in t1r if num(r['EmptyHaulDuration'])>0 and num(r['EmptyExpectedDuration'])>0]
        avg_eh_actual=_avg([num(r['EmptyHaulDuration']) for r in t1r if num(r['EmptyHaulDuration'])>0])
        avg_eh_expected=_avg([num(r['EmptyExpectedDuration']) for r in t1r if num(r['EmptyExpectedDuration'])>0])
        if eh_gaps and _avg(eh_gaps)>30:
            _add('EMPTY_HAUL_DURATION',avg_eh_actual,avg_eh_expected)

        # --- 3. Shovel Hang Time vs budget ---
        # Budget hang = budget cycle − spot_b − load_b (per pit/material; use tonnage-weighted average)
        hang_bud_sum=0.0; hang_bud_n=0
        for r in rs:
            pit=r['LoadPit']; mat=r['MaterialGroupName']
            f_mat=fx_data[pit].get(mat); s=stype(r['Excav'])
            if not f_mat or not s: continue
            tp=shTPNOH[pit].get((s,mat),0.0)
            if tp<=0: continue
            spot_b=f_mat['Spot']*60; load_b=LMIN(f_mat,SHVTYPE.get(s,'Average'))*60
            c_b=PAYLOAD_TARGET*3600.0/tp; hang_b=max(0.0,c_b-spot_b-load_b)
            hang_bud_sum+=hang_b; hang_bud_n+=1
        avg_hang_actual=_avg([num(r['HangTime']) for r in rs if num(r['HangTime'])>=0])
        avg_hang_bud=hang_bud_sum/hang_bud_n if hang_bud_n else 180.0
        if avg_hang_actual>180 and avg_hang_actual>avg_hang_bud:
            _add('SHOVEL_HANG_TIME',avg_hang_actual,avg_hang_bud)

        # --- 4. Dump Queue (QueueTimeDmp) vs budget DumpIdle ---
        dq_bud_vals=[]
        for r in t1r:
            pit=r['LoadPit']; mat=r['MaterialGroupName']
            f_mat=fx_data[pit].get(mat)
            if f_mat: dq_bud_vals.append(f_mat['DumpIdle']*60)
        avg_dq_actual=_avg([num(r['QueueTimeDmp']) for r in t1r])
        avg_dq_bud=_avg(dq_bud_vals) if dq_bud_vals else 60.0
        if avg_dq_actual>avg_dq_bud+30:
            _add('DUMP_QUEUE_TIME',avg_dq_actual,avg_dq_bud)

        # --- 5. Loading Time vs budget ---
        lt_bud_vals=[]
        for r in t1r:
            pit=r['LoadPit']; mat=r['MaterialGroupName']
            f_mat=fx_data[pit].get(mat); s=stype(r['Excav'])
            if f_mat and s: lt_bud_vals.append(LMIN(f_mat,SHVTYPE.get(s,'Average'))*60)
        avg_lt_actual=_avg([num(r['LoadingTime']) for r in t1r if num(r['LoadingTime'])>0])
        avg_lt_bud=_avg(lt_bud_vals) if lt_bud_vals else 180.0
        if avg_lt_actual>avg_lt_bud*1.10:
            _add('LOADING_TIME',avg_lt_actual,avg_lt_bud)

        # --- 6. Spot Time vs budget ---
        sp_bud_vals=[]
        for r in t1r:
            pit=r['LoadPit']; mat=r['MaterialGroupName']
            f_mat=fx_data[pit].get(mat)
            if f_mat: sp_bud_vals.append(f_mat['Spot']*60)
        avg_sp_actual=_avg([num(r['SpotTime']) for r in t1r if num(r['SpotTime'])>0])
        avg_sp_bud=_avg(sp_bud_vals) if sp_bud_vals else 72.0
        if avg_sp_actual>avg_sp_bud+20:
            _add('SPOT_TIME',avg_sp_actual,avg_sp_bud)

        # --- 7. Truck-Shovel Balance (hang/queue ratio) ---
        avg_queue_shvl=_avg([num(r['QueueTimeShvl']) for r in t1r])
        if avg_queue_shvl>0:
            ratio=avg_hang_actual/avg_queue_shvl
            if ratio>2.0:
                # encode as a gap: ratio excess expressed in hang seconds above a "balanced" target of 1.5×queue
                balanced_hang=avg_queue_shvl*1.5
                gap_hang=avg_hang_actual-balanced_hang
                if gap_hang>0:
                    tonnes_risk=round(gap_hang/avg_cycle*shift_tonnes)
                    frac=gap_hang/avg_cycle
                    measure, area, tab, detail=gap_msg('HANG_QUEUE_RATIO')
                    recs.append({
                        'message_key':'HANG_QUEUE_RATIO',
                        'priority':_priority(frac),
                        'area':area,
                        'measure':measure,
                        'actual_s':round(avg_hang_actual,1),
                        'baseline_s':round(balanced_hang,1),
                        'actual_label':f"ratio {ratio:.1f}× (hang {_fmts(avg_hang_actual)}, queue {_fmts(avg_queue_shvl)})",
                        'baseline_label':'ratio ≤ 1.5×',
                        'gap_label':f"+{ratio-1.5:.1f}\u00d7 over balanced",
                        'tonnes_at_risk':tonnes_risk,
                        'tab':tab,
                        'detail':detail,
                    })

        recs.sort(key=lambda x:-x['tonnes_at_risk'])
        return recs
    views={}
    for name,pits in [('MRM',['MRM']),('JPM',['JPM']),('Combined',PITS)]:
        vw={'haulage':agg_haul(pits),'loading':agg_load(pits),'truckBalance':agg_tb(pits),
                     'trucksWF':agg_wf(pits),'shovelWF':agg_shov(pits),'shovelWF2':agg_shov2(pits),'haulCycles':agg_flows(pits),
                     'availability':agg_avail(pits),'dumpTimeline':agg_dumptl(pits),'lube':agg_lube(pits),
                     'delaysStandby':delaysStandby(pits),'hourlyPerf':compute_hourlyPerf(pits),'shovelProd':compute_shovelProd(pits),'truckProd':compute_truckProd(pits),'shiftStats':compute_shiftStats(pits),'analytics':compute_analytics(pits)}
        vw['fleetMatch']=agg_fleetMatch(pits,vw['trucksWF'],vw['shovelWF2'],vw['truckBalance'])
        vw['shiftRecs']=compute_shift_recommendations(pits,fx,loads,t1)
        vw['opDeployed']=agg_ophourly(pits)
        views[name]=vw
    # ---------- appendix: all target / budget numbers used, for this shift ----------
    def _oe(nohc,gohc,p): g=bud(p,gohc); return (bud(p,nohc)/g*100) if g>0 else None
    avail=[]
    for p in PITS:
        avail.append({'pit':p,
            'PA797':round(bud(p,'PA797')*100,1),'UA797':round(bud(p,'UA797')*100,1),'OE797':round(bud(p,'OE797')*100,1),
            'NOH797':round(bud(p,'NOH797'),1),'GOH797':round(bud(p,'GOH797'),1),'TPNOH797':round(bud(p,'TPNOH797')),
            'PA495':round(bud(p,'PA495')*100,1),'UA495':round(bud(p,'UA495')*100,1),'OE495':(round(_oe('NOH495','GOH495',p),1) if _oe('NOH495','GOH495',p) is not None else None),
            'NOH495':round(bud(p,'NOH495'),1),'GOH495':round(bud(p,'GOH495'),1),'TPNOHCable':round(bud(p,'TPNOHCable')),
            'PA8000':round(bud(p,'PA8000')*100,1),'UA8000':round(bud(p,'UA8000')*100,1),'OE8000':(round(_oe('NOH8000','GOH8000',p),1) if _oe('NOH8000','GOH8000',p) is not None else None),
            'NOH8000':round(bud(p,'NOH8000'),1),'GOH8000':round(bud(p,'GOH8000'),1),'TPNOHHydro':round(bud(p,'TPNOHHydro'))})
    fixed=[]
    for p in PITS:
        for mat in ['Ore','Waste']:
            f=fx[p].get(mat)
            if f: fixed.append({'pit':p,'mat':mat,'queue':round(f['Queue'],3),'spot':round(f['Spot'],3),'load':round(f['Load'],3),
                'loadCable':round(LMIN(f,'Cable'),3),'loadHydro':round(LMIN(f,'Hydraulic'),3),
                'dumpidle':round(f['DumpIdle'],3),'dumping':round(f['Dumping'],3)})
    _psum=defaultdict(lambda:[0.0,0])
    for r in loads:
        s=stype(r['Excav'])
        if not s: continue
        mat=r['MaterialGroupName']; T=num(r['Tonnage'])
        if shTPNOH[r['LoadPit']].get((s,mat),0.0)<=0 or T<=0: continue
        _psum[(s,r['LoadPit'],mat)][0]+=T; _psum[(s,r['LoadPit'],mat)][1]+=1
    shovelCyc=[]
    for (s,p,mat),(ts,cn) in sorted(_psum.items()):
        Prep=ts/cn; tp=shTPNOH[p].get((s,mat),0.0)
        f=fx[p].get(mat) or {'Spot':0,'Load':0}; spot_b=f['Spot']*60; load_b=LMIN(f,SHVTYPE.get(s,'Average'))*60
        c_b=PAYLOAD_TARGET*3600.0/tp; hang_b=max(0.0,c_b-spot_b-load_b)   # budget cycle for the 361 t target
        shovelCyc.append({'type':('BE 495B' if s=='BE495' else 'HIT 800'),'pit':p,'mat':mat,'tpnoh':round(tp),
            'prep':round(Prep,1),'spot_b':round(spot_b),'load_b':round(load_b),'hang_b':round(hang_b),'c_b':round(c_b)})
    used=set((r['LoadPit'],r['MaterialGroupName'],hd_of(r)) for r in t1)   # HD buckets actually hauled this shift
    curveRows=[]
    for p in PITS:
        for (mat,hd),c in sorted(curve[p].items()):
            if (p,mat,hd) in used:
                curveRows.append({'pit':p,'mat':mat,'hd':hd,'tpnoh':round(c['TPNOH']),'cycle':round(c['Cycle'],2),'travel':round(c['Travel'],2)})
    plan=[{'pit':p,'POre':round(bud(p,'POre')),'PWst':round(bud(p,'PWst')),'NPOre':round(bud(p,'NPOre')),'NPWst':round(bud(p,'NPWst'))} for p in PITS]
    appendix={'month':mabbr,'date':date,'shiftName':sname,'ffull':round(FFULL,3),'elapsed':round(elapsed,2),
        'payloadTarget':PAYLOAD_TARGET,'nohFloorMin':round(NOH_FLOOR_S/60,2),
        'avail':avail,'fixed':fixed,'shovelCyc':shovelCyc,'curve':curveRows,'plan':plan}
    # ---- Blend page: per shovel × grade-block, hourly tonnes + block assay (Bit/Fines/D50), weighted by tonnes ----
    _bl=defaultdict(lambda:{'ton':[0.0]*12,'gw':[0.0,0.0,0.0],'gwt':0.0})   # (pit,shovel,block) -> hourly ton + Σ(grade·ton)
    for r in loads:
        if not (r.get('DumpLocation') or '').startswith('CR'): continue   # crusher feed only
        d=_dtp(r.get('LoadingTimestamp')); mn=mfs(d) if d else None
        if mn is None or mn<0 or mn>=720: continue
        ton=num(r.get('Tonnage'))
        if ton<=0: continue
        hh=int(mn//60)
        e=_bl[(r.get('LoadPit') or '', r.get('Excav') or '', (r.get('Grade') or '').strip() or '—')]
        e['ton'][hh]+=ton
        bit=num(r.get('Bit'))
        if bit>0:   # valid ore-grade load
            e['gw'][0]+=bit*ton; e['gw'][1]+=num(r.get('Fines'))*ton; e['gw'][2]+=num(r.get('D50'))*ton; e['gwt']+=ton
    blendRows=[]
    for (pit,shov,blk),e in _bl.items():
        w=e['gwt']
        blendRows.append({'pit':pit,'shovel':shov,'block':blk,'valid':w>0,
            'bit':round(e['gw'][0]/w,2) if w else 0,'fines':round(e['gw'][1]/w,2) if w else 0,'d50':round(e['gw'][2]/w) if w else 0,
            'ton':[round(x) for x in e['ton']]})
    blendRows.sort(key=lambda r:(r['shovel'],r['block']))
    blend={'base':base,'rows':blendRows}
    smeta={'shift':sname,'fullLegFrac':round(FFULL,3),'vFull':round(v_full*3.6,1),'vEmpty':round(v_empty*3.6,1),'elapsed':round(elapsed,2),'appendix':appendix,'blend':blend}
    return views,smeta

# ==================== LP dispatch-optimizer solves (LP Solutions tab) ====================
# ShovelCoverageFactors.csv is the LIVE optimizer feed: an edge list per solve (dId) for the single shift
# that was current when the CSVs were refreshed. Each row is a directed leg dNodeFrom->dNodeTo, LOADED
# (dig shovel -> dump/crusher, dMatType != 'Empty', carrying dPathRate/dLoadRate/dLPCoverage) or BACKHAUL
# (dMatType == 'Empty', ignored here). The shovel node matches S###/S####. We emit ONE global LP_LIVE
# (the file's shift) with the latest solve (Current LP) and per-shovel hourly averages (Shift LP), since
# the live LP shift can differ from the historical production shift the user is browsing.
_SHOV_NODE_RE=re.compile(r'^S\d{3,4}$')
def _snap_start(sid):
    yy=int(sid[0:2]);mm=int(sid[2:4]);dd=int(sid[4:6]);seq=int(sid[6:9])
    return _dtm(2000+yy,mm,dd,(6 if seq%2==1 else 18),0,0)
def _lp_live():
    solves={}; shiftId=''
    try:
        with open(_resolve_file('ShovelCoverageFactors.csv'),encoding='utf-8-sig',newline='') as f:
            rd=csv.reader(f); hdr=[_normcol(c) for c in next(rd)]; ix={c:i for i,c in enumerate(hdr)}
            def g(row,c): j=ix.get(c); return row[j] if (j is not None and j<len(row)) else ''
            for row in rd:
                if not row or len(row)<len(hdr): continue
                did=g(row,'dId')
                if not did: continue
                shiftId=g(row,'dShiftId') or shiftId
                nf,nt=g(row,'dNodeFrom'),g(row,'dNodeTo')
                shov = nf if _SHOV_NODE_RE.match(nf or '') else (nt if _SHOV_NODE_RE.match(nt or '') else None)
                if not shov: continue
                dump = nt if shov==nf else nf
                isload = (g(row,'dLocationType')=='DigLoc')                    # DigLoc = shovel→dump (loaded); else = dump→shovel (backhaul)
                s=solves.get(did)
                if s is None: s=solves[did]={'time':g(row,'dLPTime'),'shiftId':g(row,'dShiftId'),'edges':[]}
                s['edges'].append({'calcId':did,'dir':('load' if isload else 'back'),'pit':g(row,'dPit'),'shovel':shov,'dump':dump,'digLoc':g(row,'dLocation'),
                                   'mat':g(row,'dMatType'),'path':num(g(row,'dPathRate')),'priority':(g(row,'dPriority') or '').strip(),
                                   'load':num(g(row,'dLoadRate')),'cov':num(g(row,'dLPCoverage')),
                                   'dig':num(g(row,'dDigRate')),'grade':g(row,'dGrade'),
                                   'bit':num(g(row,'dBit')),'fines':num(g(row,'dFines')),'d50':num(g(row,'dD50'))})
    except Exception:
        return {}
    if not solves: return {}
    _byshift=defaultdict(list)
    for _s in solves.values(): _byshift[_s.get('shiftId') or ''].append(_s)
    def _one(shiftId, order):
        order=sorted(order,key=lambda s:(s['time'] or '')); latest=order[-1]
        group_cov={}
        for e in latest['edges']:
            if e['dir']!='load': continue
            key=(e.get('calcId') or '', e.get('shovel') or '')
            grp_acc=group_cov.setdefault(key, {'path':0.0,'dig':0.0})
            grp_acc['path'] += e['path']
            if e['dig']>grp_acc['dig']: grp_acc['dig'] = e['dig']
        current=[]
        for e in latest['edges']:
            if e['dir']!='load': continue
            key=(e.get('calcId') or '', e.get('shovel') or '')
            grp=group_cov.get(key,{})
            node_rate=grp.get('dig',0.0) or 0.0
            cov=0.0 if node_rate<=0 else round((grp.get('path',0.0) / node_rate),3)
            current.append({'pit':e['pit'],'excav':e['shovel'],'dig':e['digLoc'],'dump':e['dump'],'mat':e['mat'],'priority':e['priority'],
                            'pathRate':round(e['path']),'loadRate':round(e['load']),'cov':cov,
                            'digRate':round(e['dig']),'grade':e['grade'],'bit':round(e['bit'],2),'fines':round(e['fines'],2),'d50':round(e['d50']),
                            'poe':(round(e['load']/e['dig'],3) if e['dig']>0 else None)})
        # backhaul (dump→shovel, non-DigLoc) — path rate only, from the most recent solve that has such legs
        backhaul=[]; backTime=''
        for s in reversed(order):
            bk=[e for e in s['edges'] if e['dir']=='back' and e['path']>0]
            if bk:
                backhaul=[{'pit':e['pit'],'dump':e['dump'],'excav':e['shovel'],'path':round(e['path']),'mat':e['mat']} for e in bk]
                backTime=s['time']; break
        start=_snap_start(shiftId)
        # actual tonnes per shovel per hour (from AllLoadsDumps) for this LP shift — for the Shift LP "actual t/h" line
        actAcc=defaultdict(lambda:[0.0]*12)
        for r in loads_all:
            if _sid(r)!=shiftId: continue
            d=_dtp(r.get('LoadingTimestamp'))
            if not d: continue
            hr=int((d-start).total_seconds()//3600)
            if hr<0 or hr>11: continue
            actAcc[r.get('Excav') or ''][hr]+=num(r.get('Tonnage'))
        hacc=defaultdict(lambda:defaultdict(lambda:[0.0,0.0,0]))     # hr->shov->[Spath,Scov,cnt]
        shPit={}; matAcc=defaultdict(lambda:defaultdict(float))
        # Shift LP hourly uses ONLY the current shift's solves — the coverage file may hold many prior shifts,
        # and bucketing those by hour-from-this-shift-start would clamp them all into hour 0.
        cur_order=order   # order is already this shift's solves
        # duration weighting: each solve is weighted by the seconds it stays in effect (time until the next solve).
        # The final solve has no successor, so it is weighted by the median in-effect duration.
        _tms=[_dtp(s['time']) for s in cur_order]
        _dts=[((_tms[i+1]-_tms[i]).total_seconds() if (i+1<len(cur_order) and _tms[i] and _tms[i+1]) else 0.0) for i in range(len(cur_order))]
        _pos=sorted(d for d in _dts if d>0); _medDt=(_pos[len(_pos)//2] if _pos else 1.0)
        if _dts: _dts[-1]=_medDt
        for i,s in enumerate(cur_order):
            tt=_tms[i]
            if not tt: continue
            dt=_dts[i]                                 # seconds this solve was the active plan (duration weight)
            hr=int((tt-start).total_seconds()//3600); hr=0 if hr<0 else (11 if hr>11 else hr)
            per={}
            for e in s['edges']:
                if e['dir']!='load': continue          # shift LP hourly = dig activity only
                a=per.get(e['shovel'])
                if a is None: a=per[e['shovel']]={'p':0.0,'l':0.0,'pit':e['pit']}
                # sum path rates across the shovel's legs, but the dig/load rate is a single node value the LP
                # repeats on each leg — take it once (max) so coverage = Σpath ÷ node rate (not ÷ Σ of repeats)
                a['p']+=e['path']; a['l']=max(a['l'],e['load']); matAcc[e['shovel']][e['mat']]+=e['path']
            for shov,a in per.items():
                cov=a['p']/a['l'] if a['l']>0 else 0.0
                h=hacc[hr][shov]; h[0]+=a['p']*dt; h[1]+=cov*dt; h[2]+=dt; shPit[shov]=a['pit']   # Σ(x·Δt), Σ(Δt)
        shovset=sorted(set().union(*[set(hacc[hr].keys()) for hr in hacc])) if hacc else []
        seq=int(shiftId[6:9]) if len(shiftId)>=9 else 1; base=6 if seq%2==1 else 18
        hours=[f"{(base+i)%24:02d}:00" for i in range(12)]
        shiftLP=[]
        for shov in shovset:
            hourly=[]
            for hr in range(12):
                cell=hacc.get(hr,{}).get(shov)
                act=round(actAcc.get(shov,[0.0]*12)[hr])
                hourly.append({'th':int(round(cell[0]/cell[2])),'cov':round(cell[1]/cell[2],2),'act':act} if (cell and cell[2]>0) else None)
            mats=matAcc.get(shov,{}); mat=max(mats,key=mats.get) if mats else ''
            shiftLP.append({'excav':shov,'pit':shPit.get(shov,''),'mat':mat,'hourly':hourly})
        # shovels that physically LOADED this shift but are NOT in the LATEST solve — i.e. not in the optimizer's
        # current plan (the one the Current LP sankey shows). Catches both shovels that never entered the feed AND
        # shovels that were disabled / dropped out mid-shift (e.g. a prio-256 shovel that kept loading).
        lp_shov=set(e['shovel'] for e in latest['edges'])
        # Status of each shovel AT the latest LP-solve time (the moment the Current LP reflects). A shovel that is
        # DOWN then is legitimately excluded from the LP (broke down) and is NOT flagged; only shovels that are up
        # (Ready/Delay/Standby) yet absent from the current plan are real optimizer gaps.
        _lt=_dtp(latest['time']); _ltts=_lt.timestamp() if _lt else None
        _seg=defaultdict(list)
        for e in status_all:
            if _sid(e)!=shiftId: continue
            _st=_dtp(e.get('StartTime'))
            if not _st: continue
            _seg[e.get('Eqmt') or ''].append((_st.timestamp(),_st.timestamp()+num(e.get('Duration')),e.get('ASEStatus') or ''))
        def _statAt(eq):
            if _ltts is None: return ''
            for a,b,s in _seg.get(eq,[]):
                if a<=_ltts<b: return s
            prev=[(a,s) for a,b,s in _seg.get(eq,[]) if a<=_ltts]
            return max(prev)[1] if prev else ''
        load_cnt=defaultdict(int); load_pit={}
        for r in loads_all:
            if _sid(r)!=shiftId: continue
            ex=r.get('Excav') or ''
            if stype(ex):                     # primary BE495/HIT8000 fleet only
                load_cnt[ex]+=1; load_pit[ex]=r.get('LoadPit') or ''
        missingShovels=[]
        for k,v in sorted(load_cnt.items(),key=lambda x:-x[1]):
            if k in lp_shov or v<5: continue
            stt=_statAt(k)
            if stt=='Down': continue          # down at solve time → legitimately out of the LP, not a gap
            missingShovels.append({'excav':k,'loads':v,'pit':load_pit.get(k,''),'status':stt})
        _statOf={m['excav']:m['status'] for m in missingShovels}
        # actual shovel→dump loading legs for those missing shovels (to overlay on the sankey as "not in optimizer"
        # tonnes). Rate = tonnes ÷ shift elapsed hours so the ribbon width is comparable to the LP path rates.
        _lts=[_dtp(r.get('LoadingTimestamp')) for r in loads_all if _sid(r)==shiftId]; _lts=[t for t in _lts if t]
        _elapH=max(0.1,(max(_lts)-start).total_seconds()/3600.0) if _lts else 12.0
        _missSet=set(m['excav'] for m in missingShovels)
        _mAcc=defaultdict(lambda:[0.0,defaultdict(float),''])   # (excav,dump)->[tons,{mat:tons},pit]
        for r in loads_all:
            if _sid(r)!=shiftId: continue
            ex=r.get('Excav') or ''
            if ex not in _missSet: continue
            dump=r.get('DumpLocation') or ''
            if not dump or dump.upper().startswith('IN'): continue
            t=num(r.get('Tonnage')); a=_mAcc[(ex,dump)]; a[0]+=t; a[1][r.get('MaterialGroupName') or '']+=t; a[2]=r.get('LoadPit') or a[2]
        missingLegs=[{'excav':ex,'dump':dump,'pit':a[2],'mat':(max(a[1],key=a[1].get) if a[1] else ''),
                      'tons':round(a[0]),'rate':round(a[0]/_elapH),'status':_statOf.get(ex,'')} for (ex,dump),a in _mAcc.items() if a[0]>0]
        return {'shiftId':shiftId,'lpTime':latest['time'],'nSolves':len(order),'current':current,'backhaul':backhaul,'backTime':backTime,'shiftLP':shiftLP,'hours':hours,'missingShovels':missingShovels,'missingLegs':missingLegs}
    out={}
    for _shk,_sh in _byshift.items():
        if not _shk: continue
        try: out[_shk]=_one(_shk,_sh)
        except Exception: pass
    return out
LP_BY_SHIFT=_lp_live()

# ============================ current-state snapshot (per shift) ============================
# Who is hard-Down at the shift's most recent LP-solve instant (falls back to shift-end for shifts with
# no LP data). Down-equipment lists come from Statusevents.csv; the snapshot *instant* now comes from the
# LP solves parsed above (dLPTime), replacing the old LPCalcId-keyed snapshot picker.
_SHOV_TYPES={'BE 495B','HIT 800','Hit ZX8','HIT 250','HIT 5600','HIT 1900','Komatsu'}
_TRUCK_TYPES={'Cat 797','Cat 785','Cat 789','Cat 793','Cat 740','Cat 770'}
# raw EqmtType strings are truncated (~7 chars) in the export → clean display labels
_EQ_LABEL={'HIT 800':'HIT8000','HIT 250':'HIT 2500','Komatsu':'Komatsu 3000',
           'Cat D11':'Cat D11T','Cat D8T':'Cat D8','Cat 854':'Cat 854K'}
def _snap_start(sid):
    yy=int(sid[0:2]);mm=int(sid[2:4]);dd=int(sid[4:6]);seq=int(sid[6:9])
    return _dtm(2000+yy,mm,dd,(6 if seq%2==1 else 18),0,0)
def _build_snapshots():
    st_by=defaultdict(list)
    for e in status_all:
        s=_sid(e)
        if s: st_by[s].append(e)
    snaps={}
    for sid in set(st_by):
        start=_snap_start(sid)
        snap_mfs=720.0   # historical shift → "current" = end of shift
        segs=defaultdict(list)
        for e in st_by.get(sid,[]):
            dtq=_dtp(e.get('StartTime') or e.get('TimeStamp') or '')
            if not dtq: continue
            segs[e.get('Eqmt','')].append((( dtq-start).total_seconds()/60.0, num(e.get('Duration'))/60.0,
                e.get('ASEStatus',''),(e.get('Reason') or '').strip().title(),e.get('TimeCat',''),e.get('EqmtType',''),e.get('Pit',''),e.get('Unit','')))
        shov=[];truck=[];aux=[]
        for eq,sl in segs.items():
            sl.sort(); cur=None
            for s in sl:
                if s[0]<=snap_mfs<s[0]+s[1]: cur=s; break
            if cur is None and sl and snap_mfs>=sl[-1][0]: cur=sl[-1]
            if cur and cur[2] in ('Down','Delay','Standby'):
                rec={'eq':eq,'type':_EQ_LABEL.get(cur[5],cur[5]),'status':cur[2],'reason':(cur[3] or cur[4] or cur[2]),'cat':cur[4],'min':int(round(snap_mfs-cur[0])),'pit':cur[6]}
                unit=(cur[7] or '').strip().casefold()
                (truck if unit=='truck' else shov if unit=='shovel' else aux).append(rec)
        for a in (shov,truck,aux): a.sort(key=lambda r:-r['min'])
        snaps[sid]={'mfs':int(round(snap_mfs)),'shov':shov,'truck':truck,'aux':aux}
    return snaps
SNAPSHOTS=_build_snapshots()

byShift={}; shiftlist=[]
for sm in SHIFTS:
    vw,mt=build_shift(sm)
    byShift[sm['id']]={'views':vw,'meta':mt,'snapshot':SNAPSHOTS.get(sm['id'],{})}
    shiftlist.append({'id':sm['id'],'name':sm['name'],'crew':sm.get('crew','')})

# ---- monthly run-rate: avg tonnes/shift future shifts must average to still hit the month's ore+waste budget ----
_MB=defaultdict(lambda:defaultdict(float)); _MN=defaultdict(set)   # month -> pit -> Σ(POre+PWst) ; month -> {(date,shift)}
for p in PITS:
    for (mo,da,sh),row in PITBUD[p].items():
        _MB[mo][p]+=num(row.get('POre',0))+num(row.get('PWst',0)); _MN[mo].add((da,sh))
_VPITS={'MRM':['MRM'],'JPM':['JPM'],'Combined':['MRM','JPM']}
for sid in byShift:
    mo=str(int(sid[2:4]))
    monthids=sorted(s for s in byShift if str(int(s[2:4]))==mo)   # data shifts this month, chronological
    total=len(_MN.get(mo,()))                                     # total budgeted shifts in the month
    elapsed=len([s for s in monthids if s<=sid])
    future=total-elapsed
    for vn,vp in _VPITS.items():
        v=byShift[sid]['views'].get(vn)
        if not v or 'analytics' not in v: continue
        mbud=sum(_MB[mo][p] for p in vp)
        act=sum(byShift[s]['views'][vn]['trucksWF']['actual'] for s in monthids
                if s<=sid and byShift[s]['views'].get(vn) and byShift[s]['views'][vn].get('trucksWF'))
        req=(max(0.0,mbud-act)/future) if future>0 else None
        c=v['analytics']['cumulative']
        c['reqFuture']=round(req) if req is not None else None
        c['monthBudget']=round(mbud); c['monthActual']=round(act); c['futureShifts']=future; c['monthShifts']=total

out={'meta':{'generated':datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),'payloadTarget':PAYLOAD_TARGET},
     'shifts':shiftlist,'defaultShift':(SHIFTS[0]['id'] if SHIFTS else None),'byShift':byShift,'locCoords':loc_coords,
     'roadCells':road_cells,'roadCell':ROAD_CELL,'baseMap':base_map,'lpByShift':LP_BY_SHIFT}
json.dump(out,open(f'{BASE}/dashboard_data.json','w',encoding='utf-8'),indent=1)
print("shifts:",[s['name'] for s in SHIFTS])
for sid in byShift:
    v=byShift[sid]['views']['Combined']
    print("  %s haul %.1f%% load %.1f%% tb %.1f%%"%(sid,v['haulage']['score'],v['loading']['score'],v['truckBalance']['pct']))

# ============================ HTML ============================
HTML = r'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>Albian Mine - Haulage Dashboard</title>
<style>
:root{--bg:#eef0f4;--card:#fff;--ink:#2b2f36;--muted:#7c828c;--line:#e3e6ec;
 --green:#4caf50;--red:#e23b32;--blue:#3f51b5;--purpleband:#3a3f9e;--head:#5c6470;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.4 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{width:100%;margin:0 auto;padding:8px 20px 14px}
.topbar{display:flex;flex-direction:column;gap:8px;margin-bottom:6px}
.title-wrap{text-align:center}
.title{font-size:18px;font-weight:700}
.sub{color:var(--muted);font-size:12px}
.topcontrols{display:flex;justify-content:flex-end;align-items:center;gap:10px;flex-wrap:wrap}
.toggle{margin-left:0;display:inline-flex;border:1px solid #cfd4dd;border-radius:8px;overflow:hidden}
.shiftnav{margin-left:0;display:inline-flex;align-items:center;gap:4px}
.shiftnav select{height:32px;border:1px solid #cfd4dd;border-radius:8px;padding:0 8px;font-size:13px;font-weight:600;color:#3a3f46;background:#fff;cursor:pointer}
.shiftnav button{width:30px;height:32px;border:1px solid #cfd4dd;background:#fff;border-radius:8px;font-size:16px;line-height:1;color:#566;cursor:pointer}
.shiftnav button:disabled{opacity:.4;cursor:default}
.sidenav-controls{display:flex;flex-direction:column;gap:8px;padding:0 6px 12px;margin-bottom:4px;border-bottom:1px solid #e4e8ef}
.sidenav-controls .shiftnav{display:flex;width:100%}
.sidenav-controls .shiftnav select{flex:1;min-width:0}
.sidenav-controls .toggle{width:100%}
.sidenav-controls .toggle button{flex:1;padding:7px 8px;font-size:13px}
.ovsection{position:relative}
.updated{position:absolute;top:12px;right:14px;padding:2px 10px;border-radius:10px;background:#eef2f7;border:1px solid #d7dee8;color:#5a6472;font-size:11px;font-weight:600;white-space:nowrap}
.page-updated{z-index:2}
.owbtn{margin:8px 0;border:1px solid #cfd4dd;background:#fff;border-radius:8px;padding:7px 14px;font-weight:600;color:var(--blue);cursor:pointer;font-size:12.5px}
.owbtn:hover{background:#f4f6fa}
.owbtn.on{background:#e8eef8;border-color:#3f51b5;color:#243b8a}
.opModeBtn{margin:2px 4px 2px 0;padding:4px 12px}
.mmlabels{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);padding:3px 2px 0}
#balanceRow{margin-bottom:14px}
.toggle button{border:0;background:#fff;padding:7px 16px;font-weight:600;cursor:pointer;color:#566}
.toggle button.on{background:var(--blue);color:#fff}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:14px}
.card{background:var(--card);border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:12px 14px;cursor:pointer}
.card h3{margin:0 0 8px;font-size:13px;display:flex;justify-content:space-between;align-items:center;color:#3a3f46}
.pct{font-size:20px;font-weight:800}
.bar{height:26px;border-radius:5px;background:#d7dbe2;position:relative;overflow:hidden}
.bar>span{position:absolute;top:0;left:0;height:100%;border-radius:5px}
.bar .marker{position:absolute;top:-3px;width:0;height:32px;border-left:2px solid #2b2f36}
.kv{display:flex;justify-content:space-between;padding:5px 2px;border-top:1px solid var(--line);font-size:12px}
.kv b{font-variant-numeric:tabular-nums}
.label-center{text-align:center;font-weight:700;color:#444;padding:14px 0 4px}
.section{background:var(--card);border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:12px 14px;margin-bottom:14px}
.section h2{margin:0 0 4px;font-size:14px}
.badges{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 10px}
.badge{background:#f4f6fa;border:1px solid var(--line);border-radius:20px;padding:4px 12px;font-size:12px}
.badge b{color:var(--blue)}
table.wf{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
table.wf th{font-size:11px;color:var(--muted);text-align:right;padding:3px 6px;font-weight:600}
table.wf td{padding:2px 6px;font-size:12px;border-bottom:1px solid #f0f2f6}
table.wf td.name{text-align:left;white-space:nowrap}
table.wf td.val{text-align:right;width:78px}
.chartcell{width:62%;position:relative}
.track{position:relative;height:20px}
.seg{position:absolute;top:2px;height:16px;border-radius:2px}
.seg.gain{background:var(--green)} .seg.loss{background:var(--red)}
.seg.anchor{background:var(--purpleband)} .seg.actual{background:var(--blue)} .seg.resid{background:#b0762f}
.rowlabel{position:absolute;top:1px;font-size:10px;color:#333;white-space:nowrap}
.axis{position:relative;height:16px;margin-top:2px;border-top:1px solid var(--line)}
.axis span{position:absolute;font-size:10px;color:var(--muted);transform:translateX(-50%);top:2px}
.drill{display:none;background:#f8f9fc;border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-top:8px}
.drill.open{display:block}
.drill table{width:100%;border-collapse:collapse;font-size:11.5px;font-variant-numeric:tabular-nums}
.drill th,.drill td{padding:3px 6px;border-bottom:1px solid #eef0f4;text-align:right}
.drill th:first-child,.drill td:first-child{text-align:left}
.foot{color:var(--muted);font-size:11px;margin-top:6px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:820px){.cards,.split{grid-template-columns:1fr}}
h4.mini{margin:10px 0 4px;font-size:12px;color:#555}
/* waterfall v2 */
table.wf td.lead{text-align:right;width:96px;color:#566;font-size:11px;white-space:nowrap}
table.wf td.lead .ta{color:#2b2f36;font-weight:600}
.zero{position:absolute;top:-2px;height:22px;border-left:1px dashed #9aa0ab}
.axis2{position:relative;height:14px;margin-top:2px}
.axis2 span{position:absolute;font-size:10px;color:var(--muted);transform:translateX(-50%);top:1px}
/* comparison bar graphs */
.gbar{display:flex;align-items:center;gap:8px;margin:3px 0}
.glabel{width:150px;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left}
.gtrack{position:relative;flex:1;height:16px;background:#e6e9ef;border-radius:3px;overflow:hidden}
.gact{position:absolute;left:0;top:0;height:100%;border-radius:3px}
.gtxt{position:absolute;right:6px;top:0;font-size:10px;line-height:16px;color:#2b2f36;font-variant-numeric:tabular-nums}
.glegend{font-size:10.5px;color:var(--muted);margin:2px 0 6px}
.dg2{display:grid;grid-template-columns:120px 1fr 1fr;gap:6px;align-items:center;font-size:11px;margin:2px 0}
.dg2 .mh{font-weight:600;color:#566;text-align:center}
.mini-track{position:relative;height:14px;background:#eef0f4;border-radius:2px}
.mini-seg{position:absolute;top:1px;height:12px;border-radius:2px}
.lanewrap{max-height:230px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin-bottom:8px}
.lanetab{width:100%;border-collapse:collapse;font-size:11.5px;font-variant-numeric:tabular-nums}
.lanetab th{position:sticky;top:0;background:#f4f6fa;font-size:10.5px;color:var(--muted);padding:5px 7px;text-align:right}
.lanetab th:first-child,.lanetab td:first-child{text-align:left}
.lanetab td{padding:4px 7px;border-bottom:1px solid #f0f2f6;text-align:right;cursor:pointer}
.lanetab tr:hover td{background:#eef3ff}
.lanetab tr.sel td{background:#e0e8ff}
.lanetab tr.grphdr td{background:#d7dded;font-weight:700;color:#2b2f36;cursor:default;position:sticky;top:22px}
.lanetab tr.grphdr:hover td{background:#d7dded}
#lanewf{margin-top:6px}
.hctabs{display:inline-flex;border:1px solid #cfd4dd;border-radius:8px;overflow:hidden;margin-bottom:10px}
.hctabs button{border:0;background:#fff;padding:6px 16px;font-weight:600;cursor:pointer;color:#566}
.hctabs button.on{background:var(--blue);color:#fff}
.dstab{border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums;width:100%;max-width:780px}
.dstab th{background:#2f3a45;color:#fff;padding:7px 11px;font-weight:700;text-align:right;border:1px solid #3f4a56}
.dstab th.dsname{text-align:left}
.dstab td{padding:5px 11px;border:1px solid #e3e6eb;text-align:right;color:#2b2f36}
.dstab td.dsname{text-align:left;font-weight:600}
.dstab td.dsu{text-align:center;color:var(--muted)}
.dstab tr:hover td{background:#f4f6fa}
.dstab tr.dsgrp td{background:#eef2f7;font-weight:700;text-align:center;border-top:2px solid #b7c0cc;font-size:12.5px;color:#2b2f36}
.dstab tr.dsgrp:hover td{background:#eef2f7}
.hpwrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px}
.hptab{border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums;white-space:nowrap;min-width:100%}
.hptab th{background:#2f3a45;color:#fff;padding:5px 9px;font-weight:700;text-align:right;border:1px solid #3f4a56}
.hptab th.hpname{text-align:left}
.hptab td{padding:4px 9px;border:1px solid #eef0f4;text-align:right;color:#2b2f36}
.hptab td.hpname{text-align:left;font-weight:600}
.hptab td.hpu{text-align:center;color:var(--muted)}
.hptab td.hptot{font-weight:700;background:#f1f4f8}
.ssbar{margin-bottom:14px;font-size:13px}
.ssbig2{font-size:20px;font-weight:800;color:#41419e;vertical-align:middle;margin:0 6px}
.sstable{margin-bottom:16px;border:1px solid var(--line);border-radius:8px;padding:8px 10px 4px;transition:box-shadow .2s,border-color .2s}
.sstable.ss-hl{border-color:#41419e;box-shadow:0 0 0 2px rgba(65,65,158,.25)}
.sstable .sshdr{font-weight:700;font-size:12.5px;margin-bottom:6px;color:#33373e}
.sstwrap{overflow-x:auto}
.sst2{width:100%;border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums;white-space:nowrap}
.sst2 th{text-align:right;padding:3px 10px;border-bottom:2px solid #dfe3ea;color:#586172;font-weight:700}
.sst2 th.sstl{text-align:left}
.sst2 td{padding:3px 10px;border-bottom:1px solid #f0f2f5;text-align:right}
.sst2 td.sstl{text-align:left;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sst2 td.sstn,.sst2 th.sstn{text-align:right;color:var(--muted)}
.sst2 td.sstv{font-weight:700;color:#2b2f36}
.sst2 tr.ssgrp td{background:#f2f4f8;font-weight:700;font-size:10.5px;color:#33373e;text-align:left;padding:3px 10px}
.spwrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px}
.sptab{border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums;width:100%}
.sptab th,.sptab td{padding:3px 6px;border:1px solid #e7eaef}
/* numbers and the fixed left labels never wrap; group headers may wrap so shovel columns can shrink */
.sptab .spk,.sptab .spu,.sptab td.spnum,.sptab td.spact,.sptab td.spd{white-space:nowrap}
.sptab .sph1 th,.sptab .sph2 th{white-space:normal;word-break:break-word}
.sptab .sph1 th{background:#2f3a45;color:#fff;border-color:#3f4a56;text-align:center}
.sptab .sph2 th{background:#586172;color:#fff;border-color:#48505f;text-align:right;font-weight:600}
.sptab .spk{text-align:left;font-weight:600;background:#f7f9fc;position:sticky;left:0;z-index:2;min-width:150px;width:150px}
.sptab .spu{text-align:center;color:var(--muted);background:#f7f9fc;position:sticky;left:150px;z-index:2;min-width:46px;width:46px;box-shadow:2px 0 0 #cfd6e0}
.sptab .sph1 .spk,.sptab .sph1 .spu,.sptab .sph2 .spk,.sptab .sph2 .spu{background:#2f3a45;color:#fff;z-index:3}
.sptab td.spnum{text-align:right}
.sptab td.spact{font-weight:700}
.sptab td.spd{text-align:right}
.prodhd{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.avexp{margin-left:auto;font-size:11.5px;font-weight:600;border:1px solid #cfd4dd;background:#fff;color:#566;border-radius:999px;padding:3px 12px;cursor:pointer;white-space:nowrap}
.avexp:hover{background:#f2f5fa;border-color:var(--blue);color:var(--blue)}
.tptoggles{font-weight:400;white-space:nowrap;display:inline-flex;align-items:center}
.tgl{font-size:12px;font-weight:600;border:1px solid #cfd4dd;background:#fff;color:#8a909c;border-radius:999px;padding:3px 12px;margin-left:8px;cursor:pointer;vertical-align:middle;line-height:1.4}
.tgl::before{content:"○ ";font-size:11px}
.tgl.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.tgl.on::before{content:"● "}
.sptab .spsep{border-left:2px solid #b7c0cc}
.spgh{display:flex;align-items:center;gap:4px;justify-content:center;flex-wrap:wrap;text-align:center}
.spb{display:inline-block;padding:1px 7px;border-radius:4px;color:#fff;font-weight:700;font-size:10px}
.spb.spbn{background:#9aa0ab}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.chartcard{background:var(--card);border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:10px 12px;min-width:0}
.chartcard h3{margin:0 0 8px;font-size:13px;color:#3a3f46}
.chartwrap{position:relative;height:240px}
.hourrow{display:flex;gap:10px;align-items:stretch}
.hourdetail.hidden{display:none}
.hourdetail{flex:0 0 210px;height:240px;overflow:auto;border-left:1px solid var(--line);padding-left:9px;font-size:10px;font-variant-numeric:tabular-nums}
.hourdetail .hdhint{color:var(--muted);font-size:10px;padding-top:8px}
.hourdetail .hdhd{font-weight:700;color:var(--blue);font-size:12px;margin-bottom:3px;position:sticky;top:0;background:var(--card)}
.hourdetail .hdrow{display:grid;grid-template-columns:1fr 40px 58px;gap:4px;align-items:center;padding:2px 0;border-bottom:1px solid #f2f4f8;white-space:nowrap}
.hourdetail .hdrow.hdhead{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.02em;border-bottom:1px solid #dfe3ea}
.hourdetail .hdrow>span:first-child{color:#566;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hourdetail .hdrow .hdv{color:#2b2f36;font-weight:600;white-space:nowrap;text-align:right;padding:0 4px;border-radius:3px}
.hourdetail .hdrow .hdd{font-weight:600;white-space:nowrap;text-align:right}
.hourdetail .hdrow.hdhead span{text-align:right}
.hourdetail .hdrow.hdhead span:first-child{text-align:left}
@media(max-width:820px){.hourrow{flex-direction:column}.hourdetail{flex:auto;height:auto;border-left:0;border-top:1px solid var(--line);padding:8px 0 0}}
.widecard{grid-column:1/-1}
.avgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:12px}
.avcard{background:var(--card);border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:12px 15px}
.avcard h4{margin:0 0 6px;font-size:13.5px;color:#2b2f36;text-align:center}
.avdeploy{text-align:center;font-size:12.5px;color:#2b3340;margin:-2px 0 8px;padding:3px 0;background:#eef2f8;border-radius:6px}
.avdeploy b{font-size:15px;color:#1f2937}
.tlbtn{font-size:11px;border:1px solid #cfd4dd;background:#fff;border-radius:6px;padding:2px 12px;color:var(--blue);cursor:pointer}
.tlbtn:hover{background:#eef2f8;border-color:#3f51b5}
#eqTlPop{position:fixed;display:none;z-index:9999;background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 28px rgba(0,0,0,.18);padding:10px 12px;width:min(780px,92vw)}
.eqpoptitle{font-weight:700;font-size:12.5px;margin-bottom:5px;color:#2b2f36}
.eqpophr{display:flex;gap:2px;margin-top:5px;font-size:10.5px;color:#243b8a;border-top:1px solid #eef0f4;padding-top:4px}
.eqpophr .eqk{color:var(--muted);width:92px;flex:0 0 auto;font-size:9.5px}
.eqpophr span{flex:1 1 0;text-align:center;font-variant-numeric:tabular-nums}
.eqpophrtop{margin:0 0 6px;border-top:0;border-bottom:1px solid #e3e8ef;padding:0 0 5px;font-size:13px;font-weight:700}
.eqpophrtop .eqk{font-weight:600;color:#243b8a;font-size:10.5px}
.avdepsub{color:var(--muted);font-size:10px;margin-left:4px}
.avtpnoh{font-size:12px;color:#3a3f46;margin:0 0 5px}
.avtpnoh b{font-weight:700;font-size:13px}
.avhrs{font-size:11px;color:var(--muted);margin:0 0 7px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.avhrs b{color:#3a3f46;font-weight:700}
.avrow.avclick{cursor:pointer;border-radius:5px;transition:background .12s}
.avrow.avclick:hover{background:#f2f5fa}
.avrow.avclick .cx{display:inline-block;font-style:normal;font-size:9px;color:var(--muted);margin-right:4px;transition:transform .12s}
.avrow.avclick.open .cx{transform:rotate(90deg)}
.avdrop{display:none;padding:5px 8px 8px;background:#f7f9fc;border-radius:6px;margin:1px 0 5px}
.avdrop.avdroptp{background:transparent;padding:2px 0 6px}
.avdrop.avdroptp .dr.drtp{display:grid;grid-template-columns:62px 1fr 1fr 60px;gap:6px;align-items:center;font-size:12px}
.avdrop.avdroptp .dr.drtp .tpk{white-space:nowrap;color:#3a3f46;font-weight:600}
.avdrop.avdroptp .dr.drtp .tpa{text-align:right;font-weight:700}
.avdrop.avdroptp .dr.drtp .tpb{text-align:right;color:#566}
.avdrop.avdroptp .dr.drtp .tpd{text-align:right;font-weight:700}
.avdrop.open{display:block}
.avdrop .drhd{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;margin:1px 0 4px}
.avdrop .dr{display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:2px 0;font-variant-numeric:tabular-nums}
.avdrop .dr.dsvhead{font-size:13.5px;font-weight:700;color:#2b2f36;margin-top:3px}
.avdrop .dr span:first-child{color:#3a3f46;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.avdrop .dr span:last-child{color:#6b7280;white-space:nowrap}
.avdrop .dr.tot{border-top:1px solid #e3e6ec;margin-top:3px;padding-top:4px;font-weight:600}
.avdrop .drsub{font-size:12px;font-weight:700;color:#3a3f46;margin:5px 0 2px;padding-bottom:2px;border-bottom:1px solid #e3e6ec}
.avdrop .drdump{display:flex;justify-content:space-between;align-items:baseline;gap:6px;padding:3px 0 3px 16px;border-bottom:1px solid #f3f5f8;font-size:11px}
.avdrop .drdump .ddh{color:#5a626e;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto;min-width:34px}
.avdrop .drdump .ddm{color:#8a929e;white-space:nowrap;flex:0 0 auto}
.avdrop .drdump.drx{cursor:pointer}
.avdrop .drdump.drx:hover{background:#f2f5fa}
.avdrop .drdump .cx{display:inline-block;font-style:normal;font-size:7px;color:var(--muted);margin-right:3px;transition:transform .12s}
.avdrop .drdump.drx.open .cx{transform:rotate(90deg)}
.avdrop .dumpspark{display:none;padding:2px 0 5px 20px}
.avdrop .dumpspark.open{display:block}
.avdrop .dumpspark .sparkcap{font-size:9px;color:var(--muted);margin-bottom:1px}
.avdrop .dr.drx{cursor:pointer;border-radius:4px}
.avdrop .dr.drx:hover{background:#eef2f7}
.avdrop .dr.drx .cx{display:inline-block;font-style:normal;font-size:8px;color:var(--muted);margin-right:4px;transition:transform .12s}
.avdrop .dr.drx.open .cx{transform:rotate(90deg)}
.avdrop .drsublist{display:none;padding:1px 0 3px 18px}
.avdrop .drsublist.open{display:block}
.avdrop .dr.drunit{font-size:11px;color:#8a929e;padding:1px 0}
.avdrop .dr.drunit span:last-child{color:#8a929e}
.avrow{display:grid;grid-template-columns:62px 1fr 1fr 60px;gap:6px;font-size:13px;padding:5px 0;align-items:center;font-variant-numeric:tabular-nums;border-bottom:1px solid #f0f2f6}
.avrow:last-child{border-bottom:0}
.avrow.avhead{font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid var(--line);padding-bottom:4px}
.avrow .avm{font-weight:700;color:#3a3f46;white-space:nowrap}
.avrow .ava{text-align:right;font-weight:700}
.avrow .avb{text-align:right;color:#566}
.avrow .avd{text-align:right;font-weight:700}
.hitrow{display:grid;grid-template-columns:104px 1fr 42px;gap:7px;align-items:center;margin:3px 0;font-size:11px;font-variant-numeric:tabular-nums}
.hitname{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#3a3f46}
.hittrack{position:relative;height:14px;background:#eef0f4;border-radius:3px;overflow:hidden}
.hittrack>span{position:absolute;left:0;top:0;height:100%;border-radius:3px}
.hitval{text-align:right;color:#2b2f36}
.hitlegend{font-size:10.5px;color:var(--muted);margin:0 0 8px}
.hitlegend b{font-weight:700}
.effwrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:12px;margin:6px 0 14px}
.effgroup{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.effhdr{background:#eef2f7;padding:7px 11px;font-size:12px;font-weight:700;color:#2b2f36;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #cfd6e0}
.effhdr small{font-weight:600;color:var(--muted);font-size:10px}
.effrow{display:grid;grid-template-columns:1fr 42px 62px;gap:6px;align-items:center;padding:5px 11px;font-size:12px;border-bottom:1px solid #f0f2f6;font-variant-numeric:tabular-nums;cursor:pointer}
.effrow:last-child{border-bottom:0}
.effrow:hover{background:#eef3ff}
.effrow.effsel{background:#dbe6ff;box-shadow:inset 3px 0 0 var(--blue)}
.effrow .es{font-weight:600;color:#3a3f46;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.effrow .ev{text-align:right;font-weight:700}
.effrow .el{text-align:right;color:#a33}
/* ---- loss analysis panels (Pareto · top-3 band · heatmap · chips) ---- */
.exec{background:linear-gradient(90deg,#eef3fb,#f4f7fc);border:1px solid #d6e0ef;border-left:4px solid var(--blue);border-radius:8px;padding:9px 15px;margin-bottom:14px;font-size:13.5px;color:#2b3340;display:flex;align-items:center;gap:10px;white-space:nowrap;overflow:hidden}
.exec .exlead{font-weight:700;color:var(--blue);flex:0 0 auto;white-space:nowrap}
.exmq{flex:1 1 auto;min-width:0;overflow:hidden;position:relative;-webkit-mask-image:linear-gradient(90deg,transparent 0,#000 24px,#000 calc(100% - 24px),transparent 100%);mask-image:linear-gradient(90deg,transparent 0,#000 24px,#000 calc(100% - 24px),transparent 100%)}
.exmq-in{display:inline-flex;align-items:center;white-space:nowrap;will-change:transform;animation:exroll 34s linear infinite}
.exmq:hover .exmq-in{animation-play-state:paused}
.exmq-seg{padding-right:60px}
@keyframes exroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.exec .exscore{cursor:pointer;border-bottom:1px dashed currentColor}
.exec .exscore:hover{filter:brightness(.85)}
.top3{background:#fff4f2;border:1px solid #f3d5cf;border-radius:7px;padding:8px 12px;margin:4px 0 14px;font-size:12px;color:#6f2c24;line-height:1.55}
.top3 b{color:#b3382b}
.top3 .t3none{color:#2f7a44;font-weight:600}
.pareto{margin:2px 0 16px}
.prow{display:grid;grid-template-columns:minmax(120px,180px) 1fr 82px 42px;gap:9px;align-items:center;margin:4px 0;font-size:11.5px;font-variant-numeric:tabular-nums}
.prow .pname{font-weight:600;color:#3a3f46;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prow .ptrack{background:#f0f2f6;border-radius:4px;height:16px;overflow:hidden}
.prow .pbar{display:block;height:100%;background:linear-gradient(90deg,#e37b6f,#cc3c32)}
.prow .pval{text-align:right;color:#b3382b;font-weight:700}
.prow .pcum{text-align:right;color:var(--muted);font-size:10.5px}
.hmwrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;margin:2px 0 16px}
.hmtab{border-collapse:collapse;font-size:10.5px;font-variant-numeric:tabular-nums;width:100%}
.hmtab td{border:1px solid #eceef2;padding:3px 6px;text-align:right;white-space:nowrap}
.hmtab th{border:1px solid #48505f;padding:3px 6px;background:#586172;color:#fff;font-weight:600;text-align:right;white-space:normal;word-break:break-word;vertical-align:bottom}
.hmtab th:first-child,.hmtab td.hmk{text-align:left;font-weight:600;background:#f7f9fc;color:#2b2f36;white-space:nowrap}
.hmtab th:first-child{background:#586172;color:#fff}
.hmtab td.hmtot{font-weight:700;color:#b3382b;background:#fbfcfe}
.hmtab tr.hmfoot td{background:#eef2f7;font-weight:700;color:#8a2c22;border-top:2px solid #aeb6c2}
.hmtab tr.hmfoot td.hmk{background:#e3e8ef;color:#2b3340}
.hmwrap.svwrap{overflow-x:visible}
.svloss{width:100%;table-layout:fixed;font-size:10px}
.svloss th,.svloss td{padding:2px 4px}
.svloss th{font-size:9px;line-height:1.05}
.svloss td.hmk{white-space:nowrap}
.svloss .gc{color:var(--muted);font-weight:500}
.svloss tr.svgrp td{background:#eaeef4;color:#2b3340;font-weight:700;border-top:1px solid #c4ccd8;white-space:nowrap}
.svloss tr.svgrp td:first-child{background:#dfe5ee;text-align:left}
.svloss tr.svrow{cursor:pointer}
.svloss tr.svrow:hover td{background:#eef4ff}
.svloss tr.svrow.sel td{background:#dbe8ff;box-shadow:inset 0 0 0 9999px rgba(63,81,181,.06)}
.svloss tr.svrow.sel td.hmk{background:#c9dcff}
.svloss td.hmcell{color:#7a241c}
.losschip{display:inline-block;padding:1px 7px;border-radius:10px;background:#fdecea;color:#b3382b;font-weight:700;font-size:10.5px;white-space:nowrap}
.losschip.zero{background:#eef6ef;color:#2f7a44}
.layout{display:flex;gap:16px;align-items:flex-start}
.sidenav{flex:0 0 clamp(200px,15vw,320px);position:sticky;top:14px;display:flex;flex-direction:column;gap:6px}
.sidenav-logo{width:100%;padding:8px 6px 20px;text-align:center}
.sidenav-logo img{width:100%;max-width:312px;height:auto;display:block;margin:0 auto}
.sidenav-logo-label{margin-top:6px;font:400 24px/1.05 "Vineta BT","Bookman Old Style",Georgia,serif;letter-spacing:.8px;color:#5f6f7c;background:none;text-shadow:none;text-transform:uppercase}
.sidenav button{text-align:left;border:1px solid #cfd4dd;background:#fff;border-radius:8px;padding:10px 14px;font-weight:600;color:#566;cursor:pointer;font-size:16px;line-height:1.2;white-space:nowrap}
.sidenav button.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.sidenav button.sub{margin-left:18px;width:calc(100% - 18px);font-size:14px;padding:8px 12px;color:#6b7280;border-color:#dde1e8;position:relative}
.sidenav button.sub::before{content:"";position:absolute;left:-10px;top:50%;width:7px;height:1px;background:#c3c8d2}
.sidenav button.sub.on{color:#fff}
.sidenav button.uc{background:#fdf4c2;border-color:#e0c200;color:#7a5f00}
.sidenav button.uc.on{background:#e6b800;border-color:#c9a200;color:#3a2d00}
.content{flex:1;min-width:0}
/* ── Collapsible sidebar ── */
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
.pagenav{display:none;align-items:center;gap:10px;margin-bottom:10px}
body.sb-auto .pagenav{display:flex}
.pagenav button{border:1px solid #cfd4dd;background:#fff;color:#566;border-radius:8px;padding:5px 12px;font-weight:600;font-size:12.5px;cursor:pointer;white-space:nowrap}
.pagenav button:hover:not(:disabled){background:#f2f5fa;border-color:var(--blue);color:var(--blue)}
.pagenav button:disabled{opacity:.4;cursor:default}
.pagenav .pglbl{font-size:12px;color:var(--muted);font-weight:600}
.backbtn{position:fixed;top:9px;left:16px;z-index:65;border:1px solid #cfd4dd;background:#fff;color:#566;border-radius:8px;padding:7px 12px;font-weight:600;font-size:12.5px;line-height:1;cursor:pointer;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.12);text-decoration:none}
.backbtn:hover{background:#f2f5fa;border-color:var(--blue);color:var(--blue)}
.prtbtn{position:fixed;top:9px;right:16px;z-index:65;border:1px solid #cfd4dd;background:#fff;color:#566;border-radius:8px;padding:4px 9px;font-size:16px;line-height:1;cursor:pointer;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.12)}
.prtbtn:hover{background:var(--blue);color:#fff;border-color:var(--blue)}
.prtbtn:hover{background:#f2f5fa;border-color:var(--blue);color:var(--blue)}
@media(max-width:820px){.charts{grid-template-columns:1fr}.avgrid{grid-template-columns:1fr}.layout{flex-direction:column}.sidenav{flex:auto;flex-direction:row;flex-wrap:wrap;position:static}}
@media print{
  @page{margin:12mm}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  body,.wrap{background:#fff}
  .wrap{padding:0}
  .sidenav,#shiftnav,#toggle,.backbtn,.prtbtn,#sbShow,#sbArrow,#sbEdge,.pagenav{display:none!important}
  .layout{display:block}
  .content{min-width:0}
  .topbar{margin-bottom:8px}
  /* only the active tab's page is un-hidden, so print shows just that one */
  .section,.chartcard,.avcard,.effgroup,.spwrap,.hmwrap,.card{break-inside:avoid}
  .lanetab tr,.sptab tr,.hmtab tr{break-inside:avoid}
  .spwrap,.hmwrap{overflow:visible}
  .sptab,.hmtab,.lanetab{font-size:9px}
  h2{break-after:avoid}
}
</style></head><body><div class="wrap">
<a class="backbtn" href="Albian_Mine_Operations_Portal.html" title="Back to Albian Mine Operations Portal">&#8249; Portal</a>
<button id="sbShow" onclick="toggleSidebar()" title="Show sidebar">&#8250;</button>
<button id="sbArrow" onclick="toggleSidebar()" title="Hide sidebar">&#8249;</button>
<div id="sbEdge"></div>
<button class="prtbtn" onclick="window.print()" title="Print / Save the current tab as PDF">&#9113;</button>
<div class="layout">
  <nav class="sidenav" id="sidenav">
    <div class="sidenav-logo">
      <img src="https://github.com/user-attachments/assets/d258c096-68f1-4f87-8919-c0d0cab5eba6" alt="CNRL iSTAR logo" onerror="this.onerror=null;this.src='istarlogov2.png';">
      <div class="sidenav-logo-label">INTRASHIFT</div>
    </div>
    <div class="sidenav-controls">
      <div class="shiftnav" id="shiftnav"></div>
      <div class="toggle" id="toggle"></div>
    </div>
  </nav>
  <main class="content">
    <div class="pagenav">
      <button id="pgPrev" onclick="pageStep(-1)" title="Previous page">&#8249; Back</button>
      <span class="pglbl" id="pgLabel"></span>
      <button id="pgNext" onclick="pageStep(1)" title="Next page">Forward &#8250;</button>
    </div>

    <div id="eqTlPop" onmouseleave="eqTlHide()"></div>
    <section class="page" id="pg-overview" hidden>
      <div class="exec" id="ovExec"></div>
      <div class="section ovsection">
        <div class="updated" id="ovUpdated"></div>
        <h2>Shift Overview <span class="sub" id="ovsub"></span></h2>
        <div class="charts">
          <div class="chartcard"><h3 class="prodhd"><span id="cumTitle">Shift Progress — Cumulative Tonnes vs Target</span> <span class="sub" id="cumThrough" style="font-weight:400"></span><span class="toggle" style="margin-left:auto"><button id="cumModeTot" class="on" onclick="setCumMode('tot')">Total Production</button><button id="cumModeSand" onclick="setCumMode('sand')">Sand Haul</button></span></h3><div class="chartwrap"><canvas id="chCum"></canvas></div><div class="foot" id="cumNote" style="display:none;color:#b0762f">Actual unavailable — load timestamps missing in the export; showing target only.</div></div>
          <div class="chartcard"><h3 class="prodhd">Hourly tonnes vs target <span class="sub">— click an hour for its detail</span><button class="tgl" id="hourDetBtn" onclick="toggleHourDet()">Details</button></h3><div class="hourrow"><div class="chartwrap" style="flex:1;min-width:0"><canvas id="chHour"></canvas></div><div id="hourDetail" class="hourdetail"></div></div><div class="foot" id="hourNote" style="display:none;color:#b0762f">Actual unavailable — load timestamps missing in the export; showing target only.</div></div>
        </div>
      </div>
      <div class="section">
        <h2 class="prodhd"><span>Equipment Hours and Performance</span> <span class="sub" id="ovkpisub"></span><button class="avexp" id="avExpandBtn" onclick="toggleAvExpand()">Expand all</button></h2>
        <div id="ovKpi" class="avgrid"></div>
      </div>
    </section>

    <section class="page" id="pg-playbook" hidden>
      <div class="section">
        <h2>Playbook <span class="sub" id="playsub"></span></h2>
        <div id="playbookBody"></div>
      </div>
    </section>

    <section class="page" id="pg-suggestions" hidden>
      <div class="section">
        <h2>Suggestions <span class="sub" id="sgsub"></span></h2>
        <div id="suggestionsBody"></div>
      </div>
    </section>

    <section class="page" id="pg-matplace" hidden>
      <div class="section">
        <h2>Material Placement <span class="sub" id="hcsub2"></span></h2>
        <h3 style="margin:0 0 2px">Dump-centric — simple</h3>
        <div class="foot" style="margin-bottom:8px"><b>Simple dump-centric</b> — identical to the dump-centric view above (<b>Shovel</b> full-haul in → <b>Dump</b> → <b>Next Shovel</b> empty-haul out), keeping <b>full &amp; empty haul tonnage</b>, <b>% locked</b> labels and locked-load hatching. The <b>only</b> difference: haul distance is <b>not</b> encoded — nodes sit in evenly-spaced columns and ribbon width still ∝ tonnage.</div>
        <div id="hcSimple"></div>
        <div class="badges" id="hclegSimple"></div>
        <div style="border-top:1px solid var(--line);margin:22px 0 10px"></div>
        <h3 style="margin:0 0 2px">Cycle map <span class="sub" style="font-weight:400;color:var(--muted)" id="hcmapsrc"></span></h3>
        <div class="foot" style="margin-bottom:8px"><b>Spatial map</b> — each <b>shovel</b> (blue) and <b>dump location</b> (ore/waste colour) is a circle placed by its <b>x/y position</b>, sized by tonnes. <b>Loaded hauls</b> (shovel → dump) are solid coloured arcs; <b>empty returns</b> (dump → next shovel) are faint dashed arcs — arrowheads show the cycle direction. Circle spacing is to scale in km.</div>
        <div style="margin:2px 0 6px">
          <button class="owbtn on" id="cmBase" onclick="cmToggle('base',this)" style="display:none">Basemap</button>
          <button class="owbtn on" id="cmRoad" onclick="cmToggle('road',this)" style="display:none">Haul roads</button>
          <button class="owbtn on" id="cmSnap" onclick="cmToggle('snap',this)" style="display:none">Snap flows to roads</button>
          <button class="owbtn on" id="cmLoaded" onclick="cmToggle('loaded',this)">Loaded hauls</button>
          <button class="owbtn on" id="cmEmpty" onclick="cmToggle('empty',this)">Empty returns</button>
          <button class="owbtn" id="cmHi" onclick="cmToggle('hi',this)">Highlight longest empties</button>
          <button class="owbtn" id="cmTbl" onclick="cmToggle('tbl',this)">Longest-empty table</button>
        </div>
        <div id="hc4"></div>
        <div id="hc4tbl"></div>
        <div class="badges" id="hcleg4"></div>
      </div>
    </section>

    <section class="page" id="pg-balance">
      <div class="section">
        <h2>Truck Balance — Over the Shift <span class="sub" id="btlsub"></span></h2>
        <div style="margin:-2px 0 6px;font-size:12px;color:var(--muted)">Per-hour equipment #:
          <button class="owbtn on opModeBtn" onclick="toggleOpMode()">Showing: by GOH</button></div>
        <div id="balanceTL"></div>
      </div>
      <div class="section">
        <h2>Hang/Queue Time - Over the Shift <span class="sub" id="whsub"></span></h2>
        <div id="waitTL"></div>
      </div>
    </section>

    <section class="page" id="pg-sandbox" hidden>
      <div class="cards" id="cards"></div>
      <div class="section"><h2>Haulage Score — Breakdown <span class="sub" id="dhsub"></span></h2><div id="dh"></div></div>
      <div class="section"><h2>Loading Score — Breakdown <span class="sub" id="dlsub"></span></h2><div id="dl"></div></div>
      <div class="section">
        <h2>Bottleneck — Trucks vs Shovels <span class="sub" id="sbbnsub"></span></h2>
        <div id="sbBottle"></div>
        <div class="foot">Each fleet's <b>capacity</b> is its waterfall scheduled Potential — Loading = what the shovels could load, Haulage = what the trucks could haul; <b>Actual moved</b> is what was achieved. The <b>lower capacity is the binding constraint</b> (dashed line): if Loading &gt; Haulage, trucks are the limit and adding trucks can lift output; if Haulage &gt; Loading, shovels are the limit and more trucks won't help. Capacities are on each fleet's own cycle basis, so treat the comparison as directional.</div>
      </div>
      <div class="section">
        <h2>Truck Balance — Over the Shift <span class="sub" id="sbtlsub"></span></h2>
        <div id="sbBalanceTL"></div>
        <div class="foot">Trucks Required (LP) vs Actual over the shift (15-min readings), with periods shaded <span style="color:#e23b32">red = under-trucked</span> / <span style="color:#3f51b5">blue = over-trucked</span>, the actual tonnage rate (green, right axis, smoothed) vs target, and est. tonnes lost while under-trucked.</div>
      </div>
      <div class="section">
        <h2>Top 15 Lost-Time Reasons — Down · Standby · Delay <span class="sub" id="hitsub"></span></h2>
        <div class="hitlegend"><b style="color:#e23b32">■</b> Down &nbsp; <b style="color:#3f7fe0">■</b> Standby &nbsp; <b style="color:#e0a41f">■</b> Delay &nbsp;— hours lost this shift, ranked per equipment.</div>
        <div id="avHitters" class="avgrid"></div>
      </div>
    </section>

    <section class="page" id="pg-trucks" hidden>
      <div class="section">
        <h2>Losses &amp; Gains — Trucks <span class="sub" id="wfsub"></span></h2>
        <div class="badges" id="ind"></div>
        <div id="wf"></div>
        <button class="owbtn" id="truckExpandBtn" onclick="toggleTruckExpand()">Expand all &#9662;</button>
        <button class="owbtn" id="owBtnT" onclick="toggleOW('trucks')">Ore/Waste Details &#9662;</button>
        <button class="owbtn" id="qBtnT" onclick="toggleTruckBox('queue')">Queue at Shovel &#9662;</button>
        <button class="owbtn" id="iBtnT" onclick="toggleTruckBox('idle')">Dump Idle &#9662;</button>
        <button class="owbtn" id="dBtnT" onclick="toggleTruckBox('dump')">Dumping &#9662;</button>
        <button class="owbtn" id="fBtnT" onclick="toggleTruckBox('full')">Haul Distance &#9662;</button>
        <button class="owbtn" id="tadBtnT" onclick="toggleTrucksAtDump()">Trucks at Dump &#9662;</button>
        <div class="foot">Payload-normalized basis: rate* = 361 t ÷ budget cycle time, so budget-cycle tonnes = 361/load. Each time row = rate* × (budget − actual) time; Payload row = actual − 361 t. The bridge closes exactly (no residual) and Potential matches the Haulage-Score gauge. Green = gain, red = loss. Data includes only large shovels and trucks.</div>
        <div class="chartcard widecard" id="secQueT" hidden style="margin-top:10px"><h3>Queue at Shovel per Shovel <span class="sub" style="font-weight:400;color:var(--muted)">(truck queue, actual minutes vs dashed budget tick · grouped by loading shovel)</span></h3><div id="chQueT"></div></div>
        <div class="chartcard widecard" id="secIdleT" hidden style="margin-top:10px"><h3>Dump Idle per Dump <span class="sub" style="font-weight:400;color:var(--muted)">(queue at dump, actual minutes vs dashed budget tick · grouped by dump location)</span></h3><div id="chIdleT"></div></div>
        <div class="chartcard widecard" id="secDumpT" hidden style="margin-top:10px"><h3>Dumping Time per Dump <span class="sub" style="font-weight:400;color:var(--muted)">(actual minutes vs dashed budget tick · grouped by dump location)</span></h3><div id="chDumpT"></div></div>
        <div class="chartcard widecard" id="secFullT" hidden style="margin-top:10px"><h3>Haul Distance per Shovel <span class="sub" style="font-weight:400;color:var(--muted)">(km, two boxes per shovel: <b style="color:#3f51b5">full</b> &amp; <b style="color:#e0952a">empty</b> haul, vs dashed expected-distance tick)</span></h3><div id="chFullT"></div></div>
        <div class="chartcard widecard" id="secTadT" hidden style="margin-top:10px"><h3>Trucks at Dump <span class="sub" id="dtlsub" style="font-weight:400;color:var(--muted)"></span></h3><div id="dumpTl"></div><div class="foot">Trucks present at each dump location over the shift (TrucksAtDump from TruckAtDump.csv), by dominant loading pit — all dumps except internal roads ("IN…") and dumps at the dig location. The value under each name is the average dump-queue time per load (QueueTimeDmp) for loads dumping there in this view.</div></div>
      </div>
      <div class="section" id="owSectionT" hidden>
        <h2>Ore/Waste — Waterfall <span class="sub" id="owsub"></span></h2>
        <div class="foot">Full bridge per material, including PA · UA · OE. Availability is fleet-level, so its tonnage effect is allocated to ore vs waste by each material's share of Potential (act/bud % are the fleet values, identical on ore and waste; only the tonnage bars differ).</div>
        <div id="owWF"></div>
      </div>
    </section>

    <section class="page" id="pg-shovel2" hidden>
      <div class="section">
        <h2>Shovel Losses &amp; Gains <span class="sub" id="shov2sub"></span></h2>
        <div class="badges" id="shov2ind"></div>
        <div id="shovwf2"></div>
        <button class="owbtn" id="shovExpandBtn" onclick="toggleShovExpand()">Expand all &#9662;</button>
        <button class="owbtn" id="pcBtnS2" onclick="toggleShovBox('pay')">Payload Compliance &#9662;</button>
        <button class="owbtn" id="stBtnS2" onclick="toggleShovBox('spot')">Spot Time &#9662;</button>
        <button class="owbtn" id="ltBtnS2" onclick="toggleShovBox('load')">Load Time &#9662;</button>
        <button class="owbtn" id="htBtnS2" onclick="toggleShovBox('hang')">Hang Time &#9662;</button>
        <div class="foot">Data includes only large shovels and trucks.</div>
        <div class="chartcard widecard" id="secPayS2" hidden style="margin-top:10px"><h3>Payload Compliance per Shovel <span class="sub" style="font-weight:400;color:var(--muted)">(box: Q1–Q3 · median line · ◆ mean · dashed 361 t target)</span></h3><div id="chPayS2"></div></div>
        <div class="chartcard widecard" id="secSpotS2" hidden style="margin-top:10px"><h3>Spot Time per Shovel <span class="sub" style="font-weight:400;color:var(--muted)">(actual minutes vs dashed budget tick · the <span style="color:#7a4fd0">purple % above each box</span> = share of the shift spent double-side loading, from ShovelLoadingSide)</span></h3><div id="chSpotS2"></div></div>
        <div class="chartcard widecard" id="secLoadS2" hidden style="margin-top:10px"><h3>Load Time per Shovel <span class="sub" style="font-weight:400;color:var(--muted)">(actual minutes vs dashed budget tick)</span></h3><div id="chLoadS2"></div></div>
        <div class="chartcard widecard" id="secHangS2" hidden style="margin-top:10px"><h3>Hang Time per Shovel <span class="sub" style="font-weight:400;color:var(--muted)">(actual minutes vs dashed budget tick)</span></h3><div id="chHangS2"></div></div>
      </div>
      <div class="section" id="owSectionS2" hidden>
        <h2>Ore / Waste — Shovel Losses &amp; Gains <span class="sub" style="font-weight:400;color:var(--muted)">(PA/UA/OE allocated by potential share)</span></h2>
        <div class="foot">Same PA · UA · OE → Payload · Spot · Load · Hang bridge as above, split by material. Availability is fleet-level, so PA/UA/OE tonnage effects are allocated to ore vs waste by each material's share of Potential (the shown act/bud % are the fleet values, identical on ore and waste; only the tonnage bars differ).</div>
        <div id="shovOW2"></div>
      </div>
    </section>

    <section class="page" id="pg-trends" hidden>
      <div class="section">
        <h2>Cross-Shift Trends <span class="sub" id="trsub"></span></h2>
        <div class="foot" id="trNote">Each point is one shift for the current view (oldest → newest, left → right); the highlighted point is the shift you're viewing now. Use these to tell a one-off from a real trend.</div>
        <div class="charts">
          <div class="chartcard widecard"><h3>Haulage &amp; Loading Score (%)</h3><div class="chartwrap"><canvas id="chTrScore"></canvas></div></div>
          <div class="chartcard widecard"><h3>Cat 797 Availability — PA · UA · OE (%)</h3><div class="chartwrap"><canvas id="chTrAvail"></canvas></div></div>
          <div class="chartcard widecard"><h3>Production — Actual Dumped vs Plan (Tonnes)</h3><div class="chartwrap"><canvas id="chTrProd"></canvas></div></div>
          <div class="chartcard widecard"><h3>Truck Match (Loading − Haulage, pts)</h3><div class="chartwrap"><canvas id="chTrMatch"></canvas></div></div>
        </div>
      </div>
    </section>

    <section class="page" id="pg-loading" hidden>
      <div class="section">
        <h2>Shovel Drill-Down <span class="sub" id="loadsub">click a shovel for its cycle waterfall</span></h2>
        <div id="loadTop3" class="top3"></div>
        <h4 class="mini">Shovel Loss Matrix <span class="sub" style="font-weight:400;color:var(--muted)">— click a shovel for its status timeline &amp; cycle waterfall</span></h4>
        <div id="shovList2"></div>
        <div class="foot">Data includes only large shovels and trucks.</div>
      </div>
      <div class="section">
        <h2>Shovel Status Timeline <span class="sub" id="tlsub2"></span></h2>
        <div id="chTl2"></div>
        <div class="foot">Data includes only large shovels and trucks.</div>
      </div>
      <div class="section">
        <h2>Shovel Cycle Waterfall <span class="sub" id="shovwf2sub"></span></h2>
        <div id="shovUnitWF2"></div>
        <div class="foot">Data includes only large shovels and trucks.</div>
      </div>
    </section>

    <section class="page" id="pg-haulage" hidden>
      <div class="section">
        <h2>Haulage Drill-Down <span class="sub" id="lanesub"></span></h2>
        <div id="haulTop3" class="top3"></div>
        <h4 class="mini">Haulage Loss Matrix <span class="sub" style="font-weight:400;color:var(--muted)">— paths grouped by loading shovel; redder cell = more tonnes lost; <b>click a path</b> for its waterfall &amp; full-haul-time trend below</span></h4>
        <div id="laneMatrix"></div>
        <div class="foot">Data includes only large shovels and trucks.</div>
      </div>
      <div class="section">
        <h2>Path Waterfall <span class="sub" id="lanewfsub"></span></h2>
        <div id="lanewf"></div>
        <div class="foot">Data includes only large shovels and trucks.</div>
      </div>
    </section>

    <section class="page" id="pg-analytics" hidden>
      <div class="section">
        <h2>Equipment Status Timeline — Shovels <span class="sub" id="tlsub"></span></h2>
        <div id="chTl"></div>
      </div>
      <div class="section">
        <h2>Equipment Status Timeline — Trucks <span class="sub" id="ttlsub"></span></h2>
        <div id="chTlTrk"></div>
        <div class="foot">Cat 797 haul trucks — status over the shift (Ready / Delay / Standby / Down) placed by real timestamp, one thin row per truck, ordered by Truck ID.</div>
      </div>
    </section>

    <section class="page" id="pg-blend" hidden>
      <div class="section">
        <h2>Blend <span class="sub" id="blendsub"></span></h2>
        <div class="foot" id="blendWhen" style="margin-bottom:8px"></div>
        <div class="foot" style="margin-bottom:8px">Crusher feed only (dumps to <b>CR*</b>). Grade blocks mined per shovel, with block assay (bitumen % / fines % / D50 µm). Each hour bucket (by load time) shows the block's tonnes and its <b>% of that hour's mined tonnes</b>; the Grades rows are the <b>tonnes-weighted</b> average bitumen / fines / D50 delivered each hour and shift-to-date. Click a shovel row to show its grade blocks.</div>
        <button class="owbtn" id="blendExpandBtn" onclick="blendExpandAll(this)">Expand blocks &#9662;</button>
        <div id="blendBody" style="overflow-x:auto;padding-bottom:16px"></div>
      </div>
    </section>

    <section class="page" id="pg-snapshot" hidden>
      <div class="section">
        <h2>Equipment Status <span class="sub" id="snapsub"></span></h2>
        <div class="foot" id="snapWhen" style="margin-bottom:8px"></div>
        <div id="snapCatBtns" style="margin-bottom:6px">
          <button class="owbtn on" id="snapBtn_shov" onclick="snapCatTab('shov')">Shovel</button>
          <button class="owbtn" id="snapBtn_truck" onclick="snapCatTab('truck')">Truck</button>
          <button class="owbtn" id="snapBtn_aux" onclick="snapCatTab('aux')">Aux</button>
        </div>
        <div id="snapBody"></div>
      </div>
    </section>

    <section class="page" id="pg-pulse" hidden>
      <div class="section">
        <h2>Current LP <span class="sub" id="pulsesub"></span></h2>
        <div class="foot" id="pulseWhen" style="margin-bottom:8px"></div>
        <div id="lpMissing" style="margin-bottom:8px"></div>
        <div id="currentLPTab"></div>
      </div>
      <div class="section">
        <h2>Shift LP <span class="sub" id="pulseavgsub"></span></h2>
        <div class="foot" style="margin-bottom:8px">Per hour, each cell stacks: <b>coverage factor</b> ·
          <b>LP path-rate (t/h)</b> · <b style="color:#3a6ea5">actual t/h</b> (loaded tonnes from AllLoadsDumps) —
          the LP figures duration-weighted over the solves within that hour (from shift start). One row per shovel.</div>
        <div id="shiftLPTab" style="overflow-x:auto"></div>
      </div>
    </section>

    <section class="page" id="pg-lube" hidden>
      <div class="charts">
        <div class="chartcard" style="display:flex;flex-direction:column;gap:14px">
          <div><h3>Fuel Level at Refuel <span class="sub" id="lusub"></span></h3><div class="chartwrap" style="height:312px"><canvas id="chLubeFuel"></canvas></div></div>
          <div><h3>Assignment Automation</h3><div id="lubeAssignAuto"></div></div>
          <div><h3>Faulty Fuel-Level Sensors <span class="sub" id="lfssub"></span></h3><div id="lubeFaulty"></div></div>
        </div>
        <div class="chartcard" style="display:flex;flex-direction:column;gap:14px">
          <div><h3>Hourly Fuel Delay — This Shift <span class="sub" id="lhsub"></span></h3><div class="chartwrap" style="height:312px"><canvas id="chLubeTrend"></canvas></div></div>
          <div><h3>Actual vs Expected by Reason</h3><div id="lubeReasons"></div></div>
          <div><h3>Overrun Leaderboard — This Shift</h3><div id="lubeLead"></div></div>
        </div>
      </div>
      <div class="foot" id="lubeNote"></div>
    </section>

    <section class="page" id="pg-delays" hidden>
      <div class="section">
        <h2 class="prodhd">Delays &amp; Standbys <span class="sub" id="dssub"></span></h2>
        <div id="dsBody"></div>
        <div class="foot"><b id="dsFleet">Cat 797</b> <b>Delay</b> and <b>Standby</b> reasons for the selected shift/view, <b>grouped by status</b> and sorted by actual impact (biggest first). <b>Actual % / Target %</b> = each reason's actual (and budgeted `ExpectedDuration`) time as its <b>impact on the affected metric</b> — Delays on <b>OE</b> (÷ Ready+Delay), Standbys on <b>UA</b> (÷ Ready+Delay+Standby); Target blank where no standard exists. <b>+/− tonnes</b> = (target − actual) time × the fleet's TPNOH — green = under target (gain), red = over target (loss); reasons with no target count fully as loss. Data includes only large shovels and trucks.</div>
        <button class="owbtn" id="dsParBtn" onclick="toggleDsChart('par')">Lost-Time Pareto &#9662;</button>
        <button class="owbtn" id="dsDvBtn" onclick="toggleDsChart('dv')">Delay Variance &#9662;</button>
      </div>
      <div class="section" id="dsParSec" hidden>
        <h2>Lost Time — Delay Pareto <span class="sub" id="dsparsub"></span></h2>
        <div class="chartcard widecard"><div class="chartwrap"><canvas id="chPar"></canvas></div></div>
        <div class="foot">Delay hours by reason for this waterfall's equipment, biggest first, with a cumulative-% line.</div>
      </div>
      <div class="section" id="dsDvSec" hidden>
        <h2>Delay Variance — Actual vs Expected <span class="sub" id="dsdvsub"></span></h2>
        <div class="chartcard widecard"><div class="chartwrap"><canvas id="chDelayVar"></canvas></div></div>
        <div class="foot">Signed <b>actual − expected</b> delay hours by reason for this waterfall's equipment — <span style="color:#e23b32">red up = over-run</span>, <span style="color:#1f9e8b">green down = under</span>; only reasons with an `ExpectedDuration` standard appear.</div>
      </div>
    </section>

    <section class="page" id="pg-shovprod" hidden>
      <div class="section">
        <h2 class="prodhd"><span>Shovel Productivity</span><span class="tptoggles"><button class="tgl on" id="spTgtBtn" onclick="toggleProd('sp','tgt')">Target</button><button class="tgl on" id="spDltBtn" onclick="toggleProd('sp','dlt')">+/− t</button></span><span class="sub" id="spsub"></span></h2>
        <div id="spBody"></div>
        <div class="foot">Per-shovel KPI scorecard for the selected shift/view — <b>All Shovels</b> plus one column per active unit, each split <b>Target · Actual · +/− tonnes</b>. The header <b>score badge</b> = Actual ÷ Target loaded (<span style="color:#2f8f4e">≥90 green</span> · <span style="color:#c98a1f">70–90 amber</span> · <span style="color:#c0392b">&lt;70 red</span>). <b>Total Loaded target</b> = scheduled potential = total/calendar hours × budget <b>PA</b> × <b>UA</b> × <b>OE</b> × budget <b>Dig Rate</b> (TPNOH); PA + UA + OE + Dig Rate +/− tonnes sum to the Total Loaded gap. <b>NOH</b> = net operating hrs (Ready), <b>NOH %</b> = NOH ÷ TH. Cycle rows (Hang/Spot/Load, mm:ss) show the separate cycle-time tonnage impact. <b>Payload — CAT 797</b> compares the average weighed payload to the 361 t target (+/− tonnes = payload cycle impact). Payload quality vs the 361 t target uses the 10-10-20 rule: <b>&gt;120%</b> should be 0%, <b>110–120%</b> under 10%, <b>Underloads</b> (70–90%) target 5%. +/− tonnes shaded green = gain, red = loss. Data includes only large shovels and trucks.</div>
      </div>
    </section>

    <section class="page" id="pg-truckprod" hidden>
      <div class="section">
        <h2 class="prodhd"><span>Truck Productivity</span><span class="tptoggles"><button class="tgl on" id="tpTgtBtn" onclick="toggleProd('tp','tgt')">Target</button><button class="tgl on" id="tpDltBtn" onclick="toggleProd('tp','dlt')">+/− t</button></span><span class="sub" id="tpsub"></span></h2>
        <div id="tpBody"></div>
        <div class="foot">Cat 797 truck KPI scorecard for the selected shift/view — <b>All Trucks</b> plus one column per active <b>shovel</b> (grouping all trucks that loaded at that shovel), each split <b>Target · Actual · +/− tonnes</b>. The header <b>score badge</b> = Actual ÷ cycle-potential (<span style="color:#2f8f4e">≥90 green</span> · <span style="color:#c98a1f">70–90 amber</span> · <span style="color:#c0392b">&lt;70 red</span>). <b>Total Moved target</b> = payload-normalised cycle potential (rate* × actual cycle seconds; rate* = 361 t ÷ budget cycle). The availability rows use the same PA/UA/OE formulas and sequential tonnes decomposition as the methodology; cycle rows use payload-normalized truck-cycle budgets (361 t basis), and payload quality follows the 10-10-20 rule. Data includes only large shovels and trucks.</div>
      </div>
    </section>

    <section class="page" id="pg-hourlyperf" hidden>
      <div class="section">
        <h2>Hourly Performance <span class="sub" id="hpsub"></span></h2>
        <div id="hpBody"></div>
        <div class="foot">Per-hour breakdown of production and cycle KPIs for the selected shift/view (loads bucketed by dump time). <b>Total</b> = shift sum (tonnes/count) or shift average (times/payload). Cells are shaded vs target — <span style="color:#4a8f2f">green = better</span>, <span style="color:#cc4b4b">red = worse</span>: production higher-is-better vs plan/12; cycle times lower-is-better vs the load-weighted budget; payload vs 361 t; Shovel Hang vs the shift average. Load Count is unshaded.</div>
      </div>
    </section>

    <section class="page" id="pg-shiftstats" hidden>
      <div class="section">
        <h2>Shift Stats <span class="sub" id="sssub"></span></h2>
        <div id="ssBody"></div>
        <div class="foot">Consolidated stats for the selected shift/view, in three tables: <b>Loaded haul paths</b> (Load&nbsp;→&nbsp;Dump, top 12 by cycle time), <b>Empty haul legs</b> (prev&nbsp;dump&nbsp;→&nbsp;dig, top 12 by empty time), and <b>Shovels</b>. Each value is the average over loads where the metric is present (≥5 loads), shaded <span style="color:#2f8f4e">green = better than budget</span> / <span style="color:#c0392b">red = worse</span> (hover a cell for its target). Distances km, times mm:ss. The <span style="color:#41419e">▮▮▮</span> button beside a waterfall KPI jumps here and highlights the related table.</div>
      </div>
    </section>

    <section class="page" id="pg-appendix" hidden>
      <div class="section">
        <h2>Appendix — Target &amp; Budget Numbers <span class="sub" id="apxsub"></span></h2>
        <div class="foot">All targets/budgets used by the dashboard for the <b>selected shift</b>. Availability and rate budgets come from <code>&lt;Pit&gt; 2026 Budget.csv</code>; cycle fixed times from <code>&lt;Pit&gt;_Fixed_Times.csv</code>; haul curve from <code>&lt;Pit&gt; Haul Curve.csv</code>. Budgets are keyed by month (curve, fixed times) or by month·date·shift (availability, plan tonnes).</div>
        <div id="appendixBody"></div>
      </div>
    </section>

  </main>
</div>
<div class="foot" id="gen"></div>
</div>
__CHARTJS__
<script>
const DATA = __DATA__;
let view = 'Combined';
let shift = DATA.defaultShift;
let selShovelId = null;   // haulage drill-down: selected shovel (from summary cards)
function SD(){return DATA.byShift[shift];}
function V(){return SD().views[view];}
const fmt = n => Math.round(n).toLocaleString();
const fmtT = n => (n>=0?'+':'')+Math.round(n).toLocaleString();

function renderShiftNav(){
  const el=document.getElementById('shiftnav');
  const idx=DATA.shifts.findIndex(s=>s.id===shift);
  el.innerHTML=`<button id="shPrev" title="Older shift">&#8249;</button>`+
    `<select id="shSel">`+DATA.shifts.map(s=>`<option value="${s.id}"${s.id===shift?' selected':''}>${s.name}${s.crew?' · '+s.crew:''}</option>`).join('')+`</select>`+
    `<button id="shNext" title="Newer shift">&#8250;</button>`;
  document.getElementById('shSel').onchange=e=>{shift=e.target.value;saveState();renderAll();};
  const pv=document.getElementById('shPrev'),nx=document.getElementById('shNext');
  pv.disabled=idx>=DATA.shifts.length-1; nx.disabled=idx<=0;
  pv.onclick=()=>{if(idx<DATA.shifts.length-1){shift=DATA.shifts[idx+1].id;saveState();renderAll();}};
  nx.onclick=()=>{if(idx>0){shift=DATA.shifts[idx-1].id;saveState();renderAll();}};
}
function renderToggle(){
  const t=document.getElementById('toggle'); t.innerHTML='';
  ['MRM','JPM','Combined'].forEach(v=>{
    const b=document.createElement('button'); b.textContent=v; if(v===view)b.className='on';
    b.onclick=()=>{view=v;saveState();renderAll();}; t.appendChild(b);
  });
}
// ---- state persistence + deep-linking (shift · view · tab · productivity toggles) ----
function saveState(){
  try{localStorage.setItem('albianDash',JSON.stringify({shift,view,tab,tp:PROD.tp,sp:PROD.sp,sbAuto}));}catch(e){}
  try{history.replaceState(null,'','#tab='+encodeURIComponent(tab)+'&shift='+encodeURIComponent(shift)+'&view='+encodeURIComponent(view));}catch(e){}
}
function loadState(){
  let st={};
  try{const s=localStorage.getItem('albianDash'); if(s)st=JSON.parse(s)||{};}catch(e){}
  const hash=(location.hash||'').replace(/^#/,'');   // URL hash overrides saved state (shareable deep-link)
  if(hash)hash.split('&').forEach(kv=>{const p=kv.split('=');if(p[0]&&p[1]!==undefined)st[p[0]]=decodeURIComponent(p[1]);});
  if(st.view&&['MRM','JPM','Combined'].indexOf(st.view)>=0)view=st.view;
  if(st.shift&&DATA.byShift[st.shift])shift=st.shift;
  if(st.tab&&TABS.some(t=>t[0]===st.tab))tab=st.tab;
  if(st.tp){PROD.tp.tgt=st.tp.tgt!==false;PROD.tp.dlt=st.tp.dlt!==false;}
  if(st.sp){PROD.sp.tgt=st.sp.tgt!==false;PROD.sp.dlt=st.sp.dlt!==false;}
  if(typeof st.sbAuto==='boolean')sbAuto=st.sbAuto;
}
function scoreColor(s){ return s<90?'#e23b32':(s<100?'#eab308':'#4caf50'); }   // <90 red · 90-100 yellow · >=100 green
function gaugeCard(title,score,pot,act,potLbl,actLbl){
  const fill=Math.max(0,Math.min(100,score));
  return `<div class="card">
    <h3><span>${title}</span><span class="pct" style="color:${scoreColor(score)}">${score.toFixed(0)} %</span></h3>
    <div class="bar"><span style="width:${fill}%;background:${scoreColor(score)}"></span></div>
    <div class="kv"><span>${potLbl}</span><b>${fmt(pot)}</b></div>
    <div class="kv"><span>${actLbl}</span><b>${fmt(act)}</b></div></div>`;
}
function truckMatchCard(haulScore,loadScore){
  const diff=loadScore - haulScore;   // Loading − Haulage: +over-trucked, −under-trucked
  const label=Math.abs(diff)<=3?'Balanced':(diff<0?'Under-Trucked':'Over-Trucked');
  const col=label==='Balanced'?'var(--green)':'#e0952a';
  const mag=Math.min(48,Math.abs(diff)*2.5), left=diff<0?50-mag:50;
  return `<div class="card">
    <h3><span>Truck Match</span><span class="pct" style="color:${col}">${(diff>=0?'+':'−')+Math.abs(diff).toFixed(0)} %</span></h3>
    <div class="bar"><span style="left:${left}%;width:${mag}%;background:${col}"></span><div class="marker" style="left:50%"></div></div>
    <div class="mmlabels"><span>◄ under-trucked</span><span>over-trucked ►</span></div>
    <div class="label-center" style="color:${col}">${label}</div>
    <div class="kv"><span>Haulage Score</span><b>${haulScore.toFixed(0)}%</b></div>
    <div class="kv"><span>Loading Score</span><b>${loadScore.toFixed(0)}%</b></div></div>`;
}
function balanceCard(tbd){
  const mag=Math.min(50,Math.abs(tbd.pct)); const center=50;
  const left=tbd.pct>0?center-mag:center; const w=mag;
  const col=tbd.label==='Balanced'?'var(--green)':'var(--red)';
  const asof=tbd.asOf?` <span class="sub" style="font-weight:400;color:var(--muted)">as of ${tbd.asOf}</span>`:'';
  return `<div class="card">
    <h3><span>Truck Balance${asof}</span><span class="pct">${Math.abs(tbd.pct).toFixed(0)} %</span></h3>
    <div class="bar"><span style="left:${left}%;width:${w}%;background:${col}"></span>
      <div class="marker" style="left:50%"></div></div>
    <div class="label-center" style="color:${col}">${tbd.label}</div>
    <div class="kv"><span>Trucks Required</span><b>${tbd.required.toFixed(1)}</b></div>
    <div class="kv"><span>Trucks Actual (LP)</span><b>${tbd.actual.toFixed(1)}</b></div></div>`;
}
function balanceCard2(tbd,fm){
  const pct=tbd.pct, label=tbd.label;
  const col=label==='Balanced'?'var(--green)':'#e0952a';
  const mag=Math.min(48,Math.abs(pct)*2.0), left=pct>0?50-mag:50;   // pct>0 = under-trucked → left
  const asof=tbd.asOf?` <span class="sub" style="font-weight:400;color:var(--muted)">as of ${tbd.asOf}</span>`:'';
  let wt='';
  if(fm){const he=fm.hangAvg-(fm.hangBud||0),qe=fm.queueAvg-(fm.queueBud||0),sig=he-qe;
    const w=Math.abs(sig)<8?'matched':(sig>0?'shovels starved':'trucks queuing');
    wt=`<div class="kv"><span>Wait signal</span><b style="color:${Math.abs(sig)<8?'var(--green)':'#e0952a'}">${w}</b></div>`;}
  return `<div class="card">
    <h3><span>Truck Balance${asof}</span><span class="pct" style="color:${col}">${Math.abs(pct).toFixed(0)} %</span></h3>
    <div class="bar"><span style="left:${left}%;width:${mag}%;background:${col}"></span><div class="marker" style="left:50%"></div></div>
    <div class="mmlabels"><span>◄ under-trucked</span><span>over-trucked ►</span></div>
    <div class="label-center" style="color:${col}">${label}</div>
    <div class="kv"><span>Trucks Required (LP)</span><b>${tbd.required.toFixed(1)}</b></div>
    <div class="kv"><span>Trucks Actual</span><b>${tbd.actual.toFixed(1)}</b></div>
    ${wt}
    <div class="kv" style="border:0"><span style="font-size:10px;color:var(--muted)">details → Fleet Match tab</span><b></b></div></div>`;
}
function ssec(v){return (v<0?'−':'')+fmtTime(Math.abs(Math.round(v)));}
function drawBottleneck(fm){
  if(!fm) return '<div class="foot">No fleet-match data for this view.</div>';
  const rows=[['Loading capacity',fm.loadCap,'#c77'],['Haulage capacity',fm.haulCap,'#3f51b5'],['Actual moved',fm.actual,'#2f8f4e']];
  const mx=Math.max(fm.loadCap,fm.haulCap,fm.actual)*1.10||1;
  const W=1000,L=150,Rp=100,T=14,rowH=42,bw=W-L-Rp,H=T+3*rowH+22;
  const bindTrucks=fm.haulCap<fm.loadCap, bind=bindTrucks?'Haulage (trucks)':'Loading (shovels)';
  const bc=Math.min(fm.loadCap,fm.haulCap), bx=L+bc/mx*bw;
  let g=`<rect x="${bx}" y="${T}" width="${L+bw-bx}" height="${3*rowH-8}" fill="#e0952a" fill-opacity="0.06"/>`;
  rows.forEach((r,i)=>{const y=T+i*rowH,w=r[1]/mx*bw;
    g+=`<text x="${L-10}" y="${y+24}" text-anchor="end" font-size="11.5" fill="var(--ink)">${r[0]}</text>`;
    g+=`<rect x="${L}" y="${y+8}" width="${Math.max(1,w)}" height="24" rx="3" fill="${r[2]}" fill-opacity="0.85"/>`;
    g+=`<text x="${L+w+7}" y="${y+25}" font-size="11" font-weight="700" fill="${r[2]}">${fmt(r[1])} t</text>`;});
  g+=`<line x1="${bx}" y1="${T}" x2="${bx}" y2="${T+3*rowH-6}" stroke="#e0952a" stroke-width="1.6" stroke-dasharray="4 3"/>`;
  g+=`<text x="${bx}" y="${T+3*rowH+14}" text-anchor="middle" font-size="10" fill="#e0952a">binding capacity ${fmt(bc)} t</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg>`
    +`<div class="badges"><span class="badge"><b>${bind}</b> is the constraint</span><span class="badge">capacity headroom <b>${fmt(Math.abs(fm.loadCap-fm.haulCap))} t</b></span><span class="badge">actual = <b>${(fm.actual/bc*100).toFixed(0)}%</b> of binding capacity</span></div>`;
}
function drawWaitBalance(fm){
  if(!fm) return '<div class="foot">No fleet-match data for this view.</div>';
  const he=fm.hangAvg-(fm.hangBud||0), qe=fm.queueAvg-(fm.queueBud||0), sig=he-qe;
  const verdict=Math.abs(sig)<8?'Matched':(sig>0?'Under-trucked — shovels starved':'Over-trucked — trucks queuing');
  const vcol=Math.abs(sig)<8?'var(--green)':'#e0952a';
  const W=1000,cx=W/2,T=34,bh=34,H=T+bh+58,half=cx-100;
  const mxE=Math.max(20,Math.abs(he),Math.abs(qe))*1.3, toW=e=>Math.min(half,Math.abs(e)/mxE*half);
  const lw=toW(he>0?he:0), rw=toW(qe>0?qe:0);
  let g=`<line x1="${cx}" y1="${T-10}" x2="${cx}" y2="${T+bh+8}" stroke="#98a0ac" stroke-width="1.4"/>`;
  g+=`<rect x="${cx-lw}" y="${T}" width="${lw}" height="${bh}" fill="#e23b32" fill-opacity="0.8"/>`;
  g+=`<rect x="${cx}" y="${T}" width="${rw}" height="${bh}" fill="#3f51b5" fill-opacity="0.8"/>`;
  g+=`<text x="${cx-half}" y="${T-14}" font-size="11.5" fill="#e23b32">◄ shovels starved (hang &gt; budget)</text>`;
  g+=`<text x="${cx+half}" y="${T-14}" text-anchor="end" font-size="11.5" fill="#3f51b5">trucks queuing (queue &gt; budget) ►</text>`;
  g+=`<text x="${cx-10}" y="${T+bh+20}" text-anchor="end" font-size="10.5" fill="var(--ink)">hang ${ssec(fm.hangAvg)} · budget ${ssec(fm.hangBud)} · excess ${ssec(he)}</text>`;
  g+=`<text x="${cx+10}" y="${T+bh+20}" font-size="10.5" fill="var(--ink)">queue ${ssec(fm.queueAvg)} · budget ${ssec(fm.queueBud)} · excess ${ssec(qe)}</text>`;
  const both=(he>8&&qe>8)?`<span class="badge">both sides above budget → general congestion</span>`:'';
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg>`
    +`<div class="label-center" style="color:${vcol};font-weight:700;font-size:14px;margin-top:2px">${verdict}</div>`
    +`<div class="badges" style="justify-content:center"><span class="badge">per-load averages · ${fm.nLoads} loads</span>${both}</div>`;
}
function smoothPath(pts){   // Catmull-Rom → cubic bezier for a smooth line through pts=[[x,y],...]
  if(pts.length<3) return pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
  let d='M'+pts[0][0].toFixed(1)+' '+pts[0][1].toFixed(1);
  for(let i=0;i<pts.length-1;i++){const p0=pts[i-1]||pts[i],p1=pts[i],p2=pts[i+1],p3=pts[i+2]||p2;
    const c1x=p1[0]+(p2[0]-p0[0])/6,c1y=p1[1]+(p2[1]-p0[1])/6,c2x=p2[0]-(p3[0]-p1[0])/6,c2y=p2[1]-(p3[1]-p1[1])/6;
    d+=' C'+c1x.toFixed(1)+' '+c1y.toFixed(1)+','+c2x.toFixed(1)+' '+c2y.toFixed(1)+','+p2[0].toFixed(1)+' '+p2[1].toFixed(1);}
  return d;
}
let opMode='dep';   // per-hour readout mode: 'dep' = Deployed (Ready+Delay) · 'prod' = Productive (Ready only)
function opStrip(X,L,plotBottom,od){
  // per-hour effective units operating in a text box at the base of the plot; Truck#/Shovel# labels sit left of the y-axis.
  if(!od||!od.trucks||!od.trucks.length) return '';
  const bx=L+1,bw=X(720)-L-2,bh=30,by=plotBottom-bh,r1=by+13,r2=by+26;
  const tr=opMode==='dep'?od.trucks:od.trucksP, sh=opMode==='dep'?od.shovels:od.shovelsP;
  let g=`<rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="3" fill="#ffffff" fill-opacity="0.84" stroke="#dfe3ea" stroke-width="0.5"/>`
    +`<text x="${L-4}" y="${r1}" text-anchor="end" font-size="8.5" font-weight="600" fill="#243b8a">Truck#</text>`
    +`<text x="${L-4}" y="${r2}" text-anchor="end" font-size="8.5" font-weight="600" fill="#0f6e56">Shovel#</text>`;
  for(let i=0;i<12;i++){const x=X((i+0.5)*60);
    g+=`<text x="${x}" y="${r1}" text-anchor="middle" font-size="11.5" fill="#243b8a">${Math.round(tr[i])}</text>`
      +`<text x="${x}" y="${r2}" text-anchor="middle" font-size="11.5" fill="#0f6e56">${sh[i].toFixed(1)}</text>`;}
  return g;
}
function toggleOpMode(){ opMode=(opMode==='dep'?'prod':'dep'); syncOpBtns(); if(typeof renderCards==='function')renderCards(); }
function syncOpBtns(){document.querySelectorAll('.opModeBtn').forEach(b=>{b.textContent=(opMode==='dep'?'Showing: by GOH':'Showing: by NOH');});}
function drawFleetTimeline(fm,tbd,showRate){
  const s=tbd&&tbd.series||[];
  if(s.length<2) return '<div class="foot">Not enough truck-balance readings for a timeline (needs ≥ 2).</div>';
  showRate=(showRate!==false);   // false → omit the t/h rate line, its target, right axis and legend
  const base=tbd.base!=null?tbd.base:6;
  const W=1000,L=46,Rp=54,T=16,TOT=720,pw=W-L-Rp;
  const ph=176, bandGap=26, bandH=64, opH=32;                 // trucks plot, then a hang/queue band, then the #/hr strip
  const bandTop=T+ph+bandGap, bandMid=bandTop+bandH/2, bandBot=bandTop+bandH, H=bandBot+opH+12;
  const maxTrk=(Math.max(1,...s.map(p=>Math.max(p.req,p.act))))*1.15;
  const rate=(fm&&fm.rate)||[], maxR=Math.max(fm?fm.targetTph:1,...rate.map(r=>r.tph),1)*1.15;
  const X=m=>L+m/TOT*pw, Ytrk=v=>T+ph-v/maxTrk*ph, Yr=v=>T+ph-v/maxR*ph;
  let g='';
  const trkLblStep=maxTrk<25?5:10;   // minor gridline every 5 trucks; label every 5 or 10
  for(let v=0;v<=maxTrk;v+=5){const y=Ytrk(v),lbl=(v%trkLblStep===0);g+=`<line x1="${L}" y1="${y}" x2="${L+pw}" y2="${y}" stroke="${lbl?'#e3e6eb':'#eef0f4'}" stroke-width="${lbl?0.9:0.5}"/>${lbl?`<text x="${L-6}" y="${y+4}" text-anchor="end" font-size="12" fill="#3f51b5">${v}</text>`:''}`;}
  if(showRate) for(let i=1;i<=4;i++){const v=maxR*i/4,y=Yr(v);g+=`<text x="${L+pw+5}" y="${y+4}" font-size="12" fill="#2f8f4e">${(v/1000).toFixed(0)}k</text>`;}
  for(let hh=0;hh<=12;hh++){const x=X(hh*60);g+=`<line x1="${x}" y1="${T}" x2="${x}" y2="${T+ph}" stroke="#e8ebf0" stroke-width="0.9"/><text x="${x}" y="${T+ph+15}" text-anchor="middle" font-size="12" fill="var(--muted)">${(base+hh)%24}:00</text>`;}
  // last-hour window: very light shading (the same window the balance card scores)
  const lastM=Math.max(...s.map(p=>p.m)), lx0=X(Math.max(0,lastM-60)), lx1=X(lastM);
  g+=`<rect x="${lx0}" y="${T}" width="${Math.max(0,lx1-lx0)}" height="${ph}" fill="#586172" fill-opacity="0.06"/>`;
  g+=`<line x1="${lx0}" y1="${T}" x2="${lx0}" y2="${T+ph}" stroke="#9aa0ac" stroke-width="0.8" stroke-dasharray="2 2" opacity="0.6"/>`;
  // shade actual-vs-required: red = under-trucked, blue = over-trucked. Split each segment at the exact
  // crossover so the colour never bleeds past where the two lines meet.
  const CU=d=>d<0?'#e23b32':'#3f51b5';   // act−req < 0 → under (red) · ≥ 0 → over (blue)
  for(let i=0;i<s.length-1;i++){const a=s[i],b=s[i+1];
    const xa=X(a.m),xb=X(b.m),ra=Ytrk(a.req),rb=Ytrk(b.req),aa=Ytrk(a.act),ab=Ytrk(b.act);
    const da=a.act-a.req,db=b.act-b.req;
    if((da<0)===(db<0)||da===0||db===0){   // no sign change within the segment → one colour
      g+=`<path d="M${xa.toFixed(1)} ${ra.toFixed(1)} L${xb.toFixed(1)} ${rb.toFixed(1)} L${xb.toFixed(1)} ${ab.toFixed(1)} L${xa.toFixed(1)} ${aa.toFixed(1)} Z" fill="${CU(da||db)}" fill-opacity="0.13"/>`;
    } else {                                 // lines cross → split at the intersection (two triangles)
      const t=da/(da-db),xc=xa+t*(xb-xa),yc=ra+t*(rb-ra);
      g+=`<path d="M${xa.toFixed(1)} ${ra.toFixed(1)} L${xc.toFixed(1)} ${yc.toFixed(1)} L${xa.toFixed(1)} ${aa.toFixed(1)} Z" fill="${CU(da)}" fill-opacity="0.13"/>`;
      g+=`<path d="M${xc.toFixed(1)} ${yc.toFixed(1)} L${xb.toFixed(1)} ${rb.toFixed(1)} L${xb.toFixed(1)} ${ab.toFixed(1)} Z" fill="${CU(db)}" fill-opacity="0.13"/>`;
    }
  }
  if(showRate&&rate.length){const tph=rate.map(r=>r.tph);   // 3-pt moving average then smooth spline
    const sm=tph.map((_,i)=>{const a=Math.max(0,i-1),b=Math.min(tph.length-1,i+1);let s=0;for(let j=a;j<=b;j++)s+=tph[j];return s/(b-a+1);});
    const pts=rate.map((r,i)=>[X(r.m),Yr(sm[i])]);
    g+=`<path d="${smoothPath(pts)}" fill="none" stroke="#2f8f4e" stroke-width="1.6" opacity="0.9"/>`;
    if(fm){const ty=Yr(fm.targetTph);g+=`<line x1="${L}" y1="${ty}" x2="${L+pw}" y2="${ty}" stroke="#2f8f4e" stroke-width="1.1" stroke-dasharray="6 3" opacity="0.7"/>`;}}
  const pathOf=k=>s.map((p,i)=>(i?'L':'M')+X(p.m).toFixed(1)+' '+Ytrk(p[k]).toFixed(1)).join(' ');
  g+=`<path d="${pathOf('req')}" fill="none" stroke="#e0952a" stroke-width="1.8" stroke-dasharray="5 3"/>`;
  g+=`<path d="${pathOf('act')}" fill="none" stroke="#3f51b5" stroke-width="1.9"/>`;
  g+=`<text x="12" y="${T+ph/2}" transform="rotate(-90 12 ${T+ph/2})" text-anchor="middle" font-size="11" fill="#3f51b5">trucks</text>`;
  if(showRate) g+=`<text x="${W-8}" y="${T+ph/2}" transform="rotate(90 ${W-8} ${T+ph/2})" text-anchor="middle" font-size="11" fill="#2f8f4e">t/h</text>`;
  // ── hang/queue per-load band, sharing the time axis (hang up / queue down; green ≤ target, red hang / blue queue over) ──
  const lw=(fm&&fm.loadWaits)||[], hb=fm.hangBud||0, qbud=fm.queueBud||0;
  if(lw.length){
    const hsrt=lw.map(p=>p[1]).sort((a,b)=>a-b), qsrt=lw.map(p=>p[2]).sort((a,b)=>a-b);
    const p97=a=>a[Math.min(a.length-1,Math.floor(a.length*0.97))]||0;
    const maxV=Math.max(60,p97(hsrt),p97(qsrt),hb*1.4,qbud*1.4);
    const Yu=v=>bandMid-Math.min(v,maxV)/maxV*(bandH/2), Yd=v=>bandMid+Math.min(v,maxV)/maxV*(bandH/2);
    const bw=Math.max(0.6,Math.min(3,pw/lw.length)), GRN='#2f8f4e',RED='#e23b32',BLU='#3f51b5';
    for(let hh=0;hh<=12;hh++){const x=X(hh*60);g+=`<line x1="${x}" y1="${bandTop}" x2="${x}" y2="${bandBot}" stroke="#eef0f4" stroke-width="0.6"/>`;}
    lw.forEach(p=>{const x=X(p[0]);
      if(p[1]>0){const y=Yu(p[1]);g+=`<rect x="${(x-bw/2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(bandMid-y).toFixed(1)}" fill="${p[1]<=hb?GRN:RED}" fill-opacity="0.7"/>`;}
      if(p[2]>0){const y=Yd(p[2]);g+=`<rect x="${(x-bw/2).toFixed(1)}" y="${bandMid.toFixed(1)}" width="${bw.toFixed(1)}" height="${(y-bandMid).toFixed(1)}" fill="${p[2]<=qbud?GRN:BLU}" fill-opacity="0.7"/>`;}});
    g+=`<line x1="${L}" y1="${Yu(hb).toFixed(1)}" x2="${L+pw}" y2="${Yu(hb).toFixed(1)}" stroke="#8a2c22" stroke-width="1.1" stroke-dasharray="5 3"><title>hang target ${fmtTime(hb)}</title></line>`;
    g+=`<line x1="${L}" y1="${Yd(qbud).toFixed(1)}" x2="${L+pw}" y2="${Yd(qbud).toFixed(1)}" stroke="#243b8a" stroke-width="1.1" stroke-dasharray="5 3"><title>queue target ${fmtTime(qbud)}</title></line>`;
    g+=`<line x1="${L}" y1="${bandMid}" x2="${L+pw}" y2="${bandMid}" stroke="#98a0ac" stroke-width="1"/>`;
    g+=`<text x="12" y="${bandMid}" transform="rotate(-90 12 ${bandMid})" text-anchor="middle" font-size="9" fill="var(--muted)">min/load</text>`;
    g+=`<text x="${L+3}" y="${bandTop+9}" font-size="8.5" fill="var(--muted)">▲ hang</text>`;
    g+=`<text x="${L+3}" y="${bandBot-3}" font-size="8.5" fill="var(--muted)">▼ queue</text>`;
  }
  // last-hour status pill (Balanced / Under- / Over-trucked) — always shows the % magnitude
  const lbl=tbd.label||'', lcol=lbl==='Balanced'?'#2f8f4e':'#e0952a';
  const full='last hr · '+(lbl||'—')+(lbl?' '+Math.abs(tbd.pct||0).toFixed(0)+'%':'');
  const bh2=22, bwid=full.length*7.2+30, bcx=(lx0+lx1)/2, bx=Math.max(L+2,Math.min(L+pw-bwid-2,bcx-bwid/2)), byy=T+3;
  g+=`<rect x="${bx}" y="${byy}" width="${bwid}" height="${bh2}" rx="11" fill="#fff" fill-opacity="0.94" stroke="${lcol}" stroke-width="1.4"/>`;
  g+=`<circle cx="${bx+13}" cy="${byy+bh2/2}" r="3.8" fill="${lcol}"/>`;
  g+=`<text x="${bx+22}" y="${byy+bh2/2+4.6}" font-size="13.5" font-weight="700" fill="${lcol}">${full}</text>`;
  g+=opStrip(X,L,H-2,(typeof V==='function'&&V())?V().opDeployed:null);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px">`
    +`<span class="badge"><b style="color:#e0952a">– –</b> required</span><span class="badge"><b style="color:#3f51b5">—</b> actual</span>`
    +`<span class="badge"><b style="color:#e23b32">▨</b> under-trucked</span><span class="badge"><b style="color:#3f51b5">▨</b> over-trucked</span>`
    +(showRate?`<span class="badge"><b style="color:#2f8f4e">—</b> t/h</span>`:``)
    +`<span class="badge">band = hang/queue per load · <b style="color:#2f8f4e">■</b> ≤ target · <b style="color:#e23b32">■</b> hang over · <b style="color:#3f51b5">■</b> queue over</span></div>`;
}
function drawWaitBalanceTL(fm,tbd){
  // Hang/Queue time over the shift (the Fleet-Match diverging bar, on a time axis). Same x-axis as the balance graph.
  // Above zero (red) = shovel hang above budget → shovels starved → under-trucked. Below (blue) = truck queue above budget → over-trucked.
  const s=(fm&&fm.waitSeries)||[];
  if(s.length<2) return '<div class="foot">Not enough loads for a wait-time timeline this view.</div>';
  const base=(tbd&&tbd.base!=null)?tbd.base:6;
  const qb=fm.queueBud||0, hb=fm.hangBud||0;
  const he=s.map(p=>Math.max(0,p.h-hb)), qe=s.map(p=>Math.max(0,p.q-qb));
  const maxE=Math.max(30,...he,...qe)*1.12;
  const W=1000,H=236,L=52,Rp=54,T=16,B=40,pw=W-L-Rp,ph=H-T-B,TOT=720,mid=T+ph/2;
  const X=m=>L+m/TOT*pw, Yup=v=>mid-v/maxE*(ph/2), Ydn=v=>mid+v/maxE*(ph/2);
  let g='';
  for(let hh=0;hh<=12;hh++){const x=X(hh*60);g+=`<line x1="${x}" y1="${T}" x2="${x}" y2="${T+ph}" stroke="#e8ebf0" stroke-width="0.9"/><text x="${x}" y="${T+ph+15}" text-anchor="middle" font-size="12" fill="var(--muted)">${(base+hh)%24}:00</text>`;}
  // minor gridlines + labels every 1 minute (up = hang, down = queue); unit shown in the axis title
  for(let v=60;v<=maxE;v+=60){const yu=Yup(v),yd=Ydn(v),m=v/60;
    g+=`<line x1="${L}" y1="${yu}" x2="${L+pw}" y2="${yu}" stroke="#f3ecec" stroke-width="0.5"/><text x="${L-6}" y="${yu+4}" text-anchor="end" font-size="12" fill="#e23b32">${m}</text>`;
    g+=`<line x1="${L}" y1="${yd}" x2="${L+pw}" y2="${yd}" stroke="#eaecf3" stroke-width="0.5"/><text x="${L-6}" y="${yd+4}" text-anchor="end" font-size="12" fill="#3f51b5">${m}</text>`;}
  g+=`<text x="13" y="${mid}" transform="rotate(-90 13 ${mid})" text-anchor="middle" font-size="11" fill="var(--muted)">min/load</text>`;
  // hang excess (red, above zero)
  let up='M'+X(s[0].m).toFixed(1)+' '+mid;
  s.forEach((p,i)=>{up+=' L'+X(p.m).toFixed(1)+' '+Yup(he[i]).toFixed(1);});
  up+=' L'+X(s[s.length-1].m).toFixed(1)+' '+mid+' Z';
  g+=`<path d="${up}" fill="#e23b32" fill-opacity="0.22" stroke="#e23b32" stroke-width="1.4"/>`;
  // queue excess (blue, below zero)
  let dn='M'+X(s[0].m).toFixed(1)+' '+mid;
  s.forEach((p,i)=>{dn+=' L'+X(p.m).toFixed(1)+' '+Ydn(qe[i]).toFixed(1);});
  dn+=' L'+X(s[s.length-1].m).toFixed(1)+' '+mid+' Z';
  g+=`<path d="${dn}" fill="#3f51b5" fill-opacity="0.20" stroke="#3f51b5" stroke-width="1.4"/>`;
  g+=`<line x1="${L}" y1="${mid}" x2="${L+pw}" y2="${mid}" stroke="#98a0ac" stroke-width="1.2"/>`;
  // zero baseline label (per-minute labels drawn with the gridlines above)
  g+=`<text x="${L-6}" y="${mid+4}" text-anchor="end" font-size="12" fill="var(--muted)">0</text>`;
  g+=`<text x="${L+5}" y="${T+11}" font-size="11" fill="#e23b32">▲ shovels starved (hang &gt; budget) — under-trucked</text>`;
  g+=`<text x="${L+5}" y="${mid+16}" font-size="11" fill="#3f51b5">▼ trucks queuing (queue &gt; budget) — over-trucked</text>`;
  // dots
  s.forEach((p,i)=>{const hhm=(base+Math.floor(p.m/60))%24, mm=String(p.m%60).padStart(2,'0');
    if(he[i]>0)g+=`<circle cx="${X(p.m).toFixed(1)}" cy="${Yup(he[i]).toFixed(1)}" r="1.5" fill="#e23b32"><title>${hhm}:${mm} — hang ${fmtTime(p.h)} vs bud ${fmtTime(hb)} (+${fmtTime(he[i])})</title></circle>`;
    if(qe[i]>0)g+=`<circle cx="${X(p.m).toFixed(1)}" cy="${Ydn(qe[i]).toFixed(1)}" r="1.5" fill="#3f51b5"><title>${hhm}:${mm} — queue ${fmtTime(p.q)} vs bud ${fmtTime(qb)} (+${fmtTime(qe[i])})</title></circle>`;});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px">`
    +`<span class="badge"><b style="color:#e23b32">▨</b> Shovel hang over target</span>`
    +`<span class="badge"><b style="color:#3f51b5">▨</b> Truck queue over target</span>`
    +`<span class="badge">budget: queue ${fmtTime(qb)} · hang ${fmtTime(hb)} per load</span></div>`;
}
function drawWaitBars(fm,tbd){
  // Per-load bars over the shift: shovel HANG up (+), truck QUEUE down (−). Bar green ≤ target, red > target.
  const lw=(fm&&fm.loadWaits)||[];
  if(lw.length<2) return '<div class="foot">Not enough loads for the per-load view.</div>';
  const base=(tbd&&tbd.base!=null)?tbd.base:6, hb=fm.hangBud||0, qb=fm.queueBud||0;
  const W=1000,H=236,L=52,Rp=54,T=16,B=40,pw=W-L-Rp,ph=H-T-B,TOT=720,mid=T+ph/2;
  const hs=lw.map(p=>p[1]).sort((a,b)=>a-b), qs=lw.map(p=>p[2]).sort((a,b)=>a-b);
  const p97=a=>a[Math.min(a.length-1,Math.floor(a.length*0.97))]||0;   // clamp to ~97th pct so a few huge waits don't flatten the rest
  const maxV=Math.max(60,p97(hs),p97(qs),hb*1.4,qb*1.4);
  const X=m=>L+m/TOT*pw, Yup=v=>mid-Math.min(v,maxV)/maxV*(ph/2), Ydn=v=>mid+Math.min(v,maxV)/maxV*(ph/2);
  const bw=Math.max(0.7,Math.min(3,pw/lw.length));
  let g='';
  for(let hh=0;hh<=12;hh++){const x=X(hh*60);g+=`<line x1="${x}" y1="${T}" x2="${x}" y2="${T+ph}" stroke="#e8ebf0" stroke-width="0.9"/><text x="${x}" y="${T+ph+15}" text-anchor="middle" font-size="12" fill="var(--muted)">${(base+hh)%24}:00</text>`;}
  for(let v=60;v<=maxV;v+=60){const yu=Yup(v),yd=Ydn(v),m=v/60;
    g+=`<line x1="${L}" y1="${yu}" x2="${L+pw}" y2="${yu}" stroke="#eef0f4" stroke-width="0.5"/><text x="${L-6}" y="${yu+4}" text-anchor="end" font-size="12" fill="var(--muted)">${m}</text>`;
    g+=`<line x1="${L}" y1="${yd}" x2="${L+pw}" y2="${yd}" stroke="#eef0f4" stroke-width="0.5"/><text x="${L-6}" y="${yd+4}" text-anchor="end" font-size="12" fill="var(--muted)">${m}</text>`;}
  const GRN='#2f8f4e',RED='#e23b32',BLU='#3f51b5';
  lw.forEach(p=>{const x=X(p[0]);
    if(p[1]>0){const y=Yup(p[1]);g+=`<rect x="${(x-bw/2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(mid-y).toFixed(1)}" fill="${p[1]<=hb?GRN:RED}" fill-opacity="0.72"/>`;}
    if(p[2]>0){const y=Ydn(p[2]);g+=`<rect x="${(x-bw/2).toFixed(1)}" y="${mid.toFixed(1)}" width="${bw.toFixed(1)}" height="${(y-mid).toFixed(1)}" fill="${p[2]<=qb?GRN:BLU}" fill-opacity="0.72"/>`;}});
  const yhb=Yup(hb),yqb=Ydn(qb);
  g+=`<line x1="${L}" y1="${yhb.toFixed(1)}" x2="${L+pw}" y2="${yhb.toFixed(1)}" stroke="#8a2c22" stroke-width="1.4" stroke-dasharray="6 3"><title>hang target ${fmtTime(hb)}</title></line>`;
  g+=`<line x1="${L}" y1="${yqb.toFixed(1)}" x2="${L+pw}" y2="${yqb.toFixed(1)}" stroke="#243b8a" stroke-width="1.4" stroke-dasharray="6 3"><title>queue target ${fmtTime(qb)}</title></line>`;
  g+=`<line x1="${L}" y1="${mid}" x2="${L+pw}" y2="${mid}" stroke="#98a0ac" stroke-width="1.2"/>`;
  g+=`<text x="${L-6}" y="${mid+4}" text-anchor="end" font-size="12" fill="var(--muted)">0</text>`;
  g+=`<text x="13" y="${mid}" transform="rotate(-90 13 ${mid})" text-anchor="middle" font-size="11" fill="var(--muted)">min/load</text>`;
  g+=`<text x="${L+5}" y="${T+11}" font-size="11" fill="var(--muted)">▲ hang / load</text>`;
  g+=`<text x="${L+5}" y="${mid+16}" font-size="11" fill="var(--muted)">▼ queue / load</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px">`
    +`<span class="badge"><b style="color:#2f8f4e">■</b> at/under target</span><span class="badge"><b style="color:#e23b32">■</b> hang over target</span><span class="badge"><b style="color:#3f51b5">■</b> queue over target</span>`
    +`<span class="badge"><b style="color:#8a2c22">--</b> hang target ${fmtTime(hb)}</span><span class="badge"><b style="color:#243b8a">--</b> queue target ${fmtTime(qb)}</span>`
    +`<span class="badge">${lw.length} loads</span></div>`;
}
function drawBalanceTL(tbd){
  const s=tbd&&tbd.series||[];
  if(s.length<2) return '<div class="foot">Not enough truck-balance readings for a timeline (needs ≥ 2).</div>';
  const base=tbd.base!=null?tbd.base:6;
  const W=1000,H=210,L=44,R=16,T=14,B=32,pw=W-L-R,ph=H-T-B,TOT=720;
  const maxV=(Math.max(1,...s.map(p=>Math.max(p.req,p.act))))*1.12;
  const X=m=>L+m/TOT*pw, Y=v=>T+ph-v/maxV*ph;
  let g='';
  for(let i=0;i<=4;i++){const v=maxV*i/4,y=Y(v);g+=`<line x1="${L}" y1="${y}" x2="${L+pw}" y2="${y}" stroke="#e3e6eb"/><text x="${L-5}" y="${y+3}" text-anchor="end" font-size="9" fill="var(--muted)">${Math.round(v)}</text>`;}
  for(let hh=0;hh<=12;hh+=2){const x=X(hh*60);g+=`<line x1="${x}" y1="${T}" x2="${x}" y2="${T+ph}" stroke="#eef0f4"/><text x="${x}" y="${T+ph+13}" text-anchor="middle" font-size="9" fill="var(--muted)">${(base+hh)%24}:00</text>`;}
  const pathOf=k=>s.map((p,i)=>(i?'L':'M')+X(p.m).toFixed(1)+' '+Y(p[k]).toFixed(1)).join(' ');
  // shaded gap between required and actual
  const gap='M'+s.map(p=>X(p.m).toFixed(1)+' '+Y(p.req).toFixed(1)).join(' L ')+' L '+s.slice().reverse().map(p=>X(p.m).toFixed(1)+' '+Y(p.act).toFixed(1)).join(' L ')+' Z';
  g+=`<path d="${gap}" fill="#8a90a0" fill-opacity="0.13"/>`;
  g+=`<path d="${pathOf('req')}" fill="none" stroke="#e0952a" stroke-width="1.8" stroke-dasharray="5 3"/>`;
  g+=`<path d="${pathOf('act')}" fill="none" stroke="#3f51b5" stroke-width="1.9"/>`;
  s.forEach(p=>{g+=`<circle cx="${X(p.m).toFixed(1)}" cy="${Y(p.act).toFixed(1)}" r="1.7" fill="#3f51b5"><title>${(base+Math.floor(p.m/60))%24}:${String(Math.round(p.m%60)).padStart(2,'0')} — required ${p.req}, actual ${p.act}</title></circle>`;});
  g+=`<text x="12" y="${T+ph/2}" transform="rotate(-90 12 ${T+ph/2})" text-anchor="middle" font-size="9" fill="var(--muted)">trucks</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px"><span class="badge"><b style="color:#e0952a">– –</b> required</span><span class="badge"><b style="color:#3f51b5">—</b> actual (LP)</span><span class="badge">shaded = imbalance</span></div>`;
}
function renderLube(){
  const lu=V().lube;
  const lubeNote=document.getElementById('lubeNote');
  const lubeReasons=document.getElementById('lubeReasons');
  const lubeLead=document.getElementById('lubeLead');
  const lubeFaulty=document.getElementById('lubeFaulty');
  const lubeAssignAuto=document.getElementById('lubeAssignAuto');
  if(lubeNote) lubeNote.textContent=`${lu.n} events (this shift/view) · ${lu.shortCount} short <20s FUEL&LUBE/BREAK ignored · ${lu.faulty} faulty fuel reads (>100%) · ${lu.zero} zero/missing`;
  const m1=s=>(s/60).toFixed(1);
  let rt=`<table class="lanetab"><tr><th>Reason</th><th>Events</th><th>Actual min</th><th>Expected min</th><th>Avg min</th><th>% over</th></tr>`;
  lu.reasons.forEach(r=>rt+=`<tr><td>${r.reason}</td><td>${r.n}</td><td>${r.actual}</td><td>${r.exp}</td><td>${m1(r.avg)}</td><td>${r.over}%</td></tr>`);
  if(lubeReasons) lubeReasons.innerHTML=rt+'</table>';
  let lt=`<table class="lanetab"><tr><th>Time</th><th>Truck</th><th>Type</th><th>Reason</th><th>Act min</th><th>Exp min</th><th>Over min</th></tr>`;
  lu.leaderboard.forEach(r=>lt+=`<tr><td>${r.time}</td><td>${r.eqmt}</td><td>${r.type}</td><td>${r.reason}</td><td>${m1(r.actual)}</td><td>${m1(r.exp)}</td><td style="color:${r.over>0?'var(--red)':'var(--green)'};font-weight:700">${r.over>0?'+':''}${m1(r.over)}</td></tr>`);
  if(lubeLead) lubeLead.innerHTML=lt+'</table>';
  const fs=lu.faultySensor||[];
  const fss=document.getElementById('lfssub');
  if(!fs.length){if(lubeFaulty) lubeFaulty.innerHTML='<div class="foot">No faulty fuel-level reads (>100 %) this shift/view.</div>';if(fss)fss.textContent='';}
  else{
    const byT={}; fs.forEach(r=>{(byT[r.type]=byT[r.type]||[]).push(r);});
    const nTrucks=fs.length,nReads=fs.reduce((a,r)=>a+r.reads,0);
    if(fss)fss.textContent=`— ${nTrucks} truck${nTrucks>1?'s':''}, ${nReads} bad read${nReads>1?'s':''}`;
    let ft=`<table class="lanetab"><tr><th>Truck class</th><th>Truck</th><th>Bad reads</th><th>Reported level</th></tr>`;
    const order=lu.byClass.map(r=>r.type);
    const types=Object.keys(byT).sort((a,b)=>{const ia=order.indexOf(a),ib=order.indexOf(b);return (ia<0?99:ia)-(ib<0?99:ib);});
    types.forEach(t=>{const g=byT[t];
      g.forEach((r,i)=>{ft+=`<tr><td>${i===0?t+' ('+g.length+')':''}</td><td>${r.eqmt}</td><td>${r.reads}</td><td style="color:var(--red);font-weight:700">${r.value} %</td></tr>`;});});
    if(lubeFaulty) lubeFaulty.innerHTML=ft+'</table>';
  }
  // ---- assignment automation (System vs Manual) ----
  {
    const fa=lu.fuelAssign||{};
    const bar=(pct,color)=>`<div style="display:inline-block;width:${pct}%;height:12px;background:${color};border-radius:2px;vertical-align:middle"></div>`;
    let ht='<table class="lanetab">';
    ht+=`<tr><th>Metric</th><th>System</th><th>Manual</th><th>Total</th><th style="min-width:120px">System %</th></tr>`;
    if(fa.total>0){
      ht+=`<tr><td>Fuel Assignments</td><td>${fa.system}</td><td>${fa.manual}</td><td>${fa.total}</td>`;
      ht+=`<td>${bar(fa.sysPct,'#1f9e8b')}${bar(fa.manPct,'#e0952a')} <b>${fa.sysPct}%</b> system</td></tr>`;
    }else{
      ht+=`<tr><td colspan="5" class="foot">No fuel assignment data for this shift/view.</td></tr>`;
    }
    ht+='</table><div class="foot" style="margin-top:4px">Fuel Assignments: System vs Dispatcher (manual deduplicated to most recent per truck).</div>';
    if(lubeAssignAuto) lubeAssignAuto.innerHTML=ht;
  }
  if(typeof Chart==='undefined')return;
  const hy=lu.hourly, toH=a=>a.map(v=>v/60);
  const fuelH=toH(hy.fuel),waitH=toH(hy.wait),brkH=toH(hy.brk),expH=toH(hy.exp);
  // split fuel time into system-assigned (solid) vs manual-assigned (hatched)
  const fuelManH=toH(hy.fuelMan||new Array(12).fill(0));
  const fuelSysH=fuelH.map((v,i)=>Math.max(0,v-(fuelManH[i]||0)));
  const occFuel=hy.occFuel||new Array(12).fill(0), occFuelMan=hy.occFuelMan||new Array(12).fill(0);
  const mkHatch=(bg,line)=>{const c=document.createElement('canvas');c.width=c.height=6;const x=c.getContext('2d');
    x.fillStyle=bg;x.fillRect(0,0,6,6);x.strokeStyle=line;x.lineWidth=1.4;
    x.beginPath();x.moveTo(0,6);x.lineTo(6,0);x.stroke();
    x.beginPath();x.moveTo(-2,2);x.lineTo(2,-2);x.stroke();
    x.beginPath();x.moveTo(4,8);x.lineTo(8,4);x.stroke();return x.createPattern(c,'repeat');};
  const FUELHATCH=mkHatch('#1f9e8b','#0b544a');
  const barLabels={id:'barLabels',afterDatasetsDraw(ch){
    const ctx=ch.ctx,y=ch.scales.y,m=ch.getDatasetMeta(0); if(!m) return;
    ctx.save(); ctx.textAlign='center'; ctx.font='600 9px system-ui,sans-serif';
    hy.hours.forEach((h,i)=>{
      const bar=m.data[i]; if(!bar) return; const xp=bar.x;
      const tot=fuelH[i]+waitH[i]+brkH[i];
      if(tot>0){ctx.fillStyle='#2b2f36';ctx.textBaseline='bottom';
        ctx.fillText(tot.toFixed(1)+'h',xp,y.getPixelForValue(tot)-3);}
      const oc=hy.occ[i];
      if(oc>0){ctx.fillStyle='#ffffff';ctx.textBaseline='bottom';
        ctx.fillText(oc+'×',xp,y.getPixelForValue(0)-3);}
      const ow=hy.occWait[i];
      if(ow>0){ctx.fillStyle='#e0952a';ctx.textBaseline='top';
        ctx.fillText('bay '+ow+'×',xp,y.getPixelForValue(0)+3);}
      const fm=occFuelMan[i];                       // manual-assigned fuel events this hour
      if(fm>0){const yt=y.getPixelForValue(fuelSysH[i]),yb=y.getPixelForValue(fuelSysH[i]+fuelManH[i]);
        if(yt-yb>=11){ctx.fillStyle='#08403a';ctx.textBaseline='middle';ctx.font='700 9px system-ui,sans-serif';
          ctx.fillText(fm+'',xp,(yt+yb)/2);ctx.font='600 9px system-ui,sans-serif';}}
    });
    ctx.restore();
  }};
  mk('chLubeTrend',{type:'bar',data:{labels:hy.hours,datasets:[
    {type:'bar',label:'Fuel & lube — system',data:fuelSysH,backgroundColor:'#1f9e8b',stack:'s'},
    {type:'bar',label:'Fuel & lube — manual ▨',data:fuelManH,backgroundColor:FUELHATCH,stack:'s'},
    {type:'bar',label:'Wait for bay',data:waitH,backgroundColor:'#e0952a',stack:'s'},
    {type:'bar',label:'Break',data:brkH,backgroundColor:'#9aa0ab',stack:'s'},
    {type:'line',label:'Expected',data:expH,borderColor:'#2b2f36',borderDash:[4,3],pointRadius:0,borderWidth:1.4}
  ]},options:{responsive:true,maintainAspectRatio:false,layout:{padding:{top:14,bottom:14}},plugins:{legend:{labels:{boxWidth:11,font:{size:10}}},tooltip:{callbacks:{footer:c=>{const i=c[0].dataIndex;return 'total '+(fuelH[i]+waitH[i]+brkH[i]).toFixed(1)+'h · '+hy.occ[i]+' occ'+(hy.occWait[i]?' ('+hy.occWait[i]+' wait for bay)':'')+(occFuel[i]?' · '+occFuelMan[i]+'/'+occFuel[i]+' fuel manually assigned':'');}}}},scales:{x:{stacked:true,title:{display:true,text:'hour of shift'},ticks:{font:{size:10}}},y:{stacked:true,title:{display:true,text:'hours'},ticks:{font:{size:10}}}}},plugins:[barLabels]});
  const fh=lu.fuelHist, fhTot=fh.reduce((a,b)=>a+b,0)||1;
  const fe=lu.fuelEdges||[0,10,20,30,40,50,60,70,80,90,100];
  const fhMan=lu.fuelHistMan||new Array(fh.length).fill(0);
  const fhSys=fh.map((v,i)=>Math.max(0,v-(fhMan[i]||0)));
  const binCol=i=>fe[i]<8?'#e23b32':(fe[i]<40?'#1f9e8b':'#9aa0ab');
  const binLine=i=>fe[i]<8?'#7d1611':(fe[i]<40?'#0b544a':'#565b66');
  const fuelPct={id:'fuelPct',afterDatasetsDraw(ch){
    const ctx=ch.ctx,y=ch.scales.y,m=ch.getDatasetMeta(0); if(!m) return;
    ctx.save(); ctx.textAlign='center'; ctx.font='600 9px system-ui,sans-serif';
    fh.forEach((v,i)=>{const bar=m.data[i]; if(!bar||v<=0) return;
      ctx.fillStyle='#2b2f36'; ctx.textBaseline='bottom';
      ctx.fillText(Math.round(v/fhTot*100)+'%',bar.x,y.getPixelForValue(v)-3);
      const mv=fhMan[i]||0;                                   // manual-assigned refuels in this bin
      if(mv>0){const yt=y.getPixelForValue(fhSys[i]),yb=y.getPixelForValue(fhSys[i]+mv);
        if(yt-yb>=11){ctx.fillStyle='#fff';ctx.textBaseline='middle';ctx.font='700 9px system-ui,sans-serif';
          ctx.fillText(mv+'',bar.x,(yt+yb)/2);ctx.font='600 9px system-ui,sans-serif';}}});
    ctx.restore();
  }};
  const fuelAvg=fhTot/fh.length;
  const fuelAvgLine={id:'fuelAvgLine',afterDatasetsDraw(ch){
    const ctx=ch.ctx,y=ch.scales.y,ca=ch.chartArea; if(!ca) return;
    const yp=y.getPixelForValue(fuelAvg);
    ctx.save(); ctx.beginPath(); ctx.setLineDash([5,4]); ctx.strokeStyle='#2b2f36'; ctx.lineWidth=1.4;
    ctx.moveTo(ca.left,yp); ctx.lineTo(ca.right,yp); ctx.stroke();
    ctx.setLineDash([]); ctx.font='600 9px system-ui,sans-serif'; ctx.fillStyle='#2b2f36';
    ctx.textAlign='left'; ctx.textBaseline='bottom';
    ctx.fillText('avg',ca.right+2,yp+1);
    ctx.restore();
  }};
  mk('chLubeFuel',{type:'bar',data:{labels:fh.map((_,i)=>fe[i]+'-'+fe[i+1]),datasets:[
    {label:'system-assigned',data:fhSys,backgroundColor:fh.map((_,i)=>binCol(i)),stack:'f'},
    {label:'manual-assigned ▨',data:fhMan,backgroundColor:fh.map((_,i)=>mkHatch(binCol(i),binLine(i))),stack:'f'}
  ]},options:{responsive:true,maintainAspectRatio:false,layout:{padding:{top:12}},plugins:{legend:{display:true,labels:{boxWidth:11,font:{size:10}}},tooltip:{callbacks:{title:c=>c[0].label+'% fuel',label:c=>c.dataset.label+': '+c.parsed.y+' events',footer:c=>{const i=c[0].dataIndex;return fh[i]+' total ('+Math.round(fh[i]/fhTot*100)+'% of shift)'+(fhMan[i]?' · '+fhMan[i]+' manually assigned':'');}}}},scales:{x:{stacked:true,ticks:{font:{size:9}}},y:{stacked:true,title:{display:true,text:'events'},ticks:{font:{size:10}}}}},plugins:[fuelPct,fuelAvgLine]});
}
const AVMET=[['PA','PA','bPA'],['UA','UA','bUA'],['OE','OE','bOE'],['POE','POE',null]];
function sparkCycle(hourly){   // avg truck cycle time (mm:ss) per hour for a shovel→dump lane, with grid + point labels
  const hrs=(V().hourlyPerf&&V().hourlyPerf.hours)||[];
  const pts=(hourly||[]).map((v,i)=>({i,v})).filter(p=>p.v!=null);
  if(pts.length<2) return `<div class="foot" style="font-size:10px">Not enough hourly data.</div>`;
  const W=250,H=86,L=32,R=8,T=13,B=16,pw=W-L-R,ph=H-T-B;
  let mx=Math.max(...pts.map(p=>p.v)), mn=Math.min(...pts.map(p=>p.v));
  const pad=(mx-mn)*0.18||30; mx+=pad; mn=Math.max(0,mn-pad); const sp=(mx-mn)||1;
  const X=i=>L+(i/11)*pw, Y=v=>T+ph-((v-mn)/sp)*ph;
  let g='';
  const minM=Math.floor(mn/60), maxM=Math.ceil(mx/60), stepM=Math.max(1,Math.ceil((maxM-minM)/5));   // grid at whole minutes
  for(let m=minM;m<=maxM;m+=stepM){const y=Y(m*60); if(y<T-1||y>T+ph+1)continue;
    g+=`<line x1="${L}" y1="${y.toFixed(1)}" x2="${W-R}" y2="${y.toFixed(1)}" stroke="#eceff3" stroke-width="0.7"/>`
      +`<text x="${L-3}" y="${(y+2.5).toFixed(1)}" text-anchor="end" font-size="7.5" fill="var(--muted)">${m}m</text>`;}
  [0,3,6,9,11].forEach(i=>{const x=X(i);g+=`<line x1="${x.toFixed(1)}" y1="${T}" x2="${x.toFixed(1)}" y2="${T+ph}" stroke="#f4f6f8" stroke-width="0.6"/>`
      +`<text x="${x.toFixed(1)}" y="${H-4}" text-anchor="middle" font-size="7.5" fill="var(--muted)">${hrs[i]?hrs[i].slice(0,2):i}</text>`;});
  let path=''; pts.forEach((p,k)=>{const x=X(p.i),y=Y(p.v); path+=(k===0?`M${x.toFixed(1)} ${y.toFixed(1)}`:` L${x.toFixed(1)} ${y.toFixed(1)}`);});
  g+=`<path d="${path}" fill="none" stroke="#3f51b5" stroke-width="1.4"/>`;
  pts.forEach(p=>{const x=X(p.i),y=Y(p.v);
    g+=`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="#3f51b5"><title>${hrs[p.i]||('H'+(p.i+1))} — ${fmtTime(p.v)}</title></circle>`
      +`<text x="${x.toFixed(1)}" y="${(y-4).toFixed(1)}" text-anchor="middle" font-size="8" fill="#3f51b5" font-weight="700">${fmtTime(p.v)}</text>`;});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:270px">${g}</svg>`;
}
function sparkTP(hourly,tgt){   // avg truck TPNOH (t/h) per hour for a shovel→dump path, with grid + point labels + optional target line
  const hrs=(V().hourlyPerf&&V().hourlyPerf.hours)||[];
  const pts=(hourly||[]).map((v,i)=>({i,v})).filter(p=>p.v!=null);
  if(pts.length<2) return `<div class="foot" style="font-size:10px">Not enough hourly data.</div>`;
  const W=250,H=86,L=32,R=8,T=13,B=16,pw=W-L-R,ph=H-T-B;
  let mx=Math.max(...pts.map(p=>p.v),tgt||0), mn=Math.min(...pts.map(p=>p.v),tgt||Infinity);
  const pad=(mx-mn)*0.18||50; mx+=pad; mn=Math.max(0,mn-pad); const sp=(mx-mn)||1;
  const X=i=>L+(i/11)*pw, Y=v=>T+ph-((v-mn)/sp)*ph;
  let g='';
  const step=Math.max(50,Math.ceil((mx-mn)/4/50)*50);   // grid at round t/h
  for(let m=Math.ceil(mn/step)*step;m<=mx;m+=step){const y=Y(m); if(y<T-1||y>T+ph+1)continue;
    g+=`<line x1="${L}" y1="${y.toFixed(1)}" x2="${W-R}" y2="${y.toFixed(1)}" stroke="#eceff3" stroke-width="0.7"/>`
      +`<text x="${L-3}" y="${(y+2.5).toFixed(1)}" text-anchor="end" font-size="7.5" fill="var(--muted)">${m}</text>`;}
  [0,3,6,9,11].forEach(i=>{const x=X(i);g+=`<line x1="${x.toFixed(1)}" y1="${T}" x2="${x.toFixed(1)}" y2="${T+ph}" stroke="#f4f6f8" stroke-width="0.6"/>`
      +`<text x="${x.toFixed(1)}" y="${H-4}" text-anchor="middle" font-size="7.5" fill="var(--muted)">${hrs[i]?hrs[i].slice(0,2):i}</text>`;});
  if(tgt!=null&&tgt>0){const yt=Y(tgt); if(yt>=T-1&&yt<=T+ph+1) g+=`<line x1="${L}" y1="${yt.toFixed(1)}" x2="${W-R}" y2="${yt.toFixed(1)}" stroke="#9aa0ab" stroke-width="1" stroke-dasharray="4 3"><title>target ${fmt(tgt)} t/h</title></line>`;}
  let path=''; pts.forEach((p,k)=>{const x=X(p.i),y=Y(p.v); path+=(k===0?`M${x.toFixed(1)} ${y.toFixed(1)}`:` L${x.toFixed(1)} ${y.toFixed(1)}`);});
  g+=`<path d="${path}" fill="none" stroke="#1f9e8b" stroke-width="1.4"/>`;
  pts.forEach(p=>{const x=X(p.i),y=Y(p.v);
    g+=`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="#1f9e8b"><title>${hrs[p.i]||('H'+(p.i+1))} — ${fmt(p.v)} t/h</title></circle>`
      +`<text x="${x.toFixed(1)}" y="${(y-4).toFixed(1)}" text-anchor="middle" font-size="8" fill="#178173" font-weight="700">${fmt(p.v)}</text>`;});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:270px">${g}</svg>`;
}
function avDrop(r,lbl){
  if(lbl==='TPNOH'){const u=r.tpUnits||[], by=r.tpBy||'type';
    const hd=(by==='trkavg')?'Avg truck TPNOH per loading shovel — actual vs budget (t/h)'
                            :'TPNOH by shovel — actual · budget · Δ (t/h)';
    if(!u.length) return `<div class="drhd">${hd}</div><div class="dr"><span class="foot">no unit data</span></div>`;
    const byG={}; u.forEach(x=>{(byG[x.grp]=byG[x.grp]||[]).push(x);});   // both modes group by shovel type
    const keys=Object.keys(byG).sort((a,b)=>(TPGRP.indexOf(a)+99)-(TPGRP.indexOf(b)+99));
    let h=`<div class="drhd">${hd}</div>`;
    keys.forEach(g=>{h+=`<div class="drsub">${g} shovel</div>`+byG[g].map(x=>{
      const nm=(by==='trkavg')?`${x.unit}`:`${x.unit}${x.mat?' ('+x.mat+')':''}`;
      let s;
      if(by!=='trkavg'){   // per-shovel: columns aligned with the card's overall TPNOH row (actual · budget · Δ)
        let bcell='—',dcell='';
        if(x.btpnoh!=null){const dd=x.tpnoh-x.btpnoh,dc=dd>=0?'#2f7a44':'#b3382b';bcell=fmt(x.btpnoh);dcell=`<span style="color:${dc}">${dd>=0?'+':''}${fmt(dd)}</span>`;}
        s=`<div class="dr drtp"><span class="tpk">${nm}</span><span class="tpa">${fmt(x.tpnoh)}</span><span class="tpb">${bcell}</span><span class="tpd">${dcell}</span></div>`;
        return s;
      }
      s=`<div class="dr dsvhead"><span>${nm}</span><span><b>${fmt(x.tpnoh)}</b> vs ${x.btpnoh!=null?fmt(x.btpnoh):'—'}</span></div>`;
      (x.dumps||[]).forEach(d=>{const dc=d.dTon>=0?'#2f7a44':'#b3382b', tc=d.actTP>=d.tgtTP?'#2f7a44':'#b3382b';
        s+=`<div class="drdump drx" onclick="this.nextElementSibling.classList.toggle('open');this.classList.toggle('open')"><span class="ddh" title="${d.dump} — click for hourly TPNOH trend"><i class="cx">▸</i>${d.n} - ${d.dump} (${d.km} km)</span><span class="ddm"><b style="color:${tc}">${fmt(d.actTP)}</b>/${fmt(d.tgtTP)} · <b style="color:${dc}">${d.dTon>=0?'+':''}${fmt(d.dTon)}t</b></span></div>`+
          `<div class="dumpspark"><div class="sparkcap">Truck TPNOH / hr (t/h) · dashed = target</div>${sparkTP(d.hourlyTP,d.tgtTP)}</div>`;});
      return s;}).join('');});
    return h;
  }
  if(lbl==='POE'){const p=r.poe||{};
    if(p.kind==='trk') return `<div class="drhd">Avg non-productive / load</div>`
      +`<div class="dr"><span>Queue at shovel</span><span>${(p.queue||0).toFixed(2)} min</span></div>`
      +`<div class="dr"><span>Dump idle</span><span>${(p.idle||0).toFixed(2)} min</span></div>`
      +`<div class="dr tot"><span>Total</span><span>${(p.perLoad||0).toFixed(2)} min/load · ${fmt(p.n||0)} loads</span></div>`;
    return `<div class="drhd">Avg hang / load</div><div class="dr tot"><span>Hang</span><span>${(p.perLoad||0).toFixed(2)} min/load · ${fmt(p.n||0)} loads</span></div>`;
  }
  const list=(r.reasons&&r.reasons[lbl])||[], st={PA:'Down',UA:'Standby',OE:'Delay'}[lbl];
  const showUnit=(r.group!=='Cat 797');   // trucks: hide unit IDs; shovels: keep them
  if(!list.length) return `<div class="drhd">Top ${st} reasons — hrs · impact on ${lbl}</div><div class="dr"><span class="foot">none</span></div>`;
  return `<div class="drhd">Top ${st} reasons — hrs · impact on ${lbl}</div>`+list.map(x=>{
    const multi=showUnit&&x.units&&x.units.length>1;   // more than one shovel behind the "+N" → let the user expand
    const head=`<div class="dr${multi?' drx':''}"${multi?" onclick=\"this.nextElementSibling.classList.toggle('open');this.classList.toggle('open')\"":''}><span>${multi?'<i class="cx">▸</i>':''}${x.reason}${(showUnit&&x.unit)?' · <b>'+x.unit+'</b>':''}</span><span>${x.hours} h · −${x.eff}%</span></div>`;
    const sub=multi?`<div class="drsublist">${x.units.map(u=>`<div class="dr drunit"><span>${u.unit}</span><span>${u.hours} h</span></div>`).join('')}</div>`:'';
    return head+sub;
  }).join('');
}
function buildAvCards(av){
  let cards='';
  av.forEach(r=>{
    let rows=`<div class="avrow avhead"><span class="avm"></span><span class="ava">Actual</span><span class="avb">Budget</span><span class="avd">Δ</span></div>`;
    // TPNOH row (t/h) on top — expandable to per-unit TPNOH, grouped by type, descending
    {const td=(r.btpnoh!=null)?(r.tpnoh-r.btpnoh):null;
     const tdc=td==null?'var(--muted)':(td>=0?'var(--green)':'var(--red)');
     const tdt=td==null?'—':((td>=0?'+':'')+fmt(td));
     rows+=`<div class="avrow avclick" onclick="this.nextElementSibling.classList.toggle('open');this.classList.toggle('open')"><span class="avm"><i class="cx">▸</i>TPNOH</span><span class="ava">${fmt(r.tpnoh)}</span><span class="avb">${r.btpnoh!=null?fmt(r.btpnoh):'—'}</span><span class="avd" style="color:${tdc}">${tdt}</span></div>`;
     rows+=`<div class="avdrop${r.tpBy!=='trkavg'?' avdroptp':''}">${avDrop(r,'TPNOH')}</div>`;}
    AVMET.forEach(([lbl,ak,bk])=>{
      const a=r[ak],b=(bk?r[bk]:null),d=(b==null)?null:(a-b);
      const dc=d==null?'var(--muted)':(d>=0?'var(--green)':'var(--red)');
      const dt=d==null?'—':((d>=0?'+':'')+d.toFixed(1)+'%');
      rows+=`<div class="avrow avclick" onclick="this.nextElementSibling.classList.toggle('open');this.classList.toggle('open')"><span class="avm"><i class="cx">▸</i>${lbl}</span><span class="ava">${a.toFixed(1)}%</span><span class="avb">${b==null?'—':b.toFixed(1)+'%'}</span><span class="avd" style="color:${dc}">${dt}</span></div>`;
      rows+=`<div class="avdrop">${avDrop(r,lbl)}</div>`;
    });
    const title=AVLBL[r.group]||r.group;
    const noun=r.group==='Cat 797'?'trucks':'shovels';
    const hk=(r.group==='Cat 797'&&V().analytics&&V().analytics.hourly&&V().analytics.hourly.haulKmAvg!=null)?` <span style="color:var(--muted)">@${V().analytics.hourly.haulKmAvg.toFixed(1)}km</span>`:'';
    const dep=(r.deployed!=null)?`<div class="avdeploy" title="Effective units = gross operating hours (Ready+Delay) ÷ elapsed shift-hours · @km = overall average full-haul distance">≈ <b>${r.deployed.toFixed(1)}</b> ${noun}${hk}</div>`:'';
    const tlbtn=`<div style="text-align:center;margin:-3px 0 7px"><button class="tlbtn" onmouseenter="eqTlShow(event,'${r.group}')" onmouseleave="eqTlHide()" onclick="eqTlShow(event,'${r.group}')">Timeline</button></div>`;
    cards+=`<div class="avcard"><h4>${title}</h4>${dep}${tlbtn}${rows}</div>`;
  });
  return cards;
}
const AVLBL={'Cable shovel':'Cable Shovel - BE 495B','Hydraulic shovel':'Hydraulic Shovel - HIT 8000','Cat 797':'Large Truck - Cat 797'};
function eqTimelineHTML(group){   // status timeline filtered to a fleet + its per-hour deployed count
  const a=V().analytics, od=V().opDeployed; if(!a)return '<div class="foot">No timeline for this view.</div>';
  const arow=(V().availability||[]).find(r=>r.group===group)||{}, el=arow.elapsedH||12;   // elapsed shift-hours (matches the card headline denominator)
  let tlsvg,hrs,lbl;
  if(group==='Cat 797'){tlsvg=drawTimeline(a.truckTimeline,{rowH:6,labelFont:6,compact:true}); hrs=od?od.trucks:null; lbl='Cat 797 trucks';}
  else{const pre=group==='Cable shovel'?'S0':'S8', tl=a.timeline, eq=(tl.equip||[]).filter(k=>k.slice(0,2)===pre).sort((x,y)=>x.localeCompare(y,undefined,{numeric:true})), seg={};
    eq.forEach(k=>seg[k]=tl.seg[k]);
    tlsvg=eq.length?drawTimeline(Object.assign({},tl,{equip:eq,seg})):'<div class="foot">No status-timeline data.</div>';
    hrs=od?(group==='Cable shovel'?od.cable:od.hydraulic):null; lbl=(group==='Cable shovel'?'BE 495B':'HIT 800')+' shovels';}
  let hr='';
  if(hrs&&od){hr=`<div class="eqpophr eqpophrtop"><span class="eqk"># / hr →</span>`+hrs.map((v,i)=>`<span title="${od.hours[i]}:00">${v.toFixed(1)}</span>`).join('')+`</div>`;}
  return `<div class="eqpoptitle">${lbl} — equipment status timeline${hrs?` &nbsp;·&nbsp; avg # <b style="color:#243b8a">${(hrs.reduce((s,v)=>s+v,0)/el).toFixed(1)}</b>`:''}</div>${hr}${tlsvg}`;
}
function popShow(ev,html){const pop=document.getElementById('eqTlPop'); if(!pop)return;
  pop.innerHTML=html; pop.style.display='block';
  const r=ev.currentTarget.getBoundingClientRect(), pw=pop.offsetWidth||560, ph=pop.offsetHeight||220;
  pop.style.left=Math.max(8,Math.min(window.innerWidth-pw-8, r.left+r.width/2-pw/2))+'px';
  // fixed position = viewport coords (no scrollY); flip above the anchor if it would overflow the bottom
  let top=r.bottom+6; if(top+ph>window.innerHeight-8) top=Math.max(8,r.top-ph-6);
  pop.style.top=top+'px';}
function popHide(){const pop=document.getElementById('eqTlPop'); if(pop)pop.style.display='none';}
function eqTlShow(ev,group){popShow(ev,eqTimelineHTML(group));}
function eqTlHide(){popHide();}
const HAUL_HOVER_TREND=false;   // set true to re-enable the on-hover full-haul-time trend popup in the Haulage Loss Matrix
function fhShow(ev,i){if(!HAUL_HOVER_TREND)return;   // hover trend disabled for now (code kept intact)
  const l=V().trucksWF.lanes[i]; if(l)popShow(ev,'<div class="eqpoptitle">Full haul time per hour · '+l.load+' → '+l.dump+'</div>'+drawFullHaulLine(l));}
const TPGRP=['Cable','Hydraulic','Cat 797'];
let avAllOpen=false;   // Expand all / Contract all for the Equipment Hours card
function applyAvExpand(){
  const root=document.getElementById('ovKpi'); if(!root)return;
  root.querySelectorAll('.avdrop,.drsublist').forEach(e=>e.classList.toggle('open',avAllOpen));
  root.querySelectorAll('.avrow.avclick,.dr.drx').forEach(e=>e.classList.toggle('open',avAllOpen));
  syncAvExpandBtn();
}
function toggleAvExpand(){avAllOpen=!avAllOpen;applyAvExpand();}
function syncAvExpandBtn(){   // button label follows whether anything is open, so a single expand turns it into a "contract all" shortcut
  const root=document.getElementById('ovKpi'); if(!root)return;
  avAllOpen=!!root.querySelector('.avdrop.open,.drsublist.open');
  const b=document.getElementById('avExpandBtn'); if(b)b.textContent=avAllOpen?'Contract all':'Expand all';
}
(function(){const el=document.getElementById('ovKpi'); if(el)el.addEventListener('click',()=>setTimeout(syncAvExpandBtn,0));})();
function renderSandbox(){
  const av=V().availability;
  renderScoreCards(); fillHaulDrill(); fillLoadDrill();
  document.getElementById('sbbnsub').textContent='('+view+')';
  document.getElementById('sbBottle').innerHTML=drawBottleneck(V().fleetMatch);
  document.getElementById('sbtlsub').textContent='('+view+')';
  document.getElementById('sbBalanceTL').innerHTML=drawFleetTimeline(V().fleetMatch,V().truckBalance,true);
  document.getElementById('hitsub').textContent='('+view+')';
  // top-15 lost-time reasons per equipment (Down / Standby / Delay)
  const SC={Down:'#e23b32',Standby:'#3f7fe0',Delay:'#e0a41f'};
  let hit='';
  av.forEach(r=>{
    const hs=r.hitters||[]; const mx=Math.max(1,...hs.map(x=>x.hours));
    let rows='';
    if(!hs.length) rows='<div class="foot">No Down/Standby/Delay time.</div>';
    hs.forEach(x=>{const w=Math.max(2,x.hours/mx*100),c=SC[x.status]||'#9aa0ab';
      rows+=`<div class="hitrow" title="${x.reason} · ${x.status} · ${x.hours} h · ${x.n} events"><span class="hitname" style="border-left:3px solid ${c};padding-left:5px">${x.reason}</span><span class="hittrack"><span style="width:${w}%;background:${c}"></span></span><span class="hitval">${x.hours} h</span></div>`;});
    hit+=`<div class="avcard"><h4>${r.group}</h4>${rows}</div>`;
  });
  document.getElementById('avHitters').innerHTML=hit;
}
function renderCards(){
  const v=V();
  const btlsub=document.getElementById('btlsub');
  const balanceTL=document.getElementById('balanceTL');
  const whsub=document.getElementById('whsub');
  const waitTL=document.getElementById('waitTL');
  if(btlsub) btlsub.textContent='('+view+')';
  if(balanceTL) balanceTL.innerHTML=drawFleetTimeline(v.fleetMatch,v.truckBalance,false);
  if(whsub) whsub.textContent='('+view+')';
  if(waitTL) waitTL.innerHTML=drawWaitBalanceTL(v.fleetMatch,v.truckBalance);
}
function renderScoreCards(){
  const v=V();
  // Potential/Score from the waterfalls: top-anchor Potential (Sched. Potential incl. availability)
  const tw=v.trucksWF, sw=v.shovelWF2;
  const hPot=tw.availDecomp?tw.schedPotential:tw.potential, hAct=tw.actual, hSc=hPot?hAct/hPot*100:0;
  let lPot,lAct,lSc;
  if(sw){lPot=sw.availDecomp?sw.schedPotential:sw.potential; lAct=sw.actual; lSc=lPot?lAct/lPot*100:0;}
  else {lPot=v.loading.potential; lAct=v.loading.actual; lSc=v.loading.score;}
  document.getElementById('cards').innerHTML=
    gaugeCard('Haulage Score',hSc,hPot,hAct,'Potential','Actual Dumped')
    +truckMatchCard(hSc,lSc)
    +gaugeCard('Loading Score',lSc,lPot,lAct,'Potential','Actual Dumped');
}
function cmpBar(label,pot,act){
  const score=pot>0?act/pot*100:0, w=Math.max(1,Math.min(100,score)), c=scoreColor(score);
  return `<div class="gbar"><div class="glabel" title="${label}">${label}</div>
   <div class="gtrack"><div class="gact" style="width:${w}%;background:${c}"></div>
   <span class="gtxt">${fmt(act)} / ${fmt(pot)} · ${score.toFixed(0)}%</span></div></div>`;
}
function matSwatch(m){const c=m==='Ore'?CORE:CWASTE;return `<span style="color:${c}">■</span> ${m}`;}
const SCORELEG=`bars coloured by score: <b style="color:#e23b32">■</b> &lt;90% · <b style="color:#eab308">■</b> 90–100% · <b style="color:#4caf50">■</b> ≥100%`;
function fillHaulDrill(){
  const tw=V().trucksWF;
  let s=`<div class="glegend">${SCORELEG} · grouped by material</div>`;
  const byMat={Ore:[],Waste:[]};
  tw.lanes.forEach(l=>{if(byMat[l.mat])byMat[l.mat].push(l);});
  const hsc=l=>l.pot>0?l.act/l.pot*100:1e9;
  ['Ore','Waste'].forEach(m=>{const x=tw.byMaterial[m], haulage=(byMat[m]||[]).slice().sort((a,b)=>hsc(a)-hsc(b)).slice(0,6);
    if(!x&&!haulage.length)return;
    s+=`<h4 class="mini">${matSwatch(m)}</h4>`;
    if(x){const pot=x.availDecomp?x.schedPotential:x.potential; s+=cmpBar('total · incl. availability (matches gauge)',pot,x.actual);}
    if(haulage.length){s+=`<div class="glegend">haulage (Load → Dump) — cycle basis, excl. availability (reads higher), lowest score first:</div>`;
      haulage.forEach(l=>s+=cmpBar(l.load+' → '+l.dump,l.pot,l.act));}
  });
  document.getElementById('dh').innerHTML=s;
}
function fillLoadDrill(){
  const sw=V().shovelWF2;
  if(!sw){document.getElementById('dl').innerHTML='<div class="foot">No shovel data for this view.</div>';return;}
  let s=`<div class="glegend">${SCORELEG} · grouped by material</div>`;
  const byMat={Ore:[],Waste:[]};
  sw.units.forEach(u=>{const m=byMat[u.mat]?u.mat:'Ore'; byMat[m].push(u);});
  const usc=u=>{const p=u.availDecomp?u.schedPotential:u.potential; return p>0?u.actual/p*100:1e9;};
  ['Ore','Waste'].forEach(m=>{const x=sw.byMaterial[m], units=(byMat[m]||[]).slice().sort((a,b)=>usc(a)-usc(b)).slice(0,6);
    if(!x&&!units.length)return;
    s+=`<h4 class="mini">${matSwatch(m)}</h4>`;
    if(x){const pot=x.availDecomp?x.schedPotential:x.potential; s+=cmpBar('total · incl. availability (matches gauge)',pot,x.actual);}
    if(units.length){s+=`<div class="glegend">by shovel (dominant material) — incl. availability, lowest score first:</div>`;
      units.forEach(u=>{const pot=u.availDecomp?u.schedPotential:u.potential; s+=cmpBar(u.unit+' · '+(u.type==='BE495'?'BE 495':'HIT 8000'),pot,u.actual);});}
  });
  document.getElementById('dl').innerHTML=s;
}
function toggleDrill(id){const e=document.getElementById(id);if(e)e.classList.toggle('open');}

const ROWLABEL={Payload:'Payload',Load:'Load Time',Queue:'Queue at Shovel',Spot:'Spot at Shovel',
 DumpIdle:'Dump Idle',Dumping:'Dumping',FullHaul:'Full Haul',EmptyHaul:'Empty Haul',Residual:'Residual (basis)'};
const ORDER=['Payload','EmptyHaul','Queue','Spot','Load','FullHaul','DumpIdle','Dumping'];   // truck cycle order (matrix + all truck waterfalls)
function fmtTime(s){const m=Math.floor(s/60),x=Math.round(s-m*60);return m+':'+(x<10?'0':'')+x;}
function leadStr(k,lm){   // returns {uom,tgt,act} for the KPI/UOM/Target/Actual columns
  if(k==='Residual'||!lm||!lm[k])return null;
  const o=lm[k];
  if(o.unit==='t') return {uom:'wTons',tgt:o.target.toFixed(0),act:o.actual.toFixed(0)};
  return {uom:'mm:ss',tgt:fmtTime(o.target),act:fmtTime(o.actual)};
}
// Classic waterfall: KPI/UOM/Target/Actual columns + dark header, grey plot area with vertical gridlines.
function waterfallSVG(o){
  const AN='#41419e',GN='#6aa84f',RD='#cc4b4b';
  const rows=o.rows;
  const cum=[o.startVal]; rows.forEach(r=>cum.push(cum[cum.length-1]+r.delta));
  const allv=[o.startVal,o.endVal,...cum];
  let lo=Math.min(...allv),hi=Math.max(...allv);const span=(hi-lo)||1; lo-=span*0.10; hi+=span*0.06;
  const W=1000,xKPI=182,xUOM=214,xTgt=286,xAct=340,LX=352,RX=990;
  const hdrH=24,rowH=25,barH=16,top=hdrH,n=rows.length+2,plotBot=top+n*rowH,H=plotBot+28;
  const X=v=>LX+(v-lo)/(hi-lo)*(RX-LX),rowY=i=>top+i*rowH,cyOf=i=>rowY(i)+rowH/2+4;
  const items=[{kind:'anchor',label:o.startLabel,a:lo,b:o.startVal,end:o.startVal,color:AN,val:o.startVal}];
  rows.forEach((r,j)=>items.push({kind:'step',label:r.label,col:r.col||null,segs:r.segs||null,a:cum[j],b:cum[j+1],end:cum[j+1],
    color:r.color||(r.delta>=0?GN:RD),delta:r.delta}));
  items.push({kind:'anchor',label:o.endLabel,a:lo,b:o.endVal,end:o.endVal,color:AN,val:o.endVal});
  const GL='#e3e6eb';   // gridline / header colour
  let g='';
  // white plot background (matches the rest of the page)
  // nice round gridlines
  const raw=(hi-lo)/5,mag=Math.pow(10,Math.floor(Math.log10(raw))),nn=raw/mag,step=(nn<1.5?1:nn<3?2:nn<7?5:10)*mag;
  for(let v=Math.ceil(lo/step)*step;v<=hi;v+=step){const x=X(v);
    g+=`<line x1="${x}" y1="${top}" x2="${x}" y2="${plotBot}" stroke="${GL}" stroke-width="1.2"/>`;
    g+=`<text x="${x}" y="${plotBot+14}" text-anchor="middle" font-size="9" fill="var(--muted)">${fmt(Math.round(v))}</text>`;}
  // horizontal row separators
  for(let i=0;i<=n;i++){const y=top+i*rowH;g+=`<line x1="0" y1="${y}" x2="${W}" y2="${y}" stroke="${GL}" stroke-width="1"/>`;}
  // header bar (same colour as the gridlines)
  g+=`<rect x="0" y="0" width="${W}" height="${hdrH}" fill="${GL}"/>`;
  const hy=hdrH/2+4;
  g+=`<text x="${xKPI}" y="${hy}" text-anchor="end" font-size="11" font-weight="700" fill="#33373e">KPI</text>`;
  g+=`<text x="${xUOM}" y="${hy}" text-anchor="middle" font-size="11" font-weight="700" fill="#33373e">UOM</text>`;
  g+=`<text x="${xTgt}" y="${hy}" text-anchor="end" font-size="11" font-weight="700" fill="#33373e">Target</text>`;
  g+=`<text x="${xAct}" y="${hy}" text-anchor="end" font-size="11" font-weight="700" fill="#33373e">Actual</text>`;
  if(o.title) g+=`<text x="${(LX+RX)/2}" y="${hy}" text-anchor="middle" font-size="11.5" font-weight="700" fill="#33373e">${o.title}</text>`;
  // connectors
  for(let i=0;i<items.length-1;i++){const x=X(items[i].end);
    g+=`<line x1="${x}" y1="${rowY(i)+rowH/2}" x2="${x}" y2="${rowY(i+1)+rowH/2}" stroke="#8a92a0" stroke-width="1" stroke-dasharray="3 3" opacity="0.7"/>`;}
  // rows
  items.forEach((it,i)=>{const y=rowY(i),cy=cyOf(i),by=y+(rowH-barH)/2,x1=X(Math.min(it.a,it.b)),x2=X(Math.max(it.a,it.b)),w=Math.max(1.2,x2-x1);
    const sk=(it.kind==='step'&&typeof STATLINK!=='undefined')?STATLINK[it.label]:null;   // stats link button
    if(sk){const bx=3,byy=y+(rowH-13)/2;
      g+=`<g onclick="gotoStat('${sk}')" style="cursor:pointer"><title>Shift stats for ${it.label}</title>`
        +`<rect x="${bx}" y="${byy}" width="21" height="13" rx="2.5" fill="#eef0f7" stroke="#41419e" stroke-width="0.7"/>`
        +`<rect x="${bx+4}" y="${byy+6}" width="2.3" height="4" fill="#41419e"/><rect x="${bx+8}" y="${byy+4}" width="2.3" height="6" fill="#41419e"/><rect x="${bx+12}" y="${byy+7}" width="2.3" height="3" fill="#41419e"/>`
        +`</g>`;}
    g+=`<text x="${xKPI}" y="${cy}" text-anchor="end" font-size="12" font-weight="700" fill="${it.kind==='anchor'?'#2b2f36':it.color}">${it.label}</text>`;
    if(it.kind==='step'&&it.col){const c=it.col;
      g+=`<text x="${xUOM}" y="${cy}" text-anchor="middle" font-size="10.5" fill="#6b7280">${c.uom}</text>`;
      g+=`<text x="${xTgt}" y="${cy}" text-anchor="end" font-size="10.5" font-weight="700" fill="#2b2f36">${c.tgt}</text>`;
      g+=`<text x="${xAct}" y="${cy}" text-anchor="end" font-size="10.5" font-weight="700" fill="${it.color}">${c.act}</text>`;}
    g+=`<rect x="${x1}" y="${by}" width="${w}" height="${barH}" fill="${it.color}" fill-opacity="${it.kind==='anchor'?1:0.92}"/>`;
    if(it.kind==='anchor') g+=`<text x="${(x1+x2)/2}" y="${cy}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">${fmt(it.val)}</text>`;
    else g+=`<text x="${it.delta>=0?x2+4:x1-4}" y="${cy}" text-anchor="${it.delta>=0?'start':'end'}" font-size="10.5" font-weight="700" fill="${it.color}">${(it.delta>=0?'+':'−')+fmt(Math.abs(it.delta))}</text>`;
    if(it.segs&&it.segs.length&&it.delta<0){const tot=it.segs.reduce((s,r)=>s+r[1],0)||1;
      let cx=x1;
      it.segs.forEach((r,si)=>{const sw=w*r[1]/tot;
        if(si<it.segs.length-1) g+=`<line x1="${cx+sw}" y1="${by}" x2="${cx+sw}" y2="${by+barH}" stroke="#fff" stroke-width="0.8" opacity="0.85"/>`;
        const maxc=Math.floor((sw-4)/5.0);
        if(maxc>=5){const t=r[0].length>maxc?r[0].slice(0,maxc-1)+'…':r[0];
          g+=`<text x="${cx+3}" y="${cy-0.5}" font-size="8.2" font-weight="600" fill="#fff">${t}<title>${r[0]} · ${r[1]}h</title></text>`;}
        cx+=sw;});}});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg>`;
}
function buildWF(wf){
  const av=wf.availDecomp,hasAv=!!av;
  const rows=[];
  const pc=x=>({uom:'%',tgt:x.bud.toFixed(1),act:x.act.toFixed(1)});
  if(hasAv){
    rows.push({label:'PA',delta:av.pa.t,col:pc(av.pa),segs:av.pa.reasons});
    rows.push({label:'UA',delta:av.ua.t,col:pc(av.ua),segs:av.ua.reasons});
    rows.push({label:'OE',delta:av.oe.t,col:pc(av.oe),segs:av.oe.reasons});
  }
  ORDER.forEach(k=>{
    rows.push({label:ROWLABEL[k],delta:k==='Residual'?wf.residual:wf.rows[k],col:leadStr(k,wf.lm)});
  });
  return waterfallSVG({startLabel:'Potential',startVal:hasAv?wf.schedPotential:wf.potential,endLabel:'Actual',endVal:wf.actual,rows});
}
function matBars(wf){
  const O=wf.byMaterial.Ore, W=wf.byMaterial.Waste;
  const getv=(M,k)=>k==='Residual'?M.residual:M.rows[k];
  let maxa=1; ORDER.forEach(k=>{maxa=Math.max(maxa,Math.abs(getv(O,k)),Math.abs(getv(W,k)));});
  const cell=v=>{const w=Math.abs(v)/maxa*46,left=v>=0?50:50-w,col=v>=0?'var(--green)':'var(--red)';
    return `<div style="display:flex;align-items:center;gap:5px"><div class="mini-track" style="flex:1">
      <div class="mini-seg" style="left:${left}%;width:${Math.max(w,0.6)}%;background:${col}"></div></div>
      <span style="width:48px;text-align:right;font-variant-numeric:tabular-nums">${fmtT(v)}</span></div>`;};
  let s=`<h4 class="mini">Ore / Waste — tonnage impact by row</h4>
    <div class="dg2"><div></div><div class="mh">Ore (${fmt(O.actual)}t)</div><div class="mh">Waste (${fmt(W.actual)}t)</div></div>`;
  ORDER.forEach(k=>{s+=`<div class="dg2"><div>${ROWLABEL[k]}</div>${cell(getv(O,k))}${cell(getv(W,k))}</div>`;});
  return s;
}
function renderWF(){
  const wf=V().trucksWF;
  document.getElementById('wf').innerHTML=buildWF(wf);
  document.getElementById('ind').innerHTML=
    `<span class="badge">Empty/Full ratio: <b>${wf.emptyFullRatio.toFixed(3)}</b> (target 1.000)</span>`;
  // refresh any open truck box below the waterfall for the current view
  Object.keys(TRUCKBOX).forEach(w=>{const sec=document.getElementById(TRUCKBOX[w][0]); if(sec&&!sec.hidden)renderTruckBox(w);});
}
const TRUCKBOX={queue:['secQueT','qBtnT','chQueT','Queue at Shovel','queuebox','queue at shovel (min)',1/60,' min'],
  idle:['secIdleT','iBtnT','chIdleT','Dump Idle','idlebox','dump idle (min)',1/60,' min'],
  dump:['secDumpT','dBtnT','chDumpT','Dumping','dumpbox','dumping (min)',1/60,' min'],
  full:['secFullT','fBtnT','chFullT','Haul Distance','haulbox','haul distance (km)',1/1000,' km']};
function toggleTruckBox(w){const t=TRUCKBOX[w],sec=document.getElementById(t[0]),btn=document.getElementById(t[1]);
  sec.hidden=!sec.hidden; btn.innerHTML=t[3]+' '+(sec.hidden?'▾':'▴'); if(!sec.hidden)renderTruckBox(w);}
function renderTrucksAtDump(){const el=document.getElementById('dumpTl'); if(el)el.innerHTML=drawDumpTimeline(V().dumpTimeline);
  const sub=document.getElementById('dtlsub'); if(sub)sub.textContent='('+view+')';}
function toggleTrucksAtDump(){const sec=document.getElementById('secTadT'),btn=document.getElementById('tadBtnT');
  sec.hidden=!sec.hidden; if(btn)btn.innerHTML='Trucks at Dump '+(sec.hidden?'▾':'▴'); if(!sec.hidden)renderTrucksAtDump();}
let truckAllOpen=false;   // Expand all / Contract all for the truck-waterfall sections
function toggleTruckExpand(){
  truckAllOpen=!truckAllOpen;
  const ows=document.getElementById('owSectionT'), owb=document.getElementById('owBtnT');
  if(ows)ows.hidden=!truckAllOpen; if(owb)owb.innerHTML='Ore/Waste Details '+(truckAllOpen?'▴':'▾');
  Object.keys(TRUCKBOX).forEach(w=>{const t=TRUCKBOX[w],sec=document.getElementById(t[0]),btn=document.getElementById(t[1]);
    if(sec){sec.hidden=!truckAllOpen; if(!sec.hidden)renderTruckBox(w);}
    if(btn)btn.innerHTML=t[3]+' '+(truckAllOpen?'▴':'▾');});
  const tad=document.getElementById('secTadT'), tadb=document.getElementById('tadBtnT');
  if(tad){tad.hidden=!truckAllOpen; if(!tad.hidden)renderTrucksAtDump();} if(tadb)tadb.innerHTML='Trucks at Dump '+(truckAllOpen?'▴':'▾');
  const eb=document.getElementById('truckExpandBtn'); if(eb)eb.innerHTML=(truckAllOpen?'Contract all ▴':'Expand all ▾');
}
function renderTruckBox(w){const a=V().analytics; if(!a)return; const t=TRUCKBOX[w];
  document.getElementById(t[2]).innerHTML=drawBoxPlot(a[t[4]],{axisLabel:t[5],unit:t[7],scale:t[6],dec:1,hideOutliers:true});}
function owHdr(m,mw){
  const pot=mw.availDecomp?mw.schedPotential:mw.potential;   // top-anchor "Potential" (matches the graph)
  const sc=pot?(mw.actual/pot*100).toFixed(0):'0';
  return `<h4 class="mini">${m} — Potential ${fmt(pot)} → Actual ${fmt(mw.actual)} (${sc}%)</h4>`;
}
function renderOWWF(){
  const wf=V().trucksWF;let s='';
  for(const m of ['Ore','Waste']){const mw=wf.byMaterial[m];s+=owHdr(m,mw)+buildWF(mw);}
  document.getElementById('owWF').innerHTML=s;
}
function renderShovOW(){
  const sw=V().shovelWF;let s='';
  for(const m of ['Ore','Waste']){
    const gs=sw.groups.filter(g=>g.mat===m);
    if(!gs.length) continue;
    const pot=gs.reduce((a,g)=>a+g.pot,0),act=gs.reduce((a,g)=>a+g.act,0);
    const rows=gs.map(g=>({label:g.name,delta:g.variance,col:{uom:'t/h',tgt:g.rateBudget.toFixed(0),act:g.rateActual.toFixed(0)}}));
    s+=`<h4 class="mini">${m} — Potential ${fmt(pot)} → Actual ${fmt(act)} (${(act/pot*100).toFixed(0)}%)</h4>`+
       waterfallSVG({startLabel:'Potential',startVal:pot,endLabel:'Actual',endVal:act,rows});
  }
  document.getElementById('shovOW').innerHTML=s||'<div class="foot">No ore/waste segments.</div>';
}
function toggleOW(which){
  const M={trucks:['owSectionT','owBtnT'],shovel:['shovOW','owBtnS'],shovel2:['owSectionS2','owBtnS2']};
  const sec=document.getElementById(M[which][0]),btn=document.getElementById(M[which][1]);
  sec.hidden=!sec.hidden;
  btn.innerHTML='Ore/Waste Details '+(sec.hidden?'▾':'▴');
}
const SHOVBOX={pay:['secPayS2','pcBtnS2','Payload Compliance'],spot:['secSpotS2','stBtnS2','Spot Time'],load:['secLoadS2','ltBtnS2','Load Time'],hang:['secHangS2','htBtnS2','Hang Time']};
function toggleShovBox(which){
  const [sid,bid,lbl]=SHOVBOX[which];const sec=document.getElementById(sid),btn=document.getElementById(bid);
  sec.hidden=!sec.hidden; btn.innerHTML=lbl+' '+(sec.hidden?'▾':'▴');
  if(!sec.hidden)renderShovBox(which);
}
function renderShovBox(which){
  const a=V().analytics; if(!a)return;
  if(which==='pay')document.getElementById('chPayS2').innerHTML=drawPayBox(a.payload,a.payloadTarget);
  else if(which==='hang')document.getElementById('chHangS2').innerHTML=drawBoxPlot(a.hangbox,{axisLabel:'hang time (min)',unit:' min',scale:1/60,dec:1,hideOutliers:true});
  else if(which==='spot')document.getElementById('chSpotS2').innerHTML=drawBoxPlot(a.spotbox,{axisLabel:'spot time (min)',unit:' min',scale:1/60,dec:1,hideOutliers:true});
  else document.getElementById('chLoadS2').innerHTML=drawBoxPlot(a.loadbox,{axisLabel:'load time (min)',unit:' min',scale:1/60,dec:1,hideOutliers:true});
}
let shovAllOpen=false;   // Expand all / Contract all for the shovel-waterfall box plots
function toggleShovExpand(){
  shovAllOpen=!shovAllOpen;
  Object.keys(SHOVBOX).forEach(w=>{const [sid,bid,lbl]=SHOVBOX[w];const sec=document.getElementById(sid),btn=document.getElementById(bid);
    if(sec){sec.hidden=!shovAllOpen; if(!sec.hidden)renderShovBox(w);}
    if(btn)btn.innerHTML=lbl+' '+(shovAllOpen?'▴':'▾');});
  const eb=document.getElementById('shovExpandBtn'); if(eb)eb.innerHTML=(shovAllOpen?'Contract all ▴':'Expand all ▾');
}
function worstRow(rows,residual){
  let wk=null,wv=0;
  ORDER.forEach(k=>{const v=k==='Residual'?residual:rows[k]; if(v<wv){wv=v;wk=k;}});
  return wk?ROWLABEL[wk]+' ('+fmtT(wv)+')':'—';
}
// ---- Loss analysis shared by the Haulage & Loading drill-downs ----
// Each entity = {id,label,attr,comps:{compKey:signedTonnes}}. Negative comps = tonnes lost.
function sumLoss(comps,order){let t=0;order.forEach(k=>{const v=comps[k]||0;if(v<0)t+=-v;});return t;}
function lossAnalysis(entities,order,labelMap){
  const compLoss={},compContrib={};
  order.forEach(k=>{compLoss[k]=0;compContrib[k]=[];});
  const ent=entities.map(e=>{let tot=0,wk=null,wv=0;
    order.forEach(k=>{const v=e.comps[k]||0; if(v<0){compLoss[k]+=(-v);tot+=(-v);compContrib[k].push({attr:e.attr,loss:-v});} if(v<wv){wv=v;wk=k;}});
    return {id:e.id,label:e.label,attr:e.attr,comps:e.comps,loss:tot,worst:wk,worstVal:wv};});
  order.forEach(k=>compContrib[k].sort((a,b)=>b.loss-a.loss));
  const total=order.reduce((s,k)=>s+compLoss[k],0)||1;
  const ranked=order.map(k=>({k,label:labelMap[k],loss:compLoss[k],contrib:compContrib[k]})).filter(x=>x.loss>0).sort((a,b)=>b.loss-a.loss);
  return {ent,compLoss,total,ranked};
}
function drawPareto(ranked,total){
  if(!ranked.length) return '<div class="foot">No net tonnage losses this view — every factor is at or above target.</div>';
  const mx=ranked[0].loss||1; let cum=0;
  return '<div class="pareto">'+ranked.map(r=>{cum+=r.loss;const w=Math.max(2,r.loss/mx*100),cp=cum/total*100;
    return `<div class="prow"><span class="pname" title="${r.label}">${r.label}</span><span class="ptrack"><span class="pbar" style="width:${w}%"></span></span><span class="pval">−${fmt(r.loss)} t</span><span class="pcum">${cp.toFixed(0)}%</span></div>`;}).join('')+'</div>';
}
function drawTop3(ranked,total){
  if(!ranked.length) return '<span class="t3none">✓ No net tonnage losses this view — nothing flagged for improvement.</span>';
  const parts=ranked.slice(0,3).map(r=>{
    const who=r.contrib.slice(0,2).map(c=>c.attr).filter((v,i,a)=>a.indexOf(v)===i);
    return `<b>${r.label}</b> −${fmt(r.loss)} t${who.length?` (mostly ${who.join(', ')})`:''}`;});
  const topShare=(ranked.slice(0,3).reduce((s,r)=>s+r.loss,0)/total*100);
  return `<b style="color:#8a2c22">Fix first:</b> ${parts.join(' &nbsp;·&nbsp; ')}. <span style="color:var(--muted);font-weight:400">Top 3 = ${topShare.toFixed(0)}% of all lost tonnes.</span>`;
}
function drawLossHeat(ent,order,labelMap,firstColHdr,cap){
  const all=ent.filter(e=>e.loss>0).sort((a,b)=>b.loss-a.loss);
  if(!all.length) return '<div class="foot">No losses to map.</div>';
  let rows=all, note='';
  if(cap&&all.length>cap){note=`<div class="foot">Showing the ${cap} highest-loss of ${all.length}; the Total row covers all ${all.length}.</div>`; rows=all.slice(0,cap);}
  let mxcell=1; rows.forEach(e=>order.forEach(k=>{const v=e.comps[k]||0; if(v<0)mxcell=Math.max(mxcell,-v);}));
  // column totals over ALL entities (matches the Pareto), so they hold even when the rows shown are capped
  const colTot={}; let grand=0; order.forEach(k=>colTot[k]=0);
  all.forEach(e=>order.forEach(k=>{const v=e.comps[k]||0; if(v<0){colTot[k]+=-v;grand+=-v;}}));
  let h=`<div class="hmwrap"><table class="hmtab"><tr><th>${firstColHdr}</th>`+order.map(k=>`<th>${labelMap[k]}</th>`).join('')+'<th>Total lost</th></tr>';
  rows.forEach(e=>{h+=`<tr><td class="hmk" title="${e.label}">${e.label}</td>`;
    order.forEach(k=>{const v=e.comps[k]||0,loss=v<0?-v:0,op=(loss/mxcell*0.82).toFixed(3);
      h+=`<td style="background:rgba(204,60,50,${loss?op:0})">${loss?fmt(loss):''}</td>`;});
    h+=`<td class="hmtot">−${fmt(e.loss)} t</td></tr>`;});
  h+=`<tr class="hmfoot"><td class="hmk">Total${cap&&all.length>cap?` (all ${all.length})`:''}</td>`+
    order.map(k=>`<td>${colTot[k]>0?'−'+fmt(colTot[k]):''}</td>`).join('')+
    `<td class="hmtot">−${fmt(grand)} t</td></tr>`;
  return h+'</table></div>'+note;
}
const SHVORDER=['PA','UA','OE','Payload','Spot','Load','Hang'];
const SHVLABEL={PA:'Availability (PA)',UA:'Standby (UA)',OE:'Delay (OE)',Payload:'Payload',Spot:'Spot at Shovel',Load:'Load Time',Hang:'Hang Time'};
// Merged shovel loss table — grouped by shovel type, each row interactive (drives waterfall + status timeline).
// Combines the old loss heatmap (per-factor heat cells) with the drill-down list (loads · score · potential · actual · lost t).
function drawShovelLossTable(units){
  if(!units||!units.length) return '<div class="foot">No shovel cycle data for this view.</div>';
  const meta=units.map((u,i)=>{const pot=u.availDecomp?u.schedPotential:u.potential, comps=shovUnitComps(u);
    return {i,u,pot,act:u.actual,sc:pot?u.actual/pot*100:0,loss:sumLoss(comps,SHVORDER),comps,
            grp:u.type==='BE495'?'BE495':'HIT8000'};});
  // heat scale over every factor cell
  let mxcell=1; meta.forEach(m=>SHVORDER.forEach(k=>{const v=m.comps[k]||0; if(v<0)mxcell=Math.max(mxcell,-v);}));
  const GRP={BE495:{lbl:'BE 495',sub:'Cable'},HIT8000:{lbl:'HIT 8000',sub:'Hydraulic'}};
  const scol=s=>s>=95?'var(--green)':(s>=85?'#c9851f':'var(--red)');
  // group → members (by loss desc); groups ordered by group loss desc
  const groups={}; meta.forEach(m=>{(groups[m.grp]||(groups[m.grp]=[])).push(m);});
  Object.values(groups).forEach(a=>a.sort((x,y)=>y.loss-x.loss));
  const gkeys=Object.keys(groups).sort((a,b)=>groups[b].reduce((s,m)=>s+m.loss,0)-groups[a].reduce((s,m)=>s+m.loss,0));
  const heat=v=>{const loss=v<0?-v:0,op=(loss/mxcell*0.82).toFixed(3);return `<td class="hmcell" style="background:rgba(204,60,50,${loss?op:0})">${loss?fmt(loss):''}</td>`;};
  const SHVHDR={PA:'PA',UA:'UA',OE:'OE',Payload:'Payload',Spot:'Spot Time',Load:'Load Time',Hang:'Hang Time'};
  const cg=`<colgroup><col style="width:13%"><col style="width:8%"><col style="width:8%"><col style="width:6%">`
    +SHVORDER.map(()=>'<col style="width:8%">').join('')+`</colgroup>`;
  let h=`<div class="hmwrap svwrap"><table class="hmtab svloss">${cg}<tr><th>Shovel (loads)</th><th>Actual</th><th>Potential</th><th>Score</th>`
    +SHVORDER.map(k=>`<th>${SHVHDR[k]}</th>`).join('')+'</tr>';
  // column totals over all shovels (footer)
  const colTot={}; SHVORDER.forEach(k=>colTot[k]=0); let gPot=0,gAct=0,gLoss=0;
  gkeys.forEach(gk=>{const mem=groups[gk],g=GRP[gk];
    const sp=mem.reduce((s,m)=>s+m.pot,0),sa=mem.reduce((s,m)=>s+m.act,0),sl=mem.reduce((s,m)=>s+m.loss,0),ssc=sp?sa/sp*100:0;
    h+=`<tr class="svgrp"><td>${g.lbl} · ${g.sub} <span class="gc">(${mem.length})</span></td>`
      +`<td style="font-weight:700">${fmt(sa)}</td><td>${fmt(sp)}</td><td style="color:${scol(ssc)};font-weight:700">${ssc.toFixed(0)}%</td>`
      +SHVORDER.map(()=>'<td></td>').join('')+`</tr>`;
    mem.forEach(m=>{const u=m.u;
      h+=`<tr id="su${m.i}" class="svrow" onclick="selShovUnit(${m.i})"><td class="hmk"><b>${u.unit}</b> <span class="gc">(${u.n})</span></td>`
        +`<td style="font-weight:700">${fmt(m.act)}</td><td>${fmt(m.pot)}</td><td style="color:${scol(m.sc)};font-weight:700">${m.sc.toFixed(0)}%</td>`
        +SHVORDER.map(k=>heat(m.comps[k]||0)).join('')
        +`</tr>`;
      SHVORDER.forEach(k=>{const v=m.comps[k]||0; if(v<0)colTot[k]+=-v;}); gPot+=m.pot; gAct+=m.act; gLoss+=m.loss;});});
  h+=`<tr class="hmfoot"><td class="hmk">Total (all ${meta.length})</td><td style="font-weight:700">${fmt(gAct)}</td><td>${fmt(gPot)}</td><td>${gPot?(gAct/gPot*100).toFixed(0)+'%':''}</td>`
    +SHVORDER.map(k=>`<td>${colTot[k]>0?'−'+fmt(colTot[k]):''}</td>`).join('')+`</tr>`;
  return h+'</table></div><div class="foot">Redder cell = more tonnes lost to that factor. Click a shovel row to load its cycle waterfall and status timeline below.</div>';
}
// Merged Haulage loss matrix — lanes grouped by loading shovel, each row interactive (drives the lane waterfall below).
// Analog of drawShovelLossTable: combines the lane×factor loss heatmap with the lane breakdown table.
function drawLaneMatrix(lanes){
  if(!lanes||!lanes.length) return '<div class="foot">No path data for this view.</div>';
  const meta=lanes.map((l,i)=>({i,l,pot:l.schedPotential??l.pot,act:l.act,sc:l.score,loss:sumLoss(l.rows,ORDER),comps:l.rows,shovel:l.shovel,dist:l.distFull||0,loads:l.loads||0}));
  let mxcell=1; meta.forEach(m=>ORDER.forEach(k=>{const v=m.comps[k]||0; if(v<0)mxcell=Math.max(mxcell,-v);}));
  const scol=s=>s>=95?'var(--green)':(s>=85?'#c9851f':'var(--red)');
  const groups={}; meta.forEach(m=>{(groups[m.shovel]||(groups[m.shovel]=[])).push(m);});
  Object.values(groups).forEach(a=>a.sort((x,y)=>y.loss-x.loss));
  const gkeys=Object.keys(groups).sort((a,b)=>groups[b].reduce((s,m)=>s+m.loss,0)-groups[a].reduce((s,m)=>s+m.loss,0));
  const heat=v=>{const loss=v<0?-v:0,op=(loss/mxcell*0.82).toFixed(3);return `<td class="hmcell" style="background:rgba(204,60,50,${loss?op:0})">${loss?fmt(loss):''}</td>`;};
  const cg=`<colgroup><col style="width:14%"><col style="width:8%"><col style="width:8%"><col style="width:6%">`
    +ORDER.map(()=>'<col style="width:7%">').join('')+`</colgroup>`;
  const HAULHDR=Object.assign({},ROWLABEL,{Queue:'Queue',Spot:'Spot'});
  let h=`<div class="hmwrap svwrap"><table class="hmtab svloss">${cg}<tr><th>Dump</th><th>Actual</th><th>Potential</th><th>Score</th>`
    +ORDER.map(k=>`<th>${HAULHDR[k]}</th>`).join('')+'</tr>';
  const colTot={}; ORDER.forEach(k=>colTot[k]=0); let gPot=0,gAct=0,gLoss=0;
  gkeys.forEach(gk=>{const mem=groups[gk];
    const sp=mem.reduce((s,m)=>s+m.pot,0),sa=mem.reduce((s,m)=>s+m.act,0),sl=mem.reduce((s,m)=>s+m.loss,0),ssc=sp?sa/sp*100:0;
    h+=`<tr class="svgrp"><td>${gk} <span class="gc">(${mem.length} paths)</span></td>`
      +`<td style="font-weight:700">${fmt(sa)}</td><td>${fmt(sp)}</td><td style="color:${scol(ssc)};font-weight:700">${ssc.toFixed(0)}%</td>`
      +ORDER.map(()=>'<td></td>').join('')+`</tr>`;
    mem.forEach(m=>{const l=m.l;
      h+=`<tr id="lm${m.i}" class="svrow" onclick="selLaneRow(${m.i})" onmouseenter="fhShow(event,${m.i})" onmouseleave="popHide()"><td class="hmk" title="${l.load} → ${l.dump} · ${l.mat}">${l.dump} <span class="gc">(${l.loads}) @${(m.dist||0).toFixed(1)}km</span></td>`
        +`<td style="font-weight:700">${fmt(m.act)}</td><td>${fmt(m.pot)}</td><td style="color:${scol(m.sc)};font-weight:700">${m.sc.toFixed(0)}%</td>`
        +ORDER.map(k=>heat(m.comps[k]||0)).join('')
        +`</tr>`;
      ORDER.forEach(k=>{const v=m.comps[k]||0; if(v<0)colTot[k]+=-v;}); gPot+=m.pot; gAct+=m.act; gLoss+=m.loss;});});
  h+=`<tr class="hmfoot"><td class="hmk">Total (all ${meta.length} paths)</td><td style="font-weight:700">${fmt(gAct)}</td><td>${fmt(gPot)}</td><td>${gPot?(gAct/gPot*100).toFixed(0)+'%':''}</td>`
    +ORDER.map(k=>`<td>${colTot[k]>0?'−'+fmt(colTot[k]):''}</td>`).join('')+`</tr>`;
  return h+'</table></div><div class="foot">Redder cell = more tonnes lost to that factor. Click a path row to load its waterfall below.</div>';
}
function selLaneRow(i){document.querySelectorAll('#laneMatrix tr').forEach(t=>t.classList.remove('sel'));
  const r=document.getElementById('lm'+i); if(r)r.classList.add('sel'); selLane(i);}
function drawFullHaulLine(l){   // per-hour avg full-haul time (actual vs target) for the SELECTED path, on a time x-axis
  const fh=l&&l.fhHourly; if(!fh) return '<div class="foot">Select a path above to see its full-haul time trend.</div>';
  const act=fh.act,tgt=fh.tgt,hours=fh.hours, vals=act.concat(tgt).filter(v=>v!=null);
  if(!vals.length) return '<div class="foot">No full-haul time data for this path.</div>';
  const W=900,H=210,L=52,R=54,T=14,B=34,pw=W-L-R,ph=H-T-B;
  const mn0=Math.min(...vals),mx0=Math.max(...vals),padv=(mx0-mn0)*0.18||30, lo=Math.max(0,mn0-padv), hi=mx0+padv;   // zoom to the data range (y-axis need not start at 0)
  const X=i=>L+(i/11)*pw, Y=v=>T+ph-(v-lo)/((hi-lo)||1)*ph;
  let g='';
  {const m0=Math.floor(lo/60),m1=Math.ceil(hi/60),step=Math.max(1,Math.ceil((m1-m0)/5));   // gridlines at whole minutes within range
   for(let m=m0;m<=m1;m+=step){const y=Y(m*60); if(y<T-1||y>T+ph+1)continue;
     g+=`<line x1="${L}" y1="${y}" x2="${L+pw}" y2="${y}" stroke="#eef0f4"/><text x="${L-6}" y="${y+3}" text-anchor="end" font-size="8.5" fill="var(--muted)">${m}m</text>`;}}
  for(let i=0;i<12;i+=2){g+=`<text x="${X(i)}" y="${T+ph+13}" text-anchor="middle" font-size="8.5" fill="var(--muted)">${hours[i]}:00</text>`;}
  const path=arr=>{let d='',on=false;arr.forEach((v,i)=>{if(v==null){on=false;return;}const x=X(i),y=Y(v);d+=(on?' L':' M')+x+' '+y;on=true;});return d;};
  g+=`<path d="${path(tgt)}" fill="none" stroke="#888" stroke-width="1.6" stroke-dasharray="5 3"/>`;
  g+=`<path d="${path(act)}" fill="none" stroke="#3f51b5" stroke-width="2"/>`;
  act.forEach((v,i)=>{if(v==null)return;const x=X(i),y=Y(v),over=(tgt[i]!=null&&v>tgt[i]);
    g+=`<circle cx="${x}" cy="${y}" r="2.6" fill="${over?'#e23b32':'#1f9e8b'}"><title>${hours[i]}:00 — actual ${fmtTime(v)} vs target ${tgt[i]!=null?fmtTime(tgt[i]):'—'}</title></circle>`
      +`<text x="${x}" y="${y-5}" text-anchor="middle" font-size="7.5" fill="${over?'#b3382b':'#2f7a44'}">${fmtTime(v)}</text>`;});
  g+=`<text x="13" y="${T+ph/2}" transform="rotate(-90 13 ${T+ph/2})" text-anchor="middle" font-size="9" fill="var(--muted)">full haul time (mm:ss)</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px"><span class="badge"><b style="color:#3f51b5">—</b> actual</span><span class="badge"><b style="color:#888">– –</b> target</span><span class="badge"><b style="color:#1f9e8b">●</b> at/under · <b style="color:#e23b32">●</b> over target</span><span class="badge">${l.load} → ${l.dump} · ${l.loads} loads</span></div>`;
}
function shovUnitComps(u){const a=u.availDecomp;return {PA:a?a.pa.t:0,UA:a?a.ua.t:0,OE:a?a.oe.t:0,
  Payload:u.rows.Payload||0,Spot:u.rows.Spot||0,Load:u.rows.Load||0,Hang:u.rows.Hang||0};}
function shovTypeLabel(id){return id.startsWith('S0')?'BE 495':(id.startsWith('S8')?'HIT 8000':'');}
function renderLanes(){
  const lanes=V().trucksWF.lanes;
  const gt={};   // shovel subtotals
  lanes.forEach(l=>{const g=gt[l.shovel]||(gt[l.shovel]={pot:0,act:0,loads:0});g.pot+=l.pot;g.act+=l.act;g.loads+=l.loads;});
  // ---- shovel subtotals (used to order the lane table by tonnes lost) ----
  const sh={};
  lanes.forEach(l=>{const g=sh[l.shovel]||(sh[l.shovel]={pot:0,act:0,loads:0,rows:{},lmA:{},lmT:{},unit:{}});
    g.pot+=l.pot;g.act+=l.act;g.loads+=l.loads;
    for(const k in l.rows) g.rows[k]=(g.rows[k]||0)+l.rows[k];
    if(l.lm) for(const k in l.lm){const o=l.lm[k];g.lmA[k]=(g.lmA[k]||0)+o.actual*l.loads;g.lmT[k]=(g.lmT[k]||0)+o.target*l.loads;g.unit[k]=o.unit;}});
  if(selShovelId && !sh[selShovelId]) selShovelId=null;   // guard when view changes
  // ---- loss-analysis panels (top-3 band · heatmap), over the filtered lane set ----
  const anLanes=selShovelId?lanes.filter(l=>l.shovel===selShovelId):lanes;
  const ents=anLanes.map(l=>({id:l.load+'→'+l.dump,label:l.shovel+' · '+l.load+' → '+l.dump,attr:l.shovel,comps:l.rows}));
  const LA=lossAnalysis(ents,ORDER,ROWLABEL);
  document.getElementById('haulTop3').innerHTML=drawTop3(LA.ranked,LA.total);
  document.getElementById('laneMatrix').innerHTML=drawLaneMatrix(lanes);
  // biggest-loss lane order — used to default the waterfall selection below
  const shLoss={}; Object.keys(gt).forEach(id=>shLoss[id]=sumLoss((sh[id]||{rows:{}}).rows,ORDER));
  const shRank={}; Object.keys(gt).sort((a,b)=>shLoss[b]-shLoss[a]).forEach((id,i)=>shRank[id]=i);
  const laneOrder=lanes.map((l,i)=>i).filter(i=>!selShovelId||lanes[i].shovel===selShovelId)
    .sort((a,b)=>{const A=lanes[a],B=lanes[b]; return (shRank[A.shovel]-shRank[B.shovel])||(sumLoss(B.rows,ORDER)-sumLoss(A.rows,ORDER));});
  // ---- waterfall below: shovel aggregate if selected, else first lane ----
  if(selShovelId && sh[selShovelId]){
    const g=sh[selShovelId],lm={};
    for(const k in g.lmA) lm[k]={actual:g.lmA[k]/g.loads,target:g.lmT[k]/g.loads,unit:g.unit[k]};
    const score=g.pot>0?g.act/g.pot*100:0,tl=shovTypeLabel(selShovelId);
    document.getElementById('lanewf').innerHTML=
      `<h4 class="mini">Shovel ${selShovelId}${tl?' · '+tl:''} — aggregate of ${g.loads} loads &nbsp;·&nbsp; Potential ${fmt(g.pot)} → Actual ${fmt(g.act)} &nbsp;·&nbsp; ${score.toFixed(1)}%</h4>`
      +buildWF({potential:g.pot,actual:g.act,rows:g.rows,residual:0,lm});
    const ft=document.getElementById('haulFullTL'); if(ft)ft.innerHTML='<div class="foot">Select a single path to see its full-haul time trend.</div>';
  } else {
    const first=laneOrder.length?laneOrder[0]:-1;   // default to the biggest-loss lane
    if(first>=0) selLane(first); else {document.getElementById('lanewf').innerHTML=''; const ft=document.getElementById('haulFullTL'); if(ft)ft.innerHTML='';}
  }
}
function selShovel(id){ selShovelId=(selShovelId===id?null:id); renderLanes(); }
function selLane(i){
  const l=V().trucksWF.lanes[i];
  document.querySelectorAll('.lanetab tr').forEach(t=>t.classList.remove('sel'));
  const row=document.getElementById('ln'+i); if(row)row.classList.add('sel');
  const wf={potential:l.pot,schedPotential:l.schedPotential,actual:l.act,rows:l.rows,residual:l.residual,lm:l.lm,availDecomp:l.availDecomp};
  document.getElementById('lanewf').innerHTML=
    `<h4 class="mini">${l.mat} · ${l.load} → ${l.dump} &nbsp;·&nbsp; ${l.loads} loads &nbsp;·&nbsp; haul ${l.distFull.toFixed(1)}/${l.distEmpty.toFixed(1)} km (F/E) &nbsp;·&nbsp; score ${l.score.toFixed(1)}% &nbsp;·&nbsp; ${l.pit}</h4>`
    +buildWF(wf);
  const ft=document.getElementById('haulFullTL'); if(ft)ft.innerHTML=drawFullHaulLine(l);   // per-hour full-haul time for this path
  const fs=document.getElementById('fhtsub'); if(fs)fs.textContent='('+view+' · '+l.load+' → '+l.dump+')';
}
function buildShovWF(sw){
  const rows=sw.groups.map(g=>({label:g.name,delta:g.variance,col:{uom:'t/h',tgt:g.rateBudget.toFixed(0),act:g.rateActual.toFixed(0)}}));
  return waterfallSVG({startLabel:'Potential',startVal:sw.potential,endLabel:'Actual',endVal:sw.actual,rows});
}
function buildShovWF2(wf){
  const av=wf.availDecomp,rows=[];
  const pc=x=>({uom:'%',tgt:x.bud.toFixed(1),act:x.act.toFixed(1)});
  if(av){
    rows.push({label:'PA',delta:av.pa.t,col:pc(av.pa),segs:av.pa.reasons});
    rows.push({label:'UA',delta:av.ua.t,col:pc(av.ua),segs:av.ua.reasons});
    rows.push({label:'OE',delta:av.oe.t,col:pc(av.oe),segs:av.oe.reasons});
  }
  [['Payload','Payload'],['Spot','Spot at Shovel'],['Load','Load Time'],['Hang','Hang Time']].forEach(([k,lbl])=>
    rows.push({label:lbl,delta:wf.rows[k],col:leadStr(k,wf.lm)}));
  return waterfallSVG({startLabel:'Potential',startVal:av?wf.schedPotential:wf.potential,endLabel:'Actual',endVal:wf.actual,rows});
}
function renderShovWF2(){
  const wf=V().shovelWF2;
  if(!wf){document.getElementById('shovwf2').innerHTML='<div class="foot">No shovel cycle data for this view.</div>';document.getElementById('shov2ind').innerHTML='';document.getElementById('shovOW2').innerHTML='';return;}
  document.getElementById('shovwf2').innerHTML=buildShovWF2(wf);
  document.getElementById('shov2ind').innerHTML=
    (wf.hbneg?`<span class="badge">${wf.hbneg}/${wf.n} loads in groups with hang-budget clamped to 0</span>`:'');
  // ore/waste breakdown (cycle rows only)
  let ow='';
  ['Ore','Waste'].forEach(m=>{const mw=wf.byMaterial[m]; if(!mw)return;
    ow+=owHdr(m,mw)+buildShovWF2(mw);});
  document.getElementById('shovOW2').innerHTML=ow||'<div class="foot">No ore/waste split.</div>';
}
function renderLoading(){
  const wf=V().shovelWF2;
  document.getElementById('loadsub').textContent='('+view+') — click a shovel for its cycle waterfall';
  if(!wf){['shovList2','shovUnitWF2'].forEach(id=>{const e=document.getElementById(id);if(e)e.innerHTML='';});
    document.getElementById('loadTop3').innerHTML='<span class="foot">No shovel cycle data for this view.</span>';
    document.getElementById('tlsub2').textContent='('+view+')';document.getElementById('chTl2').innerHTML='';return;}
  // ---- top-3 loss band + merged interactive loss table (heatmap × drill-down) ----
  const ents=wf.units.map(u=>({id:u.unit,label:u.unit+' · '+(u.type==='BE495'?'BE 495':'HIT 8000'),attr:u.unit,comps:shovUnitComps(u)}));
  const LA=lossAnalysis(ents,SHVORDER,SHVLABEL);
  document.getElementById('loadTop3').innerHTML=drawTop3(LA.ranked,LA.total);
  document.getElementById('shovList2').innerHTML=drawShovelLossTable(wf.units);
  // auto-select the biggest-loss shovel (drives waterfall + status timeline)
  const uloss=u=>sumLoss(shovUnitComps(u),SHVORDER);
  const oidx=wf.units.map((u,i)=>i).sort((a,b)=>uloss(wf.units[b])-uloss(wf.units[a]));
  if(oidx.length){ selShovUnit(oidx[0]); }
  else { document.getElementById('shovUnitWF2').innerHTML='';
    document.getElementById('tlsub2').textContent='('+view+')';
    document.getElementById('chTl2').innerHTML=drawTimeline(V().analytics.timeline); }
}
function selShovUnit(i){
  const u=V().shovelWF2.units[i];
  document.querySelectorAll('#shovList2 tr').forEach(t=>t.classList.remove('sel'));
  const row=document.getElementById('su'+i); if(row)row.classList.add('sel');
  const av=u.availDecomp;
  document.getElementById('shovUnitWF2').innerHTML=
    `<h4 class="mini">Shovel ${u.unit} · ${u.type==='BE495'?'BE 495':'HIT 8000'} — ${u.n} loads${av?'':' &nbsp;·&nbsp; cycle only'}</h4>`
    +buildShovWF2(u);
  // mirror the selection onto the equipment status timeline (single-shovel = large band, like the trucks-at-dump graph)
  const tlsub=document.getElementById('tlsub2'); if(tlsub)tlsub.textContent='('+view+' · '+u.unit+')';
  const ch=document.getElementById('chTl2'); if(ch)ch.innerHTML=drawShovelBand(V().analytics.timeline,u.unit);
}
function renderShov(){
  const sw=V().shovelWF;
  document.getElementById('shovsub').textContent='(Potential → Actual, '+view+')';
  document.getElementById('shovind').innerHTML=
    `<span class="badge">Loading Score <b>${sw.score.toFixed(1)}%</b></span>
     <span class="badge">Potential <b>${fmt(sw.potential)}</b> → Actual <b>${fmt(sw.actual)}</b></span>`;
  document.getElementById('shovwf').innerHTML=buildShovWF(sw);
  let u=`<table class="lanetab"><tr><th>Shovel</th><th>Type</th><th>Loads</th><th>Potential</th><th>Actual</th><th>Score</th><th>Bgt→Act t/h</th></tr>`;
  sw.units.forEach(x=>{const col=x.score>=100?'var(--green)':(x.score>=92?'#c9851f':'var(--red)');
    u+=`<tr><td>${x.unit}</td><td>${x.type==='BE495'?'BE 495':'HIT 8000'}</td><td>${x.loads}</td>
      <td>${fmt(x.pot)}</td><td>${fmt(x.act)}</td>
      <td style="color:${col};font-weight:700">${x.score.toFixed(0)}%</td>
      <td>${x.rateBudget.toFixed(0)} → ${x.rateActual.toFixed(0)}</td></tr>`;});
  document.getElementById('shovunits').innerHTML=u+'</table>';
  // refresh any open payload/hang/load box below the waterfall for the current view
  Object.keys(SHOVBOX).forEach(w=>{const sec=document.getElementById(SHOVBOX[w][0]); if(sec&&!sec.hidden)renderShovBox(w);});
}
const CORE='#1f9e8b',CWASTE='#d08a1f';
function shortId(id){
  if(id.startsWith('CR'))return id.replace(/_/g,' ');
  if(id.startsWith('DU_'))return id.replace('DU_','').replace('_HD_516','').slice(0,11);
  if(id.startsWith('ST_'))return id.replace('ST_','').slice(0,10);
  const p=id.split('-');return p[p.length-1];
}
function drawSan(d){
  let flows=d.fullFlows.filter(f=>f.tons>0).slice();
  if(!flows.length)return '<div class="foot">No flow data.</div>';
  const tpOf={};d.loadNodes.forEach(n=>tpOf[n.id]=n.tpnoh||0);
  const dtOf={};d.dumpNodes.forEach(n=>dtOf[n.id]=n.tons||0);
  let colTot=Math.max(flows.reduce((s,f)=>s+f.tons,0),1);
  const minTon=colTot*0.015;                       // skip tiny ribbons that have no space
  flows=flows.filter(f=>f.tons>=minTon);
  const src={},dst={};flows.forEach(f=>{src[f.from]=(src[f.from]||0)+f.tons;dst[f.to]=(dst[f.to]||0)+f.tons;});
  // locked-load counts per node (locked = not optimized; hatched on the ribbons)
  const lLk={},lTt={},dLk={},dTt={};
  flows.forEach(f=>{lTt[f.from]=(lTt[f.from]||0)+(f.n||0);lLk[f.from]=(lLk[f.from]||0)+(f.nlock||0);
    dTt[f.to]=(dTt[f.to]||0)+(f.n||0);dLk[f.to]=(dLk[f.to]||0)+(f.nlock||0);});
  const lpct=k=>lTt[k]?Math.round(lLk[k]/lTt[k]*100):0, dpct=k=>dTt[k]?Math.round(dLk[k]/dTt[k]*100):0;
  const loads=Object.keys(src).sort((a,b)=>src[b]-src[a]),dumps=Object.keys(dst).sort((a,b)=>dst[b]-dst[a]);
  const W=900,H=Math.max(360,30*Math.max(loads.length,dumps.length)+40),pad=18,nodeW=13,lx=230,rx=W-230-nodeW;
  const totL=loads.reduce((s,k)=>s+src[k],0),totR=dumps.reduce((s,k)=>s+dst[k],0);colTot=Math.max(totL,totR,1);
  const gap=12,avail=H-2*pad-gap*(Math.max(loads.length,dumps.length)-1),sc=avail/colTot;
  const pos={};let y=pad;loads.forEach(k=>{const h=src[k]*sc;pos['L'+k]={x:lx,y,h,off:0};y+=h+gap;});
  y=pad;dumps.forEach(k=>{const h=dst[k]*sc;pos['D'+k]={x:rx,y,h,off:0};y+=h+gap;});
  const li={},di={};loads.forEach((k,i)=>li[k]=i);dumps.forEach((k,i)=>di[k]=i);
  flows.sort((a,b)=>li[a.from]-li[b.from]||di[a.to]-di[b.to]);
  let rib='',dl='';flows.forEach(f=>{const S=pos['L'+f.from],T=pos['D'+f.to],th=f.tons*sc,y1=S.y+S.off,y2=T.y+T.off;S.off+=th;T.off+=th;const x1=S.x+nodeW,x2=T.x,xm=(x1+x2)/2,c=f.mat==='Waste'?CWASTE:CORE;
    const lf=f.n?(f.nlock||0)/f.n:0;   // locked fraction of this flow's loads
    rib+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+th} C${xm} ${y2+th},${xm} ${y1+th},${x1} ${y1+th} Z" fill="${c}" fill-opacity="0.42"><title>${shortId(f.from)} → ${shortId(f.to)}: ${f.tons.toLocaleString()}t · ${(f.km||0)}/${(f.kmE||0)} km (actual/expected) · ${Math.round(lf*100)}% locked</title></path>`;
    if(lf>0){const lth=th*lf;   // hatch the locked slice (top edge of the ribbon)
      rib+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+lth} C${xm} ${y2+lth},${xm} ${y1+lth},${x1} ${y1+lth} Z" fill="url(#lockhatch)" pointer-events="none"/>`;}
    if(th>=9&&f.km){const ly=y1+th/2+3;   // haul-distance label (actual/expected), just right of the shovel node inside the ribbon
      dl+=`<text x="${x1+5}" y="${ly}" font-size="8.5" font-weight="600" fill="#2b2f36" stroke="#fff" stroke-width="2.4" paint-order="stroke" pointer-events="none">${f.km} / ${f.kmE} km</text>`;}});
  let nd='';
  loads.forEach(k=>{const p=pos['L'+k],lbl=shortId(k)+(tpOf[k]?'  ·  '+tpOf[k].toLocaleString()+' t/h':''),lp=lpct(k);
    nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="var(--muted)"/><text x="${p.x-6}" y="${p.y+p.h/2}" text-anchor="end" font-size="10" fill="var(--ink)">${lbl}</text>`
      +`<text x="${p.x-6}" y="${p.y+p.h/2+11}" text-anchor="end" font-size="8.5" fill="${lp>=25?'#b3382b':'var(--muted)'}">${lp}% locked</text>`;});
  dumps.forEach(k=>{const p=pos['D'+k],lbl=shortId(k)+'  ·  '+Math.round(dtOf[k]||dst[k]).toLocaleString()+' t',lp=dpct(k);
    nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="var(--muted)"/><text x="${p.x+nodeW+6}" y="${p.y+p.h/2}" text-anchor="start" font-size="10" fill="var(--ink)">${lbl}</text>`
      +`<text x="${p.x+nodeW+6}" y="${p.y+p.h/2+11}" text-anchor="start" font-size="8.5" fill="${lp>=25?'#b3382b':'var(--muted)'}">${lp}% locked</text>`;});
  const defs=`<defs><pattern id="lockhatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#233" stroke-width="1.5" stroke-opacity="0.6"/></pattern></defs>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${defs}${rib}${dl}${nd}</svg>`;
}
function renderHaulCycles(){
  const d=V().haulCycles;
  document.getElementById('hcsub').textContent='('+view+')';
  document.getElementById('hc').innerHTML=drawSan(d);
  document.getElementById('hcleg').innerHTML=
    `<span class="badge"><b style="color:${CORE}">■</b> ore</span><span class="badge"><b style="color:${CWASTE}">■</b> waste</span><span class="badge">▨ hatched = locked (un-optimized) loads · % under each node</span><span class="badge">left: shovel + actual TPNOH (t/h) · right: dump + total tonnes · ribbon ∝ tonnage · km = actual/expected haul dist</span>`;
}
// ---- Pulse — Shift State: LP dispatch-optimizer Sankey (dig -> dump) ----
function drawLPSan(edges){
  if(!edges||!edges.length)return '<div class="foot">No LP solution for this view/shift.</div>';
  // aggregate edges sharing the same (dig,dump) — usually already 1:1, but a shovel can occasionally split
  const byPair={};
  edges.forEach(e=>{const k=e.dig+'|'+e.dump;const a=byPair[k]||(byPair[k]={dig:e.dig,digLoc:e.digLoc,dump:e.dump,mat:e.mat,pathRate:0,loadRate:0});
    a.pathRate+=e.pathRate||0; a.loadRate+=e.loadRate||0; if((e.pathRate||0)>0)a.mat=e.mat;});
  const rows=Object.values(byPair);
  const digTot={},dumpTot={};
  rows.forEach(r=>{digTot[r.dig]=(digTot[r.dig]||0)+r.loadRate; dumpTot[r.dump]=(dumpTot[r.dump]||0)+Math.max(0,r.pathRate);});
  // per-dump blended grade (path-weighted over ore legs) + per-shovel POE (load ÷ dig rate)
  const dg={},poeN={},poeD={};
  edges.forEach(e=>{if((e.bit||0)>0){const a=dg[e.dump]||(dg[e.dump]={b:0,f:0,d:0,w:0});a.b+=e.bit*(e.pathRate||0);a.f+=(e.fines||0)*(e.pathRate||0);a.d+=(e.d50||0)*(e.pathRate||0);a.w+=(e.pathRate||0);}
    if(e.poe!=null&&(e.pathRate||0)>0){poeN[e.dig]=(poeN[e.dig]||0)+e.poe*e.pathRate;poeD[e.dig]=(poeD[e.dig]||0)+e.pathRate;}});
  const digs=Object.keys(digTot).sort((a,b)=>digTot[b]-digTot[a]);
  const dumps=Object.keys(dumpTot).sort((a,b)=>dumpTot[b]-dumpTot[a]);
  const W=980,H=Math.max(360,32*Math.max(digs.length,dumps.length)+50),pad=20,nodeW=13,lx=230,rx=W-230-nodeW;
  const totL=digs.reduce((s,k)=>s+digTot[k],0),totR=dumps.reduce((s,k)=>s+dumpTot[k],0),colTot=Math.max(totL,totR,1);
  const gap=10;
  const availL=H-2*pad-gap*Math.max(0,digs.length-1), availR=H-2*pad-gap*Math.max(0,dumps.length-1);
  const sc=Math.min(availL,availR)/colTot;
  const posL={};let y=pad;digs.forEach(k=>{const h=Math.max(4,digTot[k]*sc);posL[k]={x:lx,y,h,off:0};y+=h+gap;});
  const posR={};y=pad;dumps.forEach(k=>{const h=Math.max(4,dumpTot[k]*sc);posR[k]={x:rx,y,h,off:0};y+=h+gap;});
  const li={},ri={};digs.forEach((k,i)=>li[k]=i);dumps.forEach((k,i)=>ri[k]=i);
  rows.sort((a,b)=>li[a.dig]-li[b.dig]||ri[a.dump]-ri[b.dump]);
  let rib='',segBars='',lbl='';
  const digCov={};digs.forEach(k=>digCov[k]=[0,0]);   // [Σpath,Σload] per dig, for the node-label %
  rows.forEach(r=>{
    const L=posL[r.dig],R=posR[r.dump]; if(!L||!R)return;
    const segH=Math.max(0,r.loadRate)*sc, solidH=Math.max(0,Math.min(r.pathRate,r.loadRate))*sc, hatchH=Math.max(0,segH-solidH);
    const y1=L.y+L.off, x1=L.x+nodeW; L.off+=segH;
    digCov[r.dig][0]+=Math.max(0,r.pathRate); digCov[r.dig][1]+=Math.max(0,r.loadRate);
    const c=r.mat==='Ore'?CORE:CWASTE;
    // segment on the dig node bar: solid (covered) + hatched (uncovered)
    if(solidH>0)segBars+=`<rect x="${L.x}" y="${y1}" width="${nodeW}" height="${solidH}" fill="${c}"/>`;
    if(hatchH>0.3)segBars+=`<rect x="${L.x}" y="${y1+solidH}" width="${nodeW}" height="${hatchH}" fill="${c}" fill-opacity="0.28"/><rect x="${L.x}" y="${y1+solidH}" width="${nodeW}" height="${hatchH}" fill="url(#covhatch)"/>`;
    if(solidH<=0)return;   // nothing actually flowing — no ribbon
    const y2=R.y+R.off, x2=R.x; R.off+=solidH;
    const xm=(x1+x2)/2, cov=r.loadRate>0?Math.round(r.pathRate/r.loadRate*100):0;
    rib+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+solidH} C${xm} ${y2+solidH},${xm} ${y1+solidH},${x1} ${y1+solidH} Z" fill="${c}" fill-opacity="0.42"><title>${shortId(r.dig)} (${r.digLoc||r.dig}) → ${shortId(r.dump)}: ${r.mat} · ${fmt(r.pathRate)} / ${fmt(r.loadRate)} t/h · ${cov}% covered</title></path>`;
  });
  digs.forEach(k=>{const p=posL[k],cy=p.y+p.h/2,cv=digCov[k][1]>0?Math.round(digCov[k][0]/digCov[k][1]*100):0,col=cv>=98?'#0e6b5c':cv>0?'#a5691a':'#6b7280';
    const poe=poeD[k]>0?(poeN[k]/poeD[k]):null;
    lbl+=`<text x="${p.x-7}" y="${cy+2}" text-anchor="end" font-size="15" font-weight="800" fill="${col}">${cv}%</text>`
      +`<text x="${p.x-57}" y="${cy-3}" text-anchor="end" font-size="10" fill="var(--ink)">${shortId(k)}</text>`
      +`<text x="${p.x-57}" y="${cy+8}" text-anchor="end" font-size="8.5" fill="var(--muted)">cov${poe!=null?' · POE '+Math.round(poe*100)+'%':''}</text>`;});
  dumps.forEach(k=>{const p=posR[k],cy=p.y+p.h/2,a=dg[k];
    lbl+=`<text x="${p.x+nodeW+6}" y="${cy-4}" text-anchor="start" font-size="10" font-weight="600" fill="var(--ink)">${shortId(k)}</text>`
      +`<text x="${p.x+nodeW+6}" y="${cy+7}" text-anchor="start" font-size="8.5" fill="var(--muted)">${fmt(dumpTot[k])} t/h</text>`
      +(a&&a.w>0?`<text x="${p.x+nodeW+6}" y="${cy+18}" text-anchor="start" font-size="8.5" fill="#0e6b5c">blend Bit ${(a.b/a.w).toFixed(1)} · Fn ${(a.f/a.w).toFixed(1)} · D50 ${Math.round(a.d/a.w)}</text>`:'');});
  const defs=`<defs><pattern id="covhatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#233" stroke-width="1.5" stroke-opacity="0.55"/></pattern></defs>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${defs}${rib}${segBars}${lbl}</svg>`;
}
function drawLPSanDump(edges){
  // Dump-centric: crusher/dump nodes on the left, shovels on the right; ribbon width ∝ path rate (t/h).
  // Left labels carry each dump's blended grade (path-weighted Bit/Fines/D50). Right labels show each shovel's
  // coverage factor (big) and POE = LoadRate ÷ DigRate.
  const es=(edges||[]).filter(e=>e.pathRate>0);
  if(!es.length)return '<div class="foot">No active LP legs.</div>';
  const dumpTot={},shovTot={},shovLoad={},poeN={},poeD={},dg={};
  es.forEach(e=>{
    dumpTot[e.dump]=(dumpTot[e.dump]||0)+e.pathRate; shovTot[e.excav]=(shovTot[e.excav]||0)+e.pathRate; shovLoad[e.excav]=(shovLoad[e.excav]||0)+e.loadRate;
    if(e.poe!=null){poeN[e.excav]=(poeN[e.excav]||0)+e.poe*e.pathRate; poeD[e.excav]=(poeD[e.excav]||0)+e.pathRate;}
    if(e.bit>0){const a=dg[e.dump]||(dg[e.dump]={b:0,f:0,d:0,w:0});a.b+=e.bit*e.pathRate;a.f+=e.fines*e.pathRate;a.d+=e.d50*e.pathRate;a.w+=e.pathRate;}
  });
  const dumps=Object.keys(dumpTot).sort((a,b)=>dumpTot[b]-dumpTot[a]), shovs=Object.keys(shovTot).sort((a,b)=>shovTot[b]-shovTot[a]);
  const dLbl=k=>k.split('_').slice(0,2).join('_');
  const W=980,H=Math.max(360,48*Math.max(dumps.length,shovs.length)+40),pad=22,nodeW=13,lx=270,rx=W-230-nodeW,gap=20;
  const colTot=Math.max(dumps.reduce((s,k)=>s+dumpTot[k],0),shovs.reduce((s,k)=>s+shovTot[k],0),1);
  const sc=Math.min(H-2*pad-gap*Math.max(0,dumps.length-1),H-2*pad-gap*Math.max(0,shovs.length-1))/colTot;
  const posL={};let y=pad;dumps.forEach(k=>{const hh=Math.max(6,dumpTot[k]*sc);posL[k]={x:lx,y,h:hh,off:0};y+=hh+gap;});
  const posR={};y=pad;shovs.forEach(k=>{const hh=Math.max(6,shovTot[k]*sc);posR[k]={x:rx,y,h:hh,off:0};y+=hh+gap;});
  const li={},ri={};dumps.forEach((k,i)=>li[k]=i);shovs.forEach((k,i)=>ri[k]=i);
  let rib='';
  es.slice().sort((a,b)=>li[a.dump]-li[b.dump]||ri[a.excav]-ri[b.excav]).forEach(e=>{const L=posL[e.dump],R=posR[e.excav];if(!L||!R)return;
    const th=e.pathRate*sc,y1=L.y+L.off,y2=R.y+R.off;L.off+=th;R.off+=th;const x1=L.x+nodeW,x2=R.x,xm=(x1+x2)/2,c=e.mat==='Ore'?CORE:CWASTE;
    rib+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+th} C${xm} ${y2+th},${xm} ${y1+th},${x1} ${y1+th} Z" fill="${c}" fill-opacity="0.42"><title>${shortId(e.dump)} ← ${e.excav}: ${e.mat} · ${fmt(e.pathRate)} t/h · ${Math.round((e.cov||0)*100)}% covered</title></path>`;});
  let nd='';
  dumps.forEach(k=>{const p=posL[k],a=dg[k];
    nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="var(--muted)"/>`;
    nd+=`<text x="${p.x-6}" y="${p.y+p.h/2-5}" text-anchor="end" font-size="11" font-weight="600" fill="var(--ink)">${dLbl(k)}</text>`;
    nd+=`<text x="${p.x-6}" y="${p.y+p.h/2+6}" text-anchor="end" font-size="9" fill="var(--muted)">${fmt(dumpTot[k])} t/h</text>`;
    if(a&&a.w>0)nd+=`<text x="${p.x-6}" y="${p.y+p.h/2+17}" text-anchor="end" font-size="9" fill="#0e6b5c">blend Bit ${(a.b/a.w).toFixed(1)} · Fn ${(a.f/a.w).toFixed(1)} · D50 ${Math.round(a.d/a.w)}</text>`;});
  shovs.forEach(k=>{const p=posR[k],cov=shovLoad[k]>0?shovTot[k]/shovLoad[k]:0,cvp=Math.round(cov*100),col=cvp>=98?'#0e6b5c':cvp>0?'#a5691a':'#6b7280';
    const poe=poeD[k]>0?(poeN[k]/poeD[k]):null;
    nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="var(--muted)"/>`;
    nd+=`<text x="${p.x+nodeW+8}" y="${p.y+p.h/2}" text-anchor="start" font-size="15" font-weight="800" fill="${col}">${cvp}%</text>`;
    nd+=`<text x="${p.x+nodeW+48}" y="${p.y+p.h/2-4}" text-anchor="start" font-size="11" font-weight="600" fill="var(--ink)">${k}</text>`;
    nd+=`<text x="${p.x+nodeW+48}" y="${p.y+p.h/2+7}" text-anchor="start" font-size="9" fill="var(--muted)">cov${poe!=null?(' · POE '+Math.round(poe*100)+'%'):''}</text>`;});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${rib}${nd}</svg>`;
}
function drawLPSan3(loadE,backE){
  // 3-column dump-centric: shovels (loaded) → dump/crusher (centre) → shovels (empty return).
  loadE=(loadE||[]).filter(e=>(e.pathRate||0)>0||(e.loadRate||0)>0); backE=(backE||[]).filter(e=>(e.path||0)>0);
  if(!loadE.length&&!backE.length)return '<div class="foot">No LP legs for this view.</div>';
  const dumps={}; loadE.forEach(e=>dumps[e.dump]=1); backE.forEach(e=>dumps[e.dump]=1);
  // node dig/load rate = a single value the LP repeats on each of the shovel's legs → take it ONCE (max),
  // not summed, so multi-dump shovels aren't double-counted (coverage was ≈ half when summed).
  const digTot={}; loadE.forEach(e=>digTot[e.dig]=Math.max(digTot[e.dig]||0,Math.max(0,e.loadRate)));
  const dIn={}; loadE.forEach(e=>dIn[e.dump]=(dIn[e.dump]||0)+Math.max(0,e.pathRate));
  const dOut={}; backE.forEach(e=>dOut[e.dump]=(dOut[e.dump]||0)+e.path);
  const backTot={}; backE.forEach(e=>backTot[e.excav]=(backTot[e.excav]||0)+e.path);
  const dumpH={}; Object.keys(dumps).forEach(d=>dumpH[d]=Math.max(dIn[d]||0,dOut[d]||0));
  const dg={},poeN={},poeD={},cN={};
  loadE.forEach(e=>{if((e.bit||0)>0){const a=dg[e.dump]||(dg[e.dump]={b:0,f:0,d:0,w:0});a.b+=e.bit*e.pathRate;a.f+=(e.fines||0)*e.pathRate;a.d+=(e.d50||0)*e.pathRate;a.w+=e.pathRate;}
    if(e.poe!=null&&e.pathRate>0){poeN[e.dig]=(poeN[e.dig]||0)+e.poe*e.pathRate;poeD[e.dig]=(poeD[e.dig]||0)+e.pathRate;}
    cN[e.dig]=(cN[e.dig]||0)+Math.max(0,e.pathRate);});   // cN = Σ path rate across the shovel's legs (coverage numerator)
  // dominant material per shovel (by loaded path rate) → order shovels ORE on top, then waste, then unknown
  const matN={}; loadE.forEach(e=>{const m=matN[e.dig]||(matN[e.dig]={});m[e.mat]=(m[e.mat]||0)+Math.max(0,e.pathRate);});
  const matOf={}; Object.keys(matN).forEach(k=>{matOf[k]=Object.keys(matN[k]).sort((a,b)=>matN[k][b]-matN[k][a])[0];});
  const NOTLP='#8a4fd0';   // distinctive colour for shovels/tonnes NOT covered by the optimizer
  const notLP={},notLPst={}; loadE.forEach(e=>{if(e.notLP){notLP[e.dig]=true;if(e.notLPstatus)notLPst[e.dig]=e.notLPstatus;}});
  const mrank=k=>notLP[k]?3:(matOf[k]==='Ore'?0:(matOf[k]==='Waste'?1:2));
  // LP priority per shovel (min numeric priority across its legs); 255+ = Disabled
  const prioN={}; loadE.forEach(e=>{const raw=(e.priority==null?'':(''+e.priority).trim());if(raw==='')return;const pv=+raw;if(isNaN(pv))return;prioN[e.dig]=(prioN[e.dig]==null?pv:Math.min(prioN[e.dig],pv));});
  const prioOf=k=>prioN[k]; const prS=k=>{const v=prioN[k];return v==null?1e7:v;};
  const digs=Object.keys(digTot).sort((a,b)=>mrank(a)-mrank(b)||prS(a)-prS(b)||digTot[b]-digTot[a]);
  const cdumps=Object.keys(dumps).sort((a,b)=>dumpH[b]-dumpH[a]);
  const rsh=Object.keys(backTot).sort((a,b)=>mrank(a)-mrank(b)||backTot[b]-backTot[a]);
  const W=1140,nodeW=13,pad=34,gap=13,lx=210,cx=W/2-nodeW/2,rx=W-210-nodeW;
  const nrows=Math.max(digs.length,cdumps.length,rsh.length,1);
  const H=Math.max(360,34*nrows+54);
  const avail=k=>H-2*pad-gap*Math.max(0,k-1);
  const colL=digs.reduce((s,k)=>s+digTot[k],0)||1, colC=cdumps.reduce((s,k)=>s+dumpH[k],0)||1, colR=rsh.reduce((s,k)=>s+backTot[k],0)||1;
  const sc=Math.min(avail(digs.length)/colL, avail(cdumps.length)/colC, rsh.length?avail(rsh.length)/colR:1e9);
  // minimum row slot so labels never collide even when a node's tonnage bar is tiny: 46 for the 3-line dump
  // labels (grade / name / t·h), 34 for the 2-line shovel labels. Node bar heights still ∝ rate; only the
  // spacing grows, so each label stays centred on its own bar.
  const MINPITCH=46, MINPS=38;
  const pL={};let y=pad;let ylBot=pad;digs.forEach(k=>{const hh=Math.max(5,digTot[k]*sc);pL[k]={x:lx,y,h:hh,off:0};ylBot=y+hh;y+=Math.max(hh+gap,MINPS);});
  const pC={};y=pad;let ycBot=pad;cdumps.forEach(k=>{const hh=Math.max(5,dumpH[k]*sc);pC[k]={x:cx,y,h:hh,offL:0,offR:0};ycBot=y+hh;y+=Math.max(hh+gap,MINPITCH);});
  const pR={};y=pad;let yrBot=pad;rsh.forEach(k=>{const hh=Math.max(5,backTot[k]*sc);pR[k]={x:rx,y,h:hh,off:0};yrBot=y+hh;y+=Math.max(hh+gap,MINPS);});
  const Hsvg=Math.max(H,ylBot+pad,ycBot+pad,yrBot+pad);   // grow the canvas to fit the tallest spaced-out column
  const di={};digs.forEach((k,i)=>di[k]=i); const cdi={};cdumps.forEach((k,i)=>cdi[k]=i); const rdi={};rsh.forEach((k,i)=>rdi[k]=i);
  let rib='',bars='';
  loadE.slice().sort((a,b)=>di[a.dig]-di[b.dig]||cdi[a.dump]-cdi[b.dump]).forEach(e=>{const L=pL[e.dig],C=pC[e.dump];if(!L||!C)return;
    const th=Math.max(0,e.pathRate)*sc;   // ribbon width = trucked path rate to this dump (covered flow)
    if(th<=0)return;
    const y1=L.y+L.off,x1=L.x+nodeW;L.off+=th;const c=e.notLP?NOTLP:(e.mat==='Ore'?CORE:CWASTE);
    bars+=`<rect x="${L.x}" y="${y1}" width="${nodeW}" height="${th}" fill="${c}"/>`+(e.notLP?`<rect x="${L.x}" y="${y1}" width="${nodeW}" height="${th}" fill="url(#notlphatch)"/>`:'');
    const y2=C.y+C.offL,x2=C.x;C.offL+=th;const xm=(x1+x2)/2;
    const _pd=`M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+th} C${xm} ${y2+th},${xm} ${y1+th},${x1} ${y1+th} Z`;
    rib+=`<path d="${_pd}" fill="${c}" fill-opacity="0.42"><title>${e.dig} → ${shortId(e.dump)}: ${e.notLP?('NOT in optimizer · '+fmt(e.tons||0)+' t actual (~'+fmt(e.pathRate)+' t/h)'):(e.mat+' · '+fmt(e.pathRate)+' t/h loaded')}</title></path>`+(e.notLP?`<path d="${_pd}" fill="url(#notlphatch)" pointer-events="none"/>`:'');});
  // uncovered dig capacity per shovel (node dig rate − Σ path) → single hatch band at the bottom of the shovel bar
  digs.forEach(k=>{const p=pL[k];const unc=Math.max(0,p.h-p.off);if(unc>0.5){const yb=p.y+p.off;bars+=`<rect x="${p.x}" y="${yb}" width="${nodeW}" height="${unc}" fill="var(--muted)" fill-opacity="0.28"/><rect x="${p.x}" y="${yb}" width="${nodeW}" height="${unc}" fill="url(#covhatch)"/>`;}});
  backE.slice().sort((a,b)=>cdi[a.dump]-cdi[b.dump]||rdi[a.excav]-rdi[b.excav]).forEach(e=>{const C=pC[e.dump],R=pR[e.excav];if(!C||!R)return;
    const th=e.path*sc,y1=C.y+C.offR,y2=R.y+R.off;C.offR+=th;R.off+=th;const x1=C.x+nodeW,x2=R.x,xm=(x1+x2)/2;
    rib+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+th} C${xm} ${y2+th},${xm} ${y1+th},${x1} ${y1+th} Z" fill="#9aa6b5" fill-opacity="0.5"><title>${shortId(e.dump)} → ${e.excav} (empty return): ${fmt(e.path)} t/h</title></path>`;});
  let nd='';
  let _lcur=-1e9;   // label cursor: keep shovel labels ≥ LGAP apart so a tall→short bar pair can't overlap them
  const LGAP=34;
  digs.forEach(k=>{const p=pL[k];let cy=p.y+p.h/2;if(cy<_lcur+LGAP)cy=_lcur+LGAP;_lcur=cy;const isN=notLP[k];const mc=isN?NOTLP:(matOf[k]==='Ore'?CORE:(matOf[k]==='Waste'?CWASTE:'#9aa6b5'));
    const pv=prioOf(k);const dis=(pv!=null&&pv>=255);const prTxt=(pv==null)?'':(dis?'Disabled':'P'+pv);const prCol=dis?'#b3382b':'#3a6ea5';
    const cvf=(!isN&&digTot[k]>0)?(cN[k]||0)/digTot[k]:null;const cvCol=cvf==null?'#9aa0ab':(cvf>=0.98?'#0e6b5c':cvf>0?'#a5691a':'#9aa0ab');
    const suf=isN?` · <tspan fill="${NOTLP}" font-weight="700">not in LP${notLPst[k]?' · '+notLPst[k]:''}</tspan>`:(prTxt?` · <tspan fill="${prCol}" font-weight="700">${prTxt}</tspan>`:'');
    nd+=`<text x="${p.x-7}" y="${cy-4}" text-anchor="end" font-size="14.5" font-weight="700" fill="var(--ink)" stroke="#fff" stroke-width="3" paint-order="stroke"><tspan fill="${mc}">■ </tspan>${k}${cvf!=null?' <tspan fill="'+cvCol+'">'+Math.round(cvf*100)+'%</tspan>':''}</text>`
      +`<text x="${p.x-7}" y="${cy+13}" text-anchor="end" font-size="12.1" fill="var(--muted)" stroke="#fff" stroke-width="2.6" paint-order="stroke">${fmt(digTot[k])} t/h${suf}</text>`;});
  cdumps.forEach(k=>{const p=pC[k],a=dg[k],cy=p.y+p.h/2;
    nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="#5b6675"/>`;
    // weighted-average grade — directly on top of the crusher name (one line above it)
    if(a&&a.w>0)nd+=`<text x="${p.x+nodeW/2}" y="${cy-20}" text-anchor="middle" font-size="12.5" font-weight="800" fill="#243056" stroke="#fff" stroke-width="3.2" paint-order="stroke">Bit ${(a.b/a.w).toFixed(1)}% · Fn ${(a.f/a.w).toFixed(1)}% · D50 ${Math.round(a.d/a.w)}</text>`;
    // dump / crusher name — centred, with the t/h throughput directly below it at the same size
    nd+=`<text x="${p.x+nodeW/2}" y="${cy-4}" text-anchor="middle" font-size="12.5" font-weight="800" fill="#fff" stroke="#20242b" stroke-width="3" paint-order="stroke">${shortId(k)}</text>`;
    nd+=`<text x="${p.x+nodeW/2}" y="${cy+12}" text-anchor="middle" font-size="12.5" font-weight="700" fill="#fff" stroke="#20242b" stroke-width="3" paint-order="stroke">${fmt(dumpH[k])} t/h</text>`;});
  let _rcur=-1e9;
  rsh.forEach(k=>{const p=pR[k];let cy=p.y+p.h/2;if(cy<_rcur+LGAP)cy=_rcur+LGAP;_rcur=cy;const mc=matOf[k]==='Ore'?CORE:(matOf[k]==='Waste'?CWASTE:'#9aa6b5');
    nd+=`<text x="${p.x+nodeW+6}" y="${cy-4}" text-anchor="start" font-size="14.5" font-weight="700" fill="var(--ink)" stroke="#fff" stroke-width="3" paint-order="stroke"><tspan fill="${mc}">■ </tspan>${k}</text>`
      +`<text x="${p.x+nodeW+6}" y="${cy+13}" text-anchor="start" font-size="12.1" fill="var(--muted)" stroke="#fff" stroke-width="2.6" paint-order="stroke">${fmt(backTot[k])} t/h empty</text>`;});
  const hd=`<text x="${lx-51}" y="13" text-anchor="end" font-size="9" font-weight="700" letter-spacing="0.6" fill="#8fa0b8">SHOVEL → LOADED</text><text x="${W/2}" y="13" text-anchor="middle" font-size="9" font-weight="700" letter-spacing="0.6" fill="var(--muted)">DUMP / CRUSHER</text><text x="${rx+nodeW+6}" y="13" text-anchor="start" font-size="9" font-weight="700" letter-spacing="0.6" fill="#8fa0b8">EMPTY → SHOVEL</text>`;
  const defs=`<defs><pattern id="covhatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#233" stroke-width="1.5" stroke-opacity="0.55"/></pattern><pattern id="notlphatch" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="#fff" stroke-width="2" stroke-opacity="0.6"/></pattern></defs>`;
  return `<svg viewBox="0 0 ${W} ${Hsvg+16}" width="100%">${defs}${hd}${rib}${bars}${nd}</svg>`;
}
function renderPulse(){
  const lpbs=DATA.lpByShift||{};
  const lpl=(lpbs[shift])||{};
  const _lpKeys=Object.keys(lpbs); const _lpLatest=_lpKeys.length?_lpKeys.sort().slice(-1)[0]:'';
  const _isLive=(shift===_lpLatest);
  const pf = view==='Combined' ? (_=>true) : (p=>p===view);
  const when=document.getElementById('pulseWhen');
  if(when)when.textContent = lpl.shiftId ? ((_isLive?'Live optimizer — current shift ':'Past shift ')+lpl.shiftId+(_isLive?' · latest solve ':' · final solution ')+(lpl.lpTime||'')+' · '+(lpl.nSolves||0)+' solves this shift') : 'No live LP feed for this shift (ShovelCoverageFactors.csv).';
  const miss=document.getElementById('lpMissing'); if(miss)miss.innerHTML='';   // shovels absent from the LP feed are overlaid directly in the sankey (distinct colour) instead of a banner
  const covBar=cov=>{const pct=Math.round((cov||0)*100),col=cov>=0.98?'#1f9e8b':cov>0?'#e0952a':'#c3c8d0';return `<div style="display:inline-block;width:46px;height:9px;background:#eceef2;border-radius:2px;vertical-align:middle;overflow:hidden"><div style="width:${pct}%;height:9px;background:${col}"></div></div> <b style="font-size:11px">${pct}%</b>`;};
  const statusChip=r=>{const s=r.pathRate<=0?'Disabled':(r.cov>=0.98?'Normal':'Under trucked');const M={'Normal':['#e3f3ef','#0e6b5c'],'Under trucked':['#fbeed9','#a5691a'],'Disabled':['#eef0f3','#6b7280']};const c=M[s];return `<span style="background:${c[0]};color:${c[1]};padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600">${s}</span>`;};
  // ---- Current LP (latest solve) — dig → dump Sankey ----
  const cl=((lpl.current)||[]).filter(r=>pf(r.pit));
  const cur=document.getElementById('currentLPTab');
  if(cur){
    const missE=((lpl.missingLegs)||[]).filter(r=>pf(r.pit)).map(r=>({dig:r.excav,dump:r.dump,mat:r.mat,pathRate:r.rate,loadRate:r.rate,notLP:true,tons:r.tons,notLPstatus:r.status}));
    const loadE=cl.map(r=>({dig:r.excav,digLoc:r.dig,dump:r.dump,mat:r.mat,pathRate:r.pathRate,loadRate:r.loadRate,poe:r.poe,priority:r.priority,bit:r.bit,fines:r.fines,d50:r.d50})).concat(missE);
    const bk=((lpl.backhaul)||[]).filter(r=>pf(r.pit));
    const btxt=(lpl.backTime&&lpl.backTime!==lpl.lpTime)?` · empty-return legs from last complete solve ${lpl.backTime}`:'';
    const misslg=missE.length?`<span class="badge"><b style="color:#8a4fd0">▨</b> not in optimizer — actual tonnes (avg t/h) for shovels the LP didn't cover</span>`:'';
    const leg=`<div class="badges" style="margin-top:6px"><span class="badge"><b style="color:${CORE}">■</b> ore</span><span class="badge"><b style="color:${CWASTE}">■</b> waste / other</span>${misslg}<span class="badge"><b style="color:#9aa6b5">■</b> empty return (path only)</span><span class="badge">▨ hatched = uncovered dig capacity</span><span class="badge"><b>left</b> shovel: ID + coverage % · load rate (t/h) + LP priority (P#; 255+ = Disabled) · <b>centre</b> dump: name + t/h + blended grade (Bit/Fines/D50) · <b>right</b> shovel: empty-return path t/h${btxt}</span></div>`;
    cur.innerHTML=drawLPSan3(loadE,bk)+((loadE.length||bk.length)?leg:'');
  }
  // ---- Shift LP (hourly averages) — one row per shovel, ordered by shovel ID ----
  const slp=((lpl.shiftLP)||[]).filter(r=>pf(r.pit)), hrs=lpl.hours||[];
  const stab=document.getElementById('shiftLPTab');
  if(!stab)return;
  if(!slp.length){stab.innerHTML='<div class="foot">No shift LP data for this view.</div>';return;}
  const covCol=c=>c>=0.98?'#0e6b5c':c>0?'#a5691a':'#9aa0ab';
  const hrRange=hl=>{const st=parseInt(hl,10);if(isNaN(st))return hl;const en=(st+1)%24,p=n=>(n<10?'0'+n:n);return p(st)+':00 - '+p(en)+':00';};
  let h=`<table class="lanetab" style="font-size:11px;white-space:nowrap"><tr><th>Shovel</th><th>Pit</th><th>Mat</th>`;
  hrs.forEach(hl=>h+=`<th style="text-align:center">${hrRange(hl)}</th>`);
  h+='</tr>';
  const shType=s=>s.startsWith('S8')?'HIT 8000':s.startsWith('S0')?'BE 495':(s.startsWith('S25')||s.startsWith('S3'))?'2500/3000':'Small Excav';
  const TORD=['BE 495','HIT 8000','2500/3000','Small Excav'], oreCol=m=>m==='Ore'?CORE:CWASTE, ncol=3+hrs.length;
  const byT={}; slp.forEach(r=>{(byT[shType(r.excav)]=byT[shType(r.excav)]||[]).push(r);});
  TORD.forEach(ty=>{const g=byT[ty]; if(!g||!g.length)return;
    h+=`<tr style="background:#e7ecf3;font-weight:700"><td colspan="${ncol}" style="text-align:left">${ty} <span style="font-weight:400;color:#8a97a8">(${g.length})</span></td></tr>`;
    g.forEach(r=>{h+=`<tr><td><b><span style="color:${oreCol(r.mat)}">■</span> ${r.excav}</b></td><td>${r.pit}</td><td style="color:${oreCol(r.mat)}">${r.mat||''}</td>`;
      r.hourly.forEach(c=>{ if(c){h+=`<td style="text-align:center;line-height:1.25" title="cov ${Math.round(c.cov*100)}% · LP path ${fmt(c.th)} · actual ${c.act!=null?fmt(c.act):'—'} t/h"><span style="color:${covCol(c.cov)}">${Math.round(c.cov*100)}%</span><br><b>${fmt(c.th)}</b><br><span style="color:#3a6ea5">${c.act!=null?fmt(c.act):'—'}</span></td>`;}
        else{h+=`<td style="text-align:center;color:#c3c8d0">·</td>`;} });
      h+='</tr>';});});
  stab.innerHTML=h+'</table>';
}
function drawTruckFlow(d,mode,simple){
  // 3-column Sankey. mode 'shovel' (default): PrevDump→Shovel→Dump, anchored on the SHOVEL (0 km).
  //                  mode 'dump': Shovel→Dump→NextShovel, anchored on the DUMP (0 km).
  //   simple=true: even fixed columns, no haul-distance encoding or ruler — but ALL other features
  //                (full+empty haul tonnage, % locked labels, locked-load hatching, ore/waste) retained.
  // Internally col A = left/"in" nodes, col B = centre anchor, col C = right/"out" nodes;
  // `flows` = centre→right (out) and `pflows` = left→centre (in), swapped by mode.
  const DUMPC=(mode==='dump');
  const flows=(DUMPC?(d.prevFlows||[]):d.fullFlows).filter(f=>f.tons>0).slice();
  const pflows=(DUMPC?d.fullFlows:(d.prevFlows||[])).filter(f=>f.tons>0).slice();
  if(!flows.length)return '<div class="foot">No flow data.</div>';
  const tpOf={};d.loadNodes.forEach(n=>tpOf[n.id]=n.tpnoh||0);
  const dtOf={};d.dumpNodes.forEach(n=>dtOf[n.id]=n.tons||0);
  // Build node sizes from fullFlows (shovel→dump, right side)
  let colTot=Math.max(flows.reduce((s,f)=>s+f.tons,0),1);
  const minTon=colTot*0.015;
  const filt=flows.filter(f=>f.tons>=minTon);
  const pfilt=pflows.filter(f=>f.tons>=minTon*0.4);
  const srcF={},dstF={};
  filt.forEach(f=>{srcF[f.from]=(srcF[f.from]||0)+f.tons;dstF[f.to]=(dstF[f.to]||0)+f.tons;});
  const srcP={},dstP={};
  pfilt.forEach(f=>{srcP[f.from]=(srcP[f.from]||0)+f.tons;dstP[f.to]=(dstP[f.to]||0)+f.tons;});
  const shovels=Object.keys(srcF).sort((a,b)=>srcF[b]-srcF[a]);
  const dumps=Object.keys(dstF).sort((a,b)=>dstF[b]-dstF[a]);
  const prevDumps=Object.keys(srcP).sort((a,b)=>srcP[b]-srcP[a]);
  if(!shovels.length)return '<div class="foot">No flow data.</div>';
  // Layout constants (3-column, wider canvas)
  const W=1200,pad=26,nodeW=13,gap=10;
  const bx=530;   // col B shovel node x (fixed centre)
  // Distance-scaled columns: each node's x-position ∝ haul distance, so ribbon LENGTH encodes distance.
  const kmC={},kmCw={};   // per-dump full-haul km (load-weighted)
  filt.forEach(f=>{kmC[f.to]=(kmC[f.to]||0)+(f.km||0)*(f.n||0);kmCw[f.to]=(kmCw[f.to]||0)+(f.n||0);});
  dumps.forEach(k=>{kmC[k]=kmCw[k]?kmC[k]/kmCw[k]:0;});
  const kmA={},kmAw={};   // per-prev-dump empty-haul km (load-weighted)
  pfilt.forEach(f=>{kmA[f.from]=(kmA[f.from]||0)+(f.km||0)*(f.n||0);kmAw[f.from]=(kmAw[f.from]||0)+(f.n||0);});
  prevDumps.forEach(k=>{kmA[k]=kmAw[k]?kmA[k]/kmAw[k]:0;});
  const maxKm=Math.max(0.1,...filt.map(f=>f.km||0),...pfilt.map(f=>f.km||0));   // shared max distance — per-path, so the farthest individual leg fits on the ruler
  const leftLbl=132, rightLbl=210;                 // label gutters left/right
  const leftAvail=bx-leftLbl, rightAvail=(W-rightLbl)-(bx+nodeW);
  const PXPK=Math.min(leftAvail,rightAvail)/maxKm; // ONE absolute km→pixel scale for both sides, anchored (0 km) at the shovel edge
  const CLEN=210;   // simple mode: fixed, even column spacing (distance NOT encoded)
  const cxOf=k=>simple?bx+nodeW+CLEN:bx+nodeW+(kmC[k]||0)*PXPK;          // full-haul distance to the right of the shovel
  const axOf=k=>simple?bx-CLEN-nodeW:bx-(kmA[k]||0)*PXPK-nodeW;          // empty-haul distance to the left of the shovel
  colTot=Math.max(shovels.reduce((s,k)=>s+srcF[k],0),dumps.reduce((s,k)=>s+dstF[k],0),1);
  const nShovGaps=Math.max(0,shovels.length-1);
  const H=Math.max(400,32*Math.max(shovels.length,dumps.length,prevDumps.length)+70);
  const sc=(H-2*pad-gap*nShovGaps)/colTot;
  // Build node position objects (x carries the distance encoding)
  const posB={};let yB=pad;
  shovels.forEach(k=>{const h=Math.max(4,srcF[k]*sc);posB[k]={x:bx,y:yB,h,off:0,poff:0};yB+=h+gap;});
  const posC={};let yC=pad;
  dumps.forEach(k=>{const h=Math.max(4,dstF[k]*sc);posC[k]={x:cxOf(k),y:yC,h,off:0};yC+=h+gap;});
  const posA={};let yA=pad;
  prevDumps.forEach(k=>{const h=Math.max(4,srcP[k]*sc);posA[k]={x:axOf(k),y:yA,h,off:0};yA+=h+gap;});
  const nodeBot=Math.max(yA,yB,yC);
  const H_svg=nodeBot+(simple?18:44);   // extra room for the km ruler + labels below the nodes
  // Locked-load stats. Centre node's locked% comes from its material-placement side:
  //   shovel-mode = loads leaving the shovel (flows.from); dump-mode = full loads arriving (pflows.to).
  const lockBy=(arr,key)=>{const T={},L={};arr.forEach(f=>{const k=f[key];T[k]=(T[k]||0)+(f.n||0);L[k]=(L[k]||0)+(f.nlock||0);});return k=>T[k]?Math.round(L[k]/T[k]*100):0;};
  const lpct=DUMPC?lockBy(pfilt,'to'):lockBy(filt,'from');   // centre-node locked %
  const dpct=lockBy(filt,'to');                              // right-node locked %
  // ---- Per-path docking: each ribbon ends at its OWN haul distance, and every node stays a SINGLE
  //      bar whose inner (ribbon-facing) edge steps in/out so its WIDTH spans that node's spread of
  //      path distances. Precompute each flow's band y (at its node) + inner-edge x (its own km).
  const R2=bx+nodeW;                                   // shovel right face = 0 km (full haul starts here)
  const byDump={},byShov={},byPrev={},byShovL={};
  dumps.forEach(k=>byDump[k]=[]); shovels.forEach(k=>{byShov[k]=[];byShovL[k]=[];}); prevDumps.forEach(k=>byPrev[k]=[]);
  filt.forEach(f=>{if(byDump[f.to])byDump[f.to].push(f); if(byShov[f.from])byShov[f.from].push(f);});
  pfilt.forEach(f=>{if(byPrev[f.from])byPrev[f.from].push(f); if(byShovL[f.to])byShovL[f.to].push(f);});
  dumps.forEach(k=>{byDump[k].sort((a,b)=>(a.km||0)-(b.km||0));let yy=posC[k].y;byDump[k].forEach(f=>{f._rh=f.tons*sc;f._ry=yy;f._rx=simple?R2+CLEN:R2+(f.km||0)*PXPK;yy+=f._rh;});});
  shovels.forEach(k=>{byShov[k].sort((a,b)=>(a.km||0)-(b.km||0));let yy=posB[k].y;byShov[k].forEach(f=>{f._sy=yy;yy+=f.tons*sc;});});
  prevDumps.forEach(k=>{byPrev[k].sort((a,b)=>(a.km||0)-(b.km||0));let yy=posA[k].y;byPrev[k].forEach(f=>{f._ah=f.tons*sc;f._ay=yy;f._ax=simple?bx-CLEN:bx-(f.km||0)*PXPK;yy+=f._ah;});});
  shovels.forEach(k=>{byShovL[k].sort((a,b)=>(a.km||0)-(b.km||0));let yy=posB[k].y;byShovL[k].forEach(f=>{f._ly=yy;yy+=f.tons*sc;});});
  let ribR='',ribL='';
  // Right ribbons: shovel (0 km) → dump, docking at this path's own full-haul distance
  filt.forEach(f=>{if(f._sy==null||f._ry==null)return;
    const th=f.tons*sc,y1=f._sy,y2=f._ry,x1=R2,x2=f._rx,xm=(x1+x2)/2,c=f.mat==='Waste'?CWASTE:CORE;
    const lf=f.n?(f.nlock||0)/f.n:0;
    ribR+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+th} C${xm} ${y2+th},${xm} ${y1+th},${x1} ${y1+th} Z" fill="${c}" fill-opacity="0.42"><title>${shortId(f.from)} → ${shortId(f.to)}: ${f.tons.toLocaleString()}t · ${DUMPC?((f.km||0)+' km empty'):((f.km||0)+'/'+(f.kmE||0)+' km (actual/expected)')} · ${Math.round(lf*100)}% locked</title></path>`;
    if(lf>0){const lth=th*lf;
      ribR+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+lth} C${xm} ${y2+lth},${xm} ${y1+lth},${x1} ${y1+lth} Z" fill="url(#lockhatch)" pointer-events="none"/>`;}
  });
  // Left ribbons: prev-dump → shovel (0 km), docking at this path's own empty-haul distance
  pfilt.forEach(f=>{if(f._ay==null||f._ly==null)return;
    const th=f.tons*sc,y1=f._ay,y2=f._ly,x1=f._ax,x2=bx,xm=(x1+x2)/2,c=f.mat==='Waste'?CWASTE:CORE;
    const lf=f.n?(f.nlock||0)/f.n:0;
    ribL+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+th} C${xm} ${y2+th},${xm} ${y1+th},${x1} ${y1+th} Z" fill="${c}" fill-opacity="0.30"><title>${shortId(f.from)} → ${shortId(f.to)}: ${f.tons.toLocaleString()}t${DUMPC?'':' (prev dump → shovel)'} · ${DUMPC?((f.km||0)+'/'+(f.kmE||0)+' km (actual/expected)'):((f.km||0)+' km empty')} · ${Math.round(lf*100)}% locked</title></path>`;
    if(lf>0){const lth=th*lf;
      ribL+=`<path d="M${x1} ${y1} C${xm} ${y1},${xm} ${y2},${x2} ${y2} L${x2} ${y2+lth} C${xm} ${y2+lth},${xm} ${y1+lth},${x1} ${y1+lth} Z" fill="url(#lockhatch)" pointer-events="none"/>`;}
  });
  // Column header labels
  let nd=`<text x="${bx-leftAvail/2}" y="${pad-12}" text-anchor="middle" font-size="9.5" font-weight="700" letter-spacing="0.8" fill="#8fa0b8">${simple?(DUMPC?'SHOVEL · FULL HAUL':'PREV DUMP'):(DUMPC?'◄ FULL HAUL · SHOVEL':'◄ EMPTY HAUL · PREV DUMP')}</text>`;
  nd+=`<text x="${bx+nodeW/2}" y="${pad-12}" text-anchor="middle" font-size="9.5" font-weight="700" letter-spacing="0.8" fill="var(--muted)">${DUMPC?'DUMP':'SHOVEL'}</text>`;
  nd+=`<text x="${bx+nodeW+rightAvail/2}" y="${pad-12}" text-anchor="middle" font-size="9.5" font-weight="700" letter-spacing="0.8" fill="var(--muted)">${simple?(DUMPC?'NEXT SHOVEL · EMPTY HAUL':'DUMP'):(DUMPC?'NEXT SHOVEL · EMPTY HAUL ►':'DUMP · FULL HAUL ►')}</text>`;
  // Col A: prev-dump nodes — SINGLE variable-width bar; inner (right) edge steps to each path's own empty-haul km
  prevDumps.forEach(k=>{const p=posA[k],bs=byPrev[k]||[];const cy=p.y+p.h/2;let xout;
    if(bs.length){xout=1e9;bs.forEach(f=>xout=Math.min(xout,f._ax));xout-=nodeW;
      let dd='M'+bs[0]._ax.toFixed(1)+' '+bs[0]._ay.toFixed(1);
      bs.forEach(f=>{dd+=' L'+f._ax.toFixed(1)+' '+f._ay.toFixed(1)+' L'+f._ax.toFixed(1)+' '+(f._ay+f._ah).toFixed(1);});
      const lb=bs[bs.length-1];
      dd+=' L'+xout.toFixed(1)+' '+(lb._ay+lb._ah).toFixed(1)+' L'+xout.toFixed(1)+' '+bs[0]._ay.toFixed(1)+' Z';
      nd+=`<path d="${dd}" fill="#8fa0b8" stroke="#fff" stroke-width="0.6"/>`;
    }else{xout=p.x;nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="#8fa0b8"/>`;}
    const lbl=shortId(k)+'  ·  '+Math.round(srcP[k]).toLocaleString()+' t';
    nd+=`<text x="${(xout-6).toFixed(1)}" y="${(cy+3.5).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--ink)">${lbl}</text>`;
  });
  // Col B: shovel nodes (middle) — label to the left (same as existing drawSan style)
  shovels.forEach(k=>{const p=posB[k];
    const lbl=DUMPC?(shortId(k)+'  ·  '+Math.round(dtOf[k]||srcF[k]).toLocaleString()+' t'):(shortId(k)+(tpOf[k]?'  ·  '+tpOf[k].toLocaleString()+' t/h':''));
    const lp=lpct(k);
    nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="var(--muted)"/>`;
    nd+=`<text x="${p.x-6}" y="${p.y+p.h/2}" text-anchor="end" font-size="10" fill="var(--ink)">${lbl}</text>`;
    nd+=`<text x="${p.x-6}" y="${p.y+p.h/2+11}" text-anchor="end" font-size="8.5" fill="${lp>=25?'#b3382b':'var(--muted)'}">${lp}% locked</text>`;
  });
  // Col C: dump nodes — SINGLE variable-width bar; inner (left) edge steps to each path's own full-haul km
  dumps.forEach(k=>{const p=posC[k],bs=byDump[k]||[];const cy=p.y+p.h/2,lp=dpct(k);let xout;
    if(bs.length){xout=-1e9;bs.forEach(f=>xout=Math.max(xout,f._rx));xout+=nodeW;
      let dd='M'+bs[0]._rx.toFixed(1)+' '+bs[0]._ry.toFixed(1);
      bs.forEach(f=>{dd+=' L'+f._rx.toFixed(1)+' '+f._ry.toFixed(1)+' L'+f._rx.toFixed(1)+' '+(f._ry+f._rh).toFixed(1);});
      const lb=bs[bs.length-1];
      dd+=' L'+xout.toFixed(1)+' '+(lb._ry+lb._rh).toFixed(1)+' L'+xout.toFixed(1)+' '+bs[0]._ry.toFixed(1)+' Z';
      nd+=`<path d="${dd}" fill="var(--muted)" stroke="#fff" stroke-width="0.6"/>`;
    }else{xout=p.x+nodeW;nd+=`<rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${p.h}" rx="2" fill="var(--muted)"/>`;}
    const lbl=shortId(k)+'  ·  '+Math.round(DUMPC?dstF[k]:(dtOf[k]||dstF[k])).toLocaleString()+' t';
    nd+=`<text x="${(xout+6).toFixed(1)}" y="${cy.toFixed(1)}" text-anchor="start" font-size="10" fill="var(--ink)">${lbl}</text>`;
    nd+=`<text x="${(xout+6).toFixed(1)}" y="${(cy+11).toFixed(1)}" text-anchor="start" font-size="8.5" fill="${lp>=25?'#b3382b':'var(--muted)'}">${lp}% locked</text>`;
  });
  // km reference ruler — the shovel is 0 km; ticks run out to the right (full haul) and left (empty haul) on the shared scale
  let ruler='';
  if(!simple){
  const kstep=maxKm<=3?0.5:(maxKm<=8?1:2), ry0=pad-4, ry1=nodeBot+4;
  for(let d=0;d<=maxKm+1e-6;d+=kstep){
    const xr=bx+nodeW+d*PXPK, xl=bx-d*PXPK, lab=(Math.abs(d-Math.round(d))<1e-6?d.toFixed(0):d.toFixed(1));
    ruler+=`<line x1="${xr.toFixed(1)}" y1="${ry0}" x2="${xr.toFixed(1)}" y2="${ry1}" stroke="#eaedf2" stroke-width="${d===0?1.2:0.6}"/>`
      +`<text x="${xr.toFixed(1)}" y="${ry1+11}" text-anchor="middle" font-size="8.5" fill="var(--muted)">${lab}</text>`;
    if(d>0)ruler+=`<line x1="${xl.toFixed(1)}" y1="${ry0}" x2="${xl.toFixed(1)}" y2="${ry1}" stroke="#eaedf2" stroke-width="0.6"/>`
      +`<text x="${xl.toFixed(1)}" y="${ry1+11}" text-anchor="middle" font-size="8.5" fill="var(--muted)">${lab}</text>`;}
  ruler+=`<text x="${bx+nodeW/2}" y="${ry1+22}" text-anchor="middle" font-size="9" font-weight="700" fill="var(--muted)">${DUMPC?'km from dump  (◄ full · empty ►)':'km from shovel  (◄ empty · full ►)'}</text>`;
  }
  const defs=`<defs><pattern id="lockhatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#233" stroke-width="1.5" stroke-opacity="0.6"/></pattern></defs>`;
  return `<svg viewBox="0 0 ${W} ${H_svg}" width="100%">${defs}${ruler}${ribL}${ribR}${nd}</svg>`;
}
// ---- road-network routing for the Cycle Map: A* over the truck-trace grid so flow ribbons follow real roads ----
let _cmNet=null, _cmRouteCache={};
function _cmBuildNet(){
  if(_cmNet)return _cmNet;
  const RC=(typeof DATA!=='undefined'&&DATA.roadCells)||[], CELL=(DATA&&DATA.roadCell)||20, MUL=1e7;
  const occ=new Map();
  for(const c of RC){const gx=Math.round(c[0]/CELL-0.5),gy=Math.round(c[1]/CELL-0.5);occ.set(gx*MUL+gy,c[2]);}
  _cmNet={occ,CELL,MUL}; return _cmNet;
}
function _cmSnap(net,nx,ny){const {occ,CELL,MUL}=net;const gx0=Math.round(nx/CELL-0.5),gy0=Math.round(ny/CELL-0.5);
  let best=null,bd=1e18;
  for(let r=0;r<=12;r++){for(let dx=-r;dx<=r;dx++)for(let dy=-r;dy<=r;dy++){if(Math.max(Math.abs(dx),Math.abs(dy))!==r)continue;
    const gx=gx0+dx,gy=gy0+dy;if(occ.has(gx*MUL+gy)){const dd=Math.hypot((gx+0.5)*CELL-nx,(gy+0.5)*CELL-ny);if(dd<bd){bd=dd;best=[gx,gy];}}}
    if(best&&r>=2)break;}
  return best;
}
function _cmAstar(net,s,t){const {occ,CELL,MUL}=net;const key=(x,y)=>x*MUL+y;
  const tx=(t[0]+0.5)*CELL,ty=(t[1]+0.5)*CELL,H=(x,y)=>Math.hypot((x+0.5)*CELL-tx,(y+0.5)*CELL-ty);
  const g=new Map(),came=new Map(),sk=key(s[0],s[1]),tk=key(t[0],t[1]);
  const heap=[],push=(f,x,y)=>{heap.push([f,x,y]);let i=heap.length-1;while(i>0){const p=(i-1)>>1;if(heap[p][0]<=heap[i][0])break;const q=heap[p];heap[p]=heap[i];heap[i]=q;i=p;}};
  const pop=()=>{const top=heap[0],last=heap.pop();if(heap.length){heap[0]=last;let i=0;for(;;){let l=2*i+1,r=2*i+2,m=i;if(l<heap.length&&heap[l][0]<heap[m][0])m=l;if(r<heap.length&&heap[r][0]<heap[m][0])m=r;if(m===i)break;const q=heap[m];heap[m]=heap[i];heap[i]=q;i=m;}}return top;};
  g.set(sk,0);push(H(s[0],s[1]),s[0],s[1]);let ex=0;
  while(heap.length){const it=pop(),x=it[1],y=it[2],ck=key(x,y);if(ck===tk)break;const gc=g.get(ck);if(it[0]-H(x,y)>gc+1e-6)continue;if(++ex>12000)return null;
    for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++){if(!dx&&!dy)continue;const nx=x+dx,ny=y+dy,nk=key(nx,ny);if(!occ.has(nk))continue;
      const ng=gc+Math.hypot(dx,dy)*CELL;if(ng<(g.has(nk)?g.get(nk):1e18)){g.set(nk,ng);came.set(nk,ck);push(ng+H(nx,ny),nx,ny);}}}
  if(sk!==tk&&!came.has(tk))return null;
  let ck=tk,path=[];for(;;){const x=Math.round(ck/MUL),y=ck-x*MUL;path.push([(x+0.5)*CELL,(y+0.5)*CELL]);if(ck===sk)break;if(!came.has(ck))break;ck=came.get(ck);}
  path.reverse(); return path;
}
function _cmDP(pts,eps){   // Douglas–Peucker simplify
  if(pts.length<3)return pts;
  const a=pts[0],b=pts[pts.length-1],dl=Math.hypot(b[0]-a[0],b[1]-a[1])||1;let dmax=0,idx=0;
  for(let i=1;i<pts.length-1;i++){const dd=Math.abs((b[0]-a[0])*(a[1]-pts[i][1])-(a[0]-pts[i][0])*(b[1]-a[1]))/dl;if(dd>dmax){dmax=dd;idx=i;}}
  if(dmax>eps){const l=_cmDP(pts.slice(0,idx+1),eps),r=_cmDP(pts.slice(idx),eps);return l.slice(0,-1).concat(r);}
  return [a,b];
}
function _cmRoutePath(aU,bU,ckey){   // returns simplified UTM polyline along roads, or null
  if(ckey in _cmRouteCache)return _cmRouteCache[ckey];
  const net=_cmBuildNet(); let out=null;
  if(net.occ.size){const s=_cmSnap(net,aU[0],aU[1]),t=_cmSnap(net,bU[0],bU[1]);
    if(s&&t){const cp=_cmAstar(net,s,t);
      if(cp&&cp.length>=2){const full=[aU].concat(cp,[bU]); out=_cmDP(full,net.CELL*1.2);}}}
  _cmRouteCache[ckey]=out; return out;
}
function _cmSmooth(p){   // quadratic-midpoint smoothing of a screen-space polyline
  if(p.length<3)return `M${p[0][0].toFixed(1)} ${p[0][1].toFixed(1)} L${p[p.length-1][0].toFixed(1)} ${p[p.length-1][1].toFixed(1)}`;
  let d=`M${p[0][0].toFixed(1)} ${p[0][1].toFixed(1)}`;
  for(let i=1;i<p.length-1;i++){const mx=(p[i][0]+p[i+1][0])/2,my=(p[i][1]+p[i+1][1])/2;d+=` Q${p[i][0].toFixed(1)} ${p[i][1].toFixed(1)} ${mx.toFixed(1)} ${my.toFixed(1)}`;}
  return d+` L${p[p.length-1][0].toFixed(1)} ${p[p.length-1][1].toFixed(1)}`;
}
function drawCycleMap(d,opt){
  // Spatial "mine map": locations placed by an x/y layout derived from the haul distances (MDS /
  // stress majorization), so circle-to-circle spacing ≈ real km. Shovels + dump locations = circles
  // (radius ∝ tonnes); loaded hauls (shovel→dump) are solid material-coloured arcs, empty returns
  // (dump→shovel) faint dashed arcs — together they show how trucks cycle. No true north (relative only).
  const ff=(d.fullFlows||[]).filter(f=>f.tons>0), pf=(d.prevFlows||[]).filter(f=>f.tons>0);
  if(!ff.length)return '<div class="foot">No flow data.</div>';
  const shov=new Set(),dump=new Set();
  ff.forEach(f=>{shov.add(f.from);dump.add(f.to);}); pf.forEach(f=>{dump.add(f.from);shov.add(f.to);});
  const nodes=[...shov].map(id=>({id,t:'S'})).concat([...dump].map(id=>({id,t:'D'})));
  const idx={}; nodes.forEach((n,i)=>idx[n.id]=i); const N=nodes.length;
  const th={}; ff.forEach(f=>{th[f.from]=(th[f.from]||0)+f.tons; th[f.to]=(th[f.to]||0)+f.tons;});
  const dm={}; ff.forEach(f=>{(dm[f.to]=dm[f.to]||{})[f.mat]=((dm[f.to]||{})[f.mat]||0)+f.tons;});
  const dmat={}; Object.keys(dm).forEach(k=>{dmat[k]=Object.keys(dm[k]).sort((a,b)=>dm[k][b]-dm[k][a])[0];});
  // target pairwise distance = mean of the available full/empty km for each shovel–dump pair
  const pair={};
  const addp=(a,b,km)=>{if(!(km>0))return;const key=a<b?a+'|'+b:b+'|'+a;const p=pair[key]||(pair[key]={s:0,w:0,a,b});p.s+=km;p.w++;};
  ff.forEach(f=>addp(f.from,f.to,f.km)); pf.forEach(f=>addp(f.from,f.to,f.km));
  const dedges=Object.values(pair).map(p=>({i:idx[p.a],j:idx[p.b],D:p.s/p.w})).filter(e=>e.i!=null&&e.j!=null);
  // Real exported x/y take priority; nodes with coordinates are pinned (screen-y flipped so north = up).
  const CO=(typeof DATA!=='undefined'&&DATA.locCoords)||{};
  const pinned=nodes.map(n=>CO[n.id]?[+CO[n.id][0],-(+CO[n.id][1])]:null);
  const nPin=pinned.filter(p=>p).length;
  const useCoords=nPin>0 && nPin>=Math.ceil(N*0.5);
  // coordinate units per km (so km-based targets mix correctly with metre-grid coords). Estimate from
  // pinned pairs that also have a haul distance; 1 when the plane is already in km (fallback layout).
  let U=1;
  if(useCoords){const rr=[];dedges.forEach(e=>{if(pinned[e.i]&&pinned[e.j]&&e.D>0){rr.push(Math.hypot(pinned[e.i][0]-pinned[e.j][0],pinned[e.i][1]-pinned[e.j][1])/e.D);}});
    if(rr.length){rr.sort((a,b)=>a-b);U=rr[Math.floor(rr.length/2)]||1;}}
  const Dfac=useCoords?U:1;
  // layout: pinned nodes fixed; the rest (and, if too few pins, everyone) via stress majorization
  let X=nodes.map((n,k)=>(useCoords&&pinned[k])?pinned[k].slice():[Math.cos(2*Math.PI*k/N)*3*Dfac,Math.sin(2*Math.PI*k/N)*3*Dfac]);
  const nbr=nodes.map(()=>[]); dedges.forEach(e=>{nbr[e.i].push([e.j,e.D*Dfac]);nbr[e.j].push([e.i,e.D*Dfac]);});
  const iters=useCoords?120:400;
  for(let it=0;it<iters;it++){for(let i=0;i<N;i++){if(useCoords&&pinned[i])continue; const nb=nbr[i]; if(!nb.length)continue;
    let nx=0,ny=0,den=0;
    for(const kv of nb){const j=kv[0],D=kv[1],w=1/(D*D);const dx=X[i][0]-X[j][0],dy=X[i][1]-X[j][1];const dist=Math.hypot(dx,dy)||1e-6;
      nx+=w*(X[j][0]+D*dx/dist); ny+=w*(X[j][1]+D*dy/dist); den+=w;}
    X[i]=[nx/den,ny/den];}}
  const uPerKm=useCoords?U:1;   // plane units per km, for the scale bar
  const W=900,Hh=560,padL=30,padR=30,padT=30,padB=48;
  const O0=opt||{}, showRoad=O0.road!==false, showBase=O0.base!==false, snapRoads=O0.snap!==false;
  const RC=(useCoords&&(typeof DATA!=='undefined')&&DATA.roadCells)||[];         // truck-trace road cells
  const BM=(useCoords&&(typeof DATA!=='undefined')&&DATA.baseMap)||null;         // georeferenced GIS basemap
  const xs=X.map(p=>p[0]),ys=X.map(p=>p[1]);   // fit to the flow nodes; road cells beyond the view clip
  let minx,maxx,miny,maxy;
  if(showBase&&BM){minx=BM.ext[0];maxx=BM.ext[2];miny=-BM.ext[3];maxy=-BM.ext[1];}  // window = image extent (P-space, y flipped)
  else{minx=Math.min(...xs);maxx=Math.max(...xs);miny=Math.min(...ys);maxy=Math.max(...ys);}
  const spanx=(maxx-minx)||1,spany=(maxy-miny)||1;
  const sc=Math.min((W-padL-padR)/spanx,(Hh-padT-padB)/spany);   // equal px/km on both axes → true distances
  const ox=padL+((W-padL-padR)-spanx*sc)/2, oy=padT+((Hh-padT-padB)-spany*sc)/2;
  const PX=k=>ox+(X[k][0]-minx)*sc, PY=k=>oy+(X[k][1]-miny)*sc;
  const maxT=Math.max(1,...nodes.map(n=>th[n.id]||0));
  const rOf=id=>4+16*Math.sqrt((th[id]||0)/maxT);
  const maxTon=Math.max(1,...ff.map(f=>f.tons),...pf.map(f=>f.tons));
  const arrow=(bx,by,ang,c)=>{const s=5,a1=ang+2.6,a2=ang-2.6;return `<path d="M${bx.toFixed(1)} ${by.toFixed(1)} L${(bx+s*Math.cos(a1)).toFixed(1)} ${(by+s*Math.sin(a1)).toFixed(1)} L${(bx+s*Math.cos(a2)).toFixed(1)} ${(by+s*Math.sin(a2)).toFixed(1)} Z" fill="${c}"/>`;};
  const curve=(i,j,tons,c,op,dash,wMul,sign)=>{
    const ax=PX(i),ay=PY(i),bx0=PX(j),by0=PY(j);
    const dx=bx0-ax,dy=by0-ay,len=Math.hypot(dx,dy)||1,ux=dx/len,uy=dy/len;
    const rb=rOf(nodes[j].id),ra=rOf(nodes[i].id);
    const w=Math.max(0.6,Math.sqrt(tons/maxTon)*wMul);
    // road-snapped routing: draw the ribbon along the actual road path when one exists
    if(snapRoads&&RC.length){
      const aU=[X[i][0],-X[i][1]], bU=[X[j][0],-X[j][1]];
      const rp=_cmRoutePath(aU,bU,nodes[i].id+'|'+nodes[j].id);
      if(rp&&rp.length>=2){
        let sp=rp.map(p=>[ox+(p[0]-minx)*sc, oy+((-p[1])-miny)*sc]);
        // trim the ends that fall inside the node circles
        let a0=0; while(a0<sp.length-1&&Math.hypot(sp[a0][0]-sp[0][0],sp[a0][1]-sp[0][1])<ra) a0++;
        let b0=sp.length-1; const last=sp[sp.length-1]; while(b0>0&&Math.hypot(sp[b0][0]-last[0],sp[b0][1]-last[1])<rb) b0--;
        if(b0>a0){sp=sp.slice(a0,b0+1);
          const n=sp.length-1, ang=Math.atan2(sp[n][1]-sp[n-1][1], sp[n][0]-sp[n-1][0]);
          return `<path d="${_cmSmooth(sp)}" fill="none" stroke="${c}" stroke-width="${w.toFixed(1)}" stroke-opacity="${op}" stroke-linejoin="round" stroke-linecap="round"${dash?` stroke-dasharray="${dash}"`:''}/>`+arrow(sp[n][0],sp[n][1],ang,c);
        }
      }
    }
    // fallback: smooth arc (no road path available)
    const ax2=ax+ux*ra,ay2=ay+uy*ra,bx=bx0-ux*rb,by=by0-uy*rb;
    const mx=(ax2+bx)/2,my=(ay2+by)/2,off=Math.min(55,len*0.16)*sign,cx=mx-uy*off,cy=my+ux*off;
    const ang=Math.atan2(by-cy,bx-cx);
    return `<path d="M${ax2.toFixed(1)} ${ay2.toFixed(1)} Q${cx.toFixed(1)} ${cy.toFixed(1)} ${bx.toFixed(1)} ${by.toFixed(1)}" fill="none" stroke="${c}" stroke-width="${w.toFixed(1)}" stroke-opacity="${op}"${dash?` stroke-dasharray="${dash}"`:''}/>`+arrow(bx,by,ang,c);
  };
  const O=opt||{}, showEmpty=O.empty!==false, showLoaded=O.loaded!==false, hiEmpty=!!O.hi;
  // longest empty-return legs (by distance) to emphasise — the biggest hidden cost in the cycle
  const hiSet=new Set();
  if(hiEmpty)pf.slice().sort((a,b)=>(b.km||0)-(a.km||0)).slice(0,Math.max(3,Math.round(pf.length*0.15))).forEach(f=>hiSet.add(f));
  let eFull='',eEmpty='';
  if(showEmpty)pf.forEach(f=>{if(idx[f.from]==null||idx[f.to]==null)return;
    const hot=hiSet.has(f),col=hot?'#d1495b':'#9aa6b5',op=hiEmpty?(hot?0.9:0.14):0.5,wm=hot?10:6;
    eEmpty+=`<g><title>${shortId(f.from)} → ${shortId(f.to)} (empty return): ${f.tons.toLocaleString()}t · ${f.km||0} km${hot?' · long empty haul':''}</title>${curve(idx[f.from],idx[f.to],f.tons,col,op,'3 3',wm,-1)}</g>`;});
  if(showLoaded)ff.forEach(f=>{if(idx[f.from]==null||idx[f.to]==null)return;const c=f.mat==='Waste'?CWASTE:CORE;
    eFull+=`<g><title>${shortId(f.from)} → ${shortId(f.to)} (loaded): ${f.tons.toLocaleString()}t · ${f.km||0} km</title>${curve(idx[f.from],idx[f.to],f.tons,c,0.62,'',9,1)}</g>`;});
  let nod='';
  nodes.forEach((n,i)=>{const x=PX(i),y=PY(i),r=rOf(n.id);
    const fill=n.t==='S'?'#2f6f9f':(dmat[n.id]==='Waste'?CWASTE:CORE),stroke=n.t==='S'?'#173f5c':'#5a6472';
    nod+=`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}" fill="${fill}" fill-opacity="${n.t==='S'?0.92:0.5}" stroke="${stroke}" stroke-width="1.2"><title>${shortId(n.id)} · ${(th[n.id]||0).toLocaleString()}t</title></circle>`;
    nod+=`<text x="${(x+r+3).toFixed(1)}" y="${(y+3).toFixed(1)}" font-size="9" font-weight="${n.t==='S'?700:500}" fill="#2b2f36" stroke="#fff" stroke-width="2.4" paint-order="stroke" pointer-events="none">${shortId(n.id)}</text>`;
  });
  const spanKm=spanx/uPerKm,kmBar=spanKm>=6?2:1,bx1=padL,by1=Hh-16,bx2=padL+kmBar*uPerKm*sc;
  const bar=`<line x1="${bx1}" y1="${by1}" x2="${bx2.toFixed(1)}" y2="${by1}" stroke="#2b2f36" stroke-width="2"/><text x="${((bx1+bx2)/2).toFixed(1)}" y="${by1-4}" text-anchor="middle" font-size="9" fill="var(--muted)">${kmBar} km (to scale)</text>`;
  // road-network underlay: faint density squares from the truck-trace grid (drawn first, beneath everything)
  let road='';
  if(showRoad&&RC.length){const rr=Math.max(1.1,(DATA.roadCell||30)*sc*0.62);
    for(const c of RC){const px=ox+(c[0]-minx)*sc, py=oy+((-c[1])-miny)*sc;
      if(px<-30||px>W+30||py<-30||py>Hh+30)continue;
      road+=`<rect x="${(px-rr).toFixed(1)}" y="${(py-rr).toFixed(1)}" width="${(2*rr).toFixed(1)}" height="${(2*rr).toFixed(1)}" rx="${(rr*0.5).toFixed(1)}" fill="#8a8f98" fill-opacity="${(0.07+0.33*c[2]).toFixed(3)}"/>`;}}
  // georeferenced GIS basemap, placed by its world extent (bottom layer)
  let base='';
  if(showBase&&BM){const iw=(maxx-minx)*sc, ih=(maxy-miny)*sc;
    base=`<image href="${BM.img}" xlink:href="${BM.img}" x="${ox.toFixed(1)}" y="${oy.toFixed(1)}" width="${iw.toFixed(1)}" height="${ih.toFixed(1)}" preserveAspectRatio="none" opacity="0.95"/>`;}
  return `<svg viewBox="0 0 ${W} ${Hh}" width="100%" xmlns:xlink="http://www.w3.org/1999/xlink">${base}${road}${eEmpty}${eFull}${nod}${bar}</svg>`;
}
function renderTruckFlow(){
  const d=V().haulCycles;
  document.getElementById('tfsub').textContent='('+view+')';
  document.getElementById('tf').innerHTML=drawTruckFlow(d);
  document.getElementById('tfleg').innerHTML=
    `<span class="badge"><b style="color:${CORE}">■</b> ore</span>`+
    `<span class="badge"><b style="color:${CWASTE}">■</b> waste</span>`+
    `<span class="badge">▨ hatched = locked (un-optimized) loads · % under shovel/dump nodes</span>`+
    `<span class="badge">left: prev dump + tonnes arriving · centre: shovel + TPNOH (t/h) · right: dump + total tonnes · ribbon ∝ tonnage · km = actual/expected haul dist</span>`;
}
const CHARTS={};
let hourSel=null;   // selected hour in the Shift Overview hourly chart (index into hourlyPerf)
function renderHourDetail(i){
  const hp=V().hourlyPerf, el=document.getElementById('hourDetail'); if(!el)return;
  hourSel=(i==null?null:i);
  if(!hp||i==null){el.innerHTML='<div class="hdhint">Click an hour (bar or axis) to see its hourly performance.</div>';return;}
  const fmtV=(v,u)=>v==null?'—':(u==='mmss'?fmtTime(v):(u==='#'?v:fmt(v)));
  const st=(hp.hours&&hp.hours[i]!=null)?hp.hours[i]:('Hour '+(i+1));
  let en=(hp.hours&&hp.hours[i+1])||''; if(!en){const m=/^(\d+):/.exec(st); if(m)en=String((parseInt(m[1],10)+1)%24).toString().padStart(2,'0')+':00';}
  const fmtD=(d,u)=>{const s=d>0?'+':(d<0?'−':'');const a=Math.abs(d);return s+(u==='mmss'?fmtTime(a):(u==='#'?a:fmt(a)));};
  // fixed KPI order + short display names for the detail panel (match by prefix to be robust to the em-dash in "Payload — CAT 797")
  const HDORDER=[['Load Count','Loads'],['Payload','Payload'],['Dumped','Tonnes'],['Ore Moved','Ore Tonnes'],['Cycle Time - Ore','Cycle Ore'],['Waste Moved','Waste Tonnes'],['Cycle Time - Waste','Cycle Waste'],['Shovel Hang','Hang'],['Spot at Shovel','Spot'],['Load Time','Load'],['Wait at Dump','Dump Idle'],['Dumping Time','Dump Time']];
  const findRow=lbl=>hp.rows.find(r=>r.label===lbl)||hp.rows.find(r=>r.label.indexOf(lbl)===0);
  let s=`<div class="hdhd">${en?st+' – '+en:st}</div><div class="hdrow hdhead"><span>KPI</span><span>Act</span><span>Δ</span></div>`;
  HDORDER.forEach(([lbl,disp])=>{const r=findRow(lbl); if(!r)return; const v=r.vals?r.vals[i]:null, has=(v!=null&&r.good&&r.budget);
    let bg='transparent',chip='',dcol='var(--muted)';   // green ▲ = better than target · red ▼ = worse
    if(has){const d=v-r.budget,dev=d/r.budget,better=r.good==='high'?dev>=0:dev<=0,op=Math.min(0.5,Math.abs(dev)*1.3).toFixed(2);
      bg=better?`rgba(106,168,79,${op})`:`rgba(204,75,75,${op})`; dcol=better?'#2f7a44':'#b3382b'; chip=`${better?'▲':'▼'} ${fmtD(d,r.uom)}`;}
    s+=`<div class="hdrow" title="${disp}"><span>${disp}</span><span class="hdv" style="background:${bg}">${fmtV(v,r.uom)}</span><span class="hdd" style="color:${dcol}">${chip}</span></div>`;});
  el.innerHTML=s;
  if(CHARTS.chHour)CHARTS.chHour.update('none');   // recolour bars (selected = opaque)
}
let hourDetOn=false;   // hourly detail panel hidden by default; the "Details" toggle shows it
function applyHourDet(){
  const p=document.getElementById('hourDetail'), b=document.getElementById('hourDetBtn');
  if(p)p.classList.toggle('hidden',!hourDetOn);
  if(b)b.classList.toggle('on',hourDetOn);
  if(CHARTS.chHour)CHARTS.chHour.resize();
}
function toggleHourDet(){hourDetOn=!hourDetOn;applyHourDet();}
let cumMode='tot';   // Shift Progress cumulative card: 'tot' = all production · 'sand' = sand-haul only
function setCumMode(m){cumMode=m;
  const t=document.getElementById('cumModeTot'),s=document.getElementById('cumModeSand');
  if(t)t.classList.toggle('on',m==='tot'); if(s)s.classList.toggle('on',m==='sand');
  renderOverview();}
function mk(id,cfg){if(CHARTS[id]){CHARTS[id].destroy();}const el=document.getElementById(id);if(el)CHARTS[id]=new Chart(el,cfg);}
const TLCOL={Ready:'#4caf50',Delay:'#ffc107',Down:'#e23b32',Standby:'#3f7fe0',Parked:'#9c6ade',Other:'#b0bec5'};
function drawTimeline(tl,opt){
  opt=opt||{};
  const eq=tl.equip;if(!eq.length)return '<div class="foot">No status events.</div>';
  const rowH=opt.rowH||24, lf=opt.labelFont||10, showSub=(opt.compact!==true);   // compact = thin rows (truck timeline)
  const W=900,top=22,left=150,plotW=W-left-16,H=top+eq.length*rowH+16,TOT=720;
  const X=m=>left+m/TOT*plotW;let g='';
  const Q=tl.queue||{},qmax=tl.qmax||1,MM=tl.mat||{};
  const tlbase=(tl.base!=null?tl.base:6);
  const plotBot=top+eq.length*rowH;
  for(let hh=0;hh<=12;hh++){const x=X(hh*60);   // solid vertical gridline + time label every hour (matches Hourly tonnes chart)
    g+=`<line x1="${x}" y1="${top}" x2="${x}" y2="${plotBot}" stroke="var(--line)" stroke-width="0.7"/>`;
    g+=`<text x="${x}" y="${top-5}" text-anchor="middle" font-size="9" fill="var(--muted)">${String((tlbase+hh)%24).padStart(2,'0')}:00</text>`;}
  for(let i=0;i<=eq.length;i++){const y=top+i*rowH;g+=`<line x1="${left}" y1="${y}" x2="${left+plotW}" y2="${y}" stroke="#eef0f4" stroke-width="0.5"/>`;}   // horizontal row separators
  const barPad=rowH>=16?2:1, barH=Math.max(2,rowH-barPad*2);
  eq.forEach((k,i)=>{const y=top+i*rowH;const mc=MM[k]==='Waste'?'#d08a1f':(MM[k]==='Ore'?'#1f9e8b':'var(--ink)');
    g+=`<text x="${left-6}" y="${y+rowH/2+lf*0.34}" text-anchor="end" font-size="${lf}" font-weight="600" fill="${mc}">${k}</text>`;
    if(showSub){const aq=tl.avgq?tl.avgq[k]:null, ah=tl.avgh?tl.avgh[k]:null, parts=[];
      if(aq!=null)parts.push('queue '+aq); if(ah!=null)parts.push('hang '+ah);
      if(parts.length) g+=`<text x="${left-6}" y="${y+rowH/2+10}" text-anchor="end" font-size="8" fill="var(--muted)">${parts.join(' · ')} min/load</text>`;}
    tl.seg[k].forEach(s=>{const x=X(s[0]),w=Math.max(0.4,s[1]/TOT*plotW);g+=`<rect x="${x}" y="${y+barPad}" width="${w}" height="${barH}" fill="${TLCOL[s[2]]||'#ccc'}"><title>${k} · ${s[3]||s[2]} · ${s[1]} min</title></rect>`;});
    const qs=Q[k];
    if(showSub&&qs&&qs.length){const yb=y+rowH-3,ht=rowH-6,yq=v=>yb-v/qmax*ht;
      let d='';qs.forEach((p,j)=>{const x=X(p[0]);d+=(j===0?`M${x} ${yq(p[1])}`:` L${x} ${yq(qs[j-1][1])} L${x} ${yq(p[1])}`);});
      d+=` L${X(TOT)} ${yq(qs[qs.length-1][1])}`;
      g+=`<path d="${d}" fill="none" stroke="#fff" stroke-width="2.6" stroke-opacity="0.55"/><path d="${d}" fill="none" stroke="#111" stroke-width="1.3"/>`;}});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px">`+
    Object.entries(TLCOL).map(([k,c])=>`<span class="badge"><b style="color:${c}">■</b> ${k}</span>`).join('')+
    `<span class="badge">— black line: trucks at shovel (0–${qmax})</span><span class="badge">label colour = ore/waste</span></div>`;
}
function drawDumpTimeline(tl){
  const eq=tl.equip;if(!eq||!eq.length)return '<div class="foot">No trucks-at-dump data for this view.</div>';
  const W=900,rowH=140,top=30,left=46,plotW=W-left-18,gap=36,TOT=720;
  const qmax=Math.max(1,tl.qmax||1),stepC=qmax<=8?1:Math.ceil(qmax/8);
  const H=top+eq.length*(rowH+gap)-gap+18;
  const X=m=>left+m/TOT*plotW,base=tl.base!=null?tl.base:6;let g='';
  for(let hh=0;hh<=12;hh+=2){const x=X(hh*60);g+=`<text x="${x}" y="${top-12}" text-anchor="middle" font-size="9.5" fill="var(--muted)">${(base+hh)%24}:00</text>`;}
  eq.forEach((k,i)=>{const y=top+i*(rowH+gap),yb=y+rowH,yq=v=>yb-v/qmax*rowH;
    // horizontal guide lines at each truck count + labels
    for(let c=0;c<=qmax;c+=stepC){const gy=yq(c);
      g+=`<line x1="${left}" y1="${gy}" x2="${left+plotW}" y2="${gy}" stroke="${c===0?'#aab2c0':'#e6e9f0'}" stroke-width="${c===0?1:0.7}"${c===0?'':' stroke-dasharray="3 3"'}/>`;
      g+=`<text x="${left-5}" y="${gy+3}" text-anchor="end" font-size="9" fill="var(--muted)">${c}</text>`;}
    // vertical hour lines within the band
    for(let hh=0;hh<=12;hh+=2){const x=X(hh*60);g+=`<line x1="${x}" y1="${y}" x2="${x}" y2="${yb}" stroke="var(--line)" stroke-width="0.5"/>`;}
    // labels
    g+=`<text x="${left}" y="${y-7}" font-size="12" font-weight="700" fill="var(--ink)">${shortId(k)}</text>`;
    const aq=tl.avgq?tl.avgq[k]:null;
    if(aq!=null) g+=`<text x="${left+plotW}" y="${y-7}" text-anchor="end" font-size="9.5" fill="var(--muted)">avg queue ${aq} min/load</text>`;
    g+=`<text x="13" y="${y+rowH/2}" transform="rotate(-90 13 ${y+rowH/2})" text-anchor="middle" font-size="9" fill="var(--muted)">trucks</text>`;
    const qs=tl.seg[k];
    if(qs&&qs.length){
      let d='';qs.forEach((p,j)=>{const x=X(p[0]);d+=(j===0?`M${x} ${yq(p[1])}`:` L${x} ${yq(qs[j-1][1])} L${x} ${yq(p[1])}`);});
      d+=` L${X(TOT)} ${yq(qs[qs.length-1][1])}`;
      g+=`<path d="${d}" fill="none" stroke="#fff" stroke-width="3" stroke-opacity="0.6"/><path d="${d}" fill="none" stroke="#3f51b5" stroke-width="1.9"/>`;}
    // crusher status strip at the base of the band (colour = ASEStatus)
    const cs=tl.status?tl.status[k]:null,sy=yb+4,shH=9;
    g+=`<text x="${left-5}" y="${sy+shH/2+2.5}" text-anchor="end" font-size="7.5" fill="var(--muted)">status</text>`;
    if(cs&&cs.length) cs.forEach(s=>{const x=X(s[0]),w=Math.max(0.5,s[1]/TOT*plotW);
      g+=`<rect x="${x}" y="${sy}" width="${w}" height="${shH}" fill="${TLCOL[s[2]]||'#ccc'}"><title>${k} · ${s[3]||s[2]} · ${s[1]} min</title></rect>`;});
    else g+=`<rect x="${left}" y="${sy}" width="${plotW}" height="${shH}" fill="#eef0f4"/><text x="${left+plotW/2}" y="${sy+shH/2+3}" text-anchor="middle" font-size="7.5" fill="var(--muted)">no crusher status</text>`;});
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px"><span class="badge"><b style="color:#3f51b5">—</b> trucks at dump</span><span class="badge">thin guides = truck count (0–${qmax})</span>`+
    `<span class="badge">base strip = crusher status (where available):</span>`+
    Object.entries(TLCOL).map(([k,c])=>`<span class="badge"><b style="color:${c}">■</b> ${k}</span>`).join('')+`</div>`;
}
function drawShovelBand(tl,k){
  // Single-shovel status timeline as a tall band (trucks-at-shovel line + status strip at base), like the trucks-at-dump graph.
  if(!tl||!tl.seg||!(k in tl.seg)) return '<div class="foot">No status-timeline data for '+k+' this shift/view.</div>';
  const W=940,rowH=122,top=30,left=46,plotW=W-left-58,TOT=720;
  const qmax=Math.max(1,tl.qmax||1),stepC=qmax<=8?1:Math.ceil(qmax/8);
  const X=m=>left+m/TOT*plotW,base=tl.base!=null?tl.base:6,yb=top+rowH,yq=v=>yb-v/qmax*rowH;
  const bandTop=yb+4+11+18, bandH=112, bandMid=bandTop+bandH/2, bandBot=bandTop+bandH, H=bandBot+12;   // status strip, then a per-load hang/queue band (y-axis ±14 min)
  let g='';
  for(let hh=0;hh<=12;hh++){const x=X(hh*60);g+=`<text x="${x}" y="${top-12}" text-anchor="middle" font-size="9.5" fill="var(--muted)">${(base+hh)%24}:00</text>`;}
  for(let c=0;c<=qmax;c+=stepC){const gy=yq(c);
    g+=`<line x1="${left}" y1="${gy}" x2="${left+plotW}" y2="${gy}" stroke="${c===0?'#aab2c0':'#e6e9f0'}" stroke-width="${c===0?1:0.7}"${c===0?'':' stroke-dasharray="3 3"'}/>`;
    g+=`<text x="${left-5}" y="${gy+3}" text-anchor="end" font-size="9" fill="var(--muted)">${c}</text>`;}
  for(let hh=0;hh<=12;hh++){const x=X(hh*60);g+=`<line x1="${x}" y1="${top}" x2="${x}" y2="${yb}" stroke="#e8ebf0" stroke-width="0.9"/>`;}
  const aq=tl.avgq?tl.avgq[k]:null, ah=tl.avgh?tl.avgh[k]:null, parts=[];
  if(ah!=null)parts.push('hang '+ah); if(aq!=null)parts.push('queue '+aq);   // shown in the band's top-right corner below
  g+=`<text x="13" y="${top+rowH/2}" transform="rotate(-90 13 ${top+rowH/2})" text-anchor="middle" font-size="9" fill="var(--muted)">trucks at shovel</text>`;
  const qs=tl.queue?tl.queue[k]:null;
  if(qs&&qs.length){let d='';qs.forEach((p,j)=>{const x=X(p[0]);d+=(j===0?`M${x} ${yq(p[1])}`:` L${x} ${yq(qs[j-1][1])} L${x} ${yq(p[1])}`);});
    d+=` L${X(TOT)} ${yq(qs[qs.length-1][1])}`;
    g+=`<path d="${d}" fill="none" stroke="#fff" stroke-width="3" stroke-opacity="0.6"/><path d="${d}" fill="none" stroke="#111" stroke-width="1.7"/>`;}
  // Total-tonnes trend overlay (right axis) — tonnes loaded per hour bucket
  const tp=tl.tonhr?tl.tonhr[k]:null;
  if(tp&&tp.some(v=>v!=null)){
    const tpmax=Math.max(...tp.filter(v=>v!=null)), niceMax=Math.max(100,Math.ceil(tpmax/100)*100);
    const yT=v=>yb-v/niceMax*rowH, Xc=i=>X((i+0.5)*60), TPC='#6a3fd0';
    for(let s=0;s<=4;s++){const tv=niceMax*s/4,gy=yT(tv);g+=`<text x="${left+plotW+5}" y="${gy+3}" font-size="8.5" fill="${TPC}">${Math.round(tv)}</text>`;}
    g+=`<text x="${left+plotW+34}" y="${top+rowH/2}" transform="rotate(-90 ${left+plotW+34} ${top+rowH/2})" text-anchor="middle" font-size="9" fill="${TPC}">tonnes</text>`;
    let run=[]; const runs=[];
    tp.forEach((v,i)=>{if(v==null){if(run.length)runs.push(run);run=[];}else run.push([Xc(i),yT(v),v,i]);});
    if(run.length)runs.push(run);
    runs.forEach(r=>{if(r.length>1){const dd='M'+r.map(p=>p[0]+' '+p[1]).join(' L ');
      g+=`<path d="${dd}" fill="none" stroke="#fff" stroke-width="3.4" stroke-opacity="0.7"/><path d="${dd}" fill="none" stroke="${TPC}" stroke-width="1.8"/>`;}
      r.forEach(p=>{g+=`<circle cx="${p[0]}" cy="${p[1]}" r="2.4" fill="${TPC}"/><text x="${p[0]}" y="${p[1]-6}" text-anchor="middle" font-size="11" font-weight="700" fill="${TPC}">${fmt(p[2])}</text>`;});});}
  const cs=tl.seg[k],sy=yb+4,shH=11;
  g+=`<text x="${left-5}" y="${sy+shH/2+2.5}" text-anchor="end" font-size="7.5" fill="var(--muted)">status</text>`;
  if(cs&&cs.length)cs.forEach(s=>{const x=X(s[0]),w=Math.max(0.5,s[1]/TOT*plotW);
    g+=`<rect x="${x}" y="${sy}" width="${w}" height="${shH}" fill="${TLCOL[s[2]]||'#ccc'}"><title>${k} · ${s[3]||s[2]} · ${s[1]} min</title></rect>`;});
  // per-load hang/queue band (same treatment as the Truck/Shovel Balance graph): hang up / queue down, green ≤ target, red hang / blue queue over
  const lw=(tl.loadWaits&&tl.loadWaits[k])||[];
  const an=(typeof V==='function'&&V())?V().analytics:null;
  const budOf=arr=>{if(!an||!an[arr])return 0;const b=an[arr].find(x=>x.shovel===k);return b?b.tgt:0;};
  const hb=budOf('hangbox'), qb=budOf('queuebox');
  if(lw.length){
    const maxV=14*60;   // fixed y-axis: ±14 min, values above are clamped
    const Yu=v=>bandMid-Math.min(v,maxV)/maxV*(bandH/2), Yd=v=>bandMid+Math.min(v,maxV)/maxV*(bandH/2);
    const bw=Math.max(1,Math.min(4,plotW/lw.length)), GRN='#2f8f4e',RED='#e23b32',BLU='#3f51b5';
    for(let hh=0;hh<=12;hh++){const x=X(hh*60);g+=`<line x1="${x}" y1="${bandTop}" x2="${x}" y2="${bandBot}" stroke="#eef0f4" stroke-width="0.6"/>`;}
    for(let v=120;v<=maxV;v+=120){const yu=Yu(v),yd=Yd(v),m=v/60;   // horizontal gridlines every 2 min (up + down)
      g+=`<line x1="${left}" y1="${yu.toFixed(1)}" x2="${left+plotW}" y2="${yu.toFixed(1)}" stroke="#eef0f4" stroke-width="0.6"/><text x="${left-6}" y="${(yu+3).toFixed(1)}" text-anchor="end" font-size="8" fill="var(--muted)">${m}</text>`;
      g+=`<line x1="${left}" y1="${yd.toFixed(1)}" x2="${left+plotW}" y2="${yd.toFixed(1)}" stroke="#eef0f4" stroke-width="0.6"/><text x="${left-6}" y="${(yd+3).toFixed(1)}" text-anchor="end" font-size="8" fill="var(--muted)">${m}</text>`;}
    lw.forEach(p=>{const x=X(p[0]);
      if(p[1]>0){const y=Yu(p[1]);g+=`<rect x="${(x-bw/2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(bandMid-y).toFixed(1)}" fill="${p[1]<=hb?GRN:RED}" fill-opacity="0.72"/>`;}
      if(p[2]>0){const y=Yd(p[2]);g+=`<rect x="${(x-bw/2).toFixed(1)}" y="${bandMid.toFixed(1)}" width="${bw.toFixed(1)}" height="${(y-bandMid).toFixed(1)}" fill="${p[2]<=qb?GRN:BLU}" fill-opacity="0.72"/>`;}});
    if(hb>0)g+=`<line x1="${left}" y1="${Yu(hb).toFixed(1)}" x2="${left+plotW}" y2="${Yu(hb).toFixed(1)}" stroke="#8a2c22" stroke-width="1.1" stroke-dasharray="5 3"><title>hang target ${fmtTime(hb)}</title></line>`;
    if(qb>0)g+=`<line x1="${left}" y1="${Yd(qb).toFixed(1)}" x2="${left+plotW}" y2="${Yd(qb).toFixed(1)}" stroke="#243b8a" stroke-width="1.1" stroke-dasharray="5 3"><title>queue target ${fmtTime(qb)}</title></line>`;
    g+=`<line x1="${left}" y1="${bandMid}" x2="${left+plotW}" y2="${bandMid}" stroke="#98a0ac" stroke-width="1"/>`;
    g+=`<text x="13" y="${bandMid}" transform="rotate(-90 13 ${bandMid})" text-anchor="middle" font-size="9" fill="var(--muted)">min/load</text>`;
    g+=`<text x="${left+3}" y="${bandTop+9}" font-size="8" fill="var(--muted)">▲ hang</text>`;
    g+=`<text x="${left+3}" y="${bandBot-3}" font-size="8" fill="var(--muted)">▼ queue</text>`;
  }
  if(parts.length) g+=`<text x="${left+plotW-3}" y="${bandTop+11}" text-anchor="end" font-size="9.5" font-weight="600" fill="#3a3f46" stroke="#fff" stroke-width="2.8" paint-order="stroke">avg ${parts.join(' · ')} min/load</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px"><span class="badge"><b style="color:#111">—</b> trucks at shovel (0–${qmax})</span><span class="badge"><b style="color:#6a3fd0">—</b> tonnes (right axis)</span><span class="badge">band = hang/queue per load · <b style="color:#2f8f4e">■</b> ≤ target · <b style="color:#e23b32">■</b> hang over · <b style="color:#3f51b5">■</b> queue over</span>`+
    Object.entries(TLCOL).map(([kk,c])=>`<span class="badge"><b style="color:${c}">■</b> ${kk}</span>`).join('')+`</div>`;
}
function drawPayBox(pay,tg){
  if(!pay.length)return '<div class="foot">No payload data.</div>';
  const W=900,H=320,L=52,Rm=14,T=14,B=44,pw=W-L-Rm,ph=H-T-B;
  let allv=[tg];pay.forEach(p=>{allv.push(p.q1,p.q3);(p.outliers||[]).forEach(o=>allv.push(o));});
  let lo=Math.min(...allv),hi=Math.max(...allv);const span=(hi-lo)||1;lo-=span*0.05;hi+=span*0.05;
  const Y=v=>T+ph-(v-lo)/(hi-lo)*ph,n=pay.length,step=pw/n,bw=Math.min(34,step*0.5);
  let g='';
  for(let i=0;i<=5;i++){const v=lo+(hi-lo)*i/5,y=Y(v);g+=`<line x1="${L}" y1="${y}" x2="${L+pw}" y2="${y}" stroke="var(--line)" stroke-width="0.5"/><text x="${L-6}" y="${y+3}" text-anchor="end" font-size="9.5" fill="var(--muted)">${Math.round(v)}</text>`;}
  g+=`<line x1="${L}" y1="${Y(tg)}" x2="${L+pw}" y2="${Y(tg)}" stroke="#e23b32" stroke-width="1.4" stroke-dasharray="6 4"/><text x="${L+pw}" y="${Y(tg)-4}" text-anchor="end" font-size="10" fill="#e23b32">target ${tg}t</text>`;
  pay.forEach((p,i)=>{const cx=L+step*(i+0.5),c=p.mat==='Waste'?'#d08a1f':'#1f9e8b',yt=Y(p.q3),yb=Y(p.q1);
    g+=`<rect x="${cx-bw/2}" y="${yt}" width="${bw}" height="${Math.max(1,yb-yt)}" fill="${c}" fill-opacity="0.35" stroke="${c}" stroke-width="1.2"><title>${p.shovel} (${p.type==='BE495'?'BE 495':'HIT 8000'} · ${p.mat}) n=${p.n}\nQ1 ${p.q1} · median ${p.median} · Q3 ${p.q3} · IQR ${p.iqr}\nmean ${p.avg}\ncompliance ${p.compliance}%</title></rect>`;
    g+=`<line x1="${cx-bw/2}" y1="${Y(p.median)}" x2="${cx+bw/2}" y2="${Y(p.median)}" stroke="${c}" stroke-width="2"/>`;
    (p.outliers||[]).forEach(o=>{g+=`<circle cx="${cx}" cy="${Y(o)}" r="2.2" fill="none" stroke="${c}" stroke-width="1"><title>${p.shovel} outlier ${o}t</title></circle>`;});
    const my=Y(p.avg);g+=`<path d="M${cx} ${my-4} L${cx+4} ${my} L${cx} ${my+4} L${cx-4} ${my} Z" fill="#2b2f36"/>`;
    g+=`<text x="${cx}" y="${H-B+15}" text-anchor="middle" font-size="9.5" fill="var(--ink)">${p.shovel}</text>`;});
  g+=`<text x="13" y="${T+ph/2}" transform="rotate(-90 13 ${T+ph/2})" text-anchor="middle" font-size="11" fill="var(--muted)">payload (t)</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px"><span class="badge">box = Q1–Q3</span><span class="badge">line = median</span><span class="badge">◆ mean</span><span class="badge">○ outlier</span><span class="badge"><b style="color:#1f9e8b">■</b> ore &nbsp; <b style="color:#d08a1f">■</b> waste</span></div>`;
}
// Generic per-shovel box plot (like drawPayBox) with a per-box budget target tick — used for hang/load time.
function drawBoxPlot(data,o){
  o=o||{}; const unit=o.unit||'', axisLabel=o.axisLabel||'', sc=o.scale||1, dec=o.dec, hideOut=!!o.hideOutliers;
  if(!data||!data.length)return '<div class="foot">No data for this view.</div>';
  const S=v=>v*sc, fv=v=>dec!=null?S(v).toFixed(dec):Math.round(S(v));           // scale + display format
  const W=900,H=320,L=52,Rm=14,T=14,B=44,pw=W-L-Rm,ph=H-T-B;
  let allv=[];data.forEach(p=>{allv.push(S(p.q1),S(p.q3),S(p.tgt));if(!hideOut)(p.outliers||[]).forEach(x=>allv.push(S(x)));});
  let lo=Math.min(...allv),hi=Math.max(...allv);const span=(hi-lo)||1;lo-=span*0.05;hi+=span*0.05;
  const Y=v=>T+ph-(v-lo)/(hi-lo)*ph,n=data.length,step=pw/n,bw=Math.min(34,step*0.5);
  let g='';
  for(let i=0;i<=5;i++){const v=lo+(hi-lo)*i/5,y=Y(v);g+=`<line x1="${L}" y1="${y}" x2="${L+pw}" y2="${y}" stroke="var(--line)" stroke-width="0.5"/><text x="${L-6}" y="${y+3}" text-anchor="end" font-size="9.5" fill="var(--muted)">${dec!=null?v.toFixed(dec):Math.round(v)}</text>`;}
  data.forEach((p,i)=>{const cx=L+step*(i+0.5),c=p.col||(p.mat==='Waste'?'#d08a1f':'#1f9e8b'),yt=Y(S(p.q3)),yb=Y(S(p.q1));
    const ty=Y(S(p.tgt));g+=`<line x1="${cx-bw/2-3}" y1="${ty}" x2="${cx+bw/2+3}" y2="${ty}" stroke="#e23b32" stroke-width="1.4" stroke-dasharray="4 3"><title>${p.shovel} budget ${fv(p.tgt)}${unit}</title></line>`;
    g+=`<rect x="${cx-bw/2}" y="${yt}" width="${bw}" height="${Math.max(1,yb-yt)}" fill="${c}" fill-opacity="0.35" stroke="${c}" stroke-width="1.2"><title>${p.shovel} (${p.type==='BE495'?'BE 495 · ':(p.type==='HIT8000'?'HIT 8000 · ':'')}${p.mat}) n=${p.n}\nQ1 ${fv(p.q1)} · median ${fv(p.median)} · Q3 ${fv(p.q3)}\nmean ${fv(p.avg)}\nbudget ${fv(p.tgt)}${unit} · within ±10% ${p.compliance}%${p.side2!=null?'\\nloading: single '+p.side1+'% · double '+p.side2+'%':''}</title></rect>`;
    g+=`<line x1="${cx-bw/2}" y1="${Y(S(p.median))}" x2="${cx+bw/2}" y2="${Y(S(p.median))}" stroke="${c}" stroke-width="2"/>`;
    const my=Y(S(p.avg));g+=`<path d="M${cx} ${my-4} L${cx+4} ${my} L${cx} ${my+4} L${cx-4} ${my} Z" fill="#2b2f36"/>`;
    g+=`<text x="${cx}" y="${H-B+15}" text-anchor="middle" font-size="9.5" fill="var(--ink)">${p.shovel}</text>`;
    if(p.side2!=null){const ly=Math.min(T+ph-5,Y(S(p.avg))+17);g+=`<text x="${cx}" y="${ly.toFixed(1)}" text-anchor="middle" font-size="14" font-weight="700" fill="#7a4fd0" stroke="#fff" stroke-width="3" paint-order="stroke">${p.side2}%</text>`;}});
  g+=`<text x="13" y="${T+ph/2}" transform="rotate(-90 13 ${T+ph/2})" text-anchor="middle" font-size="11" fill="var(--muted)">${axisLabel}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${g}</svg><div class="badges" style="margin-top:6px"><span class="badge">box = Q1–Q3</span><span class="badge">line = median</span><span class="badge">◆ mean</span><span class="badge"><b style="color:#e23b32">--</b> budget</span><span class="badge"><b style="color:#1f9e8b">■</b> ore &nbsp; <b style="color:#d08a1f">■</b> waste</span>${data.some(p=>p.side2!=null)?`<span class="badge"><b style="color:#7a4fd0">NN%</b> below mean = double-side loading share (from ShovelLoadingSide)</span>`:''}</div>`;
}
// ---- shared score/summary helpers (Overview exec line + Trends) ----
function viewScores(v){
  if(!v) return {hSc:null,lSc:null,tm:null,hAct:null,plan:null};
  const tw=v.trucksWF, sw=v.shovelWF2;
  const hPot=tw?(tw.availDecomp?tw.schedPotential:tw.potential):0, hAct=tw?tw.actual:null, hSc=hPot?hAct/hPot*100:null;
  let lSc=null;
  if(sw){const lPot=sw.availDecomp?sw.schedPotential:sw.potential; lSc=lPot?sw.actual/lPot*100:null;}
  else if(v.loading){lSc=v.loading.score;}
  const tm=(hSc!=null&&lSc!=null)?(lSc-hSc):null;
  const plan=(v.analytics&&v.analytics.cumulative)?v.analytics.cumulative.plan:null;
  return {hSc,lSc,tm,hAct,plan};
}
function av797(v){const a=(v&&v.availability||[]).find(x=>x.group==='Cat 797');return a?{pa:a.PA,ua:a.UA,oe:a.OE}:null;}
function matchWord(tm){return tm==null?'—':(Math.abs(tm)<=3?'Balanced':(tm<0?'Under-Trucked':'Over-Trucked'));}
const FLBL={Payload:'Payload',Load:'Load Time',Queue:'Queue at Shovel',Spot:'Spot at Shovel',DumpIdle:'Dump Idle',Dumping:'Dumping',FullHaul:'Full Haul',EmptyHaul:'Empty Haul',Hang:'Hang Time',PA:'Availability',UA:'Standby',OE:'Delay'};
function factorsFor(which){
  // biggest positive (pro) and biggest negative (con) tonnage factor of the fleet waterfall
  const v=V(); let src=null;
  if(which==='haul'){const tw=v.trucksWF; if(!tw)return null; src=Object.assign({},tw.rows); if(tw.availDecomp){src.PA=tw.availDecomp.pa.t;src.UA=tw.availDecomp.ua.t;src.OE=tw.availDecomp.oe.t;}}
  else {const sw=v.shovelWF2; if(!sw)return null; src=Object.assign({},sw.rows); if(sw.availDecomp){src.PA=sw.availDecomp.pa.t;src.UA=sw.availDecomp.ua.t;src.OE=sw.availDecomp.oe.t;}}
  let pro=null,con=null;
  for(const k in src){const val=src[k]||0;
    if(val>0&&(!pro||val>pro.val))pro={label:FLBL[k]||k,val};
    if(val<0&&(!con||val<con.val))con={label:FLBL[k]||k,val};}
  return {pro,con};
}
function renderTrends(){
  document.getElementById('trsub').textContent='('+view+')';
  const chron=DATA.shifts.slice().reverse();   // oldest → newest
  const labels=chron.map(s=>s.name);
  const cur=chron.map(s=>s.id===shift);
  const dot=(base)=>chron.map((s,i)=>cur[i]?'#111':base);
  const rad=chron.map((s,i)=>cur[i]?4.5:2);
  const S=chron.map(s=>{const sd=DATA.byShift[s.id]; return sd?viewScores(sd.views[view]):null;});
  const A=chron.map(s=>{const sd=DATA.byShift[s.id]; return sd?av797(sd.views[view]):null;});
  const g1=S.map(x=>x?x.hSc:null), g2=S.map(x=>x?x.lSc:null);
  const pa=A.map(x=>x?x.pa:null), ua=A.map(x=>x?x.ua:null), oe=A.map(x=>x?x.oe:null);
  const act=S.map(x=>x?x.hAct:null), plan=S.map(x=>x?x.plan:null), match=S.map(x=>x?x.tm:null);
  if(typeof Chart==='undefined'){document.getElementById('trsub').textContent='('+view+') — charts need internet to load Chart.js';return;}
  const F10={font:{size:10}},F9={font:{size:9}};
  const baseOpt=extra=>({responsive:true,maintainAspectRatio:false,interaction:{intersect:false,mode:'index'},
    plugins:{legend:{labels:{boxWidth:12,...F10}}},scales:Object.assign({x:{ticks:{maxTicksLimit:14,...F9}}},extra)});
  const line=(label,data,color,dash)=>({label,data,borderColor:color,backgroundColor:color,spanGaps:false,tension:.2,
    borderWidth:1.8,borderDash:dash||[],pointRadius:rad,pointBackgroundColor:dot(color),pointBorderColor:dot(color)});
  mk('chTrScore',{type:'line',data:{labels,datasets:[line('Haulage',g1,'#3f51b5'),line('Loading',g2,'#1f9e8b')]},
    options:baseOpt({y:{ticks:{...F10,callback:v=>v+'%'}}})});
  mk('chTrAvail',{type:'line',data:{labels,datasets:[line('PA',pa,'#2f8f4e'),line('UA',ua,'#c98a1f'),line('OE',oe,'#c0392b')]},
    options:baseOpt({y:{ticks:{...F10,callback:v=>v+'%'}}})});
  mk('chTrProd',{type:'line',data:{labels,datasets:[line('Actual dumped',act,'#3f51b5'),line('Plan',plan,'#888',[6,4])]},
    options:baseOpt({y:{ticks:{...F10,callback:v=>(v/1000)+'k'}}})});
  mk('chTrMatch',{type:'line',data:{labels,datasets:[line('Truck Match',match,'#7a4fd0')]},
    options:baseOpt({y:{ticks:{...F10}}})});
}
let cmOpt={loaded:true,empty:true,hi:false,tbl:false,road:true,base:true,snap:true};   // cycle-map toggles
function renderCycleMap(){
  const cyc=V().haulCycles, CO=(DATA.locCoords)||{};
  const ids=new Set(); (cyc.fullFlows||[]).forEach(f=>{ids.add(f.from);ids.add(f.to);}); (cyc.prevFlows||[]).forEach(f=>{ids.add(f.from);ids.add(f.to);});
  let have=0; ids.forEach(k=>{if(CO[k])have++;});
  const useCo=ids.size&&have>=Math.ceil(ids.size*0.5);
  const el=document.getElementById('hcmapsrc'); if(el)el.textContent=useCo?`— positions from field GPS (${have}/${ids.size} located, median per location)`:'— GPS positions unavailable; layout derived from haul distances';
  const hasRoad=useCo&&DATA.roadCells&&DATA.roadCells.length;
  const rb=document.getElementById('cmRoad'); if(rb)rb.style.display=hasRoad?'':'none';
  const sb=document.getElementById('cmSnap'); if(sb)sb.style.display=hasRoad?'':'none';
  const bb=document.getElementById('cmBase'); if(bb)bb.style.display=(useCo&&DATA.baseMap)?'':'none';
  const c=document.getElementById('hc4'); if(c)c.innerHTML=drawCycleMap(cyc,cmOpt);
  // Longest-empty table: same set the map highlights — top max(3, 15% of empty legs) by empty-haul distance.
  const tblEl=document.getElementById('hc4tbl');
  if(tblEl){
    if(!cmOpt.tbl){tblEl.innerHTML='';}
    else{
      const pf=(cyc.prevFlows||[]).filter(f=>f.tons>0).slice().sort((a,b)=>(b.km||0)-(a.km||0));
      const topN=Math.max(3,Math.round(pf.length*0.15)), hi=pf.slice(0,topN);
      if(!hi.length){tblEl.innerHTML='<div class="foot" style="margin-top:8px">No empty-return legs to rank.</div>';}
      else{
        let h=`<table class="lanetab" style="font-size:11px;margin-top:6px"><tr><th>#</th><th>Empty leg&nbsp;(dump&nbsp;→&nbsp;shovel)</th><th style="text-align:right">Empty dist&nbsp;(km)</th><th style="text-align:right">Trips</th><th style="text-align:right">% locked</th></tr>`;
        hi.forEach((f,i)=>{const lp=f.n?Math.round((f.nlock||0)/f.n*100):0;
          h+=`<tr><td>${i+1}</td><td><b style="color:#d1495b">${shortId(f.from)}</b> → ${shortId(f.to)}</td><td style="text-align:right">${(f.km||0).toFixed(1)}</td><td style="text-align:right">${f.n||0}</td><td style="text-align:right;color:${lp>=25?'#b3382b':'inherit'}">${lp}%</td></tr>`;});
        h+='</table>';
        tblEl.innerHTML=`<div class="foot" style="margin:8px 0 2px">Longest empty-return legs by <b>distance</b> — the same set the map highlights (top <b>${hi.length}</b> = greater of 3 or 15% of ${pf.length} empty legs). Ranked purely by per-leg empty-haul km.</div>`+h;
      }
    }
  }
}
function cmToggle(k,btn){cmOpt[k]=!cmOpt[k]; if(btn)btn.classList.toggle('on',cmOpt[k]); renderCycleMap();}
function renderMatPlace(){   // Material Placement Sankey (its own tab) — SVG, renders even without Chart.js
  document.getElementById('hcsub2').textContent='('+view+')';
  const legTxt=anchor=>`<span class="badge"><b style="color:${CORE}">■</b> ore</span><span class="badge"><b style="color:${CWASTE}">■</b> waste</span><span class="badge">▨ hatched = locked (un-optimized) loads · % under nodes</span><span class="badge">ribbon width ∝ tonnage · <b>each ribbon's length is its own haul distance to scale</b> (${anchor} = 0 km — see the ruler). Each node stays a single bar whose <b>width spans that node's range of path distances</b>.</span>`;
  document.getElementById('hcSimple').innerHTML=drawTruckFlow(V().haulCycles,'dump',true);
  document.getElementById('hclegSimple').innerHTML=`<span class="badge"><b style="color:${CORE}">■</b> ore</span><span class="badge"><b style="color:${CWASTE}">■</b> waste</span><span class="badge">▨ hatched = locked (un-optimized) loads · % under nodes</span><span class="badge">same dump-centric layout — full-haul tonnage (shovel→dump) + empty-haul tonnage (dump→next shovel) + % locked all retained · ribbon width ∝ tonnage · <b>haul distance NOT encoded</b> (even columns).</span>`;
  // ---- cycle map (spatial) ----
  renderCycleMap();
  const roadBadge=(DATA.roadCells&&DATA.roadCells.length)?`<span class="badge"><b style="color:#8a8f98">▪</b> haul roads (truck-trace density)</span>`:'';
  document.getElementById('hcleg4').innerHTML=`<span class="badge"><b style="color:#2f6f9f">●</b> shovel</span><span class="badge"><b style="color:${CORE}">●</b> ore dump</span><span class="badge"><b style="color:${CWASTE}">●</b> waste dump</span><span class="badge">circle size ∝ tonnes</span><span class="badge"><b style="color:${CORE}">—</b> loaded haul (shovel → dump)</span><span class="badge"><b style="color:#9aa6b5">- -</b> empty return (dump → next shovel)</span><span class="badge"><b style="color:#d1495b">- -</b> longest empties (when highlighted)</span>${roadBadge}<span class="badge">arrowheads show cycle direction · spacing to scale in km</span>`;
}
function recalcHourTargets(ci){   // cumulative target lines counting only the currently-visible components
  const ds=ci.data.datasets,n=(ds[0].data||[]).length,vis=k=>ci.isDatasetVisible(k);
  const o=ds[3]._seg||0,w=ds[4]._seg||0,np=ds[5]._seg||0;
  const c3=o,c4=(vis(0)?o:0)+w,c5=(vis(0)?o:0)+(vis(1)?w:0)+np;
  ds[3].data=Array(n).fill(c3); ds[4].data=Array(n).fill(c4); ds[5].data=Array(n).fill(c5);
}
function renderOverview(){
  const a=V().analytics;
  const sc=viewScores(V());
  const exCol=s=>s>=100?'#4caf50':(s>=90?'#eab308':'#e23b32');   // ≥100 green · 90–<100 amber · <90 red
  const seg=(name,score,tab,fac)=>{
    if(score==null)return `${name} —`;
    let s=`<b class="exscore" style="color:${exCol(score)}" onclick="setTab('${tab}')" title="Open the ${name==='Haulage'?'Truck':'Shovel'} Waterfall">${name} ${score.toFixed(0)}%</b>`;
    if(fac&&fac.pro)s+=` <span style="color:#2f7a44">▲ ${fac.pro.label} +${fmt(fac.pro.val)} t</span>`;
    if(fac&&fac.con)s+=` <span style="color:#b3382b">▼ ${fac.con.label} −${fmt(-fac.con.val)} t</span>`;
    return s;};
  const tmw=matchWord(sc.tm);
  const rest=`${seg('Haulage',sc.hSc,'trucks',factorsFor('haul'))} &nbsp;·&nbsp; ${seg('Loading',sc.lSc,'shovel2',factorsFor('load'))} &nbsp;·&nbsp; Truck match <b>${sc.tm==null?'—':(sc.tm>0?'+':'')+sc.tm.toFixed(0)+'%'}</b> (${tmw}).`;
  document.getElementById('ovExec').innerHTML=
    `<span class="exlead">This shift · ${view}:</span>`+
    `<div class="exmq"><div class="exmq-in"><span class="exmq-seg">${rest}</span><span class="exmq-seg">${rest}</span></div></div>`;
  document.getElementById('ovsub').textContent='('+view+')';
  document.getElementById('ovkpisub').textContent='('+view+')';
  document.getElementById('ovKpi').innerHTML=buildAvCards(V().availability);
  applyAvExpand();   // re-apply Expand all / Contract all state after a view/shift change
  if(typeof Chart==='undefined'){document.getElementById('ovsub').textContent='('+view+') — charts need internet to load Chart.js';return;}
  const F10={font:{size:10}},F9={font:{size:9}};
  const hasProd=a.cumulative.actual.some(x=>x!=null&&x>0);
  document.getElementById('hourNote').style.display=hasProd?'none':'block';
  const c=(cumMode==='sand'&&a.cumulativeSand)?a.cumulativeSand:a.cumulative;
  {const t=document.getElementById('cumTitle'); if(t)t.textContent='Shift Progress — Cumulative Tonnes vs Target'+(cumMode==='sand'?' · Sand Haul':'');}
  const hasCum=c.actual.some(x=>x!=null&&x>0);
  document.getElementById('cumNote').style.display=hasCum?'none':'block';
  {const ct=document.getElementById('cumThrough'); if(ct){const tm=c.throughMin||0,b=c.base||6,hh=(b+Math.floor(tm/60))%24,mm=tm%60,p=n=>(n<10?'0'+n:''+n);
    ct.textContent=c.complete?'· shift complete':('· live — data through '+p(hh)+':'+p(mm));}}
  // Carry the actual line flat to the shift-end tick when the shift is essentially complete (last dump within
  // the final hour), so a completed shift has no trailing gap; an in-progress shift stays open on the right.
  let cumActual=c.actual.slice();
  {let li=-1; for(let k=cumActual.length-1;k>=0;k--){if(cumActual[k]!=null){li=k;break;}}
   if(c.complete && li>=0 && li<cumActual.length-1){for(let k=li+1;k<cumActual.length;k++)cumActual[k]=cumActual[li];}}   // flat-carry only when the shift is actually complete (data reaches shift-end)
  // data label at the tip of the actual line = latest cumulative tonnes
  const cumEndLabel={id:'cumEndLabel',afterDatasetsDraw(ch){
    const m=ch.getDatasetMeta(0); if(!m||!m.data.length)return; const ds=ch.data.datasets[0].data;
    let li=-1; for(let k=ds.length-1;k>=0;k--){if(ds[k]!=null){li=k;break;}}
    if(li<0)return; const pt=m.data[li]; if(!pt)return; const ctx=ch.ctx; ctx.save();
    const txt=fmt(ds[li])+' t'; ctx.font='700 11px system-ui,sans-serif';
    ctx.textAlign='center'; ctx.textBaseline='bottom';   // actual label ABOVE the point
    const w=ctx.measureText(txt).width; const x=Math.max(ch.chartArea.left+w/2,Math.min(ch.chartArea.right-w/2,pt.x));
    ctx.beginPath(); ctx.arc(pt.x,pt.y,3,0,7); ctx.fillStyle='#3f51b5'; ctx.fill();
    ctx.lineWidth=3; ctx.strokeStyle='#fff'; ctx.strokeText(txt,x,pt.y-6);
    ctx.fillStyle='#3f51b5'; ctx.fillText(txt,x,pt.y-6); ctx.restore();
  }};
  // required avg tonnes/shift for FUTURE shifts to hit the month budget → plotted as its own cumulative pace line
  const reqF=c.reqFuture;
  const reqSeries=(reqF!=null)?c.labels.map((_,k)=>Math.round(reqF*k/48)):null;
  // production projection — assume shift-to-date productivity continues to shift end (linear from origin through the last actual point)
  let projSeries=null,projEnd=null;
  {const av=c.actual; let last=-1; for(let k=av.length-1;k>=0;k--){if(av[k]!=null){last=k;break;}}
   // project whenever the shift is still in progress (data hasn't reached shift-end), extending the shift-to-date pace
   if(!c.complete && last>0 && last<av.length-1){projSeries=av.map((v,k)=>k<last?null:(k===last?av[last]:Math.round(av[last]*k/last))); projEnd=projSeries[projSeries.length-1];}}
  // data label at the tip of the projection line = total shift projected tonnes
  const projEndLabel={id:'projEndLabel',afterDatasetsDraw(ch){
    const di=ch.data.datasets.findIndex(d=>d.label&&d.label.indexOf('Projection')===0); if(di<0)return;
    const m=ch.getDatasetMeta(di); if(!m||!m.data.length)return; const ds=ch.data.datasets[di].data;
    let li=-1; for(let k=ds.length-1;k>=0;k--){if(ds[k]!=null){li=k;break;}}
    if(li<0)return; const pt=m.data[li]; if(!pt)return; const ctx=ch.ctx; ctx.save();
    const txt='proj '+fmt(ds[li])+' t'; ctx.font='700 11px system-ui,sans-serif';
    ctx.textAlign='center'; ctx.textBaseline='top';   // projection label BELOW the point
    const w=ctx.measureText(txt).width; const x=Math.max(ch.chartArea.left+w/2,Math.min(ch.chartArea.right-w/2,pt.x));
    ctx.beginPath(); ctx.arc(pt.x,pt.y,3.2,0,7); ctx.fillStyle='#e0952a'; ctx.fill();
    ctx.lineWidth=3; ctx.strokeStyle='#fff'; ctx.strokeText(txt,x,pt.y+7);
    ctx.fillStyle='#b3760f'; ctx.fillText(txt,x,pt.y+7); ctx.restore();
  }};
  const cumDs=[
    {label:'Actual',data:cumActual,borderColor:'#2a349e',backgroundColor:'rgba(42,52,158,.16)',spanGaps:false,tension:.2,pointRadius:0,fill:true,borderWidth:2.4}];
  if(c.plan!=null)cumDs.push({label:'Target (plan '+(c.plan/1000).toFixed(0)+'k)',data:c.target,borderColor:'#888',borderDash:[6,4],pointRadius:0,borderWidth:1.5});
  if(projSeries)cumDs.push({label:'Projection'+(projEnd!=null?' ('+(projEnd/1000).toFixed(0)+'k)':''),data:projSeries,borderColor:'#e0952a',backgroundColor:'rgba(224,149,42,.16)',borderDash:[5,3],pointRadius:0,borderWidth:2,fill:true,spanGaps:false,tension:.2});
  if(reqSeries)cumDs.push({label:'Future-shift pace ('+(reqF/1000).toFixed(0)+'k)',data:reqSeries,borderColor:'#b3382b',backgroundColor:'#b3382b',borderDash:[3,3],pointRadius:0,borderWidth:1.5,spanGaps:false,hidden:true});
  mk('chCum',{type:'line',data:{labels:c.labels,datasets:cumDs},options:{responsive:true,maintainAspectRatio:false,layout:{padding:{right:46}},interaction:{intersect:false,mode:'index'},plugins:{legend:{labels:{boxWidth:12,...F10}}},scales:{x:{ticks:{...F9,autoSkip:false,maxRotation:0,callback:(v,i)=>{const L=c.labels[i];return (L&&L.slice(-3)===':00')?L:'';}},grid:{color:ctx=>{const L=c.labels[ctx.index];return (L&&L.slice(-3)===':00')?'rgba(0,0,0,0.08)':'transparent';}}},y:{suggestedMax:Math.max(c.plan||0,reqF||0,projEnd||0)*1.05,ticks:{...F10,callback:v=>(v/1000)+'k'}}}},plugins:[cumEndLabel,projEndLabel]});
  const hh=a.hourly;
  const cO=hh.targetOre,cW=hh.targetOre+hh.targetWaste,cN=hh.targetOre+hh.targetWaste+hh.targetNonProd;
  if(cumMode==='sand'){   // Hourly card becomes tonnes-by-dump-location (horizontal) in Sand Haul mode
    const sd=(a.sandByDump||[]).slice();
    const hbarLabel={id:'hbarLabel',afterDatasetsDraw(ch){const m=ch.getDatasetMeta(0); if(!m)return; const ds=ch.data.datasets[0].data,ctx=ch.ctx;
      ctx.save(); ctx.font='700 10px system-ui,sans-serif'; ctx.fillStyle='#333'; ctx.textAlign='left'; ctx.textBaseline='middle';
      m.data.forEach((bar,i)=>{const v=ds[i]||0; if(v<=0)return; ctx.fillText(fmt(Math.round(v))+' t',bar.x+5,bar.y);}); ctx.restore();}};
    if(!sd.length){mk('chHour',{type:'bar',data:{labels:['—'],datasets:[{data:[0],backgroundColor:'#c8862a'}]},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{enabled:false}},scales:{x:{ticks:{display:false}},y:{ticks:{...F9}}}}});}
    else mk('chHour',{type:'bar',data:{labels:sd.map(x=>x.dump),datasets:[{label:'Sand tonnes',data:sd.map(x=>x.tons),backgroundColor:'#c8862a',borderRadius:3}]},
      options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,layout:{padding:{right:60}},
        plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>fmt(Math.round(ctx.parsed.x))+' t'}}},
        scales:{x:{ticks:{...F10,callback:v=>(v/1000)+'k'}},y:{ticks:{...F9}}}},plugins:[hbarLabel]});
    const hd=document.getElementById('hourDetail'); if(hd)hd.classList.add('hidden');
    if(CHARTS.chHour)CHARTS.chHour.resize();
    return;
  }
  {const hd=document.getElementById('hourDetail'); if(hd)hd.classList.remove('hidden');}
  // default the panel to the most recent hour with production
  {let lastH=-1; for(let k=hh.hours.length-1;k>=0;k--){if((hh.ore[k]||0)+(hh.waste[k]||0)+(hh.nonprod[k]||0)>0){lastH=k;break;}} hourSel=(lastH>=0?lastH:null);}
  // non-selected bars are only slightly lighter (still clearly visible); the selected hour is fully opaque
  const barColor=base=>ctx=>ctx.dataIndex===hourSel?base:base+'CC';
  const barTotLabel={id:'barTotLabel',afterDatasetsDraw(ch){const m=ch.getDatasetMeta(0); if(!m)return; const d0=ch.data.datasets[0].data,d1=ch.data.datasets[1].data,d2=ch.data.datasets[2].data,ctx=ch.ctx;
    ctx.save(); ctx.font='700 9px system-ui,sans-serif'; ctx.fillStyle='#333'; ctx.textAlign='center'; ctx.textBaseline='bottom';
    m.data.forEach((bar,i)=>{const tot=(d0[i]||0)+(d1[i]||0)+(d2[i]||0); if(tot<=0)return; const y=ch.scales.y.getPixelForValue(tot); ctx.fillText(fmt(Math.round(tot)),bar.x,y-2);}); ctx.restore();}};
  mk('chHour',{type:'bar',data:{labels:hh.hours,datasets:[
    {label:'Prod. ore',data:hh.ore,backgroundColor:barColor('#1f9e8b'),stack:'s'},
    {label:'Prod. waste',data:hh.waste,backgroundColor:barColor('#d08a1f'),stack:'s'},
    {label:'Non-prod.',data:hh.nonprod,backgroundColor:barColor('#9aa0ab'),stack:'s'},
    {type:'line',label:'Target ore/hr',data:hh.hours.map(()=>cO),_seg:hh.targetOre,borderColor:'#1f9e8b',borderDash:[6,4],pointRadius:0,borderWidth:1.8,stack:'to'},
    {type:'line',label:'Target +waste/hr',data:hh.hours.map(()=>cW),_seg:hh.targetWaste,borderColor:'#d08a1f',borderDash:[6,4],pointRadius:0,borderWidth:1.8,stack:'tw'},
    {type:'line',label:'Target +non-prod/hr',data:hh.hours.map(()=>cN),_seg:hh.targetNonProd,borderColor:'#e23b32',borderDash:[6,4],pointRadius:0,borderWidth:2,stack:'tn'}
  ]},options:{responsive:true,maintainAspectRatio:false,onClick:(e,els,ch)=>{
      let idx=(els&&els.length)?els[0].index:null;
      if(idx==null){const xs=ch.scales.x; if(xs){const v=xs.getValueForPixel(e.x); if(v!=null){const r=Math.round(v); if(r>=0&&r<hh.hours.length)idx=r;}}}
      if(idx!=null)renderHourDetail(idx);
    },plugins:{legend:{onClick:(e,item,legend)=>{const ci=legend.chart,i=item.datasetIndex,pair={0:3,1:4,2:5,3:0,4:1,5:2},v=!ci.isDatasetVisible(i);ci.setDatasetVisibility(i,v);if(pair[i]!=null)ci.setDatasetVisibility(pair[i],v);recalcHourTargets(ci);ci.update();},labels:{boxWidth:11,...F9}},tooltip:{callbacks:{label:ctx=>{const L=ctx.dataset.label;if(L[0]!=='T')return L+' '+Math.round(ctx.parsed.y)+' t';return L+' '+Math.round(ctx.parsed.y)+' t (cum)';}}}},scales:{x:{stacked:true,ticks:{...F9,callback:(v,i)=>hh.hours[i]+':00'}},y:{stacked:true,ticks:{...F10,callback:v=>(v/1000)+'k'}}}},plugins:[barTotLabel]});
  renderHourDetail(hourSel);   // fill the side panel with the default (most recent) hour
  applyHourDet();              // honour the show/hide-detail toggle (also aligns the cumulative chart margin)
}
function drawParetoChart(pp){   // Lost-time delay pareto (now on the Delays & Standby tab, per-fleet)
  if(typeof Chart==='undefined')return;
  const F10={font:{size:10}},F9={font:{size:9}};
  const parLabels={id:'parLabels',afterDatasetsDraw(ch){
    const ctx=ch.ctx,y=ch.scales.y,m=ch.getDatasetMeta(0); if(!m) return;
    ctx.save(); ctx.textAlign='center'; ctx.font='600 9px system-ui,sans-serif';
    ctx.lineWidth=3; ctx.strokeStyle='#ffffff';
    pp.reasons.forEach((r,i)=>{const bar=m.data[i]; if(!bar) return;
      ctx.fillStyle='#2b2f36'; ctx.textBaseline='bottom'; ctx.fillText(pp.hours[i].toFixed(1)+'h',bar.x,y.getPixelForValue(pp.hours[i])-3);
      const oy=y.getPixelForValue(0)-3;
      ctx.strokeText(pp.counts[i]+'×',bar.x,oy); ctx.fillStyle='#374151'; ctx.fillText(pp.counts[i]+'×',bar.x,oy);
    });
    ctx.restore();
  }};
  mk('chPar',{type:'bar',data:{labels:pp.reasons,datasets:[
    {type:'bar',label:'Lost hours',data:pp.hours,backgroundColor:'#e23b32',yAxisID:'y'},
    {type:'line',label:'Cumulative %',data:pp.cumpct,borderColor:'#3f51b5',pointRadius:2,yAxisID:'y1',borderWidth:1.5}
  ]},options:{responsive:true,maintainAspectRatio:false,layout:{padding:{top:20}},plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>ctx.dataset.label==='Cumulative %'?ctx.parsed.y+'%':ctx.parsed.y+' h · '+pp.counts[ctx.dataIndex]+' events'}}},scales:{x:{ticks:{...F9,maxRotation:70,minRotation:55,autoSkip:false}},y:{title:{display:true,text:'hours'},ticks:F10},y1:{position:'right',min:0,max:100,grid:{drawOnChartArea:false},ticks:{...F10,callback:v=>v+'%'}}}},plugins:[parLabels]});
}
function drawDelayVarChart(dv){   // Delay variance (actual − expected), now on the Delays & Standby tab, per-fleet
  if(typeof Chart==='undefined')return;
  const F10={font:{size:10}},F9={font:{size:9}};
  const dvLabels={id:'dvLabels',afterDatasetsDraw(ch){
    const ctx=ch.ctx,y=ch.scales.y,m=ch.getDatasetMeta(0); if(!m) return;
    ctx.save(); ctx.textAlign='center'; ctx.font='600 9px system-ui,sans-serif';
    const zy=y.getPixelForValue(0); ctx.lineWidth=3; ctx.strokeStyle='#ffffff';
    dv.reasons.forEach((r,i)=>{const bar=m.data[i]; if(!bar) return; const v=dv.variance[i],up=v>0;
      ctx.textBaseline=up?'bottom':'top';
      ctx.fillStyle='#2b2f36'; ctx.fillText((v>0?'+':'')+v.toFixed(1)+'h',bar.x,y.getPixelForValue(v)+(up?-3:3));
      ctx.textBaseline='middle';
      ctx.strokeText(dv.counts[i]+'×',bar.x,zy); ctx.fillStyle='#374151'; ctx.fillText(dv.counts[i]+'×',bar.x,zy);
    });
    ctx.restore();
  }};
  mk('chDelayVar',{type:'bar',data:{labels:dv.reasons,datasets:[
    {type:'bar',label:'Variance (act − exp)',data:dv.variance,backgroundColor:dv.variance.map(v=>v>0?'#e23b32':'#1f9e8b'),yAxisID:'y'}
  ]},options:{responsive:true,maintainAspectRatio:false,layout:{padding:{top:18,bottom:18}},plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>{const i=ctx.dataIndex;return (dv.variance[i]>0?'+':'')+dv.variance[i]+' h variance · act '+dv.actual[i]+' / exp '+dv.expected[i]+' h · '+dv.counts[i]+' events';}}}},scales:{x:{ticks:{...F9,maxRotation:70,minRotation:55,autoSkip:false}},y:{title:{display:true,text:'actual − expected (h)'},grid:{color:c=>c.tick.value===0?'#2b2f36':'rgba(0,0,0,0.06)'},ticks:{...F10,callback:v=>(v>0?'+':'')+v}}}},plugins:[dvLabels]});
}
function renderAnalytics(){
  const a=V().analytics;
  document.getElementById('tlsub').textContent='('+view+')';
  document.getElementById('chTl').innerHTML=drawTimeline(a.timeline);
  document.getElementById('ttlsub').textContent='('+view+')';
  document.getElementById('chTlTrk').innerHTML=drawTimeline(a.truckTimeline,{rowH:6,labelFont:6,compact:true});
}
function renderAppendix(){
  const a=SD().meta.appendix;
  document.getElementById('apxsub').textContent='('+SD().meta.shift+' · '+a.month+' '+a.date+')';
  const T=(hd,rows)=>`<table class="lanetab"><tr>${hd.map(x=>'<th>'+x+'</th>').join('')}</tr>${rows}</table>`;
  let h='';
  h+=`<h4 class="mini">Global constants &amp; shift parameters</h4>`+T(['Item','Value'],
    [['Payload target (trucks &amp; shovels)',a.payloadTarget+' t'],
     ['Shovel NOH floor (min load cycle)',a.nohFloorMin+' min'],
     ['Full-leg travel share FFULL (this shift)',a.ffull],
     ['Elapsed shift hours (NOH proration)',a.elapsed+' h'],
     ['Haulage / Loading score bands','&lt;90% red · 90–100% amber · ≥100% green'],
     ['Truck Balance band','±5% = Balanced'],['Truck Match band','±3 pts = Balanced'],
     ['Empty/Full distance ratio target','1.000'],
     ['Payload compliance window','±5% of '+a.payloadTarget+' t']].map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join(''));
  let av='';
  a.avail.forEach(x=>{
    av+=`<tr><td>${x.pit}</td><td>Cat 797</td><td>${x.PA797}</td><td>${x.UA797}</td><td>${x.OE797}</td><td>${x.NOH797}</td><td>${x.GOH797}</td><td>${fmt(x.TPNOH797)}</td></tr>`;
    av+=`<tr><td>${x.pit}</td><td>BE 495B</td><td>${x.PA495}</td><td>${x.UA495}</td><td>${x.OE495==null?'—':x.OE495}</td><td>${x.NOH495}</td><td>${x.GOH495}</td><td>${fmt(x.TPNOHCable)}</td></tr>`;
    av+=`<tr><td>${x.pit}</td><td>HIT 800</td><td>${x.PA8000}</td><td>${x.UA8000}</td><td>${x.OE8000==null?'—':x.OE8000}</td><td>${x.NOH8000}</td><td>${x.GOH8000}</td><td>${fmt(x.TPNOHHydro)}</td></tr>`;});
  h+=`<h4 class="mini">Availability &amp; rate budgets (per pit)</h4>`+T(['Pit','Unit','PA %','UA %','OE %','NOH h','GOH h','TPNOH t/h'],av)+
     `<div class="foot">Shovel OE is derived NOH ÷ GOH (no OE column in the budget); PA/UA are from the CSV.</div>`;
  h+=`<h4 class="mini">Truck cycle fixed-time budgets (min, per pit·material) — Fixed_Times</h4>`+
     T(['Pit','Material','Queue','Spot','Load (avg)','Load · Cable','Load · Hydraulic','Dump Idle','Dumping'],
       a.fixed.map(x=>`<tr><td>${x.pit}</td><td>${x.mat}</td><td>${x.queue}</td><td>${x.spot}</td><td>${x.load}</td><td>${x.loadCable}</td><td>${x.loadHydro}</td><td>${x.dumpidle}</td><td>${x.dumping}</td></tr>`).join(''))+
     `<div class="foot">The shovel <b>Load</b> time target is split by shovel type — <b>Cable ×0.94</b>, <b>Hydraulic ×1.24</b> of the averaged value — and each load is measured against its loading shovel's type throughout the dashboard.</div>`;
  h+=`<h4 class="mini">Shovel cycle budgets (seconds, per fleet·pit·material) — TPNOH-implied</h4>`+
     T(['Fleet','Pit','Material','TPNOH t/h','Mean payload t','Spot_b s','Load_b s','Hang_b s','Cycle_b s'],
       a.shovelCyc.map(x=>`<tr><td>${x.type}</td><td>${x.pit}</td><td>${x.mat}</td><td>${fmt(x.tpnoh)}</td><td>${x.prep}</td><td>${x.spot_b}</td><td>${x.load_b}</td><td>${x.hang_b}</td><td>${x.c_b}</td></tr>`).join(''))+
     `<div class="foot">Hang_b = (361 t target × 3600 ÷ TPNOH) − Spot_b − Load_b (clamped ≥ 0). Spot_b / Load_b = Fixed_Times × 60. "Mean payload" is shown for reference only — the budget cycle uses 361 t.</div>`;
  h+=`<h4 class="mini">Plan tonnes (per pit, this shift) — hourly / cumulative targets</h4>`+
     T(['Pit','Prod. Ore','Prod. Waste','Non-prod Ore','Non-prod Waste'],
       a.plan.map(x=>`<tr><td>${x.pit}</td><td>${fmt(x.POre)}</td><td>${fmt(x.PWst)}</td><td>${fmt(x.NPOre)}</td><td>${fmt(x.NPWst)}</td></tr>`).join(''));
  h+=`<h4 class="mini">Haul curve — budget TPNOH / cycle / travel by haul distance (per pit·material, buckets hauled this shift)</h4>`+
     `<div class="lanewrap">`+T(['Pit','Material','HD km','TPNOH t/h','Cycle min','Travel min'],
       a.curve.map(x=>`<tr><td>${x.pit}</td><td>${x.mat}</td><td>${x.hd}</td><td>${fmt(x.tpnoh)}</td><td>${x.cycle}</td><td>${x.travel}</td></tr>`).join(''))+`</div>`;
  document.getElementById('appendixBody').innerHTML=h;
}
let dsEquip='trucks';   // Delays & Standby: 'trucks' (Cat 797) or 'shovels' (BE 495B + HIT 8000)
function setDsEquip(k){dsEquip=k;
  document.querySelectorAll('#dsEquipToggle .tgl').forEach(b=>b.classList.toggle('on',b.getAttribute('data-eq')===k));
  renderDelays();}
function renderDelays(){
  const ds=V().delaysStandby||{}, d=ds[dsEquip]||{rows:[]}, rows=d.rows||[];
  const fl=document.getElementById('dsFleet'); if(fl)fl.textContent=(dsEquip==='shovels'?'BE 495B + HIT 8000 shovels':'Cat 797 trucks');
  const flt='('+view+' · '+(dsEquip==='shovels'?'shovels':'trucks')+')';
  const psub=document.getElementById('dsparsub'); if(psub)psub.textContent=flt;
  const dvsub=document.getElementById('dsdvsub'); if(dvsub)dvsub.textContent=flt;
  renderDsCharts();
  if(!rows.length&&!d.ua&&!d.oe){document.getElementById('dsBody').innerHTML='<div class="foot">No '+(dsEquip==='shovels'?'shovel':'truck')+' delay/standby data for this view.</div>';return;}
  const maxAbs=Math.max(1,...rows.map(r=>Math.abs(r.tonnes)));
  // shared ▲/▼ chip: good ≥ 0 → green ▲ ; < 0 → red ▼
  const dsChip=(good,txt)=>{const up=good>=0,c=up?'#2f7a44':'#b3382b';return `<span style="color:${c};font-weight:600">${up?'▲':'▼'} ${txt}</span>`;};
  let s=`<table class="dstab"><tr><th class="dsname">Delays and Standbys</th><th>Actual %</th><th>Target %</th><th>+/− tonnes</th></tr>`;
  // always show both groups — UA (Standby) on top, then OE (Delay) — headed by the metric's actual · budget · Δ
  [['Standby','UA',d.ua],['Delay','OE',d.oe]].forEach(([status,metric,m])=>{
    let hd=`<b>${metric}</b><span style="display:inline-block;width:14px"></span>`;   // space between metric and numbers
    if(m){hd+=`<b>${m.act.toFixed(1)}%</b> <span style="color:var(--muted);font-weight:600">${m.bud!=null?m.bud.toFixed(1)+'%':'—'}</span>`;
      if(m.bud!=null){const dl=m.act-m.bud;hd+=` ${dsChip(dl,(dl>=0?'+':'−')+Math.abs(dl).toFixed(1)+'%')}`;}}   // UA/OE: higher is better
    s+=`<tr class="dsgrp"><td colspan="4">${hd}</td></tr>`;
    const gr=rows.filter(r=>r.status===status);
    if(!gr.length){s+=`<tr><td colspan="4" class="foot" style="text-align:left">no ${status.toLowerCase()} reasons this view</td></tr>`;}
    gr.forEach(r=>{
      s+=`<tr><td class="dsname">${r.reason}</td>`+
         `<td><b>${r.actPct.toFixed(1)}%</b></td>`+
         `<td>${r.tgtPct==null?'':r.tgtPct.toFixed(1)+'%'}</td>`+
         `<td>${dsChip(r.tonnes,(r.tonnes>=0?'+':'−')+fmt(Math.abs(r.tonnes)))}</td></tr>`;});   // +tonnes = under target (gain) = green ▲
  });
  document.getElementById('dsBody').innerHTML=s+'</table>';
}
let dsShowPar=false, dsShowDv=false;   // Delays & Standby: show/hide the lost-time pareto and delay-variance charts
function renderDsCharts(){
  const A=V().analytics||{};
  const ps=document.getElementById('dsParSec'), dvs=document.getElementById('dsDvSec');
  if(ps&&!ps.hidden)drawParetoChart(A[dsEquip==='shovels'?'paretoShv':'paretoTrk']||{reasons:[],hours:[],cumpct:[],counts:[]});
  if(dvs&&!dvs.hidden)drawDelayVarChart(A[dsEquip==='shovels'?'delayVarShv':'delayVarTrk']||{reasons:[],actual:[],expected:[],variance:[],counts:[]});
}
function toggleDsChart(which){
  const map={par:['dsParSec','dsParBtn','Lost-Time Pareto'],dv:['dsDvSec','dsDvBtn','Delay Variance']};
  const [sid,bid,lbl]=map[which], sec=document.getElementById(sid), btn=document.getElementById(bid);
  if(which==='par')dsShowPar=!dsShowPar; else dsShowDv=!dsShowDv;
  const open=(which==='par')?dsShowPar:dsShowDv;
  sec.hidden=!open; if(btn)btn.innerHTML=lbl+' '+(open?'▴':'▾');
  if(open)renderDsCharts();
}
function renderHourlyPerf(){
  const d=V().hourlyPerf;
  if(!d){document.getElementById('hpBody').innerHTML='<div class="foot">No hourly production data for this view (dump timestamps needed).</div>';return;}
  const fmtV=(v,u)=>v==null?'':(u==='mmss'?fmtTime(v):(u==='#'?v:fmt(v)));
  let s=`<div class="hpwrap"><table class="hptab"><tr><th class="hpname">KPI</th><th>UOM</th><th>Total</th>`+
    d.hours.map(h=>`<th>${h}</th>`).join('')+`</tr>`;
  d.rows.forEach(r=>{
    const uom=r.uom==='mmss'?'mm:ss':r.uom;
    s+=`<tr><td class="hpname">${r.label}</td><td class="hpu">${uom}</td><td class="hptot">${fmtV(r.total,r.uom)}</td>`;
    r.vals.forEach(v=>{let bg='transparent';
      if(v!=null&&r.good&&r.budget){const dev=(v-r.budget)/r.budget,better=r.good==='high'?dev>=0:dev<=0;
        const op=Math.min(0.5,Math.abs(dev)*1.3).toFixed(2);
        bg=better?`rgba(106,168,79,${op})`:`rgba(204,75,75,${op})`;}
      s+=`<td style="background:${bg}">${fmtV(v,r.uom)}</td>`;});
    s+='</tr>';});
  document.getElementById('hpBody').innerHTML=s+'</table></div>';
}
const STATLINK={};   // Shift-Stats link buttons removed from the truck waterfalls
function renderShiftStats(){
  const d=V().shiftStats;
  if(!d||!d.tables){document.getElementById('ssBody').innerHTML='<div class="foot">No shift-stats data for this view.</div>';return;}
  const fc=(t,v)=>v==null?'—':(t==='mmss'?fmtTime(v):(t==='km'?v.toFixed(2)+' km':(t==='ton'?fmt(v)+' t':v)));
  let s=`<div class="ssbar"><b>Empty ÷ Full haul distance</b> <span class="ssbig2">${d.ratio.val.toFixed(3)}</span> <span class="foot">Σ empty ${fmt(d.ratio.empty)} m ÷ Σ full ${fmt(d.ratio.full)} m &nbsp;·&nbsp; values shaded vs budget: <span style="color:#2f8f4e">■</span> better · <span style="color:#c0392b">■</span> worse (hover for target)</span></div>`;
  d.tables.forEach(T=>{
    s+=`<div class="sstable" data-kpi="${T.kpis}"><div class="sshdr">${T.title}</div><div class="sstwrap"><table class="sst2"><tr>`;
    T.cols.forEach(c=>s+=`<th class="${c.t==='name'?'sstl':(c.t==='n'?'sstn':'')}">${c.name}</th>`);
    s+=`</tr>`;
    if(!T.rows.length) s+=`<tr><td colspan="${T.cols.length}" class="foot">no data</td></tr>`;
    let curMat=null;
    T.rows.forEach(r=>{
      if(r.mat&&r.mat!==curMat){curMat=r.mat; const mc=r.mat==='Waste'?CWASTE:CORE;
        s+=`<tr class="ssgrp"><td colspan="${T.cols.length}"><span style="color:${mc}">■</span> ${r.mat}</td></tr>`;}
      s+=`<tr><td class="sstl" title="${r.name}">${r.name}</td><td class="sstn">${r.n}</td>`;
      r.cells.forEach((cl,i)=>{const col=T.cols[i+2]; let bg='transparent';
        if(cl.v!=null&&cl.b&&col.good){const dev=(cl.v-cl.b)/cl.b, better=col.good==='high'?dev>=0:dev<=0;
          const op=Math.min(0.5,Math.abs(dev)*1.3).toFixed(2);
          bg=better?`rgba(74,175,80,${op})`:`rgba(226,59,50,${op})`;}
        s+=`<td class="sstv" style="background:${bg}" title="${col.name}: ${fc(col.t,cl.v)}  vs budget ${fc(col.t,cl.b)}">${fc(col.t,cl.v)}</td>`;});
      s+='</tr>';});
    s+=`</table></div></div>`;});
  document.getElementById('ssBody').innerHTML=s;
}
function gotoStat(kpi){
  setTab('shiftstats');
  setTimeout(()=>{const t=document.querySelectorAll('#ssBody .sstable'); let first=null;
    t.forEach(c=>{const m=(c.getAttribute('data-kpi')||'').split(' ').includes(kpi); c.classList.toggle('ss-hl',m); if(m&&!first)first=c;});
    if(first)first.scrollIntoView({behavior:'smooth',block:'center'});},50);
}
function renderShovProd(){ buildProdTable(V().shovelProd,'sp','No shovel productivity data for this view.'); }
// Shared productivity-table state + toggles (tp = Truck Productivity, sp = Shovel Productivity)
const PROD={tp:{tgt:true,dlt:true,body:'tpBody',render:()=>renderTruckProd()},
            sp:{tgt:true,dlt:true,body:'spBody',render:()=>renderShovProd()}};
function syncProdBtns(k){const P=PROD[k];
  const tb=document.getElementById(k+'TgtBtn'); if(tb)tb.classList.toggle('on',P.tgt);
  const db=document.getElementById(k+'DltBtn'); if(db)db.classList.toggle('on',P.dlt);}
function toggleProd(k,which){const P=PROD[k];
  if(which==='tgt')P.tgt=!P.tgt; else if(which==='dlt')P.dlt=!P.dlt;
  syncProdBtns(k); P.render(); saveState();}
function buildProdTable(d,k,emptyMsg){
  const P=PROD[k]; syncProdBtns(k);
  const tgtOn=P.tgt, dltOn=P.dlt;
  if(!d){document.getElementById(P.body).innerHTML=`<div class="foot">${emptyMsg}</div>`;return;}
  const cols=d.cols,rows=d.rows;
  const span=(tgtOn?1:0)+1+(dltOn?1:0);   // Target? + Actual + +/−t?
  const badge=s=>{if(s==null)return '<span class="spb spbn">—</span>';const c=s>=90?'#2f8f4e':(s>=70?'#c98a1f':'#c0392b');return `<span class="spb" style="background:${c}">${s.toFixed(1)} %</span>`;};
  const cell=(f,x)=>{if(x==null)return '';
    if(f==='tons'||f==='rate')return fmt(x);
    if(f==='pct'||f==='pct1')return x.toFixed(1);
    if(f==='hrs')return x.toFixed(1);
    if(f==='mmss'||f==='mmss_')return fmtTime(x);
    if(f==='ratio')return x.toFixed(3);
    return x;};
  const mx={}; cols.forEach(c=>{let m=1;rows.forEach(r=>{const v=r.vals[c.id];if(v&&v.d!=null)m=Math.max(m,Math.abs(v.d));});mx[c.id]=m;});
  // shading background from the +/− tonnes sign & magnitude (shared by the Actual and +/− t cells)
  const shade=(v,cid)=>{if(!v||v.d==null)return 'transparent';const op=Math.min(0.55,Math.abs(v.d)/mx[cid]).toFixed(3);
    return v.d>0?`rgba(106,168,79,${op})`:(v.d<0?`rgba(204,75,75,${op})`:'transparent');};
  let s=`<div class="spwrap"><table class="sptab"><tr class="sph1"><th class="spk" rowspan="2">KPI</th><th class="spu" rowspan="2">UOM</th>`;
  cols.forEach((c,ci)=>{s+=`<th colspan="${span}" class="${ci>0?'spsep':''}"><div class="spgh"><span>${c.label}${c.type?' · '+c.type:''}</span>${badge(c.score)}</div></th>`;});
  s+=`</tr><tr class="sph2">`;
  cols.forEach((c,ci)=>{s+=`${tgtOn?`<th class="${ci>0?'spsep':''}">Target</th>`:''}<th class="${(ci>0&&!tgtOn)?'spsep':''}">Actual</th>${dltOn?`<th>+/− t</th>`:''}`;});
  s+=`</tr>`;
  rows.forEach(r=>{
    s+=`<tr><td class="spk" style="padding-left:${8+r.indent*14}px">${r.label}</td><td class="spu">${r.uom}</td>`;
    cols.forEach((c,ci)=>{const v=r.vals[c.id]||{};
      let dbg='transparent',dtx='';
      if(r.tons&&v.d!=null){dbg=shade(v,c.id); dtx=(v.d>0?'+':'')+fmt(v.d);}
      const tgtCell=tgtOn?`<td class="spnum${ci>0?' spsep':''}">${cell(r.fmt,v.t)}</td>`:'';
      const actCell=`<td class="spnum spact${(ci>0&&!tgtOn)?' spsep':''}" style="background:${dbg}">${cell(r.fmt,v.a)}</td>`;
      const dltCell=dltOn?`<td class="spd" style="background:${dbg}">${dtx}</td>`:'';
      s+=tgtCell+actCell+dltCell;});
    s+='</tr>';});
  document.getElementById(P.body).innerHTML=s+'</table></div>';
}
function renderTruckProd(){ buildProdTable(V().truckProd,'tp','No truck productivity data for this view.'); }
const PLAYBOOK_GAP_LIBRARY = __PLAYBOOK_GAP_LIBRARY__;
function renderPlaybook(){
  const el=document.getElementById('playbookBody');
  if(!el)return;
  const escH=s=>String(s==null?'':s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
  const escA=s=>escH(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const msgKeys=Object.keys(PLAYBOOK_GAP_LIBRARY||{}).filter(k=>PLAYBOOK_GAP_LIBRARY[k]&&PLAYBOOK_GAP_LIBRARY[k].detail);
  const pCol={1:'#e23b32',2:'#b85c00',3:'#e0a41f'};
  const pLbl={1:'High',2:'Medium',3:'Low'};
  const recs=(V()&&V().shiftRecs)||[];
  const sc=viewScores(V());
  const ana=(V()&&V().analytics)||{};
  const cumAct=(ana.cumulative&&ana.cumulative.actual)||[];
  const latestAct=cumAct.reduce((a,v)=>(v!=null?v:a),null);
  const cumTarget=(ana.cumulative&&ana.cumulative.target)||[];
  const totalPlan=(ana.cumulative&&ana.cumulative.plan!=null)?ana.cumulative.plan:null;
  // Plan-to-now = target value at the same 15-min slot as the latest actual reading
  let _latestPlanIdx=-1; cumAct.forEach((v,i)=>{if(v!=null)_latestPlanIdx=i;});
  const latestPlan=(_latestPlanIdx>=0&&cumTarget[_latestPlanIdx]!=null)?cumTarget[_latestPlanIdx]:null;
  const fmt2=v=>(v==null?'—':Math.round(v).toLocaleString());
  const clr=v=>(v==null?'#888':v>=100?'#2f7a44':v>=90?'#b8830a':'#c0392b');
  const CHALLENGE_PRIORITY_TPH={1:2000,2:1000};
  const FULL_SHIFT_HOURS=12;
  const HIGH_PRIORITY_PROJECTED_SHIFT_T=30000;
  function elapsedHoursFromSeries(series,fallbackSlots){
    let last=-1;
    (series||[]).forEach((v,i)=>{ if(v!=null) last=i; });
    if(last>=0) return Math.max(0.25,(last+1)*0.25);
    const slots=Number(fallbackSlots)||48;
    return Math.max(1,slots*0.25);
  }
  const challengeHours=elapsedHoursFromSeries(cumAct,(cumTarget&&cumTarget.length)?cumTarget.length:48);
  const SHOV_WF_ROWS=[['Payload','Payload'],['Spot','Spot at Shovel'],['Load','Load Time'],['Hang','Hang Time']];
  const TRUCK_WF_ROWS=ORDER.map(k=>[k,ROWLABEL[k]||k]);
  function asRiskTon(v){const n=Number(v||0); return Number.isFinite(n)&&n<0?Math.abs(n):0;}
  function challengePriority(tons){
    const tph=(Number(tons)||0)/challengeHours;
    const projectedFullShiftLoss=tph*FULL_SHIFT_HOURS;
    if(tph>=CHALLENGE_PRIORITY_TPH[1] || projectedFullShiftLoss>=HIGH_PRIORITY_PROJECTED_SHIFT_T) return {priority:1,tph,projectedFullShiftLoss};
    if(tph>=CHALLENGE_PRIORITY_TPH[2]) return {priority:2,tph};
    return {priority:null,tph};
  }
  function top3CulpritsText(cmap){
    const ent=Object.entries(cmap||{}).filter(x=>x[0]&&Number(x[1])>0).sort((a,b)=>Number(b[1])-Number(a[1])).slice(0,3);
    return ent.length?ent.map(([k,v])=>`${k} (${fmt(Math.round(v))} t)`).join(' · '):'—';
  }
  function buildNegRows(kind,rowDefs,tabId,wfKey){
    const byKey={};
    const vv=V();
    const wf=vv?vv[wfKey]:null;
    if(!wf) return [];
    const av=wf.availDecomp||null;
    if(av){
      [['PA','pa'],['UA','ua'],['OE','oe']].forEach(([label,key])=>{
        const totalDelta=Number((av[key]&&av[key].t)||0);
        const totalRisk=asRiskTon(totalDelta);
        if(!totalRisk) return;
        const k='AV::'+label;
        if(!byKey[k]) byKey[k]={measure:label,tonnes_at_risk:0,culprit:{},tab:tabId};
        byKey[k].tonnes_at_risk+=totalRisk;
        if(kind==='Shovel'){
          const units=(wf.units||[]);
          let assigned=0;
          units.forEach(u=>{
            const d=Number((u.availDecomp&&u.availDecomp[key]&&u.availDecomp[key].t)||0);
            const risk=asRiskTon(d);
            if(!risk) return;
            const su=String(u.unit||'Unknown');
            byKey[k].culprit[su]=(byKey[k].culprit[su]||0)+risk;
            assigned+=risk;
          });
          if(assigned<=0){
            const potSum=units.reduce((a,u)=>a+Math.max(0,Number(u.potential||0)),0);
            units.forEach(u=>{
              const su=String(u.unit||'Unknown');
              const sh=(potSum>0?Math.max(0,Number(u.potential||0))/potSum:0);
              byKey[k].culprit[su]=(byKey[k].culprit[su]||0)+(totalRisk*sh);
            });
          }
        }else{
          const lanes=(wf.lanes||[]);
          const potSum=lanes.reduce((a,l)=>a+Math.max(0,Number(l.pot||0)),0);
          lanes.forEach(l=>{
            const su=String(l.shovel||'Unknown');
            const sh=(potSum>0?Math.max(0,Number(l.pot||0))/potSum:0);
            if(sh<=0) return;
            byKey[k].culprit[su]=(byKey[k].culprit[su]||0)+(totalRisk*sh);
          });
        }
      });
    }
    rowDefs.forEach(([rk,label])=>{
      const totalDelta=Number((wf.rows&&wf.rows[rk])||0);
      const totalRisk=asRiskTon(totalDelta);
      if(!totalRisk) return;
      const k='ROW::'+label;
      if(!byKey[k]) byKey[k]={measure:label,tonnes_at_risk:0,culprit:{},tab:tabId};
      byKey[k].tonnes_at_risk+=totalRisk;
      if(kind==='Shovel'){
        (wf.units||[]).forEach(u=>{
          const d=Number((u.rows&&u.rows[rk])||0);
          const risk=asRiskTon(d);
          if(!risk) return;
          const su=String(u.unit||'Unknown');
          byKey[k].culprit[su]=(byKey[k].culprit[su]||0)+risk;
        });
      }else{
        (wf.lanes||[]).forEach(l=>{
          const d=Number((l.rows&&l.rows[rk])||0);
          const risk=asRiskTon(d);
          if(!risk) return;
          const su=String(l.shovel||'Unknown');
          byKey[k].culprit[su]=(byKey[k].culprit[su]||0)+risk;
        });
      }
    });
    return Object.values(byKey)
      .map(r=>({
        ...challengePriority(r.tonnes_at_risk),
        area:kind+' Waterfall',
        measure:r.measure,
        detail:'',
        actual_label:'',
        baseline_label:'',
        gap_label:'−'+fmt(r.tonnes_at_risk)+' t',
        tonnes_at_risk:r.tonnes_at_risk,
        culprit:top3CulpritsText(r.culprit||{}),
        tab:r.tab
      }))
      .filter(r=>r.priority!=null)
      .sort((a,b)=>(a.priority-b.priority)||((b.tonnes_at_risk||0)-(a.tonnes_at_risk||0)));
  }
  function challengeTable(title,rows,borderColor){
    let s=`<div style="margin-bottom:14px"><div style="font-weight:700;font-size:13px;color:${borderColor};padding:8px 6px 6px;background:#f8f9fb;border-top:2px solid ${borderColor}">${title}</div>`;
    if(!rows.length) return s+`<div class="foot" style="padding:8px 6px">No affected KPIs at or above ${fmt(CHALLENGE_PRIORITY_TPH[2])} t/h for the selected shift.</div></div>`;
    s+=`<table class="lanetab" style="width:100%"><thead><tr>`
      +`<th style="text-align:left">Affected KPI</th>`
      +`<th style="text-align:left;white-space:nowrap">Priority</th>`
      +`<th style="text-align:left;min-width:220px">Top 3 Culprit Shovels</th>`
      +`<th style="text-align:right;white-space:nowrap;min-width:100px">Gap</th><th style="width:60px"></th>`
      +`</tr></thead><tbody>`;
    rows.forEach(r=>{
      const genPayload=encodeURIComponent(JSON.stringify({
        area:r.area||'', measure:r.measure||'', detail:r.detail||'', tab:r.tab||'',
        actual_label:r.actual_label||'', baseline_label:r.baseline_label||'',
        gap_label:r.gap_label||'', tonnes_at_risk:(r.tonnes_at_risk!=null?r.tonnes_at_risk:''), priority:r.priority||''
      }));
      s+=`<tr>
        <td style="font-size:13px;text-align:left">${escH(r.measure)}</td>
        <td style="font-size:13px;text-align:left;white-space:nowrap"><span style="font-weight:700;color:${pCol[r.priority]||'#666'}">${escH(pLbl[r.priority]||'')}</span><span style="color:var(--muted)"> (${fmt(Math.round(r.tph||0))} t/h)</span></td>
        <td style="font-size:13px;text-align:left">${escH(r.culprit||'—')}</td>
        <td style="text-align:right;font-weight:700;color:${pCol[r.priority]||'#b71c1c'};font-size:13px;white-space:nowrap">${escH(r.gap_label)}</td>
        <td style="text-align:center"><button class="tlbtn" onclick="pbS3GenerateAction('${genPayload}')">Generate Action</button></td>
      </tr>`;
    });
    return s+`</tbody></table></div>`;
  }
  let h='';

  // =========================================================
  // SECTION 1 — SHIFT HANDOVER & DAILY EXECUTION PLAN
  // =========================================================
  const shovChallenges=buildNegRows('Shovel',SHOV_WF_ROWS,'loading','shovelWF2');
  const truckChallenges=buildNegRows('Truck',TRUCK_WF_ROWS,'trucks','trucksWF');
  h+=`<div id="playbook-shift-handover" style="margin-bottom:24px">`;
  h+=`<h3 style="margin:0 0 6px;font-size:20px;color:#2b2f36;border-bottom:2px solid #3f51b5;padding-bottom:6px">Shift Challenges Summary</h3>`;
  h+=`<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start">`;
  h+=challengeTable('Shovel Challenges Observed',shovChallenges,'#1d6fa4');
  h+=challengeTable('Truck Challenges Observed',truckChallenges,'#8a2c22');
  h+=`</div>`;

  h+=`</div>`;

  // =========================================================
  // SECTION 3 — MASTER TRACKING SCHEMA
  // =========================================================
  h+=`<div style="margin-bottom:24px" id="pb-s3">`;
  h+=`<h3 style="margin:0 0 6px;font-size:20px;color:#2b2f36;border-bottom:2px solid #2f7a44;padding-bottom:6px">&#128203;&nbsp; Logged Action Items</h3>`;

  /* ---- action controls ---- */
  h+=`<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px">`;
  h+=`<button type="button" class="tlbtn" onclick="pbS3OpenModal()" style="padding:8px 18px;font-size:13px;background:#2f7a44;color:#fff;border:none;border-radius:6px;cursor:pointer">&#43; Log New Action Item</button>`;
  h+=`<button type="button" class="tlbtn" onclick="pbS3ExportCsv()" style="padding:8px 18px;font-size:13px;background:#fff;color:#2f7a44;border:1px solid #2f7a44;border-radius:6px;cursor:pointer">Export Actions CSV</button>`;
  h+=`<button type="button" id="pb-s3-open-toggle" class="tlbtn" onclick="pbS3ToggleOpenAll()" style="padding:8px 18px;font-size:13px;background:#fff;color:#1d6fa4;border:1px solid #1d6fa4;border-radius:6px;cursor:pointer">Show All Open Actions</button>`;
  h+=`<span id="pb-s3-msg" style="font-size:12px;color:#2f7a44;display:none">&#10003; Action item logged.</span>`;
  h+=`</div>`;
  h+=`<div style="margin:0 0 10px;font-size:13px;color:var(--muted)">This section keeps a running register of previously entered actions, with a concise summary, owner and current status.</div>`;

  /* ---- popup entry form ---- */
  h+=`<div id="pb-s3-modal" style="display:none;position:fixed;inset:0;background:rgba(33,39,49,.45);z-index:9999;padding:24px;overflow:auto">`;
  h+=`<div style="max-width:980px;margin:30px auto;background:#fff;border-radius:12px;border:1px solid #d9dfe8;box-shadow:0 18px 46px rgba(18,28,45,.18);overflow:hidden">`;
  h+=`<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;border-bottom:1px solid #e7ebf1;background:#f8f9fb">
    <div><div id="pb-s3-modal-title" style="font-size:16px;font-weight:700;color:#2b2f36">Log New Action Item</div><div id="pb-s3-modal-subtitle" style="font-size:12px;color:#6d7683">Capture the full master-tracking record, then save it into the action register.</div></div>
    <button type="button" class="tlbtn" onclick="pbS3CloseModal()" style="font-size:20px;line-height:1;color:#667085;background:transparent;border:none;cursor:pointer;padding:0 2px">&times;</button>
  </div>`;
  h+=`<form id="pb-s3-form" onsubmit="pbS3Submit(event)" style="padding:16px 18px;background:#fff">`;

  /* ---- field-reference guide (collapsible) ---- */
  let dataEntryGuide=`<details style="margin:8px 0 16px;border:1px solid #d9dfe8;border-radius:8px;background:#fbfcfe">`;
  dataEntryGuide+=`<summary style="cursor:pointer;list-style:none;padding:10px 12px;font-size:13px;font-weight:700;color:#2b2f36;display:flex;align-items:center;gap:8px">`;
  dataEntryGuide+=`<span style="font-size:14px;line-height:1">&#9656;</span><span>Review data entry information</span>`;
  dataEntryGuide+=`</summary>`;
  dataEntryGuide+=`<div style="padding:0 10px 10px;overflow-x:auto">`;
  dataEntryGuide+=`<table style="width:100%;border-collapse:collapse;font-size:12px;color:#2b2f36">`;
  dataEntryGuide+=`<thead><tr style="border-bottom:2px solid #e7ebf1;background:#f8f9fb">`;
  dataEntryGuide+=`<th style="text-align:left;padding:7px 10px;font-weight:600;color:#6d7683;white-space:nowrap">Field Category</th>`;
  dataEntryGuide+=`<th style="text-align:left;padding:7px 10px;font-weight:600;color:#6d7683;white-space:nowrap">Column Name</th>`;
  dataEntryGuide+=`<th style="text-align:left;padding:7px 10px;font-weight:600;color:#6d7683;white-space:nowrap">Data Type / Validation</th>`;
  dataEntryGuide+=`<th style="text-align:left;padding:7px 10px;font-weight:600;color:#6d7683">Purpose</th>`;
  dataEntryGuide+=`</tr></thead><tbody>`;
  const refRows=[
    ["Context","Mine","Dropdown (MRM, JPM)","Identifies the mine pit where the action applies."],
    ["","Shift ID","Dropdown (available dashboard shifts)","Links the action to a specific shift record."],
    ["","Interval ID","Dropdown (06:00–08:00, 08:00–10:00, etc.)","Isolates when the bottleneck occurred."],
    ["","Asset / Area ID","Text / Dropdown (e.g., SHV-02, CRUSH-01)","Pinpoints the exact piece of equipment or pit zone."],
    ["Activity","Deviation Observed","Short Text","The problem statement (e.g., Truck queue exceeds 15 mins)."],
    ["","Corrective Action","Imperative Sentence","The exact directive issued to fix the variance."],
    ["Ownership","Action Owner","Single Name / Role Pin","The one specific person accountable for execution."],
    ["","Support Resource","Text / Dropdown (Optional)","Secondary teams called to help (e.g., Maintenance, Dozers)."],
    ["Outcome","SLA Deadline","Timestamp (Interval End + 30 Mins)","The hard cutoff time before automatic escalation."],
    ["","Resolution Status","Dropdown (Open, In-Progress, Closed, Escalated)","Real-time status of the fix."],
    ["","Status Changed At","Date/Time (auto-filled)","Timestamp when the action item's status was last updated."],
    ["","Root-Cause Code","Dropdown (Standardised list)","Used for end-of-month engineering audits."],
    ["","Final Production Impact","Numeric (Tons, Meters, or Hours)","Quantifiable result of the intervention."],
    ["","Date Created","Date/Time","Timestamp when the action was logged."],
  ];
  refRows.forEach(([cat,col,dtype,purpose],i)=>{
    const bg=i%2===0?'#ffffff':'#f8f9fb';
    const catCell=cat?`<td style="padding:6px 10px;vertical-align:top;font-weight:700;white-space:nowrap">${cat}</td>`:`<td style="padding:6px 10px"></td>`;
    dataEntryGuide+=`<tr style="border-bottom:1px solid #e7ebf1;background:${bg}">${catCell}`;
    dataEntryGuide+=`<td style="padding:6px 10px;vertical-align:top;font-weight:700;white-space:nowrap">${col}</td>`;
    dataEntryGuide+=`<td style="padding:6px 10px;vertical-align:top;color:#1d6fa4;white-space:nowrap">${dtype}</td>`;
    dataEntryGuide+=`<td style="padding:6px 10px;vertical-align:top;color:#c0550c">${purpose}</td>`;
    dataEntryGuide+=`</tr>`;
  });
  dataEntryGuide+=`</tbody></table></div></details>`;
  h+=`<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:12px">`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Mine <span style="font-weight:400;color:#888">(Context)</span>
    <select name="mine" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>MRM</option><option>JPM</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Shift ID <span style="font-weight:400;color:#888">(Context)</span>
    <select name="shiftId" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      ${DATA.shifts.map(s=>`<option value="${s.id}">${s.id}${s.name?` · ${s.name}`:''}</option>`).join('')}
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Interval ID <span style="font-weight:400;color:#888">(Context)</span>
    <select name="intervalId" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>06:00–08:00</option><option>08:00–10:00</option><option>10:00–12:00</option>
      <option>12:00–14:00</option><option>14:00–16:00</option><option>16:00–18:00</option>
      <option>18:00–20:00</option><option>20:00–22:00</option><option>22:00–00:00</option>
      <option>00:00–02:00</option><option>02:00–04:00</option><option>04:00–06:00</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Asset / Area ID <span style="font-weight:400;color:#888">(Context)</span>
    <input type="text" name="assetId" placeholder="e.g. SHV-02, CRUSH-01" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`</div>`;

  h+=`<div style="display:grid;grid-template-columns:1fr;gap:12px;margin-bottom:12px">`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Deviation Observed <span style="font-weight:400;color:#888">(Activity)</span>
    <input type="text" name="deviation" placeholder="e.g. Truck queue exceeds 15 mins" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Corrective Action <span style="font-weight:400;color:#888">(Activity)</span>
    <input type="text" name="corrective" placeholder="Imperative directive to fix the variance" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`</div>`;

  h+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Action Owner <span style="font-weight:400;color:#888">(Ownership)</span>
    <input type="text" name="owner" placeholder="Full name or role" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Support Resource <span style="font-weight:400;color:#888">(Ownership — optional)</span>
    <input type="text" name="support" placeholder="e.g. Maintenance, Dozers" style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`</div>`;

  h+=`<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr 1fr;gap:12px;margin-bottom:16px">`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Date Created
    <input type="datetime-local" name="dateCreated" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">SLA Deadline <span style="font-weight:400;color:#888">(Outcome)</span>
    <input type="datetime-local" name="slaDl" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Resolution Status <span style="font-weight:400;color:#888">(Outcome)</span>
    <select name="status" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>Open</option><option>In-Progress</option><option>Closed</option><option>Escalated</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Status Changed At <span style="font-weight:400;color:#888">(Auto)</span>
    <input type="datetime-local" name="statusChangedAt" readonly style="padding:6px 8px;border:1px solid #d8dee8;border-radius:6px;font-size:13px;background:#f7f9fc;color:#5b6573">
  </label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Root-Cause Code <span style="font-weight:400;color:#888">(Outcome)</span>
    <select name="rootCause" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>Equipment Breakdown</option><option>Operator Delay</option><option>Scheduling Conflict</option>
      <option>Road / Haul Route</option><option>Crusher / Dump Queue</option><option>Shovel Hang Time</option>
      <option>Fuel / Lube Delay</option><option>Weather</option><option>Other</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Final Production Impact
    <div style="display:flex;gap:4px;align-items:center;margin-top:4px">
      <input type="number" name="impactVal" placeholder="0" min="0" step="any" style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;width:0;flex:1">
      <select name="impactUnit" style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
        <option>Tons</option><option>Meters</option><option>Hours</option>
      </select>
    </div></label>`;
  h+=`</div>`;
  h+=dataEntryGuide;

  h+=`<div style="display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap">`;
  h+=`<button type="button" class="tlbtn" onclick="pbS3CloseModal()" style="padding:8px 18px;font-size:13px;background:#fff;color:#5b6573;border:1px solid #cfd4dd;border-radius:6px;cursor:pointer">Cancel</button>`;
  h+=`<button id="pb-s3-submit-btn" type="submit" class="tlbtn" style="padding:8px 22px;font-size:13px;background:#2f7a44;color:#fff;border:none;border-radius:6px;cursor:pointer">&#43; Add Action Item</button>`;
  h+=`</div>`;
  h+=`</form></div></div>`;

  /* ---- logged items summary ---- */
  h+=`<div id="pb-s3-list" style="margin-top:10px"></div>`;
  h+=`</div>`;

  el.innerHTML='<div id="playbook-layout" style="display:flex;flex-direction:column">'+h+'</div>';
  const layout=document.getElementById('playbook-layout');
  if(layout){
    const handover=document.getElementById('playbook-shift-handover');
    const actions=document.getElementById('pb-s3');
    [handover,actions].forEach(node=>{ if(node) layout.appendChild(node); });
  }
  initPbS3ActionRegister();
}
function initPbS3ActionRegister(){
  const STORAGE_KEY='albianPbS3ItemsV1';
  const OPEN_FILTER_KEY='albianPbS3OpenFilterV1';
  const CSV_FIELDS=__MASTER_TRACKING_ACTION_FIELDS__;
  window._pbS3SeedItems=Array.isArray(__MASTER_TRACKING_ACTIONS__)?__MASTER_TRACKING_ACTIONS__:[];

  function esc(v){
    return String(v==null?'':v).replace(/[&<>"']/g,function(ch){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
    });
  }

  function csvEsc(v){
    return '"'+String(v==null?'':v).replace(/"/g,'""')+'"';
  }

  function normalizeItem(item){
    const out={};
    CSV_FIELDS.forEach(function(key){ out[key]=String(item&&item[key]!=null?item[key]:'').trim(); });
    return out;
  }

  function saveItems(){
    try{localStorage.setItem(STORAGE_KEY, JSON.stringify(window._pbS3Items));}catch(e){}
  }

  function loadItems(){
    try{
      const raw=localStorage.getItem(STORAGE_KEY);
      if(raw!=null){
        const parsed=JSON.parse(raw);
        if(Array.isArray(parsed)) return parsed.map(normalizeItem);
      }
    }catch(e){}
    return window._pbS3SeedItems.map(normalizeItem);
  }

  function formatSla(v){
    return esc(String(v||'').replace('T',' ')) || '—';
  }

  function statusBadge(s){
    const m={Open:'#e23b32','In-Progress':'#b85c00',Closed:'#2f7a44',Escalated:'#7b1fa2'};
    return '<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;color:#fff;background:'+(m[s]||'#888')+'">'+esc(s||'Unknown')+'</span>';
  }
  function isOpenStatus(s){
    const v=String(s||'').trim().toLowerCase();
    return v==='open' || v==='in-progress' || v==='escalated';
  }
  function shiftDateKey(sid){
    const s=String(sid||'').trim();
    if(!/^\d{9}$/.test(s)) return '';
    const yy=s.slice(0,2), mm=s.slice(2,4), dd=s.slice(4,6);
    return `20${yy}-${mm}-${dd}`;
  }
  function dtDateKey(v){
    const s=String(v||'').trim();
    if(!s) return '';
    const m=s.match(/^(\d{4}-\d{2}-\d{2})/);
    if(m) return m[1];
    const d=new Date(s);
    if(!isNaN(d.getTime())){
      const p=n=>String(n).padStart(2,'0');
      return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
    }
    return '';
  }
  function itemDateKey(item){
    return shiftDateKey(item.shiftId) || dtDateKey(item.dateCreated);
  }
  function selectedShiftDateKey(){
    return shiftDateKey(shift);
  }
  function setOpenToggleUI(){
    const b=document.getElementById('pb-s3-open-toggle');
    if(!b) return;
    if(window._pbS3ShowOpenAll){
      b.textContent='Showing Open Actions (All Dates)';
      b.style.background='#1d6fa4';
      b.style.color='#fff';
      b.style.borderColor='#1d6fa4';
    }else{
      b.textContent='Show All Open Actions';
      b.style.background='#fff';
      b.style.color='#1d6fa4';
      b.style.borderColor='#1d6fa4';
    }
  }

  function mineBadge(m){
    if(!m) return '';
    const c=m==='MRM'?'#1d6fa4':m==='JPM'?'#7b1fa2':'#555';
    return '<span style="display:inline-block;padding:1px 7px;border-radius:8px;font-size:11px;font-weight:700;color:#fff;background:'+c+';margin-right:4px">'+esc(m)+'</span>';
  }

  function summaryText(item){
    return [
      item.deviation || 'No deviation recorded',
      item.corrective ? ('Action: '+item.corrective) : '',
      item.assetId ? ('Asset: '+item.assetId) : '',
      item.intervalId ? ('Window: '+item.intervalId) : ''
    ].filter(Boolean).join(' · ');
  }

  function setFormValues(f, item){
    CSV_FIELDS.forEach(function(key){
      const el=f.elements[key];
      if(!el) return;
      if(el.tagName==='SELECT') el.value=item[key]||'';
      else el.value=item[key]||'';
    });
  }
  function dtLocal(d){
    const p=n=>String(n).padStart(2,'0');
    return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes());
  }
  function nextSla(d){
    return dtLocal(new Date(d.getTime()+90*60000));
  }
  function inferInterval(d){
    const h=d.getHours();
    const start=Math.floor(h/2)*2;
    const end=(start+2)%24;
    const p=n=>String(n).padStart(2,'0');
    return `${p(start)}:00–${p(end)}:00`;
  }
  function inferMine(area){
    if(view==='MRM'||view==='JPM') return view;
    const a=String(area||'').toUpperCase();
    if(a.includes('MRM')) return 'MRM';
    if(a.includes('JPM')) return 'JPM';
    return 'MRM';
  }
  function inferOwner(tab){
    const m={loading:'Mine Operations Supervisor',haulage:'Fleet Coordinator',trucks:'Maintenance Lead',balance:'Dispatch Supervisor'};
    return m[String(tab||'').toLowerCase()]||'Mine Operations Supervisor';
  }
  function inferSupport(tab){
    const m={loading:'Shovel Operators',haulage:'Road Crew',trucks:'Crusher Crew',balance:'Dispatch Team'};
    return m[String(tab||'').toLowerCase()]||'Operations Team';
  }
  function inferRootCause(tab){
    const m={loading:'Shovel Hang Time',haulage:'Road / Haul Route',trucks:'Crusher / Dump Queue',balance:'Scheduling Conflict'};
    return m[String(tab||'').toLowerCase()]||'Other';
  }
  function parseImpact(v){
    const n=Number(String(v==null?'':v).replace(/[^0-9.-]/g,''));
    return isFinite(n)&&n>0?String(Math.round(n)):'';
  }
  function buildGeneratedAction(rec){
    const now=new Date();
    const deviation=[
      rec.measure||'Performance gap identified',
      rec.gap_label?`gap ${rec.gap_label}`:'',
      (rec.actual_label||rec.baseline_label)?`(Actual ${rec.actual_label||'—'} vs Budget ${rec.baseline_label||'—'})`:''
    ].filter(Boolean).join(' ');
    return normalizeItem({
      mine:inferMine(rec.area),
      shiftId:shift||'',
      intervalId:inferInterval(now),
      assetId:rec.area||'Shift Area',
      deviation:deviation,
      corrective:rec.detail||'Review operating conditions and execute corrective plan.',
      owner:inferOwner(rec.tab),
      support:inferSupport(rec.tab),
      slaDl:nextSla(now),
      status:'Open',
      statusChangedAt:dtLocal(now),
      rootCause:inferRootCause(rec.tab),
      impactVal:parseImpact(rec.tonnes_at_risk),
      impactUnit:'Tons',
      dateCreated:dtLocal(now),
    });
  }

  function renderList(){
    const el=document.getElementById('pb-s3-list');
    if(!el) return;
    if(!window._pbS3Items.length){
      el.innerHTML='<div style="padding:14px 16px;border:1px dashed #cfd6e1;border-radius:10px;background:#fbfcfe;color:#6b7280">No action items logged yet. Use <b>Log New Action Item</b> to create the first record.</div>';
      return;
    }
    // Default filter: selected mine + selected shift date.
    // Optional filter: all OPEN actions across all dates/mines.
    const selDate=selectedShiftDateKey();
    const visItems=window._pbS3Items.map(function(item,i){return{item,i};}).filter(function(x){
      if(window._pbS3ShowOpenAll) return isOpenStatus(x.item.status);
      const mineOk=(view==='Combined'||!x.item.mine||x.item.mine===view);
      const dateOk=(selDate?itemDateKey(x.item)===selDate:true);
      return mineOk && dateOk;
    });
    const mineLabel=view==='Combined'?'All Mines':view;
    const scopeLabel=window._pbS3ShowOpenAll?('Open Actions — All Mines · all dates'):`${mineLabel} · ${selDate||'Selected shift date'}`;
    let t='<h4 style="margin:0 0 8px;font-size:14px;color:#2b2f36">Action Register — '+esc(scopeLabel)+' ('+visItems.length+(visItems.length!==window._pbS3Items.length?' of '+window._pbS3Items.length:'')+' items)</h4>';
    if(!visItems.length){
      el.innerHTML=t+'<div style="padding:14px 16px;border:1px dashed #cfd6e1;border-radius:10px;background:#fbfcfe;color:#6b7280">'+
        (window._pbS3ShowOpenAll
          ? 'No open action items found across all dates/mines.'
          : 'No action items found for the selected shift date/view. Use <b>Show All Open Actions</b> to review open items across all dates.')+
        '</div>';
      return;
    }
    t+='<div style="overflow-x:auto"><table class="lanetab" style="width:100%"><thead><tr>'
      +'<th>#</th><th>Mine</th><th>Shift ID</th><th>Action Summary</th><th>Ownership</th><th>Status</th><th>Status Changed</th><th>SLA Deadline</th><th>Date Created</th><th></th>'
      +'</tr></thead><tbody>';
    visItems.forEach(function(x,row){
      const item=x.item, origIdx=x.i;
      const ownerBlock='<div style="font-weight:600;white-space:nowrap">'+esc(item.owner||'—')+'</div>'
        +(item.support?'<div style="font-size:12px;color:var(--muted)">Support: '+esc(item.support)+'</div>':'');
      t+='<tr style="cursor:pointer" onclick="pbS3ViewItem('+origIdx+')" title="Click to view / edit">'
        +'<td style="color:var(--muted)">'+(row+1)+'</td>'
        +'<td>'+mineBadge(item.mine)+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+esc(item.shiftId||'—')+'</td>'
        +'<td><div style="font-weight:600;color:#2b2f36;margin-bottom:2px">'+esc(item.assetId||'Unassigned asset')+'</div><div style="font-size:12px;line-height:1.45">'+esc(summaryText(item))+'</div><div style="font-size:11px;color:var(--muted);margin-top:4px">Root cause: '+esc(item.rootCause||'—')+' · Impact: '+esc(item.impactVal?(item.impactVal+' '+(item.impactUnit||'')):'—')+'</div></td>'
        +'<td>'+ownerBlock+'</td>'
        +'<td>'+statusBadge(item.status)+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+formatSla(item.statusChangedAt)+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+formatSla(item.slaDl)+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+formatSla(item.dateCreated)+'</td>'
        +'<td onclick="event.stopPropagation()"><button type="button" class="tlbtn" style="color:#c0392b" onclick="pbS3Delete('+origIdx+')">✕</button></td>'
        +'</tr>';
    });
    t+='</tbody></table></div>';
    t+='<div style="margin-top:6px;font-size:11px;color:var(--muted)">Click a row to view or edit that action.</div>';
    el.innerHTML=t;
  }

  if(window._pbS3Init){
    renderList();
    return;
  }

  window._pbS3Init=true;
  window._pbS3Items=loadItems();
  window._pbS3ShowOpenAll=false;
  try{window._pbS3ShowOpenAll=localStorage.getItem(OPEN_FILTER_KEY)==='1';}catch(e){}
  window._pbS3EditIdx=-1;   // -1 = new item, >=0 = editing existing

  function setModalMode(editIdx){
    window._pbS3EditIdx=editIdx;
    const isEdit=editIdx>=0;
    const titleEl=document.getElementById('pb-s3-modal-title');
    const subtitleEl=document.getElementById('pb-s3-modal-subtitle');
    const submitBtn=document.getElementById('pb-s3-submit-btn');
    if(titleEl) titleEl.textContent=isEdit?'View / Edit Action Item':'Log New Action Item';
    if(subtitleEl) subtitleEl.textContent=isEdit?'Update any field below, then save your changes.':'Capture the full master-tracking record, then save it into the action register.';
    if(submitBtn) submitBtn.textContent=isEdit?'✓ Save Changes':'+ Add Action Item';
  }

  window.pbS3OpenModal=function(){
    setModalMode(-1);
    const f=document.getElementById('pb-s3-form');
    if(f){
      f.reset();
      // Pre-select Mine based on the currently selected view
      if(view==='MRM'||view==='JPM'){const mSel=f.elements['mine'];if(mSel)mSel.value=view;}
      const shSel=f.elements['shiftId']; if(shSel) shSel.value=shift||'';
      const dtSel=f.elements['dateCreated']; if(dtSel) dtSel.value=dtLocal(new Date());
      const scSel=f.elements['statusChangedAt']; if(scSel) scSel.value='';
    }
    const modal=document.getElementById('pb-s3-modal');
    if(modal) modal.style.display='block';
  };
  window.pbS3GenerateAction=function(payload){
    let rec={};
    try{rec=JSON.parse(decodeURIComponent(String(payload||'')));}catch(e){rec={};}
    const f=document.getElementById('pb-s3-form');
    if(!f) return;
    setModalMode(-1);
    f.reset();
    setFormValues(f, buildGeneratedAction(rec));
    const modal=document.getElementById('pb-s3-modal');
    if(modal) modal.style.display='block';
  };
  window.pbS3CloseModal=function(){
    const modal=document.getElementById('pb-s3-modal');
    if(modal) modal.style.display='none';
    window._pbS3EditIdx=-1;
  };
  window.pbS3ViewItem=function(i){
    const item=window._pbS3Items[i];
    if(!item) return;
    setModalMode(i);
    const f=document.getElementById('pb-s3-form');
    if(f) setFormValues(f, item);
    const modal=document.getElementById('pb-s3-modal');
    if(modal) modal.style.display='block';
  };
  window.pbS3ExportCsv=function(){
    const rows=[CSV_FIELDS.join(',')].concat(window._pbS3Items.map(function(item){
      return CSV_FIELDS.map(function(key){ return csvEsc(item[key]); }).join(',');
    }));
    const blob=new Blob([rows.join('\\n')],{type:'text/csv;charset=utf-8;'});
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;
    a.download='master_tracking_actions.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function(){URL.revokeObjectURL(url);},0);
  };
  window.pbS3ToggleOpenAll=function(){
    window._pbS3ShowOpenAll=!window._pbS3ShowOpenAll;
    try{localStorage.setItem(OPEN_FILTER_KEY,window._pbS3ShowOpenAll?'1':'0');}catch(e){}
    setOpenToggleUI();
    renderList();
  };

  window.pbS3Submit=function(e){
    e.preventDefault();
    const f=e.target;
    const fd=new FormData(f);
    const isEdit=window._pbS3EditIdx>=0;
    const existingItem=isEdit?(window._pbS3Items[window._pbS3EditIdx]||{}):{};
    const existingDateCreated=isEdit?(existingItem.dateCreated||''):'';
    const existingStatus=isEdit?(existingItem.status||''):'';
    const existingStatusChangedAt=isEdit?(existingItem.statusChangedAt||''):'';
    const nowIso=(function(){const d=new Date();const pad=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+'T'+pad(d.getHours())+':'+pad(d.getMinutes());})();
    const newStatus=String(fd.get('status')||'').trim();
    const statusChangedAt=isEdit
      ? (newStatus && newStatus!==String(existingStatus||'').trim() ? nowIso : (existingStatusChangedAt || nowIso))
      : nowIso;
    const newItem=normalizeItem({
      mine:        fd.get('mine'),
      shiftId:     fd.get('shiftId'),
      intervalId:  fd.get('intervalId'),
      assetId:     fd.get('assetId'),
      deviation:   fd.get('deviation'),
      corrective:  fd.get('corrective'),
      owner:       fd.get('owner'),
      support:     fd.get('support'),
      slaDl:       fd.get('slaDl'),
      status:      newStatus,
      statusChangedAt: statusChangedAt,
      rootCause:   fd.get('rootCause'),
      impactVal:   fd.get('impactVal'),
      impactUnit:  fd.get('impactUnit'),
      dateCreated: fd.get('dateCreated') || existingDateCreated || nowIso,
    });
    if(isEdit){
      window._pbS3Items[window._pbS3EditIdx]=newItem;
    } else {
      window._pbS3Items.push(newItem);
    }
    saveItems();
    f.reset();
    const msg=document.getElementById('pb-s3-msg');
    if(msg){msg.style.display='inline';setTimeout(()=>{msg.style.display='none';},2500);}
    window.pbS3CloseModal();
    renderList();
  };
  window.pbS3Delete=function(i){
    window._pbS3Items.splice(i,1);
    saveItems();
    renderList();
  };
  document.addEventListener('click',function(e){
    const modal=document.getElementById('pb-s3-modal');
    if(modal&&e.target===modal) window.pbS3CloseModal();
  });
  setOpenToggleUI();
  renderList();
}
function renderSuggestions(){
  const el=document.getElementById('suggestionsBody');
  if(!el) return;
  let h='';
  h+=`<div style="margin:0 0 12px;font-size:13px;color:var(--muted)">Log improvement suggestions for the selected mine/shift. Seed records are loaded from <code>Data/suggestions.xlsx</code>; exported records can be reviewed in Excel.</div>`;
  h+=`<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px">`;
  h+=`<button type="button" class="tlbtn" onclick="sgResetForm()" style="padding:8px 18px;font-size:13px;background:#2f7a44;color:#fff;border:none;border-radius:6px;cursor:pointer">&#43; New Suggestion</button>`;
  h+=`<button type="button" class="tlbtn" onclick="sgExportCsv()" style="padding:8px 18px;font-size:13px;background:#fff;color:#2f7a44;border:1px solid #2f7a44;border-radius:6px;cursor:pointer">Export Suggestions CSV</button>`;
  h+=`<span id="sg-msg" style="font-size:12px;color:#2f7a44;display:none">&#10003; Suggestion saved.</span>`;
  h+=`</div>`;
  h+=`<form id="sg-form" onsubmit="sgSubmit(event)" style="padding:16px 18px;background:#fff;border:1px solid #d9dfe8;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.05)">`;
  h+=`<div style="font-size:16px;font-weight:700;color:#2b2f36;margin-bottom:12px">Suggestion Entry Form</div>`;
  h+=`<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:12px">`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Mine
    <select name="mine" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>MRM</option><option>JPM</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Shift ID
    <select name="shiftId" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      ${DATA.shifts.map(s=>`<option value="${s.id}">${s.id}${s.name?` · ${s.name}`:''}</option>`).join('')}
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Category
    <select name="category" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>Operations</option><option>Dispatch</option><option>Maintenance</option><option>Safety</option><option>Process Improvement</option><option>Other</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Area / Asset
    <input type="text" name="area" placeholder="e.g. MRM Pit, SHV-02" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`</div>`;
  h+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Suggestion
    <textarea name="suggestion" required rows="4" placeholder="Describe the suggestion to improve performance." style="padding:8px;border:1px solid #ccc;border-radius:6px;font-size:13px;resize:vertical"></textarea>
  </label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Expected Benefit
    <textarea name="benefit" rows="4" placeholder="Expected outcome, benefit or value." style="padding:8px;border:1px solid #ccc;border-radius:6px;font-size:13px;resize:vertical"></textarea>
  </label>`;
  h+=`</div>`;
  h+=`<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:12px">`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Owner
    <input type="text" name="owner" placeholder="Full name or role" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Priority
    <select name="priority" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>High</option><option>Medium</option><option>Low</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Status
    <select name="status" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px;background:#fff">
      <option value="">— select —</option>
      <option>New</option><option>Reviewed</option><option>Planned</option><option>Implemented</option><option>Deferred</option>
    </select></label>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px">Creation Date
    <input type="datetime-local" name="dateCreated" required style="padding:6px 8px;border:1px solid #ccc;border-radius:6px;font-size:13px">
  </label>`;
  h+=`</div>`;
  h+=`<label style="font-size:12px;font-weight:600;color:#344;display:flex;flex-direction:column;gap:4px;margin-bottom:16px">Notes
    <textarea name="notes" rows="3" placeholder="Optional follow-up notes." style="padding:8px;border:1px solid #ccc;border-radius:6px;font-size:13px;resize:vertical"></textarea>
  </label>`;
  h+=`<div style="display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap">`;
  h+=`<button type="button" class="tlbtn" onclick="sgResetForm()" style="padding:8px 18px;font-size:13px;background:#fff;color:#5b6573;border:1px solid #cfd4dd;border-radius:6px;cursor:pointer">Clear</button>`;
  h+=`<button id="sg-submit-btn" type="submit" class="tlbtn" style="padding:8px 22px;font-size:13px;background:#2f7a44;color:#fff;border:none;border-radius:6px;cursor:pointer">&#43; Save Suggestion</button>`;
  h+=`</div>`;
  h+=`</form>`;
  h+=`<div id="sg-list" style="margin-top:14px"></div>`;
  el.innerHTML=h;
  initSuggestionRegister();
}
function initSuggestionRegister(){
  const STORAGE_KEY='albianSuggestionItemsV1';
  const CSV_FIELDS=__SUGGESTION_FIELDS__;
  window._sgSeedItems=Array.isArray(__SUGGESTION_ITEMS__)?__SUGGESTION_ITEMS__:[];
  function esc(v){
    return String(v==null?'':v).replace(/[&<>"']/g,function(ch){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
    });
  }
  function csvEsc(v){ return '"'+String(v==null?'':v).replace(/"/g,'""')+'"'; }
  function normalizeItem(item){
    const out={};
    CSV_FIELDS.forEach(function(key){ out[key]=String(item&&item[key]!=null?item[key]:'').trim(); });
    return out;
  }
  function saveItems(){
    try{localStorage.setItem(STORAGE_KEY, JSON.stringify(window._sgItems));}catch(e){}
  }
  function loadItems(){
    try{
      const raw=localStorage.getItem(STORAGE_KEY);
      if(raw!=null){
        const parsed=JSON.parse(raw);
        if(Array.isArray(parsed)) return parsed.map(normalizeItem);
      }
    }catch(e){}
    return window._sgSeedItems.map(normalizeItem);
  }
  function dtLocal(d){
    const p=n=>String(n).padStart(2,'0');
    return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes());
  }
  function mineBadge(m){
    if(!m) return '';
    const c=m==='MRM'?'#1d6fa4':m==='JPM'?'#7b1fa2':'#555';
    return '<span style="display:inline-block;padding:1px 7px;border-radius:8px;font-size:11px;font-weight:700;color:#fff;background:'+c+'">'+esc(m)+'</span>';
  }
  function statusBadge(s){
    const c={New:'#1d6fa4',Reviewed:'#b85c00',Planned:'#7b1fa2',Implemented:'#2f7a44',Deferred:'#6b7280'};
    return '<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;color:#fff;background:'+(c[s]||'#888')+'">'+esc(s||'Unknown')+'</span>';
  }
  function formatDt(v){
    return esc(String(v||'').replace('T',' ')) || '—';
  }
  function summaryText(item){
    return '<div style="font-weight:600;color:#2b2f36;margin-bottom:3px">'+esc(item.category||'Suggestion')+' · '+esc(item.area||'—')+'</div>'
      +'<div style="font-size:12px;line-height:1.45">'+esc(item.suggestion||'No suggestion recorded')+'</div>'
      +(item.benefit?'<div style="font-size:11px;color:var(--muted);margin-top:4px">Benefit: '+esc(item.benefit)+'</div>':'')
      +(item.notes?'<div style="font-size:11px;color:var(--muted);margin-top:2px">Notes: '+esc(item.notes)+'</div>':'');
  }
  function setFormValues(f,item){
    CSV_FIELDS.forEach(function(key){
      const fld=f.elements[key];
      if(fld) fld.value=item[key]||'';
    });
  }
  function resetFormValues(){
    const f=document.getElementById('sg-form');
    if(!f) return;
    f.reset();
    if(view==='MRM'||view==='JPM'){const mSel=f.elements['mine']; if(mSel) mSel.value=view;}
    const shSel=f.elements['shiftId']; if(shSel) shSel.value=shift||'';
    const stSel=f.elements['status']; if(stSel) stSel.value='New';
    const dtSel=f.elements['dateCreated']; if(dtSel) dtSel.value=dtLocal(new Date());
    window._sgEditIdx=-1;
    const btn=document.getElementById('sg-submit-btn');
    if(btn) btn.textContent='+ Save Suggestion';
  }
  function renderList(){
    const el=document.getElementById('sg-list');
    if(!el) return;
    const visItems=window._sgItems.map(function(item,i){return{item,i};}).filter(function(x){
      const mineOk=(view==='Combined'||!x.item.mine||x.item.mine===view);
      const shiftOk=(!shift||!x.item.shiftId||x.item.shiftId===shift);
      return mineOk && shiftOk;
    });
    const scope=(view==='Combined'?'All Mines':view)+' · '+(shift||'All Shifts');
    let t='<h3 style="margin:0 0 8px;font-size:18px;color:#2b2f36;border-bottom:2px solid #3f51b5;padding-bottom:6px">Logged Suggestions</h3>';
    t+='<div style="margin:0 0 10px;font-size:13px;color:var(--muted)">Suggestion Register — '+esc(scope)+' ('+visItems.length+(visItems.length!==window._sgItems.length?' of '+window._sgItems.length:'')+' items)</div>';
    if(!visItems.length){
      el.innerHTML=t+'<div style="padding:14px 16px;border:1px dashed #cfd6e1;border-radius:10px;background:#fbfcfe;color:#6b7280">No suggestions logged for the selected view/shift yet.</div>';
      return;
    }
    t+='<div style="overflow-x:auto"><table class="lanetab" style="width:100%"><thead><tr>'
      +'<th>#</th><th>Mine</th><th>Shift ID</th><th>Suggestion</th><th>Owner</th><th>Priority</th><th>Status</th><th>Creation Date</th><th></th>'
      +'</tr></thead><tbody>';
    visItems.forEach(function(x,row){
      const item=x.item, idx=x.i;
      t+='<tr style="cursor:pointer" onclick="sgEdit('+idx+')" title="Click to edit this suggestion">'
        +'<td style="color:var(--muted)">'+(row+1)+'</td>'
        +'<td>'+mineBadge(item.mine)+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+esc(item.shiftId||'—')+'</td>'
        +'<td>'+summaryText(item)+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+esc(item.owner||'—')+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+esc(item.priority||'—')+'</td>'
        +'<td>'+statusBadge(item.status)+'</td>'
        +'<td style="white-space:nowrap;font-size:12px">'+formatDt(item.dateCreated)+'</td>'
        +'<td onclick="event.stopPropagation()"><button type="button" class="tlbtn" style="color:#c0392b" onclick="sgDelete('+idx+')">✕</button></td>'
        +'</tr>';
    });
    t+='</tbody></table></div><div style="margin-top:6px;font-size:11px;color:var(--muted)">Click a row to load that suggestion back into the form for editing.</div>';
    el.innerHTML=t;
  }
  if(window._sgInit){
    renderList();
    return;
  }
  window._sgInit=true;
  window._sgItems=loadItems();
  window._sgEditIdx=-1;
  window.sgResetForm=function(){
    resetFormValues();
  };
  window.sgEdit=function(i){
    const item=window._sgItems[i];
    const f=document.getElementById('sg-form');
    if(!item||!f) return;
    window._sgEditIdx=i;
    setFormValues(f,item);
    const btn=document.getElementById('sg-submit-btn');
    if(btn) btn.textContent='✓ Save Changes';
    f.scrollIntoView({behavior:'smooth',block:'start'});
  };
  window.sgDelete=function(i){
    window._sgItems.splice(i,1);
    saveItems();
    resetFormValues();
    renderList();
  };
  window.sgExportCsv=function(){
    const rows=[CSV_FIELDS.join(',')].concat(window._sgItems.map(function(item){
      return CSV_FIELDS.map(function(key){ return csvEsc(item[key]); }).join(',');
    }));
    const blob=new Blob([rows.join('\\n')],{type:'text/csv;charset=utf-8;'});
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;
    a.download='suggestions.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function(){URL.revokeObjectURL(url);},0);
  };
  window.sgSubmit=function(e){
    e.preventDefault();
    const f=e.target;
    const fd=new FormData(f);
    const isEdit=window._sgEditIdx>=0;
    const existingDateCreated=isEdit?(window._sgItems[window._sgEditIdx]||{}).dateCreated||'':'';
    const nowIso=dtLocal(new Date());
    const item=normalizeItem({
      mine: fd.get('mine'),
      shiftId: fd.get('shiftId'),
      category: fd.get('category'),
      area: fd.get('area'),
      suggestion: fd.get('suggestion'),
      benefit: fd.get('benefit'),
      owner: fd.get('owner'),
      priority: fd.get('priority'),
      status: fd.get('status'),
      dateCreated: fd.get('dateCreated') || existingDateCreated || nowIso,
      notes: fd.get('notes'),
    });
    if(isEdit) window._sgItems[window._sgEditIdx]=item;
    else window._sgItems.push(item);
    saveItems();
    const msg=document.getElementById('sg-msg');
    if(msg){msg.style.display='inline';setTimeout(function(){msg.style.display='none';},2500);}
    resetFormValues();
    renderList();
  };
  resetFormValues();
  renderList();
}
const TABS=[['overview','Shift Overview',0],['playbook','Playbook',0],['snapshot','Equipment Status',0],['pulse','Dispatch Settings',0],['matplace','Material Placement',0],['blend','Blend',0],['balance','Truck / Shovel Balance',0],['shovel2','Shovel Waterfall',0],['loading','Loading Drill-Down',1],['shovprod','Shovel Productivity',1],['delaysS','Delays & Standby',1],['trucks','Truck Waterfall',0],['haulage','Haulage Drill-Down',1],['truckprod','Truck Productivity',1],['delays','Delays & Standby',1],['hourlyperf','Hourly Production',0],['lube','Fuel and Lube',0],['shiftstats','Shift Stats',0],['trends','Cross-Shift Trends',0],['appendix','Appendix',0],['sandbox','Sandbox',0],['suggestions','Suggestions',0]];
let tab='overview';
let sbAuto=true;   // sidebar auto-hides (slides off-screen) by default; hover the left edge to reveal
function applySidebar(){document.body.classList.toggle('sb-auto',sbAuto);if(!sbAuto)document.body.classList.remove('sb-show');posHideTab();}
function posHideTab(){const sn=document.getElementById('sidenav'),b=document.getElementById('sbArrow');if(sn&&b&&!sbAuto)b.style.left=Math.round(sn.getBoundingClientRect().right)+'px';}
function toggleSidebar(){sbAuto=!sbAuto;applySidebar();saveState();}
function initSidebarHover(){
  const sn=document.getElementById('sidenav');
  const show=()=>{if(sbAuto)document.body.classList.add('sb-show');};
  const hide=()=>document.body.classList.remove('sb-show');
  ['sbEdge'].forEach(id=>{const e=document.getElementById(id);if(e)e.addEventListener('mouseenter',show);});
  if(sn){sn.addEventListener('mouseenter',show);sn.addEventListener('mouseleave',hide);}
}
const UC_TABS={shiftstats:1,trends:1,appendix:1,sandbox:1};   // under construction → highlighted yellow
function renderSidenav(){const n=document.getElementById('sidenav');
  n.querySelectorAll(':scope > button').forEach(b=>b.remove());
  TABS.forEach(([id,lbl,lvl])=>{const b=document.createElement('button');b.textContent=lbl+(UC_TABS[id]?' 🚧':'');let cls=lvl?'sub':'';if(UC_TABS[id])cls+=(cls?' ':'')+'uc';if(id===tab)cls+=(cls?' ':'')+'on';if(cls)b.className=cls;if(UC_TABS[id])b.title='Under construction';b.onclick=()=>setTab(id);n.appendChild(b);});}
function pageOf(t){return t==='delaysS'?'pg-delays':'pg-'+t;}   // both Delays tabs (shovel + truck) share one page
function setTab(t){tab=t;const pid=pageOf(t);document.querySelectorAll('.page').forEach(p=>{p.hidden=(p.id!==pid);});renderSidenav();renderTab();saveState();if(sbAuto)document.body.classList.remove('sb-show');}
function pageStep(dir){const idx=TABS.findIndex(t=>t[0]===tab),n=idx+dir; if(n>=0&&n<TABS.length)setTab(TABS[n][0]);}
function renderPageNav(){const idx=TABS.findIndex(t=>t[0]===tab);
  const pv=document.getElementById('pgPrev'),nx=document.getElementById('pgNext'),lb=document.getElementById('pgLabel');
  if(pv)pv.disabled=idx<=0; if(nx)nx.disabled=idx>=TABS.length-1;
  if(lb)lb.textContent=(idx+1)+' / '+TABS.length+' · '+(TABS[idx]?TABS[idx][1]:'');}
function setSubs(){['wfsub','shovsub','hcsub','ansub','owsub','lanesub','dhsub','dlsub','tlsub','avsub','avsub2','dtlsub','lhsub','lusub','hitsub','shov2sub','dssub','hpsub','spsub','tpsub','sssub','tfsub','playsub','sgsub','snapsub','pulsesub','pulseavgsub','blendsub'].forEach(id=>{const e=document.getElementById(id);if(e)e.textContent='('+view+')';});}
function renderTab(){
  setSubs(); renderPageNav();
  if(tab==='overview'){try{renderOverview();}catch(e){console.error(e);}}
  else if(tab==='playbook'){try{renderPlaybook();}catch(e){console.error(e);}}
  else if(tab==='suggestions'){try{renderSuggestions();}catch(e){console.error(e);}}
  else if(tab==='snapshot'){try{renderSnapshot();}catch(e){console.error(e);}}
  else if(tab==='blend'){try{renderBlend();}catch(e){console.error(e);}}
  else if(tab==='pulse'){try{renderPulse();}catch(e){console.error(e);}}
  else if(tab==='trends'){try{renderTrends();}catch(e){console.error(e);}}
  else if(tab==='balance'){renderCards();}
  else if(tab==='sandbox'){try{renderSandbox();}catch(e){console.error(e);}}
  else if(tab==='trucks'){renderWF();renderOWWF();const _td=document.getElementById('secTadT'); if(_td&&!_td.hidden)renderTrucksAtDump();}
  else if(tab==='shovel2'){renderShovWF2();}
  else if(tab==='loading'){renderLoading();}
  else if(tab==='haulage'){renderLanes();}
  else if(tab==='matplace'){try{renderMatPlace();}catch(e){console.error(e);}}
  else if(tab==='lube'){try{renderLube();}catch(e){console.error(e);}}
  else if(tab==='delays'){dsEquip='trucks';try{renderDelays();}catch(e){console.error(e);}}
  else if(tab==='delaysS'){dsEquip='shovels';try{renderDelays();}catch(e){console.error(e);}}
  else if(tab==='hourlyperf'){try{renderHourlyPerf();}catch(e){console.error(e);}}
  else if(tab==='shovprod'){try{renderShovProd();}catch(e){console.error(e);}}
  else if(tab==='truckprod'){try{renderTruckProd();}catch(e){console.error(e);}}
  else if(tab==='shiftstats'){try{renderShiftStats();}catch(e){console.error(e);}}
  else if(tab==='appendix'){try{renderAppendix();}catch(e){console.error(e);}}
}
let blendAllOpen=false;
function blendToggle(si,el){const rows=document.querySelectorAll('.blk'+si);if(!rows.length)return;const open=rows[0].style.display==='none';rows.forEach(r=>r.style.display=open?'table-row':'none');const c=el&&el.querySelector('.cx');if(c)c.textContent=open?'▾':'▸';}
function blendExpandAll(btn){blendAllOpen=!blendAllOpen;if(btn)btn.textContent=blendAllOpen?'Collapse blocks ▸':'Expand blocks ▾';renderBlend();}
function renderBlend(){
  const m=SD().meta, bl=(m&&m.blend)||{rows:[],base:6};
  const body=document.getElementById('blendBody'); if(!body)return;
  const when=document.getElementById('blendWhen'); if(when)when.textContent='Shift '+(m.shift||'')+' — grade blend by shovel, tonnes-weighted.';
  const pf = view==='Combined' ? (_=>true) : (p=>p===view);
  const rows=(bl.rows||[]).filter(r=>pf(r.pit));
  if(!rows.length){body.innerHTML='<div class="foot">No blend data for this view/shift.</div>';return;}
  const base=bl.base||6, hl=i=>{const h=(base+i)%24;return (h<10?'0'+h:h)+':00';};
  const hlR=i=>{const a=(base+i)%24,b=(base+i+1)%24,p=n=>(n<10?'0'+n:n);return p(a)+':00 - '+p(b)+':00';};
  let lastH=0; rows.forEach(r=>r.ton.forEach((t,i)=>{if(t>0&&i>lastH)lastH=i;})); const H=lastH+1;
  const hTot=Array(12).fill(0); rows.forEach(r=>r.ton.forEach((t,i)=>hTot[i]+=t)); const grand=hTot.reduce((a,b)=>a+b,0);
  const rowTot=r=>r.ton.reduce((a,b)=>a+b,0);
  const pc=(t,i)=>hTot[i]>0?(t/hTot[i]*100):0, pcT=t=>grand>0?(t/grand*100):0;
  const p1=v=>v.toFixed(1)+'%';
  // grade (tonnes-weighted, valid ore blocks only)
  const gn=[Array(12).fill(0),Array(12).fill(0),Array(12).fill(0)], gd=Array(12).fill(0); let tn=[0,0,0], td=0;
  rows.forEach(r=>{ if(!r.valid)return; r.ton.forEach((t,i)=>{ if(t>0){gn[0][i]+=r.bit*t;gn[1][i]+=r.fines*t;gn[2][i]+=r.d50*t;gd[i]+=t;} }); const rt=rowTot(r); tn[0]+=r.bit*rt;tn[1]+=r.fines*rt;tn[2]+=r.d50*rt;td+=rt; });
  const gAvg=(k,i)=>gd[i]>0?gn[k][i]/gd[i]:null, gTot=k=>td>0?tn[k]/td:null;
  const byShov={}; rows.forEach(r=>{(byShov[r.shovel]=byShov[r.shovel]||[]).push(r);});
  const shovels=Object.keys(byShov).sort();
  // ---- frozen first columns (Shovel, Block, Bit, Fines, D50) ----
  const FL=[0,84,244,288,332], FW=[84,160,44,44,44], SPANL=84, SPANW=292;
  const frz=(idx,bg,z)=>`position:sticky;left:${FL[idx]}px;min-width:${FW[idx]}px;max-width:${FW[idx]}px;width:${FW[idx]}px;background:${bg};z-index:${z||1};`;
  const frzS=(bg,z)=>`position:sticky;left:${SPANL}px;min-width:${SPANW}px;max-width:${SPANW}px;width:${SPANW}px;background:${bg};z-index:${z||1};`;
  const fzH=idx=>`position:sticky;top:0;left:${FL[idx]}px;min-width:${FW[idx]}px;max-width:${FW[idx]}px;width:${FW[idx]}px;background:#f4f6fa;z-index:6;`;
  // ---- frozen last columns (Total Shift: % Blocks, Tonnes) ----
  const TW=[62,64], TR=[TW[1],0], TSPANW=TW[0]+TW[1];
  const frzR=(idx,bg,z)=>`position:sticky;right:${TR[idx]}px;min-width:${TW[idx]}px;max-width:${TW[idx]}px;width:${TW[idx]}px;background:${bg};z-index:${z||3};`;
  const frzR2=(bg,z)=>`position:sticky;right:0;min-width:${TSPANW}px;max-width:${TSPANW}px;width:${TSPANW}px;background:${bg};z-index:${z||3};`;
  const fzHR=idx=>`position:sticky;top:0;right:${TR[idx]}px;min-width:${TW[idx]}px;max-width:${TW[idx]}px;width:${TW[idx]}px;background:#eef2f8;z-index:6;`;
  const fzHR2=`position:sticky;top:0;right:0;min-width:${TSPANW}px;max-width:${TSPANW}px;width:${TSPANW}px;background:#eef2f8;z-index:7;`;
  // ---- header ----
  let h=`<table class="lanetab" style="font-size:11px;white-space:nowrap;border-collapse:separate;border-spacing:0"><thead><tr>`;
  h+=`<th rowspan="2" style="${fzH(0)}text-align:left">Shovel</th><th rowspan="2" style="${fzH(1)}text-align:left">Block</th><th rowspan="2" style="${fzH(2)}">Bit&nbsp;%</th><th rowspan="2" style="${fzH(3)}">Fines&nbsp;%</th><th rowspan="2" style="${fzH(4)}">D50</th>`;
  for(let i=0;i<H;i++)h+=`<th colspan="2" style="z-index:5;border-left:2px solid #d7dbe2">${hlR(i)}</th>`;
  h+=`<th colspan="2" style="${fzHR2}border-left:2px solid #9aa8bd">Total Shift</th></tr><tr>`;
  for(let i=0;i<H;i++)h+=`<th style="z-index:5;border-left:2px solid #d7dbe2">% Blocks</th><th style="z-index:5">Tonnes</th>`;
  h+=`<th style="${fzHR(0)}border-left:2px solid #9aa8bd">% Blocks</th><th style="${fzHR(1)}">Tonnes</th></tr></thead><tbody>`;
  const cellH=(t,i,bold)=>{const w=bold?'font-weight:600;':'';return `<td style="text-align:right;border-left:2px solid #eef0f4;${w}">${t>0?p1(pc(t,i)):'0%'}</td><td style="text-align:right;${w}">${t>0?fmt(t):'0'}</td>`;};
  const cellT=(t,strong)=>{const bg=strong?'#a9c5e6':'#f4f7fb',w=strong?'font-weight:700;color:#123':'',z=strong?5:4;return `<td style="${frzR(0,bg,z)}text-align:right;border-left:3px solid #6f86a8;${w}">${t>0?p1(pcT(t)):'0%'}</td><td style="${frzR(1,bg,z)}text-align:right;${w}">${fmt(t)}</td>`;};
  // per-block D50: warn when over the pit threshold (MRM > 340, JPM > 325)
  const isHiD50=r=>r.valid&&Math.round(r.d50)>((r.pit==='JPM')?325:340);
  const d50Cell=r=>{if(!r.valid)return `<td style="${frz(4,'#fff',1)}text-align:right">—</td>`;
    const v=Math.round(r.d50);
    if(!isHiD50(r))return `<td style="${frz(4,'#fff',1)}text-align:right">${v}</td>`;
    const thr=(r.pit==='JPM')?325:340;
    return `<td style="${frz(4,'#fff3cd',1)}text-align:right;color:#8a5a00;font-weight:800;box-shadow:inset 0 0 0 1.5px #e8b84b" title="High D50 (> ${thr})">⚠ ${v}</td>`;};
  // ---- shovel groups (block detail collapses; click a shovel row or use Expand all) ----
  shovels.forEach((sh,si)=>{
    const bs=byShov[sh].slice().sort((a,b)=>a.block<b.block?-1:1);
    const sHour=Array(12).fill(0); bs.forEach(r=>r.ton.forEach((t,i)=>sHour[i]+=t)); const sTot=sHour.reduce((a,b)=>a+b,0);
    let _sbn=[0,0,0],_sbd=0; bs.forEach(r=>{if(r.valid){const rt=rowTot(r);_sbn[0]+=r.bit*rt;_sbn[1]+=r.fines*rt;_sbn[2]+=r.d50*rt;_sbd+=rt;}});
    const sBit=_sbd>0?_sbn[0]/_sbd:null,sFn=_sbd>0?_sbn[1]/_sbd:null,sD50=_sbd>0?_sbn[2]/_sbd:null;
    h+=`<tr class="blshov" style="background:#dbe6f4;font-weight:700;cursor:pointer" onclick="blendToggle(${si},this)"><td style="${frz(0,'#dbe6f4',2)}text-align:left"><span class="cx" style="display:inline-block;width:11px;color:#456">${blendAllOpen?'▾':'▸'}</span> ${sh} <span style="font-weight:400;color:#5a6b82">(${bs.length})</span></td><td style="${frz(1,'#dbe6f4',2)}"></td><td style="${frz(2,'#dbe6f4',2)}text-align:right">${sBit!=null?sBit.toFixed(1):'—'}</td><td style="${frz(3,'#dbe6f4',2)}text-align:right">${sFn!=null?sFn.toFixed(1):'—'}</td><td style="${frz(4,'#dbe6f4',2)}text-align:right">${sD50!=null?Math.round(sD50):'—'}</td>`;
    for(let i=0;i<H;i++)h+=cellH(sHour[i],i,true); h+=cellT(sTot,true)+'</tr>';
    bs.forEach(r=>{
      const hiBadge=isHiD50(r)?`<span style="display:inline-block;background:#fff3cd;color:#8a5a00;border:1px solid #e8b84b;border-radius:4px;font-size:9px;font-weight:700;padding:0 5px;vertical-align:middle">High D50</span>`:'';
      h+=`<tr class="blk${si}" style="display:${blendAllOpen?'table-row':'none'};background:#fff"><td style="${frz(0,'#fff',1)}text-align:right;padding-right:4px">${hiBadge}</td><td style="${frz(1,'#fff',1)}text-align:left;padding-left:16px;overflow:hidden;text-overflow:ellipsis">${r.block}</td>`+
         `<td style="${frz(2,'#fff',1)}text-align:right">${r.valid?r.bit.toFixed(1):'—'}</td><td style="${frz(3,'#fff',1)}text-align:right">${r.valid?r.fines.toFixed(1):'—'}</td>`+d50Cell(r);
      for(let i=0;i<H;i++)h+=cellH(r.ton[i],i,false); h+=cellT(rowTot(r),false)+'</tr>';
    });
  });
  // ---- totals ----
  h+=`<tr style="background:#c9d6ea;font-weight:700;border-top:2px solid #9aa8bd"><td style="${frz(0,'#c9d6ea',2)}text-align:left">Totals</td><td colspan="4" style="${frzS('#c9d6ea',2)}"></td>`;
  for(let i=0;i<H;i++)h+=`<td style="text-align:right;border-left:2px solid #9aa8bd">100%</td><td style="text-align:right">${fmt(hTot[i])}</td>`;
  h+=`<td style="${frzR(0,'#f4f7fb',5)}text-align:right;border-left:2px solid #9aa8bd">100%</td><td style="${frzR(1,'#f4f7fb',5)}text-align:right">${fmt(grand)}</td></tr>`;
  // ---- grades (tonnes-weighted) — prominent banner rows ----
  const GB='linear-gradient(#f7f9fc,#e9eef7)',GBD='1px solid #cdd6e4';
  const gRow=(label,k,dp)=>{let s=`<tr style="font-weight:800;font-size:12.5px;color:#1f2a3a"><td style="${frz(0,GB,1)}text-align:left;color:#243b8a;border-left:5px solid #3f51b5;border-top:${GBD};border-bottom:${GBD}">${label}</td><td colspan="4" style="${frzS(GB,1)}border-top:${GBD};border-bottom:${GBD}"></td>`;
    for(let i=0;i<H;i++){const v=gAvg(k,i);s+=`<td colspan="2" style="text-align:center;border-left:2px solid #dfe4ee;border-top:${GBD};border-bottom:${GBD};background:${GB}">${v!=null?v.toFixed(dp):'—'}</td>`;}
    const vt=gTot(k);s+=`<td colspan="2" style="${frzR2('#dbe6f4',4)}text-align:center;border-left:2px solid #9aa8bd;border-top:${GBD};border-bottom:${GBD}">${vt!=null?vt.toFixed(dp):'—'}</td></tr>`;return s;};
  h+=gRow('Bit %',0,1)+gRow('Fines %',1,1)+gRow('D50 µm',2,0);
  h+='</tbody></table>';
  body.innerHTML=h;
}
let snapCat='shov';
function snapCatTab(c){snapCat=c; renderSnapshot();}
const TRUCK_ORD=['Cat 797','Cat 789','Cat 785','Cat 793','Cat 740','Cat 770'];
const SHOV_ORD=['BE 495B','HIT8000','Komatsu 3000','HIT 2500','Hit ZX8','HIT 5600','HIT 1900'];
const AUX_ORD=['Cat D11T','Cat D8','Cat 854K','Cat 24M','Cat 16M'];
function renderSnapshot(){
  const sd=SD(), snap=(sd&&sd.snapshot)||{};
  const body=document.getElementById('snapBody'); if(!body)return;
  const when=document.getElementById('snapWhen'); if(when)when.textContent='Equipment Down / Delay / Standby at end of shift (from status events).';
  const pf = view==='Combined' ? (_=>true) : (p=>p===view);
  const cats={shov:(snap.shov||[]).filter(r=>pf(r.pit)), truck:(snap.truck||[]).filter(r=>pf(r.pit)), aux:(snap.aux||[]).filter(r=>pf(r.pit))};
  [['shov','Shovel'],['truck','Truck'],['aux','Aux']].forEach(([k,lbl])=>{const b=document.getElementById('snapBtn_'+k); if(b){b.textContent=lbl+' ('+cats[k].length+')'; b.classList.toggle('on',snapCat===k);}});
  const rows=cats[snapCat]||[];
  const dfmt=m=>m>=60?(Math.floor(m/60)+'h '+String(m%60).padStart(2,'0')+'m'):(m+'m');
  const STATUS=[['Down','#c0392b','#fdf3f1','#eec9c4'],['Delay','#b07d18','#fbf6e9','#e8dcbb'],['Standby','#4f6a86','#eef2f7','#d4dde8']];
  const ordTypes=(types,byType)=>{const ORD=snapCat==='truck'?TRUCK_ORD:(snapCat==='shov'?SHOV_ORD:(snapCat==='aux'?AUX_ORD:[]));
    return types.slice().sort((a,b)=>{const ia=ORD.indexOf(a),ib=ORD.indexOf(b);return (ia<0?99:ia)-(ib<0?99:ib)||byType[b].length-byType[a].length||(a<b?-1:1);}); };
  if(!rows.length){body.innerHTML=`<div class="foot" style="color:#0e6b5c">No ${snapCat==='shov'?'shovels':snapCat==='truck'?'trucks':'auxiliary units'} in Down / Delay / Standby at end of shift.</div>`;return;}
  let h='';
  STATUS.forEach(([st,acc,bg,bd])=>{
    const g=rows.filter(r=>r.status===st); if(!g.length)return;
    h+=`<section style="width:100%;margin:16px auto;padding:12px;border:2px solid ${bd};border-radius:8px;background:#fff">`+
       `<h3 style="display:table;margin:0 auto 12px;padding:5px 18px;border:1px solid ${acc};border-radius:999px;background:#fff;color:${acc};font-size:15px;line-height:1.2;text-align:center">${st} <span style="font-weight:500">(${g.length})</span></h3>`;
    const byType={}; g.forEach(r=>{(byType[r.type||'—']=byType[r.type||'—']||[]).push(r);});
    h+=`<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:10px">`;
    ordTypes(Object.keys(byType),byType).forEach(ty=>{const tg=byType[ty];
      h+=`<div style="min-width:0;border:1px solid ${bd};border-radius:6px;background:#fff;padding:8px">`;
      h+=`<div style="font-size:13px;font-weight:800;color:${acc};margin:0 0 7px;padding:1px 2px 5px;border-bottom:2px solid ${bd};text-align:center">${ty} <span style="color:#6b7280;font-weight:500">(${tg.length})</span></div>`;
      h+='<div style="display:flex;flex-direction:column;gap:6px">';
      tg.forEach(r=>{h+=`<div title="${r.cat||''}" style="display:flex;align-items:baseline;gap:10px;border:1px solid ${bd};border-left:4px solid ${acc};background:${bg};border-radius:5px;padding:7px 10px">`+
        `<div style="font-weight:700;font-size:14px;color:#333;min-width:60px">${r.eq}</div>`+
        `<div style="font-size:11px;color:#374151;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.reason}</div>`+
        `<div style="font-size:10.5px;color:#6b7280;white-space:nowrap">↓ ${dfmt(r.min)} · ${r.pit}</div></div>`;});
      h+='</div></div>';});
    h+=`</div></section>`;
  });
  body.innerHTML=h;
}
function renderAll(){
  renderToggle();
  const m=SD().meta;
  const updatedText='Last updated '+DATA.meta.generated;
  document.getElementById('ovUpdated').textContent=updatedText;
  document.querySelectorAll('.page').forEach(function(pg){
    const sec=pg.querySelector(':scope > .section');
    if(!sec) return;
    sec.style.position=sec.style.position||'relative';
    let badge=sec.querySelector(':scope > .updated.page-updated');
    if(pg.id==='pg-overview'){
      const ov=sec.querySelector(':scope > #ovUpdated');
      if(ov) ov.textContent=updatedText;
      return;
    }
    if(!badge){
      badge=document.createElement('div');
      badge.className='updated page-updated';
      sec.insertBefore(badge, sec.firstChild);
    }
    badge.textContent=updatedText;
  });
  document.getElementById('gen').textContent='Generated '+DATA.meta.generated+
    '  ·  payload target '+DATA.meta.payloadTarget+'t  ·  full-leg fraction '+m.fullLegFrac+
    ' (loaded '+m.vFull+' km/h, empty '+m.vEmpty+' km/h)';
  document.querySelectorAll('.page').forEach(p=>{p.hidden=(p.id!==pageOf(tab));});   // show the active tab's page
  renderShiftNav(); renderSidenav(); renderTab();
}
loadState();
renderAll();
applySidebar();
initSidebarHover();
window.addEventListener('hashchange',()=>{loadState();renderAll();applySidebar();});   // back/forward + edited deep-links
window.addEventListener('resize',posHideTab);   // keep the hide tab glued to the sidebar's right edge
setTimeout(()=>{
  try{
    const u=new URL(window.location.href);
    u.searchParams.set('_refresh', String(Date.now()));
    window.location.replace(u.toString());
  }catch(e){
    window.location.reload();
  }
},300000);   // auto-refresh every 5 minutes so the page picks up the latest generated HTML
</script></body></html>'''
HTML=HTML.replace('__PLAYBOOK_GAP_LIBRARY__', json.dumps(PLAYBOOK_GAP_LIBRARY))
HTML=HTML.replace('__MASTER_TRACKING_ACTION_FIELDS__', json.dumps(MASTER_TRACKING_ACTION_FIELDS))
HTML=HTML.replace('__MASTER_TRACKING_ACTIONS__', json.dumps(load_master_tracking_actions()))
HTML=HTML.replace('__SUGGESTION_FIELDS__', json.dumps(SUGGESTION_FIELDS))
HTML=HTML.replace('__SUGGESTION_ITEMS__', json.dumps(load_suggestions()))
HTML=HTML.replace('__DATA__', json.dumps(out))
# Inline Chart.js for a fully self-contained, offline / no-CDN file. Falls back to CDN if the lib is absent.
try:
    _cjs=open(f'{BASE}/lib_chartjs.js',encoding='utf-8').read()
    chart_tag='<script>\n'+_cjs+'\n</script>'
    _mode='inlined (offline-ready, no CDN)'
except FileNotFoundError:
    chart_tag='<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>'
    _mode='CDN fallback (lib_chartjs.js not found)'
HTML=HTML.replace('__CHARTJS__', chart_tag)
open(f'{BASE}/Haulage_Dashboard.html','w',encoding='utf-8').write(HTML)
print("HTML written: %s/Haulage_Dashboard.html (%d KB)  Chart.js: %s"%(BASE,len(HTML)//1024,_mode))
