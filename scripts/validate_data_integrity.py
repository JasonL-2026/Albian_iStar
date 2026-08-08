#!/usr/bin/env python3
import argparse
import csv
import json
import re
import sys
from pathlib import Path


def _normcol(header: str) -> str:
    h = (header or "").replace("\ufeff", "").strip()
    if h.lower().startswith("dtl_"):
        h = h[4:]
        h = re.sub(r"_\d+$", "", h)
    return re.sub(r"[^a-z0-9]", "", h.lower())


def _resolve_colname(headers, wanted):
    norm_map = {_normcol(h): h for h in headers}
    for name in wanted:
        if name in norm_map:
            return norm_map[name]
    return None


def _read_headers(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        return next(reader, [])


def _extract_shift_ids(path: Path, shift_candidates):
    shift_ids = set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return shift_ids
        col = _resolve_colname(reader.fieldnames, shift_candidates)
        if not col:
            return shift_ids
        for row in reader:
            val = (row.get(col) or "").strip()
            if val:
                shift_ids.add(val)
    return shift_ids


def validate_schema_and_shift(data_dir: Path) -> int:
    required = {
        "AllLoadsDumps.csv": {"pit", "totalrequired", "lpactual", "timestamp"},
        "Statusevents.csv": {"shiftid", "timestamp", "eqmt", "duration", "pit", "reason"},
        "TruckBalance.csv": {"shiftid", "fullshiftname", "loadpit", "loadingtimestamp", "category", "measuredton"},
        "TruckatShovel.csv": {"shiftid", "logtime", "trucksatshovel", "trucksinqueue", "qstatus"},
        "TruckAtDump.csv": {"shiftid", "logtime", "trucksatdump", "trucksinqueue", "qstatus"},
        "TruckAtLubeLand.csv": {"shiftid", "eqmt", "starttime", "duration", "pit"},
    }
    shift_files = ["Statusevents.csv", "TruckBalance.csv", "TruckatShovel.csv", "TruckAtDump.csv", "TruckAtLubeLand.csv"]
    shift_candidates = {"shiftid", "dshiftid"}

    failures = []
    shifts_by_file = {}

    for filename, req_cols in required.items():
        fp = data_dir / filename
        if not fp.exists():
            failures.append(f"Missing required file: {fp}")
            continue

        headers = _read_headers(fp)
        norm_headers = {_normcol(h) for h in headers}
        missing = sorted(req_cols - norm_headers)
        if missing:
            failures.append(f"{filename}: missing required columns: {', '.join(missing)}")

        if filename in shift_files:
            shifts = _extract_shift_ids(fp, shift_candidates)
            shifts_by_file[filename] = shifts
            if not shifts:
                failures.append(f"{filename}: no ShiftId values found")
            else:
                bad = sorted(s for s in shifts if not re.fullmatch(r"\d{9}", s))
                if bad:
                    failures.append(f"{filename}: invalid ShiftId format(s): {', '.join(bad[:5])}")

    if shifts_by_file:
        intersection = None
        for _, shift_ids in shifts_by_file.items():
            if intersection is None:
                intersection = set(shift_ids)
            else:
                intersection &= set(shift_ids)
        if not intersection:
            failures.append("No common ShiftId exists across shift-bearing source files")

    if failures:
        print("Data integrity schema/ShiftId validation failed:")
        for f in failures:
            print(f"- {f}")
        return 1

    print("Schema and ShiftId consistency checks passed.")
    return 0


def _get_nested(data, dotted_key):
    cur = data
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted_key)
        cur = cur[part]
    return cur


def validate_kpi(dashboard_json: Path, baseline_json: Path) -> int:
    dashboard = json.loads(dashboard_json.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_json.read_text(encoding="utf-8"))

    shift_id = baseline["shift_id"]
    view = baseline["view"]
    metrics = baseline["metrics"]

    if shift_id not in dashboard.get("byShift", {}):
        print(f"Expected baseline shift_id '{shift_id}' not found in dashboard_data.json")
        return 1

    payload = dashboard["byShift"][shift_id]["views"][view]
    failures = []

    for metric_name, cfg in metrics.items():
        expected = float(cfg["expected"])
        tolerance = float(cfg["tolerance"])
        actual = float(_get_nested(payload, metric_name))
        delta = abs(actual - expected)
        if delta > tolerance:
            failures.append(
                f"{metric_name}: actual={actual:.6f}, expected={expected:.6f}, "
                f"tolerance=±{tolerance:.6f}, delta={delta:.6f}"
            )

    if failures:
        print("KPI tolerance validation failed:")
        for f in failures:
            print(f"- {f}")
        return 1

    print("KPI tolerance checks passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Validate data integrity gates for promotions.")
    parser.add_argument("--mode", choices=["schema-shift", "kpi"], required=True)
    parser.add_argument("--data-dir", default="Data/samples")
    parser.add_argument("--dashboard-json", default="dashboard_data.json")
    parser.add_argument("--baseline", default="Data/samples/kpi_baseline.json")
    args = parser.parse_args()

    if args.mode == "schema-shift":
        return validate_schema_and_shift(Path(args.data_dir))
    return validate_kpi(Path(args.dashboard_json), Path(args.baseline))


if __name__ == "__main__":
    sys.exit(main())
