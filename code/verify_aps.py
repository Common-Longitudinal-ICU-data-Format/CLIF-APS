"""Independently re-derive the REDCap daily upload from raw CLIF 3.0 tables and check that REDCap will import it.

    uv run python code/verify_aps.py --config config/demo.json
    uv run python code/verify_aps.py --config config/real.json   # researcher, own terminal

Shares only the *definitions* with run_aps.py (config/redcap/daily_fields.csv, daily_mapping.yaml, clifpy's
outlier ranges). The computation is deliberately different: tz-aware local-midnight windows instead of
calendar-date joins, elapsed time to a localized 08:00 timestamp instead of minutes-of-day, and a plain
per-record loop instead of vectorized sorts.

Writes output/summary/verify_<label>.csv (aggregates only, counts 1-10 shown as "<11") and
<output_directory>/verify_mismatches.csv (row-level; under output/real for real data, never read by Claude).
Exit code 1 if any error-level check fails or any cell disagrees.
"""
import argparse
import contextlib
import io
import json
import logging
import re
import warnings
from collections import Counter
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "config" / "redcap" / "daily_fields.csv"
MAPPING_PATH = ROOT / "config" / "redcap" / "daily_mapping.yaml"
SUMMARY_DIR = ROOT / "output" / "summary"
FORMATS = {"integer": r"-?\d+", "number": r"-?\d+(\.\d+)?", "number_1dp": r"-?\d+\.\d"}


def suppress(n):
    return "<11" if 0 < n < 11 else str(int(n))


def unit_key(u):
    if u is None or pd.isna(u):
        return None
    return "".join(str(u).split()).lower().replace("µ", "u").replace("μ", "u").replace("mcl", "ul").replace("*", "^") or None


def fmt(x, validation, decimals=None):
    if x is None or pd.isna(x):
        return ""
    places = decimals if decimals is not None else {"integer": 0, "number_1dp": 1}.get(validation, 4)
    d = Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    s = format(d, "f")
    if validation == "number" and decimals is None and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def numeric(value_numeric, value_text):
    if pd.notna(value_numeric):
        return float(value_numeric)
    m = re.fullmatch(r"\s*[<>]=?\s*(-?\d+(?:\.\d+)?)\s*", str(value_text))
    return float(m.group(1)) if m else None


