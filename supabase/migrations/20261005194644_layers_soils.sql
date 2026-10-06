-- Phase 4 layer 8: soils. Near-surface (0-2 m) thermal conductivity estimated from SSURGO texture, bulk density and
-- field-capacity water content (Cote & Konrad 2005), plus restrictive layers. soil_lambda_w_mk is scored (Section E).
-- One row per plant; written by the pipeline (pipeline/phase_4_geo/load.py SOILS_COLUMNS, a test checks), read by
-- allowlisted users.
create table public.layers_soils (
  eia_id                    integer primary key references public.plants (eia_id) on delete cascade,
  soil_status               text check (soil_status in ('ok', 'no_soil_data')),
  soil_lambda_w_mk          double precision,   -- at field capacity, depth / component / area weighted (scored)
  soil_lambda_dry_w_mk      double precision,
  soil_lambda_sat_w_mk      double precision,
  soil_sand_pct             double precision,
  soil_clay_pct             double precision,
  soil_bulk_density         double precision,   -- g/cm3 at 1/3 bar
  soil_theta_fc             double precision,   -- volumetric water % at 1/3 bar (field capacity)
  dominant_soil             text,               -- component name (share of the array)
  dominant_mapunit          text,
  n_mapunits                integer,
  soil_data_share           double precision,   -- share of the array with horizon data (rock outcrop etc. have none)
  restriction_kinds         text,               -- SSURGO corestrictions within 2 m, e.g. Petrocalcic (caliche hardpan), Lithic bedrock
  restriction_min_depth_cm  double precision,
  restriction_share         double precision,   -- share of the array with a restriction within 2 m
  bedrock_depth_cm_min      double precision,   -- muaggatt brockdepmin
  soil_source               text,
  soil_fetched              date,
  soil_confidence           text check (soil_confidence in ('low', 'medium', 'high')),
  run_id                    text not null,
  loaded_at                 timestamptz not null default now()
);

alter table public.layers_soils enable row level security;
create policy "allowlisted users read layers_soils" on public.layers_soils
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_soils is
  'Phase 4 soils: SSURGO-based near-surface thermal conductivity estimate and restrictive layers. A screen; a thermal response test decides.';
