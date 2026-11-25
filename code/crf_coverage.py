"""Whole-CRF coverage: what share of APS CRF questions the pipeline fills today, could fill from CLIF but does not
yet, and must stay manual chart abstraction.

    uv run python code/crf_coverage.py                      # inventory at config/redcap/crf_elements.csv
    uv run python code/crf_coverage.py --inventory <csv>

The inventory (one row per CRF question/option, built from the APS_to_CLIF crosswalk; not in the repo because it
holds CRF item labels) gives each question's CLIF derivability. Daily Assessment questions are re-classified from
what config/redcap/daily_mapping.yaml actually writes. Output: output/summary/crf_coverage.csv (question counts,
no patient data). Per-patient-day fill rates on real data are in output/summary/coverage_<label>.csv.
"""
import argparse
import re
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
MAPPING = ROOT / "config" / "redcap" / "daily_mapping.yaml"
SPEC = ROOT / "config" / "redcap" / "daily_fields.csv"
PARTIAL_BUILT = {"daily_nutrition_8a_{d}"}           # oral / TPN filled; tube feeding manual
CLASSES = ["pipeline_now", "pipeline_partial", "derivable_not_built", "partial_not_built", "not_in_clif", "non_ehr"]


def repeats(text):
    m = re.match(r"x(\d+)", str(text))
    return int(m.group(1)) if m else 1


def built_fields():
    mp = yaml.safe_load(MAPPING.read_text())
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
    walk({k: v for k, v in mp.items() if k != "not_in_clif"})
    for m in mp["labs"].values():
        names.add(m["nc"])
    return names


def classify(row, built):
    status = row["status"]
    if row["instrument"].startswith("Daily Assessment") and row["redcap_field"]:
        if row["redcap_field"] in built:
            return "pipeline_partial" if row["redcap_field"] in PARTIAL_BUILT else "pipeline_now"
    return {"mapped_clif_2_1": "derivable_not_built", "mapped_needs_clif_3_0": "derivable_not_built",
            "partial": "partial_not_built", "not_in_clif": "not_in_clif", "admin_non_ehr": "non_ehr"}[status]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inventory", default=str(ROOT / "config" / "redcap" / "crf_elements.csv"))
    args = ap.parse_args()
    if not Path(args.inventory).exists():
        raise SystemExit(f"{args.inventory} not found (CRF inventory is not in the repo; see README).")
    inv = pd.read_csv(args.inventory, dtype=str).fillna("")
    q = inv[inv["level"] == "item"].copy()
    q["class"] = q.apply(classify, axis=1, built=built_fields())
    q["weight"] = q["repeats"].map(repeats)

    rows = []
    for inst, g in list(q.groupby("instrument", sort=False)) + [("ALL INSTRUMENTS", q)]:
        n = g["class"].value_counts()
        w = g.groupby("class")["weight"].sum()
        ehr = len(g) - n.get("non_ehr", 0)
        ehr_w = g["weight"].sum() - w.get("non_ehr", 0)
        rows.append({
            "instrument": inst, "questions": len(g), **{c: int(n.get(c, 0)) for c in CLASSES},
            "pct_pipeline_now": round(100 * (n.get("pipeline_now", 0) + n.get("pipeline_partial", 0)) / ehr, 1) if ehr else "",
            "pct_manual_remaining": round(100 * (ehr - n.get("pipeline_now", 0)) / ehr, 1) if ehr else "",
            "pct_derivable_from_clif": round(100 * (n.get("pipeline_now", 0) + n.get("pipeline_partial", 0) + n.get("derivable_not_built", 0)) / ehr, 1) if ehr else "",
            "question_repeats_per_participant": int(g["weight"].sum()),
            "pct_pipeline_now_weighted": round(100 * (w.get("pipeline_now", 0) + w.get("pipeline_partial", 0)) / ehr_w, 1) if ehr_w else "",
        })
    out = pd.DataFrame(rows)
    path = ROOT / "output" / "summary" / "crf_coverage.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    pd.set_option("display.width", 250)
    print(out.to_string(index=False))
    print(f"\nwrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