def choose(events, method, anchor):
    """events: list of (timestamp, value). Returns the chosen value or None."""
    if not events:
        return None
    if method == "closest":
        return min(events, key=lambda e: (abs(e[0] - anchor), e[0], e[1]))[1]
    if method == "min":
        return min(v for _, v in events)
    if method == "max":
        return max(v for _, v in events)
    raise ValueError(method)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    logging.disable(logging.INFO)
    warnings.filterwarnings("ignore", category=FutureWarning)
    from clifpy.clif_orchestrator import ClifOrchestrator
    from clifpy.utils.outlier_handler import apply_outlier_handling

    if not Path(args.config).exists():
        raise SystemExit(f"{args.config} not found: cp config/real.example.json config/real.json and set data_directory (see README step 3)")
    cfg = json.loads(Path(args.config).read_text())
    data_dir = Path(cfg["data_directory"]) if Path(cfg["data_directory"]).is_absolute() else ROOT / cfg["data_directory"]
    out_dir = Path(cfg["output_directory"]) if Path(cfg["output_directory"]).is_absolute() else ROOT / cfg["output_directory"]
    tz = cfg["timezone"]
    if not SPEC_PATH.exists():
        raise SystemExit("config/redcap/daily_fields.csv not found. The APS codebook is not in the repo: export it to config/redcap/daily_assessment_codebook_day_m2.csv and run `uv run python code/build_redcap_spec.py` (see README).")
    spec = pd.read_csv(SPEC_PATH, dtype=str).fillna("").set_index("field_template")
    mp = yaml.safe_load(MAPPING_PATH.read_text())
    days = {int(k): str(v) for k, v in mp["days"].items()}
    anchor_hour = mp["anchor_hour"]
    gate = mp["gate_field"]

    upload = pd.read_csv(out_dir / mp["output_filename"], dtype=str, keep_default_na=False)
    checks = Counter()      # (severity, check) -> count of offending cells/rows
    mismatches = []         # row-level detail, written under output_directory only

    # ------------------------------------------------------------------ REDCap import checks (file only)
    field_of = {t.replace("{d}", s): (t, s) for t in spec.index for s in days.values()}
    cols = [c for c in upload.columns if c not in ("record_id", "redcap_event_name")]
    checks[("error", "header: column not in codebook")] += sum(c not in field_of for c in cols)
    checks[("error", "header: duplicate column")] += upload.columns.duplicated().sum()
    checks[("error", "redcap_event_name wrong")] += (upload["redcap_event_name"] != mp["redcap_event_name"]).sum()
    checks[("error", "record_id duplicated")] += upload["record_id"].duplicated().sum()
    checks[("error", "record_id blank")] += (upload["record_id"].str.strip() == "").sum()
    decimals_override = {f: m["decimals"] for f, m in mp["labs"].items() if "decimals" in m}
    for c in cols:
        if c not in field_of:
            continue
        t, _ = field_of[c]
        f = spec.loc[t]
        vals = upload[c][upload[c] != ""]
        checks[("error", "literal nan/None/NaT")] += vals.isin(["nan", "NaN", "None", "NaT", "<NA>"]).sum()
        if f["field_type"] == "text" and f["validation"] in FORMATS:
            if t in decimals_override:
                bad_override = ~vals.str.fullmatch(rf"-?\d+\.\d{{{decimals_override[t]}}}")
                checks[("error", "format invalid for override")] += bad_override.sum()
                checks[("warning", f"codebook says {f['validation']}, exported at {decimals_override[t]} dp: {t}")] += (~bad_override).sum()
            else:
                checks[("error", f"format invalid ({f['validation']})")] += (~vals.str.fullmatch(FORMATS[f["validation"]])).sum()
            num = pd.to_numeric(vals, errors="coerce")
            lo = float(f["min"]) if f["min"] else float("-inf")
            hi = float(f["max"]) if f["max"] else float("inf")
            n_out = (~num.between(lo, hi)).sum()
            if n_out:
                checks[("warning", f"outside REDCap min/max (import warning): {t}")] += n_out
        elif f["field_type"] in ("radio", "yesno", "dropdown"):
            checks[("error", "choice code not in codebook")] += (~vals.isin(f["choice_codes"].split("|"))).sum()

    # ------------------------------------------------------------------ independent recomputation
    trial_file = data_dir / f"clif_clinical_trial.{cfg['filetype']}"
    if not trial_file.exists():
        raise SystemExit(f"{trial_file.name} not found in data_directory; the CLIF 3.0 clinical_trial table is required (see README).")
    trials = pd.read_parquet(trial_file) if cfg["filetype"] == "parquet" else pd.read_csv(trial_file)
    trials["record_id"] = trials["participant_id"].astype(str)
    clif = ClifOrchestrator(data_directory=str(data_dir), filetype=cfg["filetype"], timezone=tz,
                            output_directory=str(out_dir), clif_version="3.0")
    hosp = clif.load_table("hospitalization", filters={"hospitalization_id": trials["hospitalization_id"].astype(str).tolist()}).df
    hosp_ids = hosp["hospitalization_id"].tolist()

    def raw(table, value_col, columns=None):
        obj = clif.load_table(table, columns=columns, filters={"hospitalization_id": hosp_ids})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):  # clifpy progress bars
            apply_outlier_handling(obj)
        return obj.df

    vitals = raw("vitals", "vital_value")  # all columns: picks up CLIF 3.0 measured_dttm when the site has it
    if "measured_dttm" not in vitals.columns:
        vitals["measured_dttm"] = pd.NaT
    labs = raw("labs", "lab_value_numeric", ["hospitalization_id", "lab_collect_dttm", "lab_result_dttm", "lab_category",
                                              "lab_value", "lab_value_numeric", "reference_unit"])
    vit_by_h = {h: g for h, g in vitals.groupby("hospitalization_id")}
    lab_by_h = {h: g for h, g in labs.groupby("hospitalization_id")}

    def localize(dt):
        return pd.Timestamp(dt).tz_localize(tz)

    def site_time(x):
        if pd.isna(x):
            return pd.NaT
        x = pd.Timestamp(x)
        return x.tz_localize(tz) if x.tzinfo is None else x.tz_convert(tz)

    index_hosp = {}  # record_id -> the trial's hospitalization row, if it exists and belongs to that patient
    by_id = {str(h.hospitalization_id): h for h in hosp.itertuples(index=False)}
    for tr in trials.itertuples(index=False):
        h = by_id.get(str(tr.hospitalization_id))
        index_hosp[str(tr.record_id)] = h if h is not None and str(h.patient_id) == str(tr.patient_id) else None
    index_ids = {h.hospitalization_id for h in index_hosp.values() if h is not None}
    lab_cats_present = set(labs.loc[labs["hospitalization_id"].isin(index_ids), "lab_category"])

    up = upload.drop_duplicates("record_id").set_index("record_id")  # duplicates already counted above
    expected_records = set(trials["record_id"].astype(str))
    checks[("error", "record_id in trial table but not in upload")] += len(expected_records - set(up.index))
    checks[("error", "record_id in upload but not in trial table")] += len(set(up.index) - expected_records)

    compared = Counter()
    disagree = Counter()
    for tr in trials.itertuples(index=False):
        rid = str(tr.record_id)
        enroll = site_time(tr.enrollment_dttm)
        withdraw = site_time(getattr(tr, "withdrawal_dttm", pd.NaT))
        idx = index_hosp[rid]
        vit = vit_by_h.get(idx.hospitalization_id) if idx is not None else None
        lab = lab_by_h.get(idx.hospitalization_id) if idx is not None else None

        for d, s in days.items():
            day_date = enroll.date() + timedelta(days=d)
            start = localize(datetime.combine(day_date, time(0)))
            end = localize(datetime.combine(day_date + timedelta(days=1), time(0)))
            anchor = localize(datetime.combine(day_date, time(anchor_hour)))
            available = (idx is not None and idx.admission_dttm < end
                         and (pd.isna(idx.discharge_dttm) or idx.discharge_dttm >= start)
                         and (pd.isna(withdraw) or withdraw >= start))
            exp = {gate.replace("{d}", s): "1" if available else "0"}
            if available:
                for t, m in mp["vitals"].items():
                    ev = [] if vit is None else [
                        (when, float(r.vital_value)) for r in vit.itertuples(index=False)
                        for when in [r.measured_dttm if pd.notna(r.measured_dttm) else r.recorded_dttm]
                        if r.vital_category == m["clif"] and pd.notna(r.vital_value) and start <= when < end]
                    exp[t.replace("{d}", s)] = fmt(choose(ev, m["method"], anchor), spec.loc[t, "validation"])
                for t, m in mp["labs"].items():
                    factors = {m["unit"]: 1.0, **{unit_key(k): v for k, v in (m.get("convert") or {}).items()}}
                    ev = []
                    for r in ([] if lab is None else lab.itertuples(index=False)):
                        if r.lab_category not in m["clif"]:
                            continue
                        when = r.lab_collect_dttm if pd.notna(r.lab_collect_dttm) else r.lab_result_dttm
                        v = numeric(r.lab_value_numeric, r.lab_value)
                        if v is None or pd.isna(when) or not (start <= when < end):
                            continue
                        if m["unit"] is not None:
                            if unit_key(r.reference_unit) not in factors:
                                continue
                            v *= factors[unit_key(r.reference_unit)]
                        ev.append((when, v))
                    value = fmt(choose(ev, m["method"], anchor), spec.loc[t, "validation"], m.get("decimals"))
                    exp[t.replace("{d}", s)] = value
                    in_cohort = bool(lab_cats_present & set(m["clif"]))
                    exp[m["nc"].replace("{d}", s)] = "1" if (value == "" and in_cohort) else ""

            # compare every field this day could hold
            row = up.loc[rid] if rid in up.index else None
            day_fields = [c for c in cols if field_of.get(c, (None, None))[1] == s]
            for c in set(day_fields) | set(exp):
                want = exp.get(c, "")
                got = "" if row is None or c not in row.index else row[c]
                t = field_of[c][0] if c in field_of else c
                compared[t] += 1
                if want != got:
                    try:
                        if want and got and float(want) == float(got):
                            continue
                    except ValueError:
                        pass
                    disagree[t] += 1
                    mismatches.append({"record_id": rid, "day": d, "field": c, "expected": want, "upload": got})

            # branching consistency on the uploaded values
            if row is not None:
                g = row.get(gate.replace("{d}", s), "")
                if g == "0":
                    checks[("error", "gate = 0 but other fields filled")] += sum(row[c] != "" for c in day_fields if c != gate.replace("{d}", s))
                if g == "":
                    checks[("error", "gate field blank")] += 1
                for t, m in mp["labs"].items():
                    vcol, nccol = t.replace("{d}", s), m["nc"].replace("{d}", s)
                    if row.get(vcol, "") != "" and row.get(nccol, "") != "":
                        checks[("error", "lab value and not-collected both set")] += 1

    # ------------------------------------------------------------------ report
    out = [{"section": "import", "severity": sev, "check": name, "count": n} for (sev, name), n in sorted(checks.items())]
    for t in spec.index:
        if compared[t]:
            out.append({"section": "agreement", "severity": "error" if disagree[t] else "ok",
                        "check": f"cells disagreeing (of {compared[t]} compared): {t}", "count": disagree[t]})
    rep = pd.DataFrame(out)
    rep["count_reported"] = rep["count"].map(suppress)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    rep[["section", "severity", "check", "count_reported"]].to_csv(SUMMARY_DIR / f"verify_{cfg['label']}.csv", index=False)
    pd.DataFrame(mismatches, columns=["record_id", "day", "field", "expected", "upload"]).to_csv(out_dir / "verify_mismatches.csv", index=False)

    failed = rep[(rep["severity"] == "error") & (rep["count"] > 0)]
    print(rep[["section", "severity", "check", "count_reported"]].to_csv(index=False), end="")
    print(f"RESULT: {'FAIL' if len(failed) else 'PASS'}  ({len(failed)} failing checks, {suppress(sum(disagree.values()))} disagreeing cells)")
    raise SystemExit(1 if len(failed) else 0)


if __name__ == "__main__":
    main()
