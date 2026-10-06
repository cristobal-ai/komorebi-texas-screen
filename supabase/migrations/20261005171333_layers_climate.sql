-- Phase 4 layer 6: climate from NSRDB PSM v4 (NLR) hourly air temperature and humidity at one ~4 km cell per plant.
-- Hour counts are means over climate_years, normalised to 8,760 h. hours_below_25c_drybulb is scored (Section E).
-- One row per plant; written by the pipeline (pipeline/phase_4_geo/load.py CLIMATE_COLUMNS, a test checks), read by
-- allowlisted users.
create table public.layers_climate (
  eia_id                       integer primary key references public.plants (eia_id) on delete cascade,
  hours_below_25c_drybulb      double precision,   -- mean over climate_years (scored)
  hours_below_25c_drybulb_min  double precision,   -- worst year
  hours_below_25c_drybulb_tmy  double precision,   -- typical meteorological year
  hours_below_25c_by_year      text,               -- 'yyyy: n; ...'
  hours_below_15c_drybulb      double precision,
  hours_below_20c_drybulb      double precision,
  hours_below_20c_wetbulb      double precision,   -- Stull (2011) wet-bulb from T and RH
  hours_above_35c_drybulb      double precision,
  mean_annual_temp_c           double precision,   -- ~ undisturbed ground temperature below ~10 m
  design_drybulb_0p4_c         double precision,   -- 99.6th percentile of hourly dry-bulb (0.4% exceedance)
  design_wetbulb_0p4_c         double precision,
  max_drybulb_c                double precision,
  nsrdb_location_id            integer,
  nsrdb_lat                    double precision,
  nsrdb_lon                    double precision,
  nsrdb_elevation_m            double precision,
  climate_years                text,
  climate_source               text,
  climate_fetched              date,
  climate_confidence           text check (climate_confidence in ('low', 'medium', 'high')),
  run_id                       text not null,
  loaded_at                    timestamptz not null default now()
);

alter table public.layers_climate enable row level security;
create policy "allowlisted users read layers_climate" on public.layers_climate
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_climate is
  'Phase 4 climate: NSRDB PSM v4 hourly dry-bulb / wet-bulb statistics per plant (modelled MERRA-2 temperature, ~4 km cell).';
