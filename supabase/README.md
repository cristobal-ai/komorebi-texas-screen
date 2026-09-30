# supabase/

Schema, seed and row-level-security policies for the Supabase project. Managed with the Supabase CLI (`supabase db push`) or from Cowork through the Supabase connector.

Planned tables (see `docs/build-plan-v2.md` §3.6):

- `plants` — one row per plant passing hard filters; identity, capacity, COD, tier, polygon (PostGIS), USPVDB and EIA fields.
- `plant_metrics_monthly` — CF, curtailment, capture rate by month; `cf_series_resolution`, `sced_coverage`.
- `layers_transmission`, `layers_gas`, `layers_flood`, `layers_parcels`, `layers_fiber`, `layers_climate`, `layers_wells`, `layers_soils` — one row per plant per layer with a `_confidence` column.
- `plant_scores` — sub-scores A–F, composite, tier rank, overall rank, `config_version`.
- `crosswalk_eia_ercot` — mirrors `data/crosswalk_eia_ercot.csv`; the admin review page edits this table and the pipeline exports it back to CSV.
- `score_config` — versioned copies of `pipeline/config.yaml`.
- `users_allowlist` — emails allowed to receive a magic link; role (`viewer` | `admin`).

RLS: authenticated users read `plants`, `plant_metrics_monthly`, `layers_*`, `plant_scores`; only `admin` writes `crosswalk_eia_ercot`, `users_allowlist`, `score_config`. The service-role key is used only by the pipeline and server-side admin routes.

Enable the `postgis` extension before the first migration.
