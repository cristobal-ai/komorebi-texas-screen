# data/

Local cache. Everything here is gitignored **except** this file and `crosswalk_eia_ercot.csv`.

- `raw/<source>/` — untouched downloads as Parquet (EIA, USPVDB, ERCOT, TxGIO, SSURGO, NSRDB, HIFLD/OSM, RRC, FEMA).
- `*.parquet` at this level — phase outputs.
- `crosswalk_eia_ercot.csv` — the hand-verified EIA plant ID ↔ ERCOT resource mapping. **Committed.** Re-runs must read it before fuzzy matching and must never overwrite a row whose `verified_by` is set. Columns: `eia_plant_id, eia_plant_name, ercot_resource_name, ercot_settlement_point, county, ac_mw_eia, ac_mw_ercot, cod_eia, match_method, match_confidence, verified_by, verified_on, notes`.

Once the pipeline runs in GitHub Actions, the raw cache is also mirrored to Supabase Storage so a fresh clone does not need to re-download multi-GB SCED history.
