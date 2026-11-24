"""CLIF 2.1 -> REDCap import CSV for the APS "Daily Assessment/SOFA Scoring" instruments (study days -2..7).

    uv run python code/run_aps.py --config config/demo.json     # Claude / development
    uv run python code/run_aps.py --config config/real.json     # researcher, own terminal

Writes (output_directory from the config):
  <output_directory>/daily_assessment_upload.csv     row-level, for the REDCap Data Import Tool
  <output_directory>/daily_assessment_evidence.parquet  one row per filled cell with its source timestamp
  output/summary/run_<label>.csv                      aggregates only, counts 1-10 shown as "<11"
stdout carries the aggregate summary only.

Rules (see config/redcap/daily_mapping.yaml for the field list):
  - Study day 0 = calendar date of enrollment_dttm in site-local time; day d = that date + d.
  - Index hospitalization = the one whose admission..discharge contains enrollment_dttm (earliest if several).
    Only its data is used. A day is Available (gate = 1) when its date falls between the admission and
    discharge dates (and is not after withdrawal); otherwise gate = 0 and every other field stays blank.
  - "Closest to 8am" = smallest |t - 08:00 local| on that date, ties to the earlier time, then the lower value.
    Labs use lab_collect_dttm, falling back to lab_result_dttm.
  - Physiologically implausible values are nulled with clifpy's outlier ranges (counted in the summary).
    REDCap min/max are soft limits (import warnings, e.g. ferritin max 999); values outside them are kept
    and reported by code/verify_aps.py.
  - Lab "not collected" (= 1) is written only when the day is Available, no value exists, and the lab
    category appears in this cohort's data at all; a category the site never maps stays blank.
"""
import argparse
import contextlib
import io
import json
import logging
import re
import warnings
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "config" / "redcap" / "daily_fields.csv"
MAPPING_PATH = ROOT / "config" / "redcap" / "daily_mapping.yaml"
SUMMARY_DIR = ROOT / "output" / "summary"
SMALL_CELL = 11
CENSORED = re.compile(r"^\s*[<>]=?\s*(-?\d+(?:\.\d+)?)\s*$")


# ----------------------------------------------------------------------------------------------- setup
def load_config(path):
    if not Path(path).exists():
        raise SystemExit(f"{path} not found: cp config/real.example.json config/real.json and set data_directory (see README step 3)")
    cfg = json.loads(Path(path).read_text())
    for key in ("data_directory", "output_directory"):
        p = Path(cfg[key])
        cfg[key] = str(p if p.is_absolute() else ROOT / p)
    return cfg


def load_spec():
    if not SPEC_PATH.exists():
        raise SystemExit("config/redcap/daily_fields.csv not found. The APS codebook is not in the repo: export it to config/redcap/daily_assessment_codebook_day_m2.csv and run `uv run python code/build_redcap_spec.py` (see README).")
    spec = pd.read_csv(SPEC_PATH, dtype=str).fillna("")
    return spec.set_index("field_template")


def suppress(n):
    return "<11" if 0 < n < SMALL_CELL else str(int(n))


def normalize_unit(u):
    if u is None or pd.isna(u):
        return None
    u = re.sub(r"\s+", "", str(u)).lower().replace("µ", "u").replace("μ", "u").replace("mcl", "ul").replace("*", "^")
    return u or None


def to_site_tz(s, tz):
    s = pd.to_datetime(s)
    return s.dt.tz_localize(tz) if s.dt.tz is None else s.dt.tz_convert(tz)


def load_trials(cfg):
    path = Path(cfg["data_directory"]) / f"clif_clinical_trials.{cfg['filetype']}"
    trials = pd.read_parquet(path) if cfg["filetype"] == "parquet" else pd.read_csv(path)
    trials["patient_id"] = trials["patient_id"].astype(str)
    trials["record_id"] = trials["record_id"].astype(str)
    trials["enrollment_dttm"] = to_site_tz(trials["enrollment_dttm"], cfg["timezone"])
    trials["withdrawal_dttm"] = to_site_tz(trials.get("withdrawal_dttm", pd.Series(pd.NaT, index=trials.index)), cfg["timezone"])
    if trials["record_id"].duplicated().any():
        raise SystemExit("clinical_trials: record_id is not unique")
    return trials


# ------------------------------------------------------------------------------------------- cohort
def assign_index_hospitalization(trials, hosp):
    cand = trials.merge(hosp[["patient_id", "hospitalization_id", "admission_dttm", "discharge_dttm"]], on="patient_id")
    open_end = cand["discharge_dttm"].isna()
    contains = (cand["admission_dttm"] <= cand["enrollment_dttm"]) & (open_end | (cand["enrollment_dttm"] <= cand["discharge_dttm"]))
    idx = cand[contains].sort_values(["record_id", "admission_dttm"]).drop_duplicates("record_id")
    return trials.merge(idx[["record_id", "hospitalization_id", "admission_dttm", "discharge_dttm"]], on="record_id", how="left")


