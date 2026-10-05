-- Phase 4 layer 7: wells. Drillability (caliche / gypsum / anhydrite in TWDB driller logs) and depth to water (TWDB SDR
-- static levels + GWDB measurements). thick_hard_layer_share and depth_to_water_ft are scored (Section E).
-- One row per plant; written by the pipeline (pipeline/phase_4_geo/load.py WELLS_COLUMNS, a test checks), read by
-- allowlisted users.
create table public.layers_wells (
  eia_id                      integer primary key references public.plants (eia_id) on delete cascade,
  n_logs                      integer,            -- driller logs used (within logs_radius_mi); below min_logs when no radius qualified
  logs_radius_mi              double precision,   -- null = fewer than min_logs logs within the largest radius
  thick_hard_layer_share      double precision,   -- share of logs with >= thick_layer_ft caliche+gypsum+anhydrite in the bore depth (scored)
  hard_layer_ft_median        double precision,
  hard_layer_ft_p90           double precision,
  caliche_log_share           double precision,   -- logs mentioning caliche at all
  gypsum_log_share            double precision,   -- logs mentioning gypsum / anhydrite at all
  hard_rock_log_share         double precision,   -- logs with >= thick_layer_ft limestone / dolomite / igneous (display)
  lost_circulation_log_share  double precision,   -- logs reporting lost circulation / no returns / cavities (display)
  median_log_depth_ft         double precision,
  n_geothermal_bores          integer,            -- SDR 'Closed-Loop Geothermal' wells within the largest radius
  n_water_levels              integer,
  water_radius_mi             double precision,
  depth_to_water_ft           double precision,   -- median static level, ft below land surface (scored)
  depth_to_water_ft_p25       double precision,
  depth_to_water_ft_p75       double precision,
  water_level_sources         text,               -- 'gwdb n; sdr m'
  latest_water_level_year     integer,
  aquifer_majority            text,               -- most common among GWDB wells nearby (proxy for the aquifer map)
  gcd_majority                text,               -- most common groundwater conservation district among GWDB wells nearby
  wells_source                text,
  wells_fetched               date,
  wells_confidence            text check (wells_confidence in ('low', 'medium', 'high')),
  run_id                      text not null,
  loaded_at                   timestamptz not null default now()
);

alter table public.layers_wells enable row level security;
create policy "allowlisted users read layers_wells" on public.layers_wells
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_wells is
  'Phase 4 wells: drillability and depth to water from TWDB driller reports and the Groundwater Database. A screen from free-text driller logs, not a geotechnical finding.';
