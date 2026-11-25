# CLIF-APS

Pipeline: CLIF 3.0 tables for patients enrolled in the APS trial → CSV for the REDCap Data Import
Tool (bulk upload, no API). Built with `clifpy`, run with `uv`.

## Sensitive-data rules (from clif-sensitive-data-demo; code goes to the data, data never goes to the model)
- Develop and test only against `data/demo` (open CLIF-MIMIC demo, 100 patients, ODbL) with `config/demo.json`.
  Never read `data/phi`, `config/real.json`, the real data path, `output/real/` or `output/logs/`; all are denied
  and that is intentional.
- Never print rows. Report schema, dtypes, counts, and aggregates; counts under 11 are shown as "<11".
- The REDCap upload CSV is row-level patient data. On real data it is written to `output/real/` and never read
  by Claude. Only aggregate verification summaries (`output/summary/*_real.csv`) come back.
- Real-data runs are done by the researcher in a separate terminal:
  `uv run python code/run_aps.py --config config/real.json`.
- Keep intermediates under `output/` (gitignored), never in the scratchpad or /tmp.
- Run `/sensitive-data-check` before any real-data work.

## Commands
- `uv sync` — environment (clifpy 3.0.0, pinned git commit on the `main-3.0` branch)
- `uv run python code/make_demo_trial_table.py` — fake CLIF 3.0 `clif_clinical_trial` for the demo (all 100 patients)
- `uv run python code/build_redcap_spec.py` — codebook export → `config/redcap/daily_fields.csv` (rerun if the codebook changes)
- `uv run python code/run_aps.py --config config/demo.json` — REDCap upload CSV + aggregate run summary
- `uv run python code/verify_aps.py --config config/demo.json` — independent recomputation + import checks; exit 1 on failure
- Legacy, superseded by `run_aps.py`: `code/00_setup.ipynb`, `code/01_daily_assessment.ipynb`, `config/daily_assessment_mapping.yaml`

## REDCap target (source of truth: `APS_to_CLIF` Google Sheet, tab `daily_assessment_codebook`)
- Longitudinal, single arm. Daily form = 10 non-repeating instruments, event `daily_inhospital_f_arm_1`,
  day suffixes `m2, m1, 0..7`. Day 0 = calendar date (site local time) of enrollment / Study Time Zero.
- Suffix is NOT always at the end of the variable name (e.g. `daily_ne_dose_8a_<d>_mcg`), and some names
  contain typos (`daily_niv_fi02_8a`, `daily_hfnc_fi02_8a`, `daily_pa02_lowest`, `daily_phen_dos_8a_<d>_mcgkg`).
  Always take names from the codebook, never construct them by pattern.
- Lab "not collected" fields (`daily_<lab>_nc_<d>`) are radio, code `1` = Not Collected (not checkboxes).
- Gate field `dailysofa_perf_<d>`: 1 Available / 0 Not Available; every other field is branched on it.
- Integer-validated fields (e.g. hgb, hct, platelets, Na, HCO3, BUN, glucose) must be rounded or the import fails.
- No daily data for days after hospital discharge.
