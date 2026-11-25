"""Does verify_aps.py catch real bugs? Each mutant reintroduces one bug into a copy of the pipeline (or corrupts the
upload file); the verifier must FAIL on every one.

    uv run python code/test_synthetic.py     # builds output/synthetic (demo + synthetic scenario)
    uv run python code/test_mutations.py

Mutant copies and their outputs go to output/mutants/ (gitignored). Exit code 1 if any mutant is missed.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CODE = ROOT / "code"
OUT = ROOT / "output" / "mutants"
BASE = ROOT / "output" / "synthetic"
MODULES = ["run_aps.py", "daily_derive.py", "coverage.py", "redcap_branching.py"]

# name -> (file, original text, mutated text)
CODE_MUTANTS = {
    "utc_day_boundaries": ("run_aps.py", 'day0 = r.enrollment_dttm.date()', 'day0 = r.enrollment_dttm.tz_convert("UTC").date()'),
    "labs_by_result_time": ("run_aps.py", 'labs["lab_collect_dttm"].fillna(labs["lab_result_dttm"])', 'labs["lab_result_dttm"]'),
    "vitals_ignore_measured_time": ("run_aps.py", 'vit["measured_dttm"].fillna(vit["recorded_dttm"]) if has_measured', 'vit["recorded_dttm"] if has_measured'),
    "anchor_7am": ("run_aps.py", 'anchor = mapping["anchor_hour"]', 'anchor = 7'),
    "no_discharge_cutoff": ("run_aps.py", 'and (pd.isna(r.discharge_dttm) or date <= r.discharge_dttm.date())', ''),
    "no_troponin_conversion": ("run_aps.py", 'factors = {expected: 1.0, **{normalize_unit(k): v for k, v in (m.get("convert") or {}).items()}}', 'factors = {expected: 1.0}'),
    "nc_for_unmapped_labs": ("run_aps.py", '        if not category_present:\n            continue\n', ''),
    "no_integer_rounding": ("run_aps.py", '    elif validation == "integer":\n        q = Decimal(1)\n', '    elif validation == "integer":\n        q = Decimal("0.0001")\n'),
    "closest_ties_to_later": ("run_aps.py", 'ev = ev.sort_values(["record_id", "date", "_key", "dttm", "value"])', 'ev = ev.sort_values(["record_id", "date", "_key", "dttm", "value"], ascending=[True, True, True, False, True])'),
    "no_branching": ("run_aps.py", '            if rules[key].shown(vals):', '            if True:'),
    "carry_forward_24h": ("run_aps.py", 'resp_at(timeline, q, rc["carry_forward_hours"]', 'resp_at(timeline, q, 24'),
    "no_room_air_inference": ("daily_derive.py", 'm.loc[~documented & none_recent, "status"] = "room_air_inferred"', 'pass'),
    "settings_cross_device": ("daily_derive.py", 'r["episode"] = ((r["hospitalization_id"] != r["hospitalization_id"].shift()) | (dev != dev.shift())).cumsum()',
                              'r["episode"] = (r["hospitalization_id"] != r["hospitalization_id"].shift()).cumsum()'),
    "ignore_ecmo": ("daily_derive.py", 'm["ecmo"] = m["ecmo_t"].notna()', 'm["ecmo"] = False'),
    "infusion_stop_ignored": ("daily_derive.py", 'r["running"] = r["admin_dttm"].notna() & ~r["_stop"].fillna(True).astype(bool)', 'r["running"] = r["admin_dttm"].notna()'),
    "vasopressin_per_hour": ("run_aps.py", 'dose = [x * c[1] if c else None for x, c in zip(r["med_dose"], conv)]', 'dose = [x if c else None for x, c in zip(r["med_dose"], conv)]'),
    "gcs_no_T_code": ("run_aps.py", 'str(t + gc["intubated_offset"]) if i and t <= 11 else str(t)', 'str(t)'),
    "abg_not_same_draw": ("run_aps.py", 'left_on=["hospitalization_id", "t"], right_on=["hospitalization_id", "lab_time"], how="left")',
                          'left_on=["hospitalization_id"], right_on=["hospitalization_id"], how="left").drop_duplicates("key")'),
}

FILE_MUTANTS = {
    "unknown_column": lambda df: df.assign(daily_foo_0="1"),
    "literal_nan": lambda df: df.assign(daily_sbp_8a_0=df["daily_sbp_8a_0"].replace("", "nan")),
    "duplicate_record": lambda df: pd.concat([df, df.iloc[[0]]]),
    "wrong_event": lambda df: df.assign(redcap_event_name="day_0_arm_1"),
    "value_and_nc": lambda df: df.assign(daily_wbc_nc_0="1"),
    "decimal_in_integer": lambda df: df.assign(daily_sbp_8a_0=df["daily_sbp_8a_0"].where(df["daily_sbp_8a_0"] == "", df["daily_sbp_8a_0"] + ".5")),
    "hidden_field_filled": lambda df: df.assign(daily_niv_epap_8a_0="5"),
    "bad_time_format": lambda df: df.assign(daily_pa02_lowest_time_0=df["daily_pa02_lowest_time_0"].str.replace(":", "")),
}


def verify(cfg):
    r = subprocess.run([sys.executable, str(CODE / "verify_aps.py"), "--config", str(cfg)], capture_output=True, text=True)
    res = [l for l in r.stdout.splitlines() if l.startswith("RESULT")]
    return r.returncode, res[0] if res else "CRASH: " + (r.stderr.strip().splitlines() or ["?"])[-1]


def main():
    base_cfg = BASE / "config.json"
    if not base_cfg.exists():
        raise SystemExit("output/synthetic not found: run `uv run python code/test_synthetic.py` first")
    base = json.loads(base_cfg.read_text())
    if OUT.exists():
        shutil.rmtree(OUT)
    results = []
    for name, (fname, a, b) in CODE_MUTANTS.items():
        d = OUT / name
        d.mkdir(parents=True)
        for m in MODULES:
            src = (CODE / m).read_text()
            if m == fname:
                if a not in src:
                    raise SystemExit(f"mutant {name}: pattern not found in {fname}; update the mutant")
                src = src.replace(a, b)
            if m == "run_aps.py":
                src = src.replace("ROOT = Path(__file__).resolve().parent.parent", f"ROOT = Path({str(ROOT)!r})")
            (d / m).write_text(src)
        cfg = d / "config.json"
        cfg.write_text(json.dumps({**base, "output_directory": str(d / "out"), "label": f"mutant_{name}"}))
        run = subprocess.run([sys.executable, str(d / "run_aps.py"), "--config", str(cfg)], capture_output=True, text=True)
        results.append((name, *(verify(cfg) if run.returncode == 0 else (1, "pipeline crashed (counts as caught)"))))
    good = pd.read_csv(Path(base["output_directory"]) / "daily_assessment_upload.csv", dtype=str, keep_default_na=False)
    for name, f in FILE_MUTANTS.items():
        d = OUT / name
        (d / "out").mkdir(parents=True)
        f(good.copy()).to_csv(d / "out" / "daily_assessment_upload.csv", index=False)
        cfg = d / "config.json"
        cfg.write_text(json.dumps({**base, "output_directory": str(d / "out"), "label": f"mutant_{name}"}))
        results.append((name, *verify(cfg)))
    caught = 0
    for name, rc, line in results:
        caught += rc == 1
        print(f"{name:30s} {'CAUGHT' if rc == 1 else 'MISSED'}  {line}")
    print(f"{caught}/{len(results)} mutants caught")
    raise SystemExit(0 if caught == len(results) else 1)


if __name__ == "__main__":
    main()
