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

from redcap_branching import Branching

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "config" / "redcap" / "daily_fields.csv"
MAPPING_PATH = ROOT / "config" / "redcap" / "daily_mapping.yaml"
SUMMARY_DIR = ROOT / "output" / "summary"
FORMATS = {"integer": r"-?\d+", "number": r"-?\d+(\.\d+)?", "number_1dp": r"-?\d+\.\d", "time": r"([01]\d|2[0-3]):[0-5]\d"}


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
    """events: list of (timestamp, value). Returns the chosen (timestamp, value) or None.
    closest: smallest |t - anchor|, then earlier, then lower; min: lowest, then earlier; max: highest, then earlier."""
    if not events:
        return None
    if method == "closest":
        return min(events, key=lambda e: (abs(e[0] - anchor), e[0], e[1]))
    if method == "min":
        return min(events, key=lambda e: (e[1], e[0]))
    if method == "max":
        return min(events, key=lambda e: (-e[1], e[0]))
    raise ValueError(method)


def dose_unit_key(u):
    if u is None or pd.isna(u):
        return None
    u = "".join(str(u).split()).lower()
    for a, b in (("µ", "mc"), ("μ", "mc"), ("ug", "mcg"), ("hour", "hr")):
        u = u.replace(a, b)
    return u


SETTINGS = ["fio2_set", "lpm_set", "peep_set", "peep_obs", "pressure_support_set", "pressure_control_set",
            "tidal_volume_set", "tidal_volume_obs", "resp_rate_obs", "mean_airway_pressure_obs",
            "peak_inspiratory_pressure_obs", "peak_inspiratory_pressure_set", "plateau_pressure_obs", "minute_vent_obs"]


class RespChart:
    """One hospitalization's respiratory_support charting, read the way a chart reviewer would: walk back from t."""

    def __init__(self, frame):
        rows = {}
        for r in frame.to_dict("records"):  # same timestamp charted twice: rows ordered by device, first value wins
            rows.setdefault(r["recorded_dttm"], []).append(r)
        self.rows = []
        for ts in sorted(rows):
            group = sorted(rows[ts], key=lambda r: (pd.isna(r.get("device_category")), str(r.get("device_category"))))
            merged = {"t": ts}
            for col in ["device_category", "mode_category"] + SETTINGS:
                merged[col] = next((r.get(col) for r in group if pd.notna(r.get(col))), None)
            self.rows.append(merged)
        first = next((i for i, r in enumerate(self.rows) if r["device_category"] is not None), None)
        self.first_device = len(self.rows) if first is None else first

    def at(self, t, carry, room_air):
        prior = [i for i, r in enumerate(self.rows) if r["t"] <= t]
        if not prior or t - self.rows[prior[-1]]["t"] > room_air:
            return {"status": "room_air_inferred", "device": "room_air"}
        last = prior[-1]
        if t - self.rows[last]["t"] > carry or last < self.first_device:
            return {"status": "stale", "device": None}
        device = next(self.rows[i]["device_category"] for i in range(last, -1, -1) if self.rows[i]["device_category"] is not None)
        # the episode starts at the earliest charting of this device in the current run; rows without a device
        # before that belong to whatever device was charted before them
        episode, earliest = [], None
        for i in range(last, self.first_device - 1, -1):
            dc = self.rows[i]["device_category"]
            if dc is not None and dc != device:
                break
            episode.append(self.rows[i])
            if dc == device:
                earliest = len(episode)
        episode = episode[:earliest]
        state = {"status": "documented", "device": device}
        for col in ["mode_category"] + SETTINGS:
            hit = next((r for r in episode if r[col] is not None), None)
            state[col] = hit[col] if hit is not None and t - hit["t"] <= carry else None
        return state


def nearest(events, t, window):
    """events: list of (time, value); nearest within +-window, ties to the earlier, same time -> lowest value."""
    inside = [e for e in events if abs(e[0] - t) <= window]
    return min(inside, key=lambda e: (abs(e[0] - t), e[0], e[1]))[1] if inside else None