def build_days(cohort, days):
    rows = []
    for r in cohort.itertuples(index=False):
        day0 = r.enrollment_dttm.date()
        for d, suffix in days.items():
            date = day0 + timedelta(days=d)
            available = (
                pd.notna(r.hospitalization_id)
                and r.admission_dttm.date() <= date
                and (pd.isna(r.discharge_dttm) or date <= r.discharge_dttm.date())
                and (pd.isna(r.withdrawal_dttm) or date <= r.withdrawal_dttm.date())
            )
            rows.append({"record_id": r.record_id, "day": d, "suffix": suffix, "date": date, "available": bool(available)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------- values
def pick(events, method, anchor_hour):
    """events: record_id, date, dttm, value -> one row per (record_id, date)."""
    if events.empty:
        return events.assign(source_dttm=pd.NaT)[["record_id", "date", "value", "source_dttm"]]
    ev = events.copy()
    if method == "closest":
        minutes = ev["dttm"].dt.hour * 60 + ev["dttm"].dt.minute + ev["dttm"].dt.second / 60
        ev["_key"] = (minutes - anchor_hour * 60).abs()
        ev = ev.sort_values(["record_id", "date", "_key", "dttm", "value"])
    elif method == "min":
        ev = ev.sort_values(["record_id", "date", "value", "dttm"])
    elif method == "max":
        ev = ev.sort_values(["record_id", "date", "value", "dttm"], ascending=[True, True, False, True])
    else:
        raise ValueError(method)
    out = ev.drop_duplicates(["record_id", "date"])
    return out.rename(columns={"dttm": "source_dttm"})[["record_id", "date", "value", "source_dttm"]]


def format_value(x, validation, decimals=None):
    if x is None or pd.isna(x):
        return ""
    if decimals is not None:
        q = Decimal(1).scaleb(-int(decimals))
    elif validation == "integer":
        q = Decimal(1)
    elif validation == "number_1dp":
        q = Decimal("0.1")
    else:
        q = Decimal("0.0001")
    s = str(Decimal(str(float(x))).quantize(q, rounding=ROUND_HALF_UP))
    if validation == "number" and decimals is None and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def load_without_outliers(clif, table, value_col, **kwargs):
    """Load a clifpy table and null physiologically implausible values with clifpy's outlier ranges."""
    from clifpy.utils.outlier_handler import apply_outlier_handling
    obj = clif.load_table(table, **kwargs)
    before = obj.df[value_col].notna()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):  # clifpy progress bars
        apply_outlier_handling(obj)
    return obj.df, int((before & obj.df[value_col].isna()).sum())


def lab_numeric(labs):
    num = labs["lab_value_numeric"].astype(float)
    parsed = labs["lab_value"].astype(str).str.extract(CENSORED)[0].astype(float)
    return num.fillna(parsed)


# --------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    logging.disable(logging.INFO)
    warnings.filterwarnings("ignore", category=FutureWarning)
    from clifpy.clif_orchestrator import ClifOrchestrator

    cfg = load_config(args.config)
    spec = load_spec()
    mapping = yaml.safe_load(MAPPING_PATH.read_text())
    days = {int(k): str(v) for k, v in mapping["days"].items()}
    anchor = mapping["anchor_hour"]
    out_dir = Path(cfg["output_directory"])
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []  # (metric, count)

    clif = ClifOrchestrator(data_directory=cfg["data_directory"], filetype=cfg["filetype"],
                            timezone=cfg["timezone"], output_directory=str(out_dir))
    trials = load_trials(cfg)
    hosp = clif.load_table("hospitalization", filters={"patient_id": trials["patient_id"].tolist()}).df
    hosp["patient_id"] = hosp["patient_id"].astype(str)
    cohort = assign_index_hospitalization(trials, hosp)
    day_df = build_days(cohort, days)
    hosp_ids = cohort["hospitalization_id"].dropna().tolist()
    hosp_to_record = cohort.dropna(subset=["hospitalization_id"]).set_index("hospitalization_id")["record_id"]

    summary += [("records_in_trial_table", len(trials)),
                ("records_with_index_hospitalization", cohort["hospitalization_id"].notna().sum()),
                ("records_without_index_hospitalization", cohort["hospitalization_id"].isna().sum())]

    def with_record_and_date(df, time_col):
        df = df[df["hospitalization_id"].isin(hosp_to_record.index)].copy()
        df["record_id"] = df["hospitalization_id"].map(hosp_to_record)
        df["dttm"] = df[time_col]
        df["date"] = df["dttm"].dt.date
        return df

    cells = []  # record_id, date, field_template, value(str), source_dttm

    # ---- vitals
    vit_cats = sorted({m["clif"] for m in mapping["vitals"].values()})
    vit, n_out = load_without_outliers(clif, "vitals", "vital_value",
                                       columns=["hospitalization_id", "recorded_dttm", "vital_category", "vital_value"],
                                       filters={"hospitalization_id": hosp_ids, "vital_category": vit_cats})
    summary.append(("vital_rows_nulled_as_outliers", n_out))
    vit = with_record_and_date(vit, "recorded_dttm")
    for field, m in mapping["vitals"].items():
        ev = vit[vit["vital_category"] == m["clif"]].rename(columns={"vital_value": "value"})
        ev = ev[ev["value"].notna()]
        picked = pick(ev, m["method"], anchor)
        picked["value"] = picked["value"].map(lambda x, f=field: format_value(x, spec.loc[f, "validation"]))
        cells.append(picked.assign(field_template=field))

    # ---- labs
    lab_cats = sorted({c for m in mapping["labs"].values() for c in m["clif"]})
    labs, n_out = load_without_outliers(clif, "labs", "lab_value_numeric",
                                        columns=["hospitalization_id", "lab_collect_dttm", "lab_result_dttm", "lab_category",
                                                 "lab_value", "lab_value_numeric", "reference_unit"],
                                        filters={"hospitalization_id": hosp_ids, "lab_category": lab_cats})
    summary.append(("lab_rows_nulled_as_outliers", n_out))
    labs["lab_time"] = labs["lab_collect_dttm"].fillna(labs["lab_result_dttm"])
    summary.append(("lab_rows_timed_by_result_dttm", (labs["lab_collect_dttm"].isna() & labs["lab_result_dttm"].notna()).sum()))
    labs = with_record_and_date(labs, "lab_time")
    labs["value"] = lab_numeric(labs)
    labs["unit"] = labs["reference_unit"].map(normalize_unit)
    present_cats = set(labs["lab_category"].unique())
    nc_fields = {}  # nc field template -> whether the category is present in this cohort
    for field, m in mapping["labs"].items():
        ev = labs[labs["lab_category"].isin(m["clif"]) & labs["value"].notna()].copy()
        expected = m.get("unit")
        factors = {expected: 1.0, **{normalize_unit(k): v for k, v in (m.get("convert") or {}).items()}}
        if expected is None:
            ok_unit = pd.Series(True, index=ev.index)
        else:
            ok_unit = ev["unit"].isin(factors.keys())
            ev.loc[ok_unit, "value"] = ev.loc[ok_unit, "value"] * ev.loc[ok_unit, "unit"].map(factors)
        summary.append((f"unit_excluded:{field}", (~ok_unit).sum()))
        ev = ev[ok_unit]
        picked = pick(ev, m["method"], anchor)
        picked["value"] = picked["value"].map(lambda x, f=field, dp=m.get("decimals"): format_value(x, spec.loc[f, "validation"], dp))
        cells.append(picked.assign(field_template=field))
        nc_fields[m["nc"]] = (field, bool(present_cats & set(m["clif"])))
        summary.append((f"lab_rows_in_cohort:{field}", labs["lab_category"].isin(m["clif"]).sum()))

    # ---- assemble long table on available days only
    cells = pd.concat(cells, ignore_index=True)
    cells = cells.merge(day_df[day_df["available"]][["record_id", "date", "day", "suffix"]], on=["record_id", "date"])
    long = [cells[["record_id", "day", "suffix", "field_template", "value", "source_dttm"]]]

    gate = day_df.assign(field_template=mapping["gate_field"], value=day_df["available"].map({True: "1", False: "0"}))
    long.append(gate[["record_id", "day", "suffix", "field_template", "value"]])

    avail = day_df[day_df["available"]][["record_id", "day", "suffix"]]
    for nc_field, (value_field, category_present) in nc_fields.items():
        if not category_present:
            continue
        have = cells[cells["field_template"] == value_field][["record_id", "day"]]
        missing = avail.merge(have, on=["record_id", "day"], how="left", indicator=True)
        missing = missing[missing["_merge"] == "left_only"].drop(columns="_merge")
        long.append(missing.assign(field_template=nc_field, value="1"))
    long = pd.concat(long, ignore_index=True)
    long["field"] = [t.replace("{d}", s) for t, s in zip(long["field_template"], long["suffix"])]

    # ---- wide REDCap CSV: record_id, redcap_event_name, then day by day in codebook order
    used = set(long["field_template"])
    order = [t.replace("{d}", s) for s in days.values() for t in spec.index if t in used]
    wide = long.pivot(index="record_id", columns="field", values="value").reindex(columns=order)
    wide = wide.reindex(trials["record_id"]).fillna("")
    wide.insert(0, "redcap_event_name", mapping["redcap_event_name"])
    wide = wide.reset_index()
    upload = out_dir / mapping["output_filename"]
    wide.to_csv(upload, index=False)
    long.drop(columns=["field_template"]).to_parquet(out_dir / "daily_assessment_evidence.parquet", index=False)

    # ---- aggregate summary
    for d, s in days.items():
        summary.append((f"day_{s}_available", day_df[(day_df["day"] == d) & day_df["available"]].shape[0]))
    for t in [t for t in spec.index if t in used and t != mapping["gate_field"]]:
        summary.append((f"filled:{t}", (long["field_template"] == t).sum()))
    summary.append(("columns_in_upload", wide.shape[1]))
    summary.append(("rows_in_upload", wide.shape[0]))

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame(summary, columns=["metric", "count"])
    out["count_reported"] = out["count"].map(suppress)
    out[["metric", "count_reported"]].to_csv(SUMMARY_DIR / f"run_{cfg['label']}.csv", index=False)
    print(out[["metric", "count_reported"]].to_csv(index=False), end="")


if __name__ == "__main__":
    main()
