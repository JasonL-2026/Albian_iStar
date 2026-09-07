#!/usr/bin/env python3
"""
Fuel-level trustworthiness check.

Cross-checks the TruckAtLubeLand FuelLevel sensor readings against an independent
litres/tank calculation:

    % fuelled (of tank) = Fuel01 (litres)  /  FieldFueltank
    implied arrival level = 100 - % fuelled   (assumes filled to full)

Sources (in Data/):
  - shiftshiftfuel_temp.xlsx  : ShiftId, Equipment(truck), Fuel01 (litres), FuelStartTime
  - PitTruck_temp.xlsx        : FieldId(truck), FieldFueltank (tank size, litres)
  - TruckAtLubeLand.csv       : ShiftID, Eqmt, TimeStamp, Reason, FuelLevel (reported %)

Writes a text report to: fuel_level_trust_report.txt
"""
import os, csv, datetime
from collections import defaultdict

try:
    import openpyxl
except ImportError:
    raise SystemExit("openpyxl is required:  python -m pip install openpyxl")

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "Data")
REPORT = os.path.join(BASE, "fuel_level_trust_report.txt")

# tolerance bands and the flag threshold for a "believable but wrong" reading
TOL = (1, 2, 5)
FLAG_DELTA = 5          # |implied - reported| beyond this (non-faulty) = suspicious over/under-read


def xrows(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [str(h) for h in next(it)]
    out = [dict(zip(hdr, r)) for r in it]
    wb.close()
    return out


def _dtp(s):
    for f in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%y %I:%M:%S %p"):
        try:
            return datetime.datetime.strptime((s or "").strip(), f)
        except ValueError:
            pass
    return None


def main():
    tank = {str(r.get("FieldId")).strip(): r.get("FieldFueltank")
            for r in xrows(os.path.join(DATA, "PitTruck_temp.xlsx")) if r.get("FieldId")}
    fuel = xrows(os.path.join(DATA, "shiftshiftfuel_temp.xlsx"))

    # reported sensor levels: (shift, truck) -> [(sec_from_shift_base, level)]
    lube = defaultdict(list)
    with open(os.path.join(DATA, "TruckAtLubeLand.csv"), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if r["Reason"] != "FUEL&LUBE" or not r["FuelLevel"]:
                continue
            sid = r["ShiftID"]
            base = 6 if (sid and int(sid[6:9]) % 2 == 1) else 18   # day shift 06:00, night 18:00
            d = _dtp(r["TimeStamp"])
            if not d:
                continue
            sec = ((d.hour - base) % 24) * 3600 + d.minute * 60 + d.second
            try:
                lube[(sid, r["Eqmt"])].append((sec, float(r["FuelLevel"])))
            except ValueError:
                pass

    def nearest(sid, tk, sec):
        c = lube.get((sid, tk))
        return min(c, key=lambda x: abs(x[0] - sec))[1] if c else None

    tot = matched = faulty = faulty_recov = nolevel = notank = 0
    wcnt = {t: 0 for t in TOL}
    off = 0
    faultyrows, outrows = [], []

    for r in fuel:
        sid = str(r.get("ShiftId") or "")
        tk = str(r.get("Equipment") or "").strip()
        L = r.get("Fuel01")
        tsz = tank.get(tk)
        if not (isinstance(L, (int, float)) and L > 0):
            continue
        tot += 1
        if not (isinstance(tsz, (int, float)) and tsz > 0):
            notank += 1
            continue
        implied = 100 - L / tsz * 100
        rep = nearest(sid, tk, r.get("FuelStartTime") or 0)
        if rep is None:
            nolevel += 1
            continue
        matched += 1
        if rep > 100:
            faulty += 1
            if 0 <= implied <= 100:
                faulty_recov += 1
            faultyrows.append((sid, tk, round(implied, 1), round(rep, 1)))
            continue
        ad = abs(implied - rep)
        for t in TOL:
            if ad <= t:
                wcnt[t] += 1
        if ad > FLAG_DELTA:
            off += 1
            outrows.append((sid, tk, round(implied, 1), round(rep, 1), round(implied - rep, 1)))

    good = matched - faulty
    bad = faulty + off
    L = []
    L.append("=" * 72)
    L.append("TruckAtLubeLand  FuelLevel  —  Trustworthiness Report")
    L.append("Generated: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    L.append("Method: implied level = 100 - Fuel01/FieldFueltank ;  vs reported FuelLevel")
    L.append("=" * 72)
    L.append("")
    L.append(f"Fuel records (Fuel01 > 0)      : {tot}")
    L.append(f"  no tank size in PitTruck     : {notank}")
    L.append(f"  no matching lube reading     : {nolevel}   (fuelled but no FUEL&LUBE level)")
    L.append(f"  matched to a sensor reading  : {matched}")
    L.append("")
    L.append(f"Valid (non-faulty) readings    : {good}")
    for t in TOL:
        L.append(f"  within +/-{t}%                 : {wcnt[t]:4d}  ({wcnt[t]/good*100:.0f}%)")
    L.append(f"  off by > {FLAG_DELTA}%                  : {off:4d}  ({off/good*100:.0f}%)")
    L.append("")
    L.append(f"Faulty sensor (reported >100%) : {faulty}   (litres-recoverable to 0-100%: {faulty_recov})")
    L.append("")
    trust = (1 - bad / matched) * 100 if matched else 0
    L.append(f"OVERALL TRUST SCORE            : {trust:.0f}%   ({bad} of {matched} readings wrong)")
    L.append(f"  > current dashboard >100% rule catches {faulty} of {bad} bad reads "
             f"({faulty/bad*100:.0f}%); {off} plausible over/under-reads slip through.")
    L.append("")
    L.append("-" * 72)
    L.append(f"Faulty sensor rows (>100%)  [shift, truck, implied%, reported%]  (first 40)")
    L.append("-" * 72)
    for x in faultyrows[:40]:
        L.append(f"  {x[0]}  {x[1]:6}  implied {x[2]:6.1f}%   reported {x[3]:6.1f}%")
    L.append("")
    L.append("-" * 72)
    L.append(f"Non-faulty mismatches |delta| > {FLAG_DELTA}%  [shift, truck, implied%, reported%, delta]")
    L.append("-" * 72)
    for x in sorted(outrows, key=lambda z: -abs(z[4])):
        L.append(f"  {x[0]}  {x[1]:6}  implied {x[2]:6.1f}%   reported {x[3]:6.1f}%   delta {x[4]:+6.1f}")
    L.append("")

    text = "\n".join(L)
    with open(REPORT, "w", encoding="utf-8-sig") as fh:
        fh.write(text)
    print(text)
    print("\nReport written to:", REPORT)


if __name__ == "__main__":
    main()
