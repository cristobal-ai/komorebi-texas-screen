# pipeline/

Python 3.11 screening pipeline. Phases follow `docs/brief.md` §4; each phase reads the previous phase's Parquet from `data/` and writes its own.

| Phase | Package | Reads | Writes |
|---|---|---|---|
| 1 | `phase_1_ingest` | EIA 860/860M/923, USPVDB v4.0, ERCOT API, geo sources | `data/raw/<source>/*.parquet` |
| 2 | `phase_2_normalize` | raw | `data/plants.parquet` (plant master, filters, tiers) |
| 3 | `phase_3_market` | plants + ERCOT SCED/SPP | `data/plant_metrics_monthly.parquet` |
| 4 | `phase_4_geo` | plants + layers | `data/layers_<name>.parquet` |
| 5 | `phase_5_score` | all of the above + `config.yaml` | `data/plant_scores.parquet` → Supabase |
| 6 | `phase_6_report` | scores | XLSX + dossiers → Supabase Storage |

`run.py` (to be written in Phase 1–2) is the single entry point: `python -m pipeline.run --phase N [--county Pecos]`.

Rules: cache every download before transforming; never let a null reach the composite score; every estimated field carries a `_confidence` sibling; all numbers come from `config.yaml`.
