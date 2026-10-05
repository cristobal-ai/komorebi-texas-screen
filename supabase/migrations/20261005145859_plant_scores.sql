-- Phase 5 scores: the brief's §5 model as stored sub-scores so the web app can re-weight sections client-side.
-- One row per pass/review plant; written by the pipeline with the secret key (pipeline/phase_5_score/load.py), read by
-- allowlisted users. Column list must match pipeline/phase_5_score/load.py SCORE_COLUMNS (lower-cased; a test checks).
-- Missing inputs and layers not built yet score half their points (owner decision 5 Oct 2026) and lower data_completeness.
create table public.plant_scores (
  eia_id                          integer primary key references public.plants (eia_id) on delete cascade,
  score_total                     double precision not null,   -- A + B + C + D + E + F (0-100 scale; F is a penalty)
  score_a                         double precision not null,   -- acquisition discount, max 30 (low performance scores higher)
  score_b                         double precision not null,   -- repowering upside, max 15 (vintage proxy)
  score_c                         double precision not null,   -- physical envelope, max 20 (host parcels)
  score_d                         double precision not null,   -- electrical, max 20
  score_e                         double precision not null,   -- thermal & cooling, max 15 (layers not built: neutral)
  score_f                         double precision not null,   -- fixed-cost drag, -15..0
  pts_capture                     double precision,
  pts_curtailment                 double precision,
  pts_cf_benchmark                double precision,
  pts_offtake                     double precision,
  pts_poly                        double precision,
  pts_monofacial                  double precision,
  pts_tracker                     double precision,
  pts_acres                       double precision,
  pts_headroom                    double precision,
  pts_land_control                double precision,
  pts_poi_kv                      double precision,
  pts_dist_345                    double precision,
  pts_load_pocket                 double precision,
  pts_lambda                      double precision,
  pts_drill                       double precision,
  pts_hours25                     double precision,
  pts_water                       double precision,
  pts_fixed_cost                  double precision,
  data_completeness               double precision,            -- share of the 100 positive points backed by real data
  missing_inputs                  text,                        -- ';'-joined components scored neutral
  rank_overall                    integer,                     -- pass plants only; null for review
  rank_in_tier                    integer,
  region                          text,                        -- CF benchmark region (config benchmarks.regions)
  cf_used                         double precision,            -- SCED net CF, else EIA-923
  cf_source                       text,
  cf_benchmark                    double precision,
  cf_below_benchmark_pts          double precision,
  offtake_confidence              text,
  fixed_cost_est_usd              double precision,
  fixed_cost_includes_fiber       boolean,
  fixed_cost_per_kw_it            double precision,
  thermal_response_test_required  boolean,                     -- null until the SSURGO lambda layer exists
  score_version                   text,
  run_id                          text not null,
  loaded_at                       timestamptz not null default now()
);

alter table public.plant_scores enable row level security;
create policy "allowlisted users read plant_scores" on public.plant_scores
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.plant_scores is
  'Phase 5 scores (brief §5): section sub-scores A-F, component points, completeness and ranks. Re-weighting happens in the web app.';
