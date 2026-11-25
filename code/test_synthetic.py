"""End-to-end test on the demo plus a synthetic scenario that exercises what MIMIC lacks.

    uv run python code/test_synthetic.py

Copies data/demo to output/synthetic/data, picks two Available participant-days with no charting at all, injects a
fully specified day into each, runs run_aps.py and verify_aps.py, and checks hand-computed REDCap values:
  A: IMV + ECMO, measured_dttm vitals (bedside time decides "closest to 8am"), norepinephrine in mcg/min, vasopressin in units/hr, angiotensin II in ng/kg/min, a stopped phenylephrine,
     hematocrit / D-dimer / fibrinogen (CLIF 3.0 labs), lowest PaO2 with its ABG, GCS 8 on IMV (= 8T), TPN input
  B: no respiratory charting (room air inferred), oral intake
Exit code 1 on any failure.
"""
import json
import shutil
import warnings
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo"
OUT = ROOT / "output" / "synthetic"
DATA = OUT / "data"
TZ = "US/Eastern"


def local(day, hh, mm=0):
    return pd.Timestamp(datetime.combine(day, time(hh, mm))).tz_localize(TZ).tz_convert("UTC")


def blank_days():
    """(participant, hospitalization, study day, date) that are Available and have no charting in any table."""
    trial = pd.read_parquet(DEMO / "clif_clinical_trial.parquet")
    hosp = pd.read_parquet(DEMO / "clif_hospitalization.parquet").set_index("hospitalization_id")
    busy = set()
    for f, col in [("vitals", "recorded_dttm"), ("labs", "lab_collect_dttm"), ("respiratory_support", "recorded_dttm"),
                   ("medication_admin_continuous", "admin_dttm"), ("patient_assessments", "recorded_dttm")]:
        df = pd.read_parquet(DEMO / f"clif_{f}.parquet", columns=["hospitalization_id", col])
        busy |= set(zip(df["hospitalization_id"], df[col].dt.tz_convert(TZ).dt.date))
    out = []
    for r in trial.itertuples(index=False):
        h = hosp.loc[r.hospitalization_id]
        day0 = r.enrollment_dttm.tz_convert(TZ).date()
        for d in range(1, 8):
            date = day0 + timedelta(days=d)
            if h.admission_dttm.tz_convert(TZ).date() < date < h.discharge_dttm.tz_convert(TZ).date() \
                    and (r.hospitalization_id, date) not in busy and (r.hospitalization_id, date - timedelta(days=1)) not in busy:
                out.append((r.participant_id, r.hospitalization_id, d, date))
                break
    return out


def append(name, rows):
    path = DATA / f"clif_{name}.parquet"
    new = pd.DataFrame(rows)
    if path.exists():
        old = pd.read_parquet(path)
        for c in old.columns:
            if c not in new.columns:
                new[c] = None
        new = pd.concat([old, new[old.columns]], ignore_index=True)
    new.to_parquet(path, index=False)


