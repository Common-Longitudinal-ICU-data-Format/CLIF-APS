<p align="center">
  <img src="images/logo.png" alt="CLIF-APS Logo" width="400">
</p>

# CLIF-APS Connect

## CLIF VERSION 

3.0 (all tables are loaded and validated against the CLIF 3.0 schema with clifpy 3.0, pinned to the
`main-3.0` branch until it is released on PyPI)

## Objective

A pipeline to extract and transform CLIF data for patients enrolled in APS clinical trials.

## Cohort identification

Patients enrolled in APS clinical trials, from the **CLIF 3.0 `clinical_trial` table, which is required**:
`participant_id`, `patient_id`, `hospitalization_id` and `enrollment_dttm` define every row and study day of the
CRF, so the pipeline stops if the table is missing, lacks one of those columns, or has blanks in them. Save it in
`data_directory` as `clif_clinical_trial.<filetype>`, one row per enrolled participant:

| Field Name         | Type     | Description                                                                                               |
|--------------------|----------|-----------------------------------------------------------------------------------------------------------|
| participant_id     | string   | **Required.** APS REDCap record ID (written as `record_id`); must match the REDCap project or the import creates new records |
| patient_id         | string   | **Required.** CLIF patient_id                                                                             |
| hospitalization_id | string   | **Required.** The index hospitalization (the stay in which the participant was enrolled); only its data are used |
| trial_id           | string   | Required by CLIF 3.0. Trial identifier (e.g. NCT number)                                                  |
| trial_name         | string   | Descriptive name of the trial                                                                             |
| arm_id             | string   | Required by CLIF 3.0. APS has one arm (`1`)                                                               |
| consent_dttm       | datetime | Required by CLIF 3.0. Consent time                                                                        |
| enrollment_dttm    | datetime | **Required.** APS Study Time Zero; its site-local calendar date is study day 0. Naive = site-local        |
| randomized_dttm    | datetime | Required by CLIF 3.0. APS is not randomized: use enrollment_dttm                                          |
| withdrawal_dttm    | datetime | Optional. Days after the withdrawal date are marked Not Available                                         |

## Required CLIF tables and fields
The current pipeline (`code/run_aps.py`, Daily Assessment) reads **clinical_trial**, **hospitalization**,
**vitals** (`sbp`, `dbp`, `spo2`, `respiratory_rate`), **labs**, **respiratory_support**,
**medication_admin_continuous** (vasoactives, `tpn`), **patient_assessments** (`gcs_total`), and if present the
CLIF 3.0 **input** and **mcs** tables. The full APS form needs the tables below
(based on [APS-CLIF crosswalk](data/APS_CLIF_crosswalk.csv)):

1. **patient**: `patient_id`, `sex_category`, `race_category`, `ethnicity_category`, `language_category`, `death_dttm`

2. **hospitalization**: `patient_id`, `hospitalization_id`, `admission_dttm`, `discharge_dttm`, `age_at_admission`, `discharge_category`, `admission_type_category`

3. **adt**: `hospitalization_id`, `location_category`, `in_dttm`, `out_dttm`

4. **vitals**: `hospitalization_id`, `recorded_dttm`, `vital_category`, `vital_value`
   - `vital_category` = `spo2`, `sbp`, `dbp`, `temp_c`, `height_in`, `heart_rate`, `weight_kg`

5. **labs**: `hospitalization_id`, `lab_result_dttm`, `lab_category`, `lab_value`
   - `lab_category` = `po2_arterial`, `ph_arterial`, `pco2_arterial`, `so2_arterial`, `wbc`, `hemoglobin`, `platelet_count`, `neutrophils_absolute`, `lymphocytes_absolute`, `sodium`, `potassium`, `bicarbonate`, `bun`, `glucose_serum`, `creatinine`, `albumin`, `bilirubin_total`, `ast`, `alt`, `inr`, `ptt`, `crp`, `procalcitonin`, `ferritin`, `lactate`, `troponin_i`, `troponin_t`

6. **respiratory_support**: `hospitalization_id`, `recorded_dttm`, `device_category`, `mode_category`, `fio2_set`, `peep_set`, `lpm_set`, `pressure_support_set`, `tidal_volume_set`, `tidal_volume_obs`, `mean_airway_pressure_obs`, `peak_inspiratory_pressure_obs`, `plateau_pressure_obs`, `minute_vent_obs`, `resp_rate_obs`

