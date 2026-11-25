"""Build a fake CLIF 3.0 clinical_trial table for the CLIF-MIMIC demo (all 100 patients "enrolled").

Demo only: lets the pipeline run end-to-end on open data. Never run against real data.

    uv run python code/make_demo_trial_table.py

Columns follow the CLIF 3.0 clinical_trial schema. Enrollment rule: the first ICU arrival (adt in_dttm,
location_category == 'icu') across all of a patient's hospitalizations, plus 12 hours or half of that ICU
stay, whichever is shorter, so enrollment always falls inside the ICU stay; that stay's hospitalization is
the trial's hospitalization_id. Consent is 1 hour before enrollment; APS is not randomized, so
randomized_dttm = enrollment_dttm.
"""
from pathlib import Path

import pandas as pd

DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"
ENROLL_OFFSET = pd.Timedelta(hours=12)
COLUMNS = ["participant_id", "patient_id", "hospitalization_id", "trial_id", "trial_name", "arm_id",
           "consent_dttm", "enrollment_dttm", "randomized_dttm", "withdrawal_dttm"]


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
    trial = (first_icu.assign(enrollment_dttm=first_icu["in_dttm"] + offset)[["hospitalization_id", "enrollment_dttm"]]
             .reset_index().sort_values("patient_id").reset_index(drop=True))
    trial["participant_id"] = [f"APS-{i:03d}" for i in range(1, len(trial) + 1)]
    trial["trial_id"] = "DEMO-APS-0001"
    trial["trial_name"] = "APS demo"
    trial["arm_id"] = "1"  # APS REDCap project has a single arm (arm_1)
    trial["consent_dttm"] = trial["enrollment_dttm"] - pd.Timedelta(hours=1)
    trial["randomized_dttm"] = trial["enrollment_dttm"]
    trial["withdrawal_dttm"] = pd.Series(pd.NaT, index=trial.index, dtype=trial["enrollment_dttm"].dtype)
    trial = trial[COLUMNS]

    out = DEMO_DIR / "clif_clinical_trial.parquet"
    trial.to_parquet(out, index=False)
    print(f"wrote {out.name}: {len(trial)} participants, enrollment {trial['enrollment_dttm'].dtype}")


if __name__ == "__main__":
    main()