def main():
    warnings.filterwarnings("ignore", category=FutureWarning)
    if OUT.exists():
        shutil.rmtree(OUT)
    DATA.mkdir(parents=True)
    for f in DEMO.glob("*.parquet"):
        shutil.copy(f, DATA / f.name)

    (pa, ha, da, A), (pb, hb, db, B) = blank_days()[:2]
    sa, sb = {-2: "m2", -1: "m1"}.get(da, str(da)), {-2: "m2", -1: "m1"}.get(db, str(db))

    v = pd.read_parquet(DATA / "clif_vitals.parquet")
    v["measured_dttm"] = pd.Series(pd.NaT, index=v.index, dtype=v["recorded_dttm"].dtype)
    v.to_parquet(DATA / "clif_vitals.parquet", index=False)
    vit = lambda h, cat, val, rec, meas=None: {"hospitalization_id": h, "recorded_dttm": rec, "measured_dttm": meas,
                                               "vital_name": cat, "vital_category": cat, "vital_value": val}
    append("vitals", [
        vit(ha, "spo2", 93.0, local(A, 8, 40), local(A, 8, 0)),   # charted late, measured at 08:00
        vit(ha, "spo2", 99.0, local(A, 8, 15), local(A, 8, 15)),  # closest by charting time, not by bedside time
        vit(ha, "sbp", 95.0, local(A, 8, 40), local(A, 8, 0)),
        vit(ha, "dbp", 50.0, local(A, 8, 40), local(A, 8, 0)),
        vit(ha, "respiratory_rate", 22.0, local(A, 7, 0)),
        vit(hb, "spo2", 96.0, local(B, 7, 50)),
        vit(hb, "sbp", 128.0, local(B, 7, 50)),
        vit(hb, "dbp", 71.0, local(B, 7, 50)),
        vit(hb, "respiratory_rate", 18.0, local(B, 8, 10)),
    ])
    append("respiratory_support", [{"hospitalization_id": ha, "recorded_dttm": local(A, 6, 30), "device_name": "vent",
                                    "device_category": "imv", "mode_name": "AC/VC", "mode_category": "acvc", "tracheostomy": 0,
                                    "fio2_set": 0.5, "peep_set": 10.0, "tidal_volume_set": 400.0, "resp_rate_obs": 20.0}])
    append("mcs", [{"hospitalization_id": ha, "recorded_dttm": local(A, 7, 0), "support_name": "ECMO", "support_category": "ecmo",
                    "device_name": "Cardiohelp", "device_category": "cardiohelp", "config_name": "VV", "config_category": "v_v",
                    "setting_name": "flow", "setting_category": "flow", "setting_value": 4.5}])
    med = lambda cat, t, dose, unit, action, group="administered": {
        "hospitalization_id": ha, "med_order_id": f"syn-{cat}", "admin_dttm": t, "med_name": cat, "med_category": cat,
        "med_group": "vasoactives", "med_route_name": "IV", "med_route_category": "iv", "med_dose": dose, "med_dose_unit": unit,
        "mar_action_name": action, "mar_action_category": action, "mar_action_group": group}
    append("medication_admin_continuous", [
        med("norepinephrine", local(A, 6, 0), 8.0, "mcg/min", "start"),
        med("vasopressin", local(A, 5, 0), 2.4, "units/hr", "start"),
        med("angiotensin_ii", local(A, 6, 30), 20.0, "ng/kg/min", "start"),
        med("phenylephrine", local(A, 3, 0), 1.0, "mcg/kg/min", "start"),
        med("phenylephrine", local(A, 7, 0), 1.0, "mcg/kg/min", "stop"),  # stop charted with the last rate
    ])
    lab = lambda cat, t, val, unit: {"hospitalization_id": ha, "lab_order_dttm": t, "lab_collect_dttm": t, "lab_result_dttm": t + pd.Timedelta(minutes=40),
                                     "lab_order_name": cat, "lab_order_category": "syn", "lab_name": cat, "lab_category": cat,
                                     "lab_value": str(val), "lab_value_numeric": float(val), "reference_unit": unit,
                                     "lab_specimen_name": "blood", "lab_specimen_category": "plasma_blood"}
    append("labs", [
        lab("hematocrit", local(A, 7, 45), 31.6, "%"),
        lab("d_dimer", local(A, 7, 45), 2.35, "ug/mL"),
        lab("fibrinogen", local(A, 7, 45), 180, "mg/dL"),
        lab("po2_arterial", local(A, 7, 45), 62, "mmHg"),
        lab("ph_arterial", local(A, 7, 45), 7.31, "(no units)"),
        lab("pco2_arterial", local(A, 7, 45), 48, "mmHg"),
        lab("so2_arterial", local(A, 7, 45), 91.4, "%"),
        lab("po2_arterial", local(A, 15, 0), 88, "mmHg"),
    ])
    append("patient_assessments", [{"hospitalization_id": ha, "recorded_dttm": local(A, 8, 10), "assessment_name": "GCS",
                                    "assessment_category": "gcs_total", "assessment_group": "neurologic", "numerical_value": 8.0}])
    append("input", [
        {"hospitalization_id": ha, "recorded_dttm": local(A, 8, 0), "input_name": "TPN", "input_category": "parenteral_nutrition",
         "input_group": "nutrition", "input_volume": 50.0},
        {"hospitalization_id": hb, "recorded_dttm": local(B, 9, 0), "input_name": "PO", "input_category": "po_intake",
         "input_group": "nutrition", "input_volume": 240.0},
    ])

    cfg = OUT / "config.json"
    cfg.write_text(json.dumps({"site_name": "synthetic", "data_directory": str(DATA), "filetype": "parquet", "timezone": TZ,
                               "output_directory": str(OUT / "out"), "label": "synthetic"}))
    run = subprocess.run([sys.executable, str(ROOT / "code" / "run_aps.py"), "--config", str(cfg)], capture_output=True, text=True)
    if run.returncode:
        print(run.stderr[-3000:])
        raise SystemExit("run_aps.py failed")
    ver = subprocess.run([sys.executable, str(ROOT / "code" / "verify_aps.py"), "--config", str(cfg)], capture_output=True, text=True)
    result = [l for l in ver.stdout.splitlines() if l.startswith("RESULT")]

    up = pd.read_csv(OUT / "out" / "daily_assessment_upload.csv", dtype=str, keep_default_na=False).set_index("record_id")
    want = {
        (pa, f"daily_spo2_8a_{sa}"): "93", (pa, f"daily_resp_8a_{sa}"): "1", (pa, f"daily_imv_mode_8a_{sa}"): "1",
        (pa, f"daily_imv_fio2_8a_{sa}"): "0.5", (pa, f"daily_epap_8a_{sa}"): "10", (pa, f"daily_set_tv_set_8a_{sa}"): "400",
        (pa, f"daily_resp_rate_8a_{sa}"): "20", (pa, f"daily_resp_lowest_{sa}"): "1", (pa, f"daily_fio2_lowest_{sa}"): "0.5",
        (pa, f"daily_sbp_8a_{sa}"): "95", (pa, f"daily_vasopressors_{sa}___0"): "0", (pa, f"daily_vasopressors_{sa}___1"): "1",
        (pa, f"daily_ne_units_8a_{sa}"): "1", (pa, f"daily_ne_dose_8a_{sa}_mcg"): "8", (pa, f"daily_vasopressors_{sa}___3"): "0",
        (pa, f"daily_vasopressors_{sa}___4"): "1", (pa, f"daily_vaso_dose_8a_{sa}"): "0.04",
        (pa, f"daily_vasopressors_{sa}___7"): "1", (pa, f"daily_ang2_units_8a_{sa}"): "2", (pa, f"daily_ang2_8a_{sa}_mcgkg"): "0.02",
        (pa, f"daily_hct_8a_{sa}"): "32", (pa, f"daily_ddimer_8a_{sa}"): "2.35", (pa, f"daily_fibrinogen_8a_{sa}"): "180",
        (pa, f"daily_pao2_occur_{sa}"): "1", (pa, f"daily_pa02_lowest_{sa}"): "62", (pa, f"daily_pa02_lowest_time_{sa}"): "07:45",
        (pa, f"daily_ph_lowest_{sa}"): "7.31", (pa, f"daily_paco2_lowest_{sa}"): "48", (pa, f"daily_sao2_lowest_{sa}"): "91.4",
        (pa, f"daily_resp_lowest_pao2_{sa}"): "1", (pa, f"daily_fio2_lowest_pao2_{sa}"): "0.5", (pa, f"daily_spo2_lowpao2_{sa}"): "93",
        (pa, f"daily_gcs_8a_{sa}"): "21", (pa, f"daily_nutrition_8a_{sa}___4"): "1",
        (pb, f"daily_spo2_8a_{sb}"): "96", (pb, f"daily_resp_8a_{sb}"): "7", (pb, f"daily_vasopressors_{sb}___0"): "1",
        (pb, f"daily_nutrition_8a_{sb}___1"): "1", (pb, f"daily_nutrition_8a_{sb}___4"): "0", (pb, f"daily_gcs_8a_{sb}"): "99",
        (pb, f"daily_hct_nc_{sb}"): "1",
    }
    fails = []
    for (rid, col), exp in want.items():
        got = up.loc[rid, col] if col in up.columns else "<missing column>"
        if got != exp:
            fails.append(f"{rid} {col}: expected {exp!r}, got {got!r}")
    print(f"scenario A = {pa} day {da}, scenario B = {pb} day {db}")
    print(f"hand-computed values: {len(want) - len(fails)}/{len(want)} match")
    for f in fails:
        print("  MISMATCH", f)
    print("verifier:", result[0] if result else ver.stderr[-2000:])
    ok = not fails and result and result[0].startswith("RESULT: PASS")
    print("TEST", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