7. **medication_admin_continuous**: `hospitalization_id`, `admin_dttm`, `med_name`, `med_category`, `med_dose`, `med_dose_unit`
   - `med_category` = `norepinephrine`, `epinephrine`, `phenylephrine`, `vasopressin`, `dopamine`, `dobutamine`, `angiotensin`, `milrinone`, `heparin`, `eptifibatide`, `hydromorphone`, `morphine`, `fentanyl`, `lorazepam`, `midazolam`, `dexmedetomidine`, `propofol`, `ketamine`, `cisatracurium`, `rocuronium`, `vecuronium`, `insulin`, `nitric_oxide`, `epoprostenol`, `treprostinil`, `tacrolimus`, `remifentanil`

8. **medication_admin_intermittent**: `hospitalization_id`, `admin_dttm`, `med_name`, `med_category`, `med_dose`, `med_dose_unit`
   - `med_category` = antibiotics (e.g., `amikacin`, `amoxicillin`, `azithromycin`, `cefepime`, `ceftriaxone`, `ciprofloxacin`, `levofloxacin`, `meropenem`, `piperacillin_tazobactam`, `vancomycin`, etc.), antifungals (e.g., `amphotericin_b`, `caspofungin`, `fluconazole`, `micafungin`, `voriconazole`), antivirals (e.g., `acyclovir`, `ganciclovir`, `oseltamivir`, `valacyclovir`), corticosteroids (`dexamethasone`, `hydrocortisone`, `methylprednisolone`, `prednisolone`, `prednisone`), paralytics (`cisatracurium`, `rocuronium`, `vecuronium`, `atracurium`, `succinylcholine`), sedation/analgesia (`morphine`, `fentanyl`, `hydromorphone`, `lorazepam`, `midazolam`, `propofol`, `ketamine`, `oxycodone`, `diazepam`), antipsychotics (`haloperidol`, `olanzapine`, `quetiapine`, `risperidone`), immunomodulators (`anakinra`, `tocilizumab`), CAR-T therapies (`axicabtagene_ciloleucel`, `brexucabtagene_autoleucel`, `idecabtagene_vicleucel`, `lisocabtagene_maraleucel`, `tisagenlecleucel`)

9. **patient_assessment**: `hospitalization_id`, `recorded_dttm`, `assessment_category`, `assessment_value`
   - `assessment_category` = `gcs_total`, `RASS`, `cam_total`

10. **hospital_diagnosis**: `hospitalization_id`, `diagnosis_code`

11. **position**: `hospitalization_id`, `recorded_dttm`, `position_category`
    - `position_category` = `prone`

12. **microbiology_culture**: `hospitalization_id`, `collect_dttm`, `organism_category`, `fluid_category`
    - `fluid_category` = `blood_buffy`, `kidneys_renal_pelvis_ureters_bladder`, `respiratory_tract_lower`, `respiratory_tract`, `pleural_cavity_fluid`, `peritoneum`, `joints`, `meninges_csf`, `skin_rash_pustules_abscesses`, `feces_stool`
    - `organism_category` = various bacteria and fungi (see crosswalk for full list)

13. **microbiology_nonculture**: `hospitalization_id`, `collect_dttm`, `organism_category`, `result_category`
    - `organism_category` = `sars_cov2`, `respiratory_syncytial_virus`, `clostridium_difficile`

14. **microbiology_susceptibility**: `hospitalization_id`, `antimicrobial_category`, `susceptibility_category`
    - `antimicrobial_category` = `amikacin`, `amoxicillin_clavulanate`, `ampicillin_sulbactam`, `aztreonam`, `cefazolin`, `cefdinir`, `cefepime`, `cefotaxime`, `ceftazidime`, `ceftriaxone`, `ciprofloxacin`, `clindamycin`, `doxycycline`, `ertapenem`, `erythromycin`, `gentamicin`, `levofloxacin`, `meropenem`, `methicillin`, `minocycline`, `nitrofurantoin`, `oxacillin`, `penicillin`, `piperacillin_tazobactam`, `rifampin`, `streptomycin`, `tetracycline`, `tobramycin`, `trimethoprim_sulfamethoxazole`, `vancomycin`

## Detailed Instructions for running the project

