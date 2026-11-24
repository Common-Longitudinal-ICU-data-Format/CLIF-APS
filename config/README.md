## Configuration

- `demo.json` — the open CLIF-MIMIC demo in `data/demo` (tracked; used for development).
- `real.example.json` — copy to `real.json` (gitignored) and set `data_directory`, `filetype`, `timezone`
  for your site. Keep `output_directory` as `output/real` so row-level output stays in the gitignored,
  Claude-denied folder.
- `redcap/daily_mapping.yaml` — CLIF → REDCap mapping for the daily assessment fields.
- `redcap/daily_assessment_codebook_day_m2.csv` and `redcap/daily_fields.csv` — the APS codebook export and the
  spec built from it. Not in the repo (gitignored); see the main README, step 2.
- `aps_clif_data_requirements.yaml`, `daily_assessment_mapping.yaml`, `config_template.json` — used by the legacy
  notebooks only.
