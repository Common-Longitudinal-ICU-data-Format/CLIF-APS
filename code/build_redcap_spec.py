"""Turn the REDCap codebook export (day -2 instrument) into a per-field spec for all 10 daily instruments.

    uv run python code/build_redcap_spec.py

Input : config/redcap/daily_assessment_codebook_day_m2.csv  (tab `daily_assessment_codebook` of the
        APS_to_CLIF Google Sheet; columns: #, [variable] + branching logic, label, type/validation)
Output: config/redcap/daily_fields.csv  one row per field template, with `{d}` where the day suffix goes.

The day -2 instrument is the template: every `m2` token in a variable name becomes `{d}`. The suffix is not
always at the end (e.g. daily_ne_dose_8a_m2_mcg), so names are never built by appending a suffix.
"""
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CODEBOOK = ROOT / "config" / "redcap" / "daily_assessment_codebook_day_m2.csv"
SPEC = ROOT / "config" / "redcap" / "daily_fields.csv"

TEMPLATE_DAY = "m2"
DAY_TOKEN = re.compile(rf"(?<=_){TEMPLATE_DAY}(?=_|$|\]|\()")
VAR = re.compile(r"^\[([a-z0-9_]+)\]")
VALIDATION = re.compile(r"^text \((?P<val>[a-z_0-9]+)(?:, Min: (?P<min>[-\d.]+))?(?:, Max: (?P<max>[-\d.]+))?")
CHOICE_CODE = re.compile(r"(?:^|\s)(-?\d+)\s")


def parse_type(raw: str):
    raw = (raw or "").strip()
    ftype = re.match(r"[a-z]+", raw).group(0) if raw else ""
    validation = vmin = vmax = choices = ""
    if ftype == "text":
        m = VALIDATION.match(raw)
        if m:
            validation, vmin, vmax = m.group("val"), m.group("min") or "", m.group("max") or ""
    elif ftype in ("radio", "dropdown", "yesno", "checkbox"):
        body = raw[len(ftype):].split("\n")[0].replace(", Required", "")
        choices = "|".join(CHOICE_CODE.findall(" " + body))
    return ftype, validation, vmin, vmax, choices


def main():
    if not CODEBOOK.exists():
        raise SystemExit(f"{CODEBOOK.relative_to(ROOT)} not found: export the codebook tab as CSV there first (see README)")
    cb = pd.read_csv(CODEBOOK, header=None, dtype=str).fillna("")
    rows = []
    for _, r in cb.iterrows():
        m = VAR.match(r[1])
        if not m:
            continue
        name = m.group(1)
        ftype, validation, vmin, vmax, choices = parse_type(r[3])
        if ftype == "descriptive" or name.endswith("_complete"):
            continue
        if not DAY_TOKEN.search(name):
            raise SystemExit(f"{name}: no '{TEMPLATE_DAY}' day token; cannot template it")
        branching = r[1][m.end():].replace("Show the field ONLY if:", "").strip()
        rows.append({
            "field_template": DAY_TOKEN.sub("{d}", name),
            "field_type": ftype,
            "validation": validation,
            "min": vmin,
            "max": vmax,
            "choice_codes": choices,
            "branching_logic": DAY_TOKEN.sub("{d}", branching),
        })
    spec = pd.DataFrame(rows)
    spec.to_csv(SPEC, index=False)
    print(f"wrote {SPEC.relative_to(ROOT)}: {len(spec)} field templates")
    print(spec.groupby(["field_type", "validation"]).size().to_string())


if __name__ == "__main__":
    main()