**Working principle: code goes to the data, data never goes to the model.** The pipeline is developed and
tested on the open CLIF-MIMIC demo in `data/demo`. Each site runs it on its own data in its own terminal.
The REDCap upload CSV is row-level patient data and stays on site; only aggregate summaries (counts 1-10 shown
as `<11`) are shared or shown to an AI assistant. The guardrails follow
[clif-sensitive-data-demo](https://github.com/kaveriC/clif-sensitive-data-demo) (see "Using Claude Code" below).

### Prerequisites

- [uv](https://docs.astral.sh/uv/) - Python package manager

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1. Set up the environment

```bash
cd CLIF-APS
uv sync
```

### 2. Add the APS REDCap codebook (not in this repo)

The APS codebook is not published here. Get the `APS_to_CLIF` sheet from the APS data team, export the
`daily_assessment_codebook` tab (the day -2 instrument) as CSV to
`config/redcap/daily_assessment_codebook_day_m2.csv`, then build the field spec:

```bash
uv run python code/build_redcap_spec.py
```

This writes `config/redcap/daily_fields.csv` (one row per field, `{d}` where the day suffix goes). Both files
are gitignored. Rerun the build whenever the codebook changes. REDCap variable names always come from this
spec, never from a naming pattern: the day suffix is not always at the end, and some names contain typos
(e.g. `daily_niv_fi02_8a_{d}`).

### 3. Configure your site

```bash
cp config/real.example.json config/real.json
```

Edit `config/real.json` (gitignored):

| key | meaning |
|---|---|
| `data_directory` | folder with your CLIF 3.0 tables and `clif_clinical_trial.<filetype>` |
| `filetype` | `parquet` or `csv` |
| `timezone` | site time zone, e.g. `US/Central`; study days and "8am" are site-local |
| `output_directory` | keep `output/real` (row-level output, gitignored) |
| `label` | `real`; names the summary files |

`clif_clinical_trial` uses the CLIF 3.0 format in [Cohort identification](#cohort-identification). `enrollment_dttm`
is APS Study Time Zero; naive timestamps are read as site-local.

### 4. Run the pipeline and the verifier on real data (in your own terminal)

```bash
uv run python code/run_aps.py --config config/real.json
uv run python code/verify_aps.py --config config/real.json
```

`run_aps.py` writes:
- `output/real/daily_assessment_upload.csv` — the file for REDCap **Data Import Tool** (bulk upload, no API),
  event `daily_inhospital_f_arm_1`, one row per `record_id`
- `output/real/daily_assessment_evidence.parquet` — every filled cell with its source timestamp, for audit
- `output/summary/run_real.csv` — aggregate counts only

`verify_aps.py` recomputes every cell independently from the raw CLIF tables, checks the file against the
codebook (column names, formats, choice codes, min/max, record_id, event name, gate and "not collected"
consistency) and writes `output/summary/verify_real.csv` (aggregates) plus `output/real/verify_mismatches.csv`
(row-level, stays on site). It exits 1 on any error. Upload only after `RESULT: PASS`.

### How fields are derived

The pipeline fills the **Daily Assessment/SOFA Scoring** instruments (study days -2..7); the other in-hospital
instruments are next (they need the full REDCap Data Dictionary export for exact variable names). Every rule and
REDCap field name is in [config/redcap/daily_mapping.yaml](config/redcap/daily_mapping.yaml).

- **Study day 0** = calendar date of `enrollment_dttm` in site-local time; days -2..7 map to suffixes
  `m2, m1, 0..7`. Only the `clinical_trial` row's `hospitalization_id` is used.
- **Gate** `dailysofa_perf_{d}` = 1 when the patient was in the index hospitalization that calendar day (and not
  withdrawn), else 0 with every other field blank. Nothing is entered after discharge.
- **Branching**: the REDCap branching logic from the codebook is applied, so a value is written only where the
  form would show the field (e.g. IMV settings only when respiratory support is IMV).
- **Closest to 8am** = smallest time difference to 08:00 local, ties to the earlier measurement. Vitals use the
  CLIF 3.0 bedside time `measured_dttm` when present (else `recorded_dttm`); labs use `lab_collect_dttm`
  (fallback `lab_result_dttm`). Lowest SpO2 and highest lactate/troponin use the day's min/max.
- **Respiratory support** at the 8am SpO2, the lowest SpO2 and the lowest PaO2: the last `respiratory_support`
  row at or before that time **within 4 h** (rows without a device belong to the device charted before them;
  each setting is used only if charted for that device within 4 h). **No row in the prior 12 h = room air**
  (code 7). A last row 4-12 h old is left blank for chart review. ECMO comes from the CLIF 3.0 `mcs` table when
  present. IMV/NIV/HFNC/standard-O2 settings map to CLIF set/observed columns; observed RR falls back to vitals
  `respiratory_rate` within 60 min; NIV IPAP = `peak_inspiratory_pressure_set`, else PEEP + pressure support.
- **Lowest PaO2**: lowest `po2_arterial` of the day, its time (HH:MM), and pH / PaCO2 / SaO2 from the same draw
  (identical collect time); SpO2 at that time = nearest SpO2 within 60 min.
- **Vasopressors** infusing at the 8am SBP: per drug the last `medication_admin_continuous` row within 4 h,
  running unless it is a stop / not-administered action or dose 0; units and rate converted to the form's
  mcg/min, mcg/kg/min or units/min (CLIF 3.0 `angiotensin_ii` from ng/kg/min).
- **GCS** closest to 8am; on IMV a total <= 11 is coded as "T" (3T-11T). No GCS that day = "not documented".
- **Nutrition**: oral (`input` po_intake) and TPN (`input` parenteral_nutrition or a running `tpn` infusion)
  within 4 h of 8am. Full vs trophic tube feeding is left for chart review.
- **Labs**, including CLIF 3.0 hematocrit, D-dimer and fibrinogen. **Not collected** (= 1) only when the day is
  available, no value exists, and the lab category appears in the cohort's data at all; hs-troponin has no CLIF
  category and stays blank for manual abstraction.
- **Values**: implausible values removed with clifpy's outlier ranges; censored results (`<5`) entered as the
  number; integer-validated fields rounded half-up; troponin converted from CLIF ng/L to REDCap ng/mL.
  Hemoglobin is kept at 1 decimal although the codebook validates it as integer (flagged to the APS data team).

### Coverage: what the pipeline filled and what is left for chart abstraction

Every run writes `output/summary/coverage_<label>.csv` (aggregates only, counts 1-10 shown as `<11`). The unit is
one REDCap question on one in-hospital participant-day (a lab value and its "not collected" flag count as one
question; a checkbox as one), classified as:

| class | meaning |
|---|---|
| `auto` | answered by the pipeline (`auto_absence` = the subset answered from the absence of charting: lab not collected, GCS not documented, no PaO2, room air with no device charted in 12 h) |
| `partial` | answered in part (nutrition: oral/TPN filled, tube feeding left for the abstractor) |
| `manual` | shown by the form's branching but blank: **chart abstraction remaining** |
| `pending` | hidden only because its parent question is still manual (may become manual once answered) |

Rows are per question, per form section and in total, with `pct_auto` = auto / (auto + partial + manual).

`uv run python code/crf_coverage.py` summarizes the **whole CRF** (all instruments) from the crosswalk inventory
`config/redcap/crf_elements.csv` (from the APS data team; gitignored): per instrument, questions the pipeline fills
now, derivable from CLIF but not built yet, partial, not in CLIF, and non-EHR, unweighted and weighted by how often
each question repeats per participant.

### Tests

```bash
uv run python code/test_synthetic.py    # demo + synthetic scenario: hand-computed values and verifier PASS
uv run python code/test_mutations.py    # verifier must catch every reintroduced bug (about 8 minutes)
```

`test_synthetic.py` injects a fully specified day into two empty demo participant-days (IMV on ECMO with bedside
times, mcg/min, units/hr and ng/kg/min infusions, a stopped infusion, CLIF 3.0 labs, an ABG, GCS on IMV, TPN; and a room-air
day with oral intake) because MIMIC lacks those data.

### Try it on the demo data

```bash
uv run python code/make_demo_trial_table.py   # fake clif_clinical_trial: all 100 demo patients enrolled
uv run python code/run_aps.py --config config/demo.json
uv run python code/verify_aps.py --config config/demo.json
```

Demo enrollment = first ICU arrival + 12 h (or half the ICU stay if shorter). Outputs go to `output/demo/`.

### Using Claude Code on this project

`.claude/settings.json`, `.claude/hooks/` and `CLAUDE.md` keep real data out of the model:
- deny rules on `data/phi`, `output/real`, `output/logs`;
- a PreToolUse hook that blocks any tool call naming the real data path, `config/real.json` or `output/real`,
  and shell commands that print rows (`.head(`, `.iloc[`, ...);
- a prompt hook that drops pasted data rows, and an advisory PHI-pattern scanner on tool output.

Add a deny rule for your real data path to `.claude/settings.local.json` (gitignored):

```json
{"permissions": {"deny": ["Read(//absolute/path/to/real/data/**)", "Edit(//absolute/path/to/real/data/**)"]}}
```

Open Claude Code in the `CLIF-APS` folder and run `/sensitive-data-check`; every required row should pass.
Claude develops against `config/demo.json`; you run `config/real.json` yourself and share only
`output/summary/*_real.csv`.

### Legacy

`code/00_setup.ipynb`, `code/01_daily_assessment.ipynb`, `config/daily_assessment_mapping.yaml`,
`code/test_daily_assessment.py` and the Streamlit dashboard (`app/app.py`, which reads the notebook's
intermediate files) are superseded by `run_aps.py` / `verify_aps.py` and kept for reference.

## Demo data and licence

`data/demo/` is the CLIF 3.0-formatted **MIMIC-IV Clinical Database Demo** (from clifpy's `main-3.0` branch) (Johnson, A., Pollard, T., & Mark, R.,
v2.2, PhysioNet, 2023; https://physionet.org/content/mimic-iv-demo/2.2/), released under the Open Data Commons
Open Database License v1.0 (ODbL). It contains no real identifiers. `data/demo/clif_clinical_trial.parquet`
is synthetic (made by `code/make_demo_trial_table.py`).
