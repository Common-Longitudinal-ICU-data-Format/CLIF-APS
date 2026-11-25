"""CLIF 3.0 -> REDCap import CSV for the APS "Daily Assessment/SOFA Scoring" instruments (study days -2..7).

    uv run python code/run_aps.py --config config/demo.json     # Claude / development
    uv run python code/run_aps.py --config config/real.json     # researcher, own terminal

Writes (output_directory from the config):
  <output_directory>/daily_assessment_upload.csv     row-level, for the REDCap Data Import Tool
  <output_directory>/daily_assessment_evidence.parquet  one row per filled cell with its source timestamp
  output/summary/run_<label>.csv                      aggregates only, counts 1-10 shown as "<11"
stdout carries the aggregate summary only.

Rules (see config/redcap/daily_mapping.yaml for the field list):
  - Study day 0 = calendar date of enrollment_dttm in site-local time; day d = that date + d.
  - Cohort = CLIF 3.0 clinical_trial (required): participant_id -> REDCap record_id; its hospitalization_id is
    the index stay and only that stay's data is used. A day is Available (gate = 1) when its date falls between the admission and
    discharge dates (and is not after withdrawal); otherwise gate = 0 and every other field stays blank.
  - "Closest to 8am" = smallest |t - 08:00 local| on that date, ties to the earlier time, then the lower value.
    Labs use lab_collect_dttm, falling back to lab_result_dttm; vitals use measured_dttm (3.0) if present,
    else recorded_dttm.
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
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import yaml

from coverage import write_coverage
from daily_derive import infusion_at, nearest_value, norm_unit, resp_at, resp_timeline, support_code
from redcap_branching import Branching

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "config" / "redcap" / "daily_fields.csv"
MAPPING_PATH = ROOT / "config" / "redcap" / "daily_mapping.yaml"
SUMMARY_DIR = ROOT / "output" / "summary"
SMALL_CELL = 11
CLIF_VERSION = "3.0"  # every table is loaded and validated against the CLIF 3.0 schema
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


TRIAL_REQUIRED = ("participant_id", "patient_id", "hospitalization_id", "enrollment_dttm")


def table_columns(cfg, table):
    """Column names in a CLIF file without reading its rows (optional 3.0 columns may be absent)."""
    path = Path(cfg["data_directory"]) / f"clif_{table}.{cfg['filetype']}"
    if cfg["filetype"] == "parquet":
        import pyarrow.parquet as pq
        return set(pq.read_schema(path).names)
    return set(pd.read_csv(path, nrows=0).columns)


def load_trial(clif, cfg):
    """CLIF 3.0 clinical_trial: participant_id (= REDCap record_id), patient_id, hospitalization_id (index stay),
    enrollment_dttm (Study Time Zero) define every CRF row and study day, so the table is required."""
    path = Path(cfg["data_directory"]) / f"clif_clinical_trial.{cfg['filetype']}"
    if not path.exists():
        raise SystemExit(
            f"No clinical trial table found: {path.name} is not in data_directory.\n"
            "It is required (CLIF 3.0 clinical_trial): participant_id (APS REDCap record), patient_id, hospitalization_id\n"
            "and enrollment_dttm (Study Time Zero) define every row and study day of the CRF. See README, 'Cohort identification'.")
    trial = clif.load_table("clinical_trial").df.copy()
    missing = [c for c in TRIAL_REQUIRED if c not in trial.columns]
    if missing:
        raise SystemExit(f"{path.name}: missing required column(s) {missing}; required: {list(TRIAL_REQUIRED)}")
    blank = {c: int(trial[c].isna().sum()) for c in TRIAL_REQUIRED if trial[c].isna().any()}
    if blank:
        raise SystemExit(f"{path.name}: blank values in required column(s) {blank}")
    for c in ("participant_id", "patient_id", "hospitalization_id"):
        trial[c] = trial[c].astype(str)
    if trial["participant_id"].duplicated().any():
        raise SystemExit(f"{path.name}: participant_id is not unique")
    trial["record_id"] = trial["participant_id"]
    trial["enrollment_dttm"] = to_site_tz(trial["enrollment_dttm"], cfg["timezone"])
    trial["withdrawal_dttm"] = to_site_tz(trial.get("withdrawal_dttm", pd.Series(pd.NaT, index=trial.index)), cfg["timezone"])
    return trial


# ------------------------------------------------------------------------------------------- cohort
def attach_hospitalization(trial, hosp):
    """The index stay is the trial's hospitalization_id. Rows whose stay is missing or belongs to another patient
    get no index stay (every day Not Available); enrollment outside the stay is only counted."""
    h = hosp[["hospitalization_id", "patient_id", "admission_dttm", "discharge_dttm"]].rename(columns={"patient_id": "hosp_patient_id"})
    cohort = trial.merge(h, on="hospitalization_id", how="left")
    found = cohort["admission_dttm"].notna()
    wrong_patient = found & (cohort["hosp_patient_id"].astype(str) != cohort["patient_id"])
    ok = found & ~wrong_patient
    outside = ok & ~((cohort["admission_dttm"] <= cohort["enrollment_dttm"])
                     & (cohort["discharge_dttm"].isna() | (cohort["enrollment_dttm"] <= cohort["discharge_dttm"])))
    stats = [("records_hospitalization_not_found", int((~found).sum())),
             ("records_hospitalization_other_patient", int(wrong_patient.sum())),
             ("records_enrollment_outside_hospitalization", int(outside.sum()))]
    cohort.loc[~ok, ["hospitalization_id", "admission_dttm", "discharge_dttm"]] = [None, pd.NaT, pd.NaT]
    return cohort.drop(columns="hosp_patient_id"), stats


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


def apply_branching(long, spec):
    """Keep a value only if REDCap would show its field given the day's other values (checkbox options follow
    their parent's rule). Returns the filtered long table and {field_template: n hidden}."""
    rules = {}
    keep, hidden = [], {}
    order = {t: i for i, t in enumerate(spec.index)}
    long = long.assign(_o=long["field_template"].map(order)).sort_values(["record_id", "day", "_o"])
    for (rid, day), g in long.groupby(["record_id", "day"], sort=False):
        s = g["suffix"].iloc[0]
        vals = {}
        rows = []
        for idx, t, v in zip(g.index, g["field_template"], g["value"]):
            name = t.replace("{d}", s)
            key = (t, s)
            if key not in rules:
                rules[key] = Branching(spec.loc[t, "branching_logic"].replace("{d}", s))
            if rules[key].shown(vals):
                vals[name] = v
                rows.append(idx)
            else:
                hidden[t] = hidden.get(t, 0) + 1
        keep += rows
    return long.loc[keep].drop(columns="_o"), hidden


