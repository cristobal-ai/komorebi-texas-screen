-- Phase 3b: market metrics from the 60-day SCED disclosure and 15-minute real-time SPP.
-- Summary columns live on plants (one value per plant over the window); monthly history in plant_metrics_monthly.
-- Column lists must match pipeline/load_supabase.py METRIC_COLUMNS / MONTHLY_COLUMNS (a test checks).
-- Low capture rate and high curtailment are price signals (they score higher), never filters.

alter table public.plants
  add column ercot_resources          text,
  add column ercot_settlement_points  text,
  add column ercot_resource_shared    boolean,
  add column metrics_status           text check (metrics_status in
    ('ok', 'no_ercot_resource', 'no_sced_data', 'no_full_months', 'no_settlement_point', 'too_little_generation')),
  add column sced_coverage            text check (sced_coverage in ('full', 'none')),
  add column metrics_window_months    integer,
  add column metrics_window_start     text,   -- YYYY-MM, first month with >= min_month_coverage of days
  add column metrics_window_end       text,
  add column curtailment_pct          double precision,   -- sum(max(HSL - Base Point, 0)) / sum(HSL), fraction
  add column capture_rate             double precision,   -- generation-weighted node SPP / time-average HB_HUBAVG
  add column shape_capture            double precision,   -- generation-weighted hub SPP / time-average hub SPP
  add column basis_ratio              double precision,   -- capture_rate = shape_capture * basis_ratio
  add column hub_avg_spp              double precision,
  add column node_gen_wtd_spp         double precision,
  add column sced_net_cf              double precision,
  add column sced_potential_cf        double precision;

create table public.plant_metrics_monthly (
  eia_id              integer not null references public.plants (eia_id) on delete cascade,
  month               text not null,                        -- YYYY-MM, US/Central
  n_units             integer,
  days                integer,
  gen_mwh             double precision,
  hsl_mwh             double precision,
  curtailed_mwh       double precision,
  curtailment_pct     double precision,
  capture_rate        double precision,
  shape_capture       double precision,
  basis_ratio         double precision,
  hub_avg_spp         double precision,
  node_gen_wtd_spp    double precision,
  sced_net_cf         double precision,
  sced_potential_cf   double precision,
  run_id              text not null,
  primary key (eia_id, month)
);

alter table public.plant_metrics_monthly enable row level security;
create policy "allowlisted users read plant_metrics_monthly" on public.plant_metrics_monthly
  for select to authenticated
  using ((select public.is_allowlisted()));

comment on table public.plant_metrics_monthly is
  'Phase 3b monthly SCED curtailment and capture rate per plant. Written by the pipeline with the secret key.';
