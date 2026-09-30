# supabase/

Schema, seed and row-level-security policies for the Supabase project. Managed with the Supabase CLI (`supabase db push`) or from Cowork through the Supabase connector.

Planned tables (see `docs/build-plan-v2.md` §3.6):

- `plants` — **migration `20260930210716_plants.sql`**: one row per plant with `filter_status` `pass` or `review` (78 at 30 Sep 2026); identity, capacity, COD, tier, SB6, USPVDB polygon (`geom`, PostGIS, EPSG:4326), EIA-860/923 fields. Loaded by `python -m pipeline.run --phase 2 --load` (`pipeline/load_supabase.py`: upsert on `eia_id`, delete rows from earlier runs, verify count).
- `plant_metrics_monthly` — CF, curtailment, capture rate by month; `cf_series_resolution`, `sced_coverage`.
- `layers_transmission`, `layers_gas`, `layers_flood`, `layers_parcels`, `layers_fiber`, `layers_climate`, `layers_wells`, `layers_soils` — one row per plant per layer with a `_confidence` column.
- `plant_scores` — sub-scores A–F, composite, tier rank, overall rank, `config_version`.
- `crosswalk_eia_ercot` — mirrors `data/crosswalk_eia_ercot.csv`; the admin review page edits this table and the pipeline exports it back to CSV.
- `score_config` — versioned copies of `pipeline/config.yaml`.
- `users_allowlist` — **in the same migration**: emails allowed to receive a magic link; role (`viewer` | `admin`). `private.is_allowlisted(role)` (security definer, in a schema the API does not expose) backs the RLS policies.

Applied to the project 30 Sep 2026 via the Supabase connector: `20260930210716_plants`, `…210745_private_allowlist_fn`, `…210802_allowlist_policy_perf`, `…210816_allowlist_initplan`. Filenames match the server's version numbers so `supabase db push` sees them as applied. Security and performance advisors clean (only INFO "unused index" on the then-empty table). First admin row seeded by hand (not in a migration, to keep emails out of the repo).

RLS: authenticated users read `plants`, `plant_metrics_monthly`, `layers_*`, `plant_scores`; only `admin` writes `crosswalk_eia_ercot`, `users_allowlist`, `score_config`. The service-role key is used only by the pipeline and server-side admin routes.

The first migration installs `postgis` in the `extensions` schema. Project: `komorebi-texas-screen`, ref `ouetbhexrewwsxomubdv`, us-east-1.
