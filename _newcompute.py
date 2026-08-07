BASE='.'
MONTHS={'1':'Jan','2':'Feb','3':'Mar','4':'Apr','5':'May','6':'Jun','7':'Jul','8':'Aug','9':'Sep','10':'Oct','11':'Nov','12':'Dec'}
PAYLOAD_TARGET=361.0
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
def _dtp(s):
    try: return _dtm.strptime(str(s)[:19],'%Y-%m-%d %H:%M:%S')
    except: return None
def _dtp_tb(s):
    s=str(s).strip()
    for f in ('%m/%d/%y %H:%M','%m/%d/%Y %H:%M','%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try: return _dtm.strptime(s if 'T' not in s else s.replace('T',' ')[:19],f)
        except: pass
    return None

# ---------- budgets (all months, keyed for per-shift lookup) ----------
def load_curve(fn):
    d=defaultdict(dict)
    for r in csv.DictReader(open(fn,encoding='utf-8-sig')):
        d[r['Month']][(r['Material'],round(float(r['HD (km)']),1))]={
            'TPNOH':float(r['TPNOH (t/h)']),'Cycle':float(r['Cycle (min)']),'Travel':float(r['Travel (min)'])}
    return d
CURVE={'MRM':load_curve(f'{BASE}/Budget/MRM Haul Curve.csv'),'JPM':load_curve(f'{BASE}/Budget/JPM Haul Curve.csv')}
def load_fx(fn):
    d=defaultdict(dict)
    for r in csv.DictReader(open(fn,encoding='utf-8-sig')):
        d[r['month_f']][r['Material2']]={'Queue':float(r['Queue_Time']),'Spot':float(r['Spot_Time']),
            'Load':float(r['Load_Time']),'DumpIdle':float(r['Dump_Idle']),'Dumping':float(r['Dumping_Time'])}
    return d
FX={'MRM':load_fx(f'{BASE}/Budget/MRM_Fixed_Times.csv'),'JPM':load_fx(f'{BASE}/Budget/JPM_Fixed_Times.csv')}
def load_pitbud(fn):
    d={}
    for r in csv.DictReader(open(fn,encoding='utf-8-sig')):
        d[(r['Month'],r['Date'],r['Shift'])]=r
    return d
PITBUD={'MRM':load_pitbud(f'{BASE}/Budget/MRM 2026 Budget.csv'),'JPM':load_pitbud(f'{BASE}/Budget/JPM 2026 Budget.csv')}

# ---------- data (all shifts, loaded once) ----------
loads_all=list(csv.DictReader(open(f'{BASE}/Data/AllLoadsDumps.csv',encoding='utf-8-sig')))
status_all=list(csv.DictReader(open(f'{BASE}/Data/Statusevents.csv',encoding='utf-8-sig')))
tas_all=list(csv.DictReader(open(f'{BASE}/Data/TruckatShovel.csv',encoding='utf-8-sig')))
tb_all=[]
for r in csv.reader(open(f'{BASE}/Data/TruckBalance.csv',encoding='utf-8-sig')):
    if not r or r[0] in ('Pit','') : continue
    try: req,act=float(r[1]),float(r[2])
    except (ValueError,IndexError): continue
    tb_all.append((r[0],req,act,_dtp_tb(r[4]) if len(r)>4 else None))

def stype(ex): return 'BE495' if ex.startswith('S0') else ('HIT8000' if ex.startswith('S8') else None)
def _med(vals):
    v=[x for x in vals if x>0]; return _st.median(v) if v else 0.0
def _statgrp(s):
    if s=='Ready': return 'Ready'
    if s=='Delay': return 'Delay'
    if s=='Down': return 'Down'
    if s in ('Standby','Parked'): return 'Standby'
    return 'Other'
def _sid(r): return r.get('ShiftId') or r.get('ShiftID')

# ---------- shifts available in the production data (AllLoadsDumps) ----------
_seen={}
for r in loads_all:
    sid=_sid(r)
    if sid and sid not in _seen:
        _seen[sid]={'id':sid,'name':r['FullShiftName'],'start':r['ShiftStartTimestamp']}
SHIFTS=sorted(_seen.values(), key=lambda s:s['start'], reverse=True)   # most recent first

# ============================ per-shift build ============================
def build_shift(sm):
    sid=sm['id']; sname=sm['name']; start=_dtp(sm['start'])
    monthnum=str(start.month); mabbr=MONTHS[monthnum]; date=str(start.day)
    snum=str(int(sid[-3:])) if sid[-3:].isdigit() else '1'
    base=start.hour
    curve={p:CURVE[p].get(mabbr,{}) for p in PITS}
    fx={p:FX[p].get(mabbr,{}) for p in PITS}
    pitbud={p:PITBUD[p].get((monthnum,date,snum)) for p in PITS}
    def bud(p,c):
        r=pitbud[p]; return num(r[c]) if r else 0.0
    shTPNOH={p:{('BE495','Ore'):bud(p,'TPNOHO495'),('BE495','Waste'):bud(p,'TPNOHW495'),
                ('HIT8000','Ore'):bud(p,'TPNOH8000'),('HIT8000','Waste'):bud(p,'TPNOHW8000')} for p in PITS}
    loads=[r for r in loads_all if _sid(r)==sid]
    t1=[r for r in loads if r['Truck'].startswith('T1')]
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

    # ---- HAULAGE ----
    haul=defaultdict(lambda:{'pot':0.0,'act':0.0,'mat':defaultdict(lambda:[0.0,0.0]),'lane':defaultdict(lambda:[0.0,0.0,0])})
    for r in t1:
        pit=r['LoadPit']; mat=r['MaterialGroupName']; c=cv(pit,mat,hd_of(r))
        cyc_h=sum(num(r[x]) for x in ['EmptyHaulDuration','SpotTime','LoadingTime','QueueTimeShvl',
                  'FullHaulDuration','QueueTimeDmp','DumpSpotTime','DumpingTime'])/3600.0
        pot=c['TPNOH']*cyc_h; act=num(r['Tonnage'])
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
    # ---- TRUCK BALANCE (match TruckBalance timestamps into this shift window) ----
    tb=defaultdict(lambda:[0.0,0.0,0]); wend=start+_td(hours=12)
    for pit,req,act,dt in tb_all:
        if dt is not None and not (start<=dt<wend): continue
        tb[pit][0]+=req; tb[pit][1]+=act; tb[pit][2]+=1
    # ---- TRUCKS WATERFALL ----
    def new_wf(): return {'pot':0.0,'act':0.0,'n':0,'rows':defaultdict(float),'lm':defaultdict(lambda:[0.0,0.0])}
    truckswf=defaultdict(lambda:{'top':new_wf(),'mat':defaultdict(new_wf),'lane':defaultdict(new_wf)})
    lane_mat=defaultdict(lambda:defaultdict(int)); lane_exc=defaultdict(lambda:defaultdict(int)); lane_dist=defaultdict(lambda:[0.0,0.0,0,0])
    ratio_n=defaultdict(float); ratio_d=defaultdict(float)
    for r in t1:
        pit=r['LoadPit']; mat=r['MaterialGroupName']; c=cv(pit,mat,hd_of(r)); rate=c['TPNOH']/3600.0
        f=fx[pit].get(mat) or {'Queue':0,'Spot':0,'Load':0,'DumpIdle':0,'Dumping':0}
        lkey=(r['LoadLocation'],r['DumpLocation']); lane_mat[(pit,)+lkey][mat]+=1; lane_exc[(pit,)+lkey][r['Excav']]+=1
        ld_=lane_dist[(pit,)+lkey]
        if num(r['FullHaulDistance'])>0: ld_[0]+=num(r['FullHaulDistance']); ld_[2]+=1
        if num(r['EmptyHaullDistance'])>0: ld_[1]+=num(r['EmptyHaullDistance']); ld_[3]+=1
        a={'Spot':num(r['SpotTime']),'Load':num(r['LoadingTime']),'Queue':num(r['QueueTimeShvl']),
           'DumpIdle':num(r['QueueTimeDmp']),'Dumping':num(r['DumpingTime']),
           'Full':num(r['FullHaulDuration']),'Empty':num(r['EmptyHaulDuration']),'DumpSpot':num(r['DumpSpotTime'])}
        b={'Spot':f['Spot']*60,'Load':f['Load']*60,'Queue':f['Queue']*60,'DumpIdle':f['DumpIdle']*60,
           'Dumping':f['Dumping']*60,'Full':c['Travel']*60*FFULL,'Empty':c['Travel']*60*(1-FFULL),'DumpSpot':0.0}
        pot=rate*sum(a.values()); act=num(r['Tonnage'])
        for tgt in (truckswf[pit]['top'],truckswf[pit]['mat'][mat],truckswf[pit]['lane'][lkey]):
            tgt['pot']+=pot; tgt['act']+=act; tgt['n']+=1
            tgt['rows']['Payload']+=act-PAYLOAD_TARGET
            tgt['lm']['Payload'][0]+=act; tgt['lm']['Payload'][1]+=PAYLOAD_TARGET
            for comp in ['Load','Queue','Spot','DumpIdle','Dumping']: tgt['rows'][comp]+=rate*(b[comp]-a[comp])
            tgt['rows']['FullHaul']+=rate*(b['Full']-a['Full'])
            tgt['rows']['EmptyHaul']+=rate*((b['Empty']-a['Empty'])+(b['DumpSpot']-a['DumpSpot']))
            for comp,ak in LM_KEY.items(): tgt['lm'][comp][0]+=a[ak]; tgt['lm'][comp][1]+=b[ak]
        if num(r['FullHaulDistance'])>0: ratio_n[pit]+=num(r['EmptyHaullDistance']); ratio_d[pit]+=num(r['FullHaulDistance'])
    # ---- status events (this shift; renamed columns) ----
    sev=[e for e in status_all if _sid(e)==sid]
    anoh=defaultdict(float)
    for e in sev:
        if e.get('EqmtType')=='Cat 797' and e.get('TimeCat')=='Operating': anoh[e['Pit']]+=num(e['Duration'])/3600.0
    bnoh={p:(bud(p,'NOH797')*elapsed/12) for p in PITS}
    # ---- trucks-in-queue series (this shift) ----
    _qraw=defaultdict(list)
    for r in tas_all:
        if (r.get('shiftId') or r.get('ShiftId'))!=sid: continue
        d=_dtp(r['LogTime'])
        if not d: continue
        _qraw[r['Excav']].append((round(max(0.0,min(720.0,mfs(d))),1),int(num(r['TrucksInQueue']))))
    qseries={k:sorted(v) for k,v in _qraw.items()}

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
        rs=[p for p in pits if tb[p][2]>0]
        if not rs: return {'required':0,'actual':0,'pct':0,'label':'No data'}
        req=sum(tb[p][0]/tb[p][2] for p in rs); act=sum(tb[p][1]/tb[p][2] for p in rs)
        pct=(req-act)/req*100 if req else 0
        label='Balanced' if abs(pct)<=5 else ('Under-Trucked' if pct>0 else 'Over-Trucked')
        return {'required':req,'actual':act,'pct':pct,'label':label}
    def _lm(srcs):
        n=sum(s['n'] for s in srcs) or 1; out={}
        for comp in WF_ROWS:
            sa=sum(s['lm'][comp][0] for s in srcs); sb=sum(s['lm'][comp][1] for s in srcs)
            out[comp]={'actual':sa/n,'target':sb/n,'unit':'t' if comp=='Payload' else 'time'}
        return out
    def agg_wf(pits):
        tops=[truckswf[p]['top'] for p in pits]
        pot=sum(s['pot'] for s in tops); act=sum(s['act'] for s in tops)
        rows={k:sum(s['rows'][k] for s in tops) for k in WF_ROWS}; residual=act-(pot+sum(rows.values()))
        mat={}
        for m in ['Ore','Waste']:
            ms=[truckswf[p]['mat'][m] for p in pits]
            mp=sum(s['pot'] for s in ms); ma=sum(s['act'] for s in ms)
            mr={k:sum(s['rows'][k] for s in ms) for k in WF_ROWS}
            mat[m]={'potential':mp,'actual':ma,'rows':mr,'residual':ma-(mp+sum(mr.values())),'lm':_lm(ms)}
        lanes=[]
        for p in pits:
            for (ld,dp),s in truckswf[p]['lane'].items():
                if ld==dp or s['n']<5: continue
                mc=lane_mat[(p,ld,dp)]; ec=lane_exc[(p,ld,dp)]; dd=lane_dist[(p,ld,dp)]
                lr={k:s['rows'][k] for k in WF_ROWS}
                lanes.append({'pit':p,'load':ld,'dump':dp,'mat':max(mc,key=mc.get),'shovel':max(ec,key=ec.get),'loads':s['n'],
                    'distFull':dd[0]/dd[2]/1000 if dd[2] else 0.0,'distEmpty':dd[1]/dd[3]/1000 if dd[3] else 0.0,
                    'pot':s['pot'],'act':s['act'],'score':s['act']/s['pot']*100 if s['pot'] else 0,
                    'rows':lr,'residual':s['act']-(s['pot']+sum(lr.values())),'lm':_lm([s])})
        lanes.sort(key=lambda x:(x['shovel'],-x['pot']))
        an=sum(anoh[p] for p in pits); bn=sum(bnoh[p] for p in pits)
        rn=sum(ratio_n[p] for p in pits); rd=sum(ratio_d[p] for p in pits)
        return {'potential':pot,'actual':act,'rows':rows,'residual':residual,'byMaterial':mat,'lm':_lm(tops),
                'lanes':lanes,'nohPct':an/bn*100 if bn else 0,'nohActual':an,'nohBudget':bn,'emptyFullRatio':rn/rd if rd else 0}
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
        full=defaultdict(lambda:[0.0,0,defaultdict(int)])
        for r in rs:
            L,D=r['LoadLocation'],r['DumpLocation']; t=num(r['Tonnage']); m=r['MaterialGroupName']
            lx[L].append(num(r['FieldGpsxtkl']));ly[L].append(num(r['FieldGpsytkl']));ltons[L][m]+=t
            dxx[D].append(num(r['FieldGpsxtkd']));dyy[D].append(num(r['FieldGpsytkd']));dtons[D]+=t
            ltp[L][0]+=t; ltp[L][1]+=max(NOH_FLOOR_S,num(r['SpotTime'])+num(r['LoadingTime'])+num(r['HangTime']))/3600.0
            if L!=D:
                f=full[(L,D)];f[0]+=t;f[1]+=1;f[2][m]+=1
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
        fF=[{'from':k[0],'to':k[1],'tons':round(v[0]),'mat':max(v[2],key=v[2].get)} for k,v in full.items() if k[0] in lset and k[1] in dset]
        flowcache[pit]={'loadNodes':loadN,'dumpNodes':dumpN,'fullFlows':fF}
    def agg_flows(pits):
        if len(pits)==1: return flowcache[pits[0]]
        out={'loadNodes':[],'dumpNodes':[],'fullFlows':[]}
        for p in pits:
            for k in out: out[k]+=flowcache[p][k]
        return out
    # ---- shift analytics ----
    def compute_analytics(pits):
        Lr=[r for r in t1 if r['LoadPit'] in pits]
        POre=sum(bud(p,'POre') for p in pits); PWst=sum(bud(p,'PWst') for p in pits)
        NPOre=sum(bud(p,'NPOre') for p in pits); NPWst=sum(bud(p,'NPWst') for p in pits)
        planTotal=POre+PWst
        bk=[0.0]*49
        for r in Lr:
            d=_dtp(r['DumpingTimestamp'])
            if not d: continue
            mn=mfs(d)
            if mn is None or mn<0: mn=0
            bk[min(48,int(mn//15))]+=num(r['Tonnage'])
        cum=[];s=0.0
        for i in range(49): s+=bk[i]; cum.append(s)
        last=max((i for i in range(49) if bk[i]>0),default=0)
        cumulative={'labels':[f"{(base+(i*15)//60)%24:02d}:{(i*15)%60:02d}" for i in range(49)],
            'actual':[round(cum[i]) if i<=last else None for i in range(49)],
            'target':[round(planTotal*i/48) for i in range(49)],'plan':round(planTotal)}
        ho=[0.0]*12;hw=[0.0]*12;hn=[0.0]*12
        for r in Lr:
            d=_dtp(r['DumpingTimestamp'])
            if not d: continue
            mn=mfs(d)
            if mn is None or mn<0 or mn>=720: continue
            i=int(mn//60); t=num(r['Tonnage'])
            if r['Category']=='Non-Productive': hn[i]+=t
            elif r['MaterialGroupName']=='Waste': hw[i]+=t
            else: ho[i]+=t
        hourly={'hours':[f"{(base+i)%24:02d}" for i in range(12)],'ore':[round(x) for x in ho],
            'waste':[round(x) for x in hw],'nonprod':[round(x) for x in hn],
            'target':round(planTotal/12),'targetOre':round(POre/12),'targetWaste':round(PWst/12),'targetNonProd':round((NPOre+NPWst)/12)}
        # timeline (shovels S0/S8)
        ev=[e for e in sev if e['Pit'] in pits and (e['Eqmt'][:2] in ('S0','S8'))]
        seg=defaultdict(list)
        for e in ev:
            d=_dtp(e['StartTime'])
            if not d: continue
            st=mfs(d); dur=num(e['Duration'])/60.0
            if st is None or dur<=0: continue
            seg[e['Eqmt']].append([round(max(0.0,st),1),round(dur,1),_statgrp(e['ASEStatus']),(e['Reason'] or '').title()])
        eq=sorted(seg, key=lambda k:-sum(x[1] for x in seg[k] if x[2]=='Ready'))
        shmat=defaultdict(lambda:defaultdict(float))
        for r in Lr:
            if r['Excav'][:2] in ('S0','S8'): shmat[r['Excav']][r['MaterialGroupName']]+=num(r['Tonnage'])
        smat={k:(max(shmat[k],key=shmat[k].get) if shmat[k] else '') for k in eq}
        q={k:qseries[k] for k in eq if qseries.get(k)}
        qmax=max([1]+[val for k in q for (_,val) in q[k]])
        timeline={'equip':eq,'seg':{k:seg[k] for k in eq},'queue':q,'qmax':qmax,'mat':smat,'base':base}
        # delay pareto
        rs=defaultdict(float); rc=defaultdict(int)
        for e in sev:
            if e['Pit'] in pits and e['ASEStatus']=='Delay':
                k=e['Reason'] or 'UNSPECIFIED'; rs[k]+=num(e['Duration'])/3600.0; rc[k]+=1
        top=sorted(rs.items(),key=lambda x:-x[1])[:20]; topk={k for k,v in top}
        other=sum(v for k,v in rs.items() if k not in topk); otherc=sum(c for k,c in rc.items() if k not in topk)
        rlist=[(k,v,rc[k]) for k,v in top]+([('Other',other,otherc)] if other>1 else [])
        tot=sum(v for k,v,c in rlist) or 1; cu=0; pareto={'reasons':[],'hours':[],'cumpct':[],'counts':[]}
        for k,v,c in rlist:
            cu+=v; pareto['reasons'].append(k.title()); pareto['hours'].append(round(v,1)); pareto['cumpct'].append(round(cu/tot*100,1)); pareto['counts'].append(c)
        # payload compliance (Tukey)
        pl=defaultdict(list); plm=defaultdict(lambda:defaultdict(float))
        for r in Lr:
            if r['Excav'][:2] in ('S0','S8'): pl[r['Excav']].append(num(r['Tonnage'])); plm[r['Excav']][r['MaterialGroupName']]+=num(r['Tonnage'])
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
        return {'cumulative':cumulative,'hourly':hourly,'timeline':timeline,'pareto':pareto,'payload':pay,'payloadTarget':PAYLOAD_TARGET}

    views={}
    for name,pits in [('MRM',['MRM']),('JPM',['JPM']),('Combined',PITS)]:
        views[name]={'haulage':agg_haul(pits),'loading':agg_load(pits),'truckBalance':agg_tb(pits),
                     'trucksWF':agg_wf(pits),'shovelWF':agg_shov(pits),'haulCycles':agg_flows(pits),'analytics':compute_analytics(pits)}
    smeta={'shift':sname,'fullLegFrac':round(FFULL,3),'vFull':round(v_full*3.6,1),'vEmpty':round(v_empty*3.6,1),'elapsed':round(elapsed,2)}
    return views,smeta

byShift={}; shiftlist=[]
for sm in SHIFTS:
    vw,mt=build_shift(sm)
    byShift[sm['id']]={'views':vw,'meta':mt}
    shiftlist.append({'id':sm['id'],'name':sm['name']})
out={'meta':{'generated':datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),'payloadTarget':PAYLOAD_TARGET},
     'shifts':shiftlist,'defaultShift':(SHIFTS[0]['id'] if SHIFTS else None),'byShift':byShift}
json.dump(out,open('dashboard_data.json','w'),indent=1)
print("shifts:",[s['name'] for s in SHIFTS])
for sid in byShift:
    v=byShift[sid]['views']['Combined']
    print("  %s haul %.1f%% load %.1f%% tb %.1f%%"%(sid,v['haulage']['score'],v['loading']['score'],v['truckBalance']['pct']))
