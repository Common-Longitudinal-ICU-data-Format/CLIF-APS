"""Build a fake CLIF clinical_trials table for the CLIF-MIMIC demo (all 100 patients "enrolled").

Demo only: lets the pipeline run end-to-end on open data. Never run against real data.

    uv run python code/make_demo_trial_table.py

Enrollment rule: the first ICU arrival (adt in_dttm, location_category == 'icu') across all of a
patient's hospitalizations, plus 12 hours or half of that ICU stay, whichever is shorter (so enrollment
always falls inside the ICU stay and its hospitalization). That mimics APS "Study Time Zero" falling within
48 hours of meeting eligibility in an eligible unit.
"""
from pathlib import Path

import pandas as pd

DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"
ENROLL_OFFSET = pd.Timedelta(hours=12)


def main():
    hosp = pd.read_parquet(DEMO_DIR / "clif_hospitalization.parquet", columns=["patient_id", "hospitalization_id"])
    adt = pd.read_parquet(DEMO_DIR / "clif_adt.parquet", columns=["hospitalization_id", "in_dttm", "out_dttm", "location_category"])

    icu = adt[adt["location_category"].str.lower() == "icu"].merge(hosp, on="hospitalization_id")
    first_icu = icu.sort_values("in_dttm").drop_duplicates("patient_id").set_index("patient_id")

    patients = pd.read_parquet(DEMO_DIR / "clif_patient.parquet", columns=["patient_id"])["patient_id"]
    missing = set(patients) - set(first_icu.index)
    if missing:
        raise SystemExit(f"{len(missing)} demo patients have no ICU stay; enrollment rule needs a fallback")

    offset = ((first_icu["out_dttm"] - first_icu["in_dttm"]) / 2).clip(upper=ENROLL_OFFSET).fillna(ENROLL_OFFSET)
    trials = (first_icu["in_dttm"] + offset).rename("enrollment_dttm").reset_index().sort_values("patient_id")
    trials.insert(0, "record_id", [f"APS-{i:03d}" for i in range(1, len(trials) + 1)])
    trials["trial_id"] = "DEMO-APS-0001"
    trials["trial_name"] = "APS demo"
    trials["arm_id"] = "1"  # APS REDCap project has a single arm (arm_1)
    trials["withdrawal_dttm"] = pd.Series(pd.NaT, index=trials.index, dtype=trials["enrollment_dttm"].dtype)
    trials = trials[["record_id", "patient_id", "trial_id", "trial_name", "arm_id", "enrollment_dttm", "withdrawal_dttm"]]

    out = DEMO_DIR / "clif_clinical_trials.parquet"
    trials.to_parquet(out, index=False)
    print(f"wrote {out.name}: {len(trials)} patients, enrollment {trials['enrollment_dttm'].dtype}")


if __name__ == "__main__":
    main()
