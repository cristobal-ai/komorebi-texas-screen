# data/

Local cache. Everything here is gitignored **except** this file, `crosswalk_eia_ercot.csv` and `load_pockets.csv`.

- `raw/<source>/` — untouched downloads as Parquet (EIA, USPVDB, ERCOT, TxGIO, SSURGO, NSRDB, HIFLD/OSM, RRC, FEMA).
- `*.parquet` at this level — phase outputs.
- `crosswalk_eia_ercot.csv` — the hand-verified EIA plant ID ↔ ERCOT resource mapping. **Committed.** Re-runs must read it before fuzzy matching and must never overwrite a row whose `verified_by` is set. Columns: `eia_plant_id, eia_plant_name, ercot_resource_name, ercot_settlement_point, county, ac_mw_eia, ac_mw_ercot, cod_eia, match_method, match_confidence, verified_by, verified_on, notes`, then evidence columns written by Phase 3a: `cdr_unit_name, cdr_year, name_score, mw_error_pct, sced_coverage, sced_max_hsl_mw`. One row per (plant, ERCOT unit) — multi-phase plants have several rows; `match_confidence` is high | medium | low | none (thresholds in `pipeline/config.yaml` → `crosswalk`). To confirm a match, fill `verified_by` (initials) and `verified_on`; edit or delete wrong rows and add missing ones by hand. `data/validation/crosswalk_review.md` lists every row, worst confidence first.

- `load_pockets.csv` — the hand-maintained table of large loads behind the load-pocket score (Section D, 5 pts; layer `load_pocket`). **Committed**; the input until the PUCT large-load transparency rule makes ERCOT's queue a dataset. One row per project: `project_id` (unique slug), `name`, `developer`, `kind` (data_center | crypto | hydrogen | industrial | other), `status` (operating | under_construction | announced | cancelled), `load_mw` (first phase or operating, as published), `load_mw_basis`, `county`, `city`, `lat`, `lon`, `location_confidence` (site | city | county — never invent a site), `source_url` (required), `source_date`, `notes`, `added_by`, `added_on`, `verified_by`, `verified_on`. What counts: data centers of any size; other kinds at `load_mw` >= 75 (`layers.load_pocket` in `config.yaml`); cancelled rows are kept and ignored. Edit by hand, then `python -m pipeline.run --phase 4 --layer load_pocket --load` and `--phase 5 --load`; the run stops on a malformed row.

`data/raw/ercot_cdr/manual/` — drop the latest ERCOT CDR workbook here (no stable URL; https://www.ercot.com/gridinfo/resource).

Once the pipeline runs in GitHub Actions, the raw cache is also mirrored to Supabase Storage so a fresh clone does not need to re-download multi-GB SCED history.

`data/raw/parcels/manual/` — drop the TxGIO StratMap Land Parcels files here (county zips or the statewide geodatabase; no stable URL, download from https://data.geographic.texas.gov). `data/raw/transmission/manual/` may hold a HIFLD transmission-line file or a `.osm.pbf` extract.