def infusion(rows, t, carry):
    """rows: dicts with admin_dttm, mar_action_category, mar_action_group, med_dose, med_dose_unit (one drug, one stay).
    Latest charting at or before t within carry; a stop at that time wins; else the highest dose charted then."""
    prior = [r for r in rows if r["admin_dttm"] <= t and t - r["admin_dttm"] <= carry]
    if not prior:
        return False, None, None
    latest = max(r["admin_dttm"] for r in prior)
    group = [r for r in prior if r["admin_dttm"] == latest]
    if any(str(r.get("mar_action_category")).lower() == "stop" or str(r.get("mar_action_group")).lower() == "not_administered" for r in group):
        return False, None, None
    top = max(group, key=lambda r: (r["med_dose"] if pd.notna(r["med_dose"]) else float("-inf")))
    dose = top["med_dose"]
    return (pd.notna(dose) and dose > 0), dose, top.get("med_dose_unit")


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
        if f["field_type"] == "text" and f["validation"] == "time":
            checks[("error", "format invalid (time HH:MM)")] += (~vals.str.fullmatch(FORMATS["time"])).sum()
        elif f["field_type"] == "text" and f["validation"] in FORMATS:
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
        elif f["field_type"] in ("radio", "yesno", "dropdown", "checkbox_option"):
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
    vitals["when"] = vitals["measured_dttm"].where(vitals["measured_dttm"].notna(), vitals["recorded_dttm"])
    labs = raw("labs", "lab_value_numeric", ["hospitalization_id", "lab_collect_dttm", "lab_result_dttm", "lab_category",
                                              "lab_value", "lab_value_numeric", "reference_unit"])
    labs["when"] = labs["lab_collect_dttm"].where(labs["lab_collect_dttm"].notna(), labs["lab_result_dttm"])
    resp = raw("respiratory_support", "fio2_set")
    meds = raw("medication_admin_continuous", "med_dose")
    assess = raw("patient_assessments", "numerical_value")
    has_mcs = (data_dir / f"clif_mcs.{cfg['filetype']}").exists()
    mcs = clif.load_table("mcs", filters={"hospitalization_id": hosp_ids}).df if has_mcs else pd.DataFrame(columns=["hospitalization_id", "recorded_dttm", "support_category"])
    has_input = (data_dir / f"clif_input.{cfg['filetype']}").exists()
    inputs = clif.load_table("input", filters={"hospitalization_id": hosp_ids}).df if has_input else pd.DataFrame(columns=["hospitalization_id", "recorded_dttm", "input_category", "input_volume"])

    def by_h(df):
        return {h: g for h, g in df.groupby("hospitalization_id")}
    vit_by_h, lab_by_h, resp_by_h, med_by_h, ass_by_h, mcs_by_h, inp_by_h = map(by_h, (vitals, labs, resp, meds, assess, mcs, inputs))

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
    gcs_present = ((assess["hospitalization_id"].isin(index_ids)) & (assess["assessment_category"] == mp["gcs"]["clif"])
                   & assess["numerical_value"].notna()).any()
    input_cats_present = set(inputs["input_category"]) if has_input else set()
    tpn_present = (meds["med_category"] == mp["nutrition"]["tpn"]["med_category"]).any()

    up = upload.drop_duplicates("record_id").set_index("record_id")  # duplicates already counted above
    expected_records = set(trials["record_id"].astype(str))
    checks[("error", "record_id in trial table but not in upload")] += len(expected_records - set(up.index))
    checks[("error", "record_id in upload but not in trial table")] += len(set(up.index) - expected_records)

    rc, ab, vp, gc, nu = mp["respiratory"], mp["abg"], mp["vasopressors"], mp["gcs"], mp["nutrition"]
    carry, room_air = timedelta(hours=rc["carry_forward_hours"]), timedelta(hours=rc["room_air_if_none_hours"])
    vp_carry = timedelta(hours=vp["carry_forward_hours"])
    rules = {}

    def shown(t, s, vals):
        if (t, s) not in rules:
            rules[(t, s)] = Branching(spec.loc[t, "branching_logic"].replace("{d}", s))
        return rules[(t, s)].shown(vals)

    def F(t, x, decimals=None):
        return fmt(x, spec.loc[t, "validation"], decimals)

    compared = Counter()
    disagree = Counter()
    for tr in trials.itertuples(index=False):
        rid = str(tr.record_id)
        enroll = site_time(tr.enrollment_dttm)
        withdraw = site_time(getattr(tr, "withdrawal_dttm", pd.NaT))
        idx = index_hosp[rid]
        hid = idx.hospitalization_id if idx is not None else None
        empty = pd.DataFrame()
        vit = vit_by_h.get(hid, empty)
        lab = lab_by_h.get(hid, empty)
        chart = RespChart(resp_by_h.get(hid, pd.DataFrame(columns=["recorded_dttm", "device_category"])))
        ecmo_t = sorted(mcs_by_h.get(hid, pd.DataFrame(columns=["recorded_dttm", "support_category"])).query("support_category == 'ecmo'")["recorded_dttm"]) if has_mcs else []
        med_rows = {k: g.to_dict("records") for k, g in med_by_h.get(hid, pd.DataFrame(columns=["med_category"])).groupby("med_category")}
        rr_events = [(r.when, float(r.vital_value)) for r in vit.itertuples(index=False)
                     if r.vital_category == "respiratory_rate" and pd.notna(r.vital_value)] if len(vit) else []
        spo2_all = [(r.when, float(r.vital_value)) for r in vit.itertuples(index=False)
                    if r.vital_category == "spo2" and pd.notna(r.vital_value)] if len(vit) else []

        def support(t):
            st = chart.at(t, carry, room_air)
            ecmo = any(e <= t and t - e <= carry for e in ecmo_t)
            dev = st["device"]
            code = "" if dev is None else (rc["support_codes"]["ecmo_with_imv"] if ecmo and dev == "imv" else
                                           rc["support_codes"]["ecmo_without_imv"] if ecmo else
                                           rc["support_codes"].get(dev, rc["support_codes"]["other"]))
            return st, code

        def settings_value(st, sources, t):
            for src in sources:
                if src == "rr":
                    v = st.get("resp_rate_obs")
                    if v is None:
                        v = nearest(rr_events, t, timedelta(minutes=rc["rr_from_vitals_minutes"]))
                elif src == "niv_ipap":
                    v = st.get("peak_inspiratory_pressure_set")
                    if v is None and st.get("peep_set") is not None and st.get("pressure_support_set") is not None:
                        v = st["peep_set"] + st["pressure_support_set"]
                else:
                    v = st.get(src)
                if v is not None and not pd.isna(v):
                    return v
            return None

        for d, s in days.items():
            day_date = enroll.date() + timedelta(days=d)
            start = localize(datetime.combine(day_date, time(0)))
            end = localize(datetime.combine(day_date + timedelta(days=1), time(0)))
            anchor = localize(datetime.combine(day_date, time(anchor_hour)))
            available = (idx is not None and idx.admission_dttm < end
                         and (pd.isna(idx.discharge_dttm) or idx.discharge_dttm >= start)
                         and (pd.isna(withdraw) or withdraw >= start))
            X = {}  # expected, by field template

            if available:
                picked = {}
                for t, m in mp["vitals"].items():
                    ev = [(r.when, float(r.vital_value)) for r in vit.itertuples(index=False)
                          if r.vital_category == m["clif"] and pd.notna(r.vital_value) and start <= r.when < end] if len(vit) else []
                    picked[t] = choose(ev, m["method"], anchor)
                    X[t] = F(t, picked[t][1]) if picked[t] else ""
                for t, m in mp["labs"].items():
                    factors = {m["unit"]: 1.0, **{unit_key(k): v for k, v in (m.get("convert") or {}).items()}}
                    ev = []
                    for r in (lab.itertuples(index=False) if len(lab) else []):
                        if r.lab_category not in m["clif"]:
                            continue
                        v = numeric(r.lab_value_numeric, r.lab_value)
                        if v is None or pd.isna(r.when) or not (start <= r.when < end):
                            continue
                        if m["unit"] is not None:
                            if unit_key(r.reference_unit) not in factors:
                                continue
                            v *= factors[unit_key(r.reference_unit)]
                        ev.append((r.when, v))
                    hit = choose(ev, m["method"], anchor)
                    X[t] = F(t, hit[1], m.get("decimals")) if hit else ""
                    X[m["nc"]] = "1" if (X[t] == "" and lab_cats_present & set(m["clif"])) else ""

                def write_support(block, t_at):
                    if t_at is None:
                        return None
                    st, code = support(t_at)
                    X[block["support"]] = code
                    if st["status"] == "documented":
                        for f, sources in block["settings"].items():
                            X[f] = F(f, settings_value(st, sources, t_at))
                    return st

                t8 = picked["daily_spo2_8a_{d}"][0] if picked["daily_spo2_8a_{d}"] else None
                st8 = write_support(rc["at_8a"], t8)
                if st8 is not None:
                    mode = st8.get("mode_category")
                    X[rc["at_8a"]["imv_mode"]] = rc["imv_mode_unknown"] if mode is None else rc["imv_mode_codes"].get(mode, rc["imv_mode_codes"]["other"])
                    X[rc["at_8a"]["imv_mode_other"]] = rc["imv_mode_other_text"].get(mode, "") if mode else ""
                    X[rc["at_8a"]["niv_mode"]] = (rc["niv_mode"]["cpap_device"] if st8["device"] == "cpap" else
                                                  rc["niv_mode"]["avaps_mode"] if mode == "avaps" else rc["niv_mode"]["default"])
                tlow = picked["daily_spo2_lowest_{d}"][0] if picked["daily_spo2_lowest_{d}"] else None
                write_support(rc["at_lowest_spo2"], tlow)

                # lowest PaO2 + same ABG
                pao2 = [(r.when, numeric(r.lab_value_numeric, r.lab_value)) for r in (lab.itertuples(index=False) if len(lab) else [])
                        if r.lab_category == "po2_arterial" and pd.notna(r.when) and start <= r.when < end]
                pao2 = [e for e in pao2 if e[1] is not None]
                lowest = choose(pao2, "min", anchor)
                if lowest:
                    tp = lowest[0]
                    X[ab["lowest_pao2"]] = F(ab["lowest_pao2"], lowest[1])
                    X[ab["lowest_time"]] = tp.strftime("%H:%M")
                    X[ab["occurred"]] = "1"
                    for f, cat in ab["same_abg"].items():
                        same = [numeric(r.lab_value_numeric, r.lab_value) for r in lab.itertuples(index=False)
                                if r.lab_category == cat and r.when == tp]
                        same = [v for v in same if v is not None]
                        X[f] = F(f, min(same)) if same else ""
                    write_support(rc["at_lowest_pao2"], tp)
                    X[ab["spo2_at_time"]] = F(ab["spo2_at_time"], nearest(spo2_all, tp, timedelta(minutes=ab["spo2_window_minutes"])))
                elif "po2_arterial" in lab_cats_present:
                    X[ab["occurred"]] = "0"

                # vasopressors at the 8am SBP
                tbp = picked["daily_sbp_8a_{d}"][0] if picked["daily_sbp_8a_{d}"] else None
                if tbp is not None:
                    running_any = False
                    for drug, dm in vp["drugs"].items():
                        running, dose, unit = infusion(med_rows.get(drug, []), tbp, vp_carry)
                        running_any |= running
                        X[f"{vp['checkbox']}___{dm['code']}"] = "1" if running else "0"
                        conv = vp["unit_conversions"].get(dose_unit_key(unit)) if running else None
                        if not conv:
                            continue
                        rate, rc_unit = dose * conv[1], conv[0]
                        if "dose" in dm:
                            if rc_unit == "units/min":
                                X[dm["dose"]] = F(dm["dose"], rate)
                            continue
                        X[dm["units"]] = vp["unit_codes"].get(rc_unit, "")
                        if rc_unit == "mcg/min":
                            X[dm["mcg"]] = F(dm["mcg"], rate)
                        elif rc_unit == "mcg/kg/min":
                            X[dm["mcgkg"]] = F(dm["mcgkg"], rate)
                    X[f"{vp['checkbox']}___{vp['none_code']}"] = "0" if running_any else "1"

                # GCS closest to 8am
                a_rows = ass_by_h.get(hid, empty)
                gev = [(r.recorded_dttm, float(r.numerical_value)) for r in (a_rows.itertuples(index=False) if len(a_rows) else [])
                       if r.assessment_category == gc["clif"] and pd.notna(r.numerical_value) and start <= r.recorded_dttm < end]
                g = choose(gev, "closest", anchor)
                if g:
                    total = int(round(g[1]))
                    st = chart.at(g[0], carry, room_air)
                    X[gc["field"]] = str(total + gc["intubated_offset"]) if (st["status"] == "documented" and st["device"] == "imv" and total <= 11) else str(total)
                elif gcs_present:
                    X[gc["field"]] = gc["not_documented"]

                # nutrition around 8am
                win = timedelta(hours=nu["window_hours"])
                i_rows = inp_by_h.get(hid, empty)
                near8 = {r.input_category for r in (i_rows.itertuples(index=False) if len(i_rows) else [])
                         if pd.to_numeric(r.input_volume, errors="coerce") > 0 and abs(r.recorded_dttm - anchor) <= win}
                if nu["oral"]["input_category"] in input_cats_present:
                    X[f"{nu['checkbox']}___{nu['oral']['code']}"] = "1" if nu["oral"]["input_category"] in near8 else "0"
                if nu["tpn"]["input_category"] in input_cats_present or tpn_present:
                    tpn_on = infusion(med_rows.get(nu["tpn"]["med_category"], []), anchor, vp_carry)[0]
                    X[f"{nu['checkbox']}___{nu['tpn']['code']}"] = "1" if (nu["tpn"]["input_category"] in near8 or tpn_on) else "0"

            X[gate] = "1" if available else "0"
            # what REDCap would keep: branching in codebook order (parents first)
            exp = {}
            for t in spec.index:
                name = t.replace("{d}", s)
                if X.get(t, "") != "" and shown(t, s, exp):
                    exp[name] = X[t]

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

            # consistency of the uploaded values themselves
            if row is not None:
                g = row.get(gate.replace("{d}", s), "")
                if g == "0":
                    checks[("error", "gate = 0 but other fields filled")] += sum(row[c] != "" for c in day_fields if c != gate.replace("{d}", s))
                if g == "":
                    checks[("error", "gate field blank")] += 1
                vals = {c: row[c] for c in day_fields if row[c] != ""}
                kept = {}
                for t in spec.index:
                    name = t.replace("{d}", s)
                    if name in vals:
                        if shown(t, s, kept):
                            kept[name] = vals[name]
                        else:
                            checks[("error", "value on a field REDCap branching hides")] += 1
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
