<p align="center">
  <img src="images/logo.png" alt="CLIF-APS Logo" width="400">
</p>

# CLIF-APS Connect

## CLIF VERSION 

[2].[2]

## Objective

A pipeline to extract and transform CLIF data for patients enrolled in APS clinical trials.

## Cohort identification

Patients enrolled in APS clinical trials. Use the CLIF clinical trials table, defined at the patient level with the below format:

| Field Name         | Type     | Description                                                                                               |
|--------------------|----------|-----------------------------------------------------------------------------------------------------------|
| record_id          | string   | Study-specific record identifier                                                                          |
| patient_id         | string   | Unique identifier for each patient                                                                        |
| trial_id           | string   | Unique identifier for the clinical trial (e.g., institutional trial ID, NCT number).                      |
| trial_name         | string   | Descriptive name of the clinical trial.                                                                   |
| arm_id             | string   | Identifier indicating which arm of the trial the patient was enrolled in.                                 |
| enrollment_dttm    | datetime | Timestamp of patient enrollment in the clinical trial.                                                    |
| withdrawal_dttm    | datetime | Timestamp of trial withdrawal if applicable.                                                              |

## Required CLIF tables and fields
The following tables are required (based on [APS-CLIF crosswalk](data/APS_CLIF_crosswalk.csv)):

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

### Prerequisites

- [uv](https://docs.astral.sh/uv/) - Fast Python package manager (recommended)

To install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1. Update `config/config.json`
Follow instructions in the [config/README.md](config/README.md) file for detailed configuration steps.

### 2. Set up the project environment

Using **uv** (recommended):
```bash
# Clone the repository and navigate to the project directory
cd CLIF-APS

# Install dependencies and create virtual environment
uv sync

# Register the Jupyter kernel (optional, for IDE/notebook use)
uv run python -m ipykernel install --user --name clif-aps --display-name "CLIF-APS (Python 3.11)"
```


### 3. Run the data pipeline

Run the `code/00_setup.ipynb` notebook.

This will create the following files in `output/intermediate/`:
- `static_df.parquet` - Patient demographics and hospitalization info
- `wide_df.parquet` - Time-series data (vitals, labs, meds, respiratory, ADT)
- `micro_culture_df.parquet` - Microbiology culture data
- `micro_nonculture_df.parquet` - Microbiology non-culture data (PCR/molecular)

### 4. Launch the Dashboard

After running the setup notebook, launch the Streamlit dashboard to visualize patient data:

```bash
uv run streamlit run app/app.py
```

The dashboard will open in your browser at `http://localhost:8501` and includes:
- **Patient selector** - Dropdown to choose a patient ID
- **Static information** - Demographics, hospitalization details, trial info
- **Time-series data** - Preview of vitals, labs, medications, respiratory support, and ADT locations