def check_mapping_fields(mapping, spec):
    """Every REDCap field named in the mapping must exist in the codebook spec (catches typos in field names)."""
    names = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and "{d}" in x:
            names.add(x)
    walk(mapping)
    unknown = sorted(n for n in names if n not in spec.index)
    if unknown:
        raise SystemExit(f"daily_mapping.yaml names fields that are not in the codebook spec: {unknown}")


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
    check_mapping_fields(mapping, spec)
    days = {int(k): str(v) for k, v in mapping["days"].items()}
    anchor = mapping["anchor_hour"]
    out_dir = Path(cfg["output_directory"])
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []  # (metric, count)

    clif = ClifOrchestrator(data_directory=cfg["data_directory"], filetype=cfg["filetype"],
                            timezone=cfg["timezone"], output_directory=str(out_dir), clif_version=CLIF_VERSION)
    trials = load_trial(clif, cfg)
    hosp = clif.load_table("hospitalization", filters={"hospitalization_id": trials["hospitalization_id"].tolist()}).df
    hosp["hospitalization_id"] = hosp["hospitalization_id"].astype(str)
    cohort, cohort_stats = attach_hospitalization(trials, hosp)
    day_df = build_days(cohort, days)
    hosp_ids = cohort["hospitalization_id"].dropna().tolist()
    hosp_to_record = cohort.dropna(subset=["hospitalization_id"]).set_index("hospitalization_id")["record_id"]

    summary += [("records_in_trial_table", len(trials)),
                ("records_with_index_hospitalization", cohort["hospitalization_id"].notna().sum()),
                *cohort_stats]

    def with_record_and_date(df, time_col):
        df = df[df["hospitalization_id"].isin(hosp_to_record.index)].copy()
        df["record_id"] = df["hospitalization_id"].map(hosp_to_record)
        df["dttm"] = df[time_col]
        df["date"] = df["dttm"].dt.date
        return df

    cells = []  # record_id, date, field_template, value(str), source_dttm

    # ---- vitals
    vit_cats = sorted({m["clif"] for m in mapping["vitals"].values()} | {"respiratory_rate"})
    has_measured = "measured_dttm" in table_columns(cfg, "vitals")  # CLIF 3.0 optional bedside time
    vit, n_out = load_without_outliers(clif, "vitals", "vital_value",
                                       columns=["hospitalization_id", "recorded_dttm", "vital_category", "vital_value"]
                                       + (["measured_dttm"] if has_measured else []),
                                       filters={"hospitalization_id": hosp_ids, "vital_category": vit_cats})
    summary.append(("vital_rows_nulled_as_outliers", n_out))
    vit["vital_time"] = vit["measured_dttm"].fillna(vit["recorded_dttm"]) if has_measured else vit["recorded_dttm"]
    summary.append(("vital_rows_timed_by_measured_dttm", int(vit["measured_dttm"].notna().sum()) if has_measured else 0))
    vit = with_record_and_date(vit, "vital_time")
    chosen = {}  # vitals field -> picked rows (their source_dttm anchors the context questions)
    for field, m in mapping["vitals"].items():
        ev = vit[vit["vital_category"] == m["clif"]].rename(columns={"vital_value": "value"})
        ev = ev[ev["value"].notna()]
        picked = pick(ev, m["method"], anchor)
        chosen[field] = picked.copy()
        picked["value"] = picked["value"].map(lambda x, f=field: format_value(x, spec.loc[f, "validation"]))
        cells.append(picked.assign(field_template=field))

    # ---- labs
    abg = mapping["abg"]
    lab_cats = sorted({c for m in mapping["labs"].values() for c in m["clif"]} | {"po2_arterial", *abg["same_abg"].values()})
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

    fill_if_missing = []  # (field_template, value): written on Available days that have no value

    def put(frame, field, values, src=None, basis="data"):
        cells.append(pd.DataFrame({"record_id": frame["record_id"].values, "date": frame["date"].values,
                                   "value": list(values), "source_dttm": src if src is not None else pd.NaT,
                                   "field_template": field, "basis": basis}))

    rec_to_hosp = cohort.dropna(subset=["hospitalization_id"]).set_index("record_id")["hospitalization_id"]

    def queries(picked):
        q = picked.dropna(subset=["source_dttm"]).copy()
        q = q[q["record_id"].isin(rec_to_hosp.index)]
        q["hospitalization_id"] = q["record_id"].map(rec_to_hosp)
        q["t"] = q["source_dttm"]
        q["key"] = q["record_id"].astype(str) + "|" + q["date"].astype(str)
        return q[["key", "hospitalization_id", "t", "record_id", "date"]].reset_index(drop=True)

    def local_8am(frame):
        return [pd.Timestamp(datetime.combine(d, time(anchor))).tz_localize(cfg["timezone"]) for d in frame["date"]]

    # ---- respiratory support in effect at the 8am SpO2, the lowest SpO2 and the lowest PaO2
    rc = mapping["respiratory"]
    resp, n_out = load_without_outliers(clif, "respiratory_support", "fio2_set", filters={"hospitalization_id": hosp_ids})
    summary.append(("resp_rows_nulled_as_outliers", n_out))
    timeline = resp_timeline(resp)
    ecmo_times = None
    if (Path(cfg["data_directory"]) / f"clif_mcs.{cfg['filetype']}").exists():
        mcs = clif.load_table("mcs", filters={"hospitalization_id": hosp_ids}).df
        ecmo_times = mcs[mcs["support_category"].astype(str).str.lower() == "ecmo"][["hospitalization_id", "recorded_dttm"]]
    summary.append(("mcs_table_present", int(ecmo_times is not None)))
    rr_events = vit[vit["vital_category"] == "respiratory_rate"].rename(columns={"vital_time": "time", "vital_value": "value"})
    spo2_events = vit[vit["vital_category"] == "spo2"].rename(columns={"vital_time": "time", "vital_value": "value"})

    def support_at(q, block, name):
        st = resp_at(timeline, q, rc["carry_forward_hours"], rc["room_air_if_none_hours"], ecmo_times).set_index("key").reindex(q["key"])
        for status in ("documented", "room_air_inferred", "stale"):
            summary.append((f"resp_{status}:{name}", int((st["status"] == status).sum())))
        put(q, block["support"], [support_code(d, e, rc["support_codes"]) for d, e in zip(st["device"], st["ecmo"])], q["t"].values,
            basis=["absence" if x == "room_air_inferred" else "data" for x in st["status"]])
        rr = st["resp_rate_obs"].fillna(nearest_value(rr_events, q, rc["rr_from_vitals_minutes"]).reindex(q["key"]).set_axis(st.index))
        derived = {"rr": rr, "niv_ipap": st["peak_inspiratory_pressure_set"].fillna(st["peep_set"] + st["pressure_support_set"])}
        for field, sources in block["settings"].items():
            v = pd.Series(float("nan"), index=st.index)
            for src in sources:
                v = v.fillna(derived[src] if src in derived else st[src])
            put(q, field, [format_value(x, spec.loc[field, "validation"]) for x in v])
        return st

    st8 = support_at(queries(chosen["daily_spo2_8a_{d}"]), rc["at_8a"], "8a")
    q8 = queries(chosen["daily_spo2_8a_{d}"])
    modes = st8["mode_category"].astype("object")
    put(q8, rc["at_8a"]["imv_mode"], [rc["imv_mode_unknown"] if pd.isna(x) else rc["imv_mode_codes"].get(x, rc["imv_mode_codes"]["other"]) for x in modes])
    put(q8, rc["at_8a"]["imv_mode_other"], [rc["imv_mode_other_text"].get(x, "") if pd.notna(x) else "" for x in modes])
    put(q8, rc["at_8a"]["niv_mode"], [rc["niv_mode"]["cpap_device"] if d == "cpap" else rc["niv_mode"]["avaps_mode"] if x == "avaps"
                                      else rc["niv_mode"]["default"] for d, x in zip(st8["device"], modes)])
    support_at(queries(chosen["daily_spo2_lowest_{d}"]), rc["at_lowest_spo2"], "lowest_spo2")

    # ---- lowest PaO2 of the day and the same ABG
    pao2 = labs[(labs["lab_category"] == "po2_arterial") & labs["value"].notna()]
    low = pick(pao2, "min", anchor)
    put(low, abg["lowest_pao2"], [format_value(x, spec.loc[abg["lowest_pao2"], "validation"]) for x in low["value"]], low["source_dttm"].values)
    put(low, abg["lowest_time"], [t.strftime("%H:%M") for t in low["source_dttm"]])
    put(low, abg["occurred"], ["1"] * len(low))
    if len(pao2):
        fill_if_missing.append((abg["occurred"], "0"))
    same = labs[labs["lab_category"].isin(abg["same_abg"].values()) & labs["value"].notna()]
    same = same.sort_values("value").drop_duplicates(["hospitalization_id", "lab_time", "lab_category"])
    lowq = queries(low)
    for field, cat in abg["same_abg"].items():
        m_ = lowq.merge(same[same["lab_category"] == cat][["hospitalization_id", "lab_time", "value"]],
                        left_on=["hospitalization_id", "t"], right_on=["hospitalization_id", "lab_time"], how="left")
        put(lowq, field, [format_value(x, spec.loc[field, "validation"]) for x in m_["value"]])
    support_at(lowq, rc["at_lowest_pao2"], "lowest_pao2")
    sp = nearest_value(spo2_events, lowq, abg["spo2_window_minutes"]).reindex(lowq["key"])
    put(lowq, abg["spo2_at_time"], [format_value(x, spec.loc[abg["spo2_at_time"], "validation"]) for x in sp])

    # ---- vasopressors infusing at the 8am SBP; TPN infusion for nutrition
    vp = mapping["vasopressors"]
    meds, n_out = load_without_outliers(clif, "medication_admin_continuous", "med_dose",
                                        filters={"hospitalization_id": hosp_ids, "med_category": list(vp["drugs"]) + ["tpn"]})
    summary.append(("med_rows_nulled_as_outliers", n_out))
    qbp = queries(chosen["daily_sbp_8a_{d}"])
    any_running = pd.Series(False, index=range(len(qbp)))
    for drug, dm in vp["drugs"].items():
        r = infusion_at(meds[meds["med_category"] == drug], qbp, vp["carry_forward_hours"]).set_index("key").reindex(qbp["key"])
        running = r["running"].fillna(False).astype(bool).values
        any_running |= running
        put(qbp, f"{vp['checkbox']}___{dm['code']}", ["1" if x else "0" for x in running])
        conv = [vp["unit_conversions"].get(norm_unit(u)) if run else None for u, run in zip(r["med_dose_unit"], running)]
        summary.append((f"vasopressor_unit_unmapped:{drug}", sum(1 for c, run in zip(conv, running) if run and c is None)))
        dose = [x * c[1] if c else None for x, c in zip(r["med_dose"], conv)]
        unit = [c[0] if c else None for c in conv]
        if "dose" in dm:  # vasopressin: units/min only
            put(qbp, dm["dose"], [format_value(x, spec.loc[dm["dose"], "validation"]) if u == "units/min" else "" for x, u in zip(dose, unit)])
            continue
        put(qbp, dm["units"], [vp["unit_codes"].get(u, "") if u else "" for u in unit])
        put(qbp, dm["mcg"], [format_value(x, spec.loc[dm["mcg"], "validation"]) if u == "mcg/min" else "" for x, u in zip(dose, unit)])
        put(qbp, dm["mcgkg"], [format_value(x, spec.loc[dm["mcgkg"], "validation"]) if u == "mcg/kg/min" else "" for x, u in zip(dose, unit)])
    put(qbp, f"{vp['checkbox']}___{vp['none_code']}", ["0" if x else "1" for x in any_running])

    # ---- GCS closest to 8am (T codes when intubated)
    gc = mapping["gcs"]
    gcs, n_out = load_without_outliers(clif, "patient_assessments", "numerical_value",
                                       filters={"hospitalization_id": hosp_ids, "assessment_category": [gc["clif"]]})
    gcs = with_record_and_date(gcs, "recorded_dttm").rename(columns={"numerical_value": "value"})
    gcs = gcs[gcs["value"].notna()]
    gpick = pick(gcs, "closest", anchor)
    gq = queries(gpick)
    gst = resp_at(timeline, gq, rc["carry_forward_hours"], rc["room_air_if_none_hours"]).set_index("key").reindex(gq["key"])
    intub = (gst["device"] == "imv").values
    totals = [int(round(x)) for x in gq.merge(gpick[["record_id", "date", "value"]], on=["record_id", "date"], how="left")["value"]]
    put(gq, gc["field"], [str(t + gc["intubated_offset"]) if i and t <= 11 else str(t) for t, i in zip(totals, intub)], gq["t"].values)
    summary.append(("gcs_coded_intubated_T", int(sum(1 for t, i in zip(totals, intub) if i and t <= 11))))
    if len(gcs):
        fill_if_missing.append((gc["field"], gc["not_documented"]))

    # ---- nutrition around 8am (CLIF 3.0 input table if present; TPN infusion from continuous meds)
    nu = mapping["nutrition"]
    avail_days = day_df[day_df["available"] & day_df["record_id"].isin(rec_to_hosp.index)].copy()
    nq = avail_days.assign(hospitalization_id=avail_days["record_id"].map(rec_to_hosp), t=local_8am(avail_days))
    nq["key"] = nq["record_id"].astype(str) + "|" + nq["date"].astype(str)
    nq = nq.reset_index(drop=True)
    has_input = (Path(cfg["data_directory"]) / f"clif_input.{cfg['filetype']}").exists()
    summary.append(("input_table_present", int(has_input)))
    inp = clif.load_table("input", filters={"hospitalization_id": hosp_ids}).df if has_input else pd.DataFrame(
        columns=["hospitalization_id", "recorded_dttm", "input_category", "input_volume"])
    inp = with_record_and_date(inp, "recorded_dttm") if len(inp) else inp.assign(record_id=None, date=None, dttm=None)
    inp = inp[pd.to_numeric(inp["input_volume"], errors="coerce").fillna(0) > 0]
    if len(inp):
        minutes = inp["dttm"].dt.hour * 60 + inp["dttm"].dt.minute
        inp = inp[(minutes - anchor * 60).abs() <= nu["window_hours"] * 60]
    in_window = set(zip(inp["record_id"], inp["date"], inp["input_category"]))
    cohort_input_cats = set(clif.input.df["input_category"]) if has_input else set()
    if nu["oral"]["input_category"] in cohort_input_cats:
        put(nq, f"{nu['checkbox']}___{nu['oral']['code']}",
            ["1" if (r, d, nu["oral"]["input_category"]) in in_window else "0" for r, d in zip(nq["record_id"], nq["date"])])
    tpn_run = infusion_at(meds[meds["med_category"] == nu["tpn"]["med_category"]], nq, vp["carry_forward_hours"]).set_index("key").reindex(nq["key"])
    if nu["tpn"]["input_category"] in cohort_input_cats or (meds["med_category"] == nu["tpn"]["med_category"]).any():
        put(nq, f"{nu['checkbox']}___{nu['tpn']['code']}",
            ["1" if ((r, d, nu["tpn"]["input_category"]) in in_window or bool(run)) else "0"
             for r, d, run in zip(nq["record_id"], nq["date"], tpn_run["running"].fillna(False))])

    # ---- assemble long table on available days only
    cells = pd.concat(cells, ignore_index=True)
    cells = cells.merge(day_df[day_df["available"]][["record_id", "date", "day", "suffix"]], on=["record_id", "date"])
    cells = cells[cells["value"].astype(str) != ""]
    cells["basis"] = cells["basis"].fillna("data")
    long = [cells[["record_id", "day", "suffix", "field_template", "value", "source_dttm", "basis"]]]

    gate = day_df.assign(field_template=mapping["gate_field"], value=day_df["available"].map({True: "1", False: "0"}), basis="data")
    long.append(gate[["record_id", "day", "suffix", "field_template", "value", "basis"]])

    avail = day_df[day_df["available"]][["record_id", "day", "suffix"]]
    for nc_field, (value_field, category_present) in nc_fields.items():
        if not category_present:
            continue
        have = cells[cells["field_template"] == value_field][["record_id", "day"]]
        missing = avail.merge(have, on=["record_id", "day"], how="left", indicator=True)
        missing = missing[missing["_merge"] == "left_only"].drop(columns="_merge")
        long.append(missing.assign(field_template=nc_field, value="1", basis="absence"))
    for field, value in fill_if_missing:
        have = cells[cells["field_template"] == field][["record_id", "day"]]
        missing = avail.merge(have, on=["record_id", "day"], how="left", indicator=True)
        long.append(missing[missing["_merge"] == "left_only"].drop(columns="_merge").assign(field_template=field, value=value, basis="absence"))
    long = pd.concat(long, ignore_index=True)

    # ---- REDCap branching: drop values on fields the form would hide (codebook order: parents first)
    long, hidden = apply_branching(long, spec)
    for t, n in hidden.items():
        summary.append((f"hidden_by_branching:{t}", n))
    long["field"] = [t.replace("{d}", s) for t, s in zip(long["field_template"], long["suffix"])]

    # ---- wide REDCap CSV: record_id, redcap_event_name, then day by day in codebook order
    used = set(long["field_template"])
    order = [t.replace("{d}", s) for s in days.values() for t in spec.index if t in used]
    wide = long.pivot(index="record_id", columns="field", values="value").reindex(columns=order)
    wide = wide.reindex(trials["record_id"]).fillna("").copy()
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
    coverage = write_coverage(long, day_df, spec, mapping, days, SUMMARY_DIR / f"coverage_{cfg['label']}.csv")
    summary.append(("columns_in_upload", wide.shape[1]))
    summary.append(("rows_in_upload", wide.shape[0]))

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame(summary, columns=["metric", "count"])
    out["count_reported"] = out["count"].map(suppress)
    out[["metric", "count_reported"]].to_csv(SUMMARY_DIR / f"run_{cfg['label']}.csv", index=False)
    print(out[["metric", "count_reported"]].to_csv(index=False), end="")
    print("\nCOVERAGE (question x participant-day; manual = chart abstraction remaining)")
    print(coverage[coverage["level"] != "question"].drop(columns="level").to_csv(index=False), end="")


if __name__ == "__main__":
    main()
