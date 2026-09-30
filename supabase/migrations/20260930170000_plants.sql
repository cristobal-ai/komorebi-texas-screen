-- Phase 1–2: plant master + invite allowlist.
-- plants holds every plant with filter_status 'pass' or 'review' (review = footprint unresolved until Phase 4
-- parcels; only 'pass' is ranked). Column list must match pipeline/load_supabase.py PLANT_COLUMNS (a test checks).
-- Writes come only from the pipeline with the secret key (bypasses RLS); signed-in, allowlisted users read.

create extension if not exists postgis with schema extensions;

-- ---- allowlist -------------------------------------------------------------------------------------------------
create table public.users_allowlist (
  email       text primary key check (email = lower(email)),
  role        text not null default 'viewer' check (role in ('viewer', 'admin')),
  added_at    timestamptz not null default now(),
  added_by    text
);
alter table public.users_allowlist enable row level security;

-- True when the caller's JWT email is on the allowlist (optionally with the given role).
-- security definer so policies can read the allowlist without granting users select on it.
create or replace function public.is_allowlisted(required_role text default null)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.users_allowlist a
    where a.email = lower(coalesce((select auth.jwt() ->> 'email'), ''))
      and (required_role is null or a.role = required_role)
  );
$$;
revoke all on function public.is_allowlisted(text) from public, anon;
grant execute on function public.is_allowlisted(text) to authenticated;

create policy "allowlisted users see their own row" on public.users_allowlist
  for select to authenticated
  using (email = lower(coalesce((select auth.jwt() ->> 'email'), '')) or (select public.is_allowlisted('admin')));
create policy "admins manage the allowlist" on public.users_allowlist
  for all to authenticated
  using ((select public.is_allowlisted('admin')))
  with check ((select public.is_allowlisted('admin')));

-- ---- plants ----------------------------------------------------------------------------------------------------
create table public.plants (
  eia_id                     integer primary key,
  plant_name                 text not null,
  operator                   text,
  county                     text,
  ba_code                    text,
  lat                        double precision,
  lon                        double precision,
  geom                       extensions.geometry(Geometry, 4326),   -- USPVDB array polygon(s), dissolved per eia_id

  -- capacity and COD (EIA-860M)
  ac_mw                      double precision not null,
  n_generators               integer,
  cod_first                  date,
  cod_last                   date,
  cod_multi_phase            boolean,
  has_colocated_storage      boolean,
  source_month               text,                                  -- EIA-860M data month, YYYY-MM

  -- DC / ILR
  dc_mw                      double precision,
  dc_mw_source               text check (dc_mw_source in ('eia860m', 'uspvdb', 'ilr_default')),
  ilr                        double precision,
  ilr_confidence             text check (ilr_confidence in ('reported', 'assumed')),

  -- USPVDB footprint
  uspvdb_match               boolean,
  ac_mw_uspvdb               double precision,
  ac_mw_delta_pct            double precision,
  year_uspvdb                integer,
  tracking_uspvdb            text,
  n_polygons                 integer,
  array_acres                double precision,
  array_acres_calc           double precision,
  acres_per_mw_ac            double precision,
  footprint_basis            text check (footprint_basis in ('array_flag', 'parcel')),

  -- filters, tier, SB6
  filter_status              text not null check (filter_status in ('pass', 'review')),
  filter_reasons             text,
  non_ercot_texas            boolean,
  tier                       text check (tier in ('T1b', 'T1a', 'T2', 'T3')),
  firm_it_mw                 double precision,
  planned_load_mw            double precision,
  sb6_review_required        boolean,
  tax_equity_consent_likely  boolean,
  itc_recapture_open         boolean,

  -- EIA-860 annual
  eia860_year                integer,
  grid_voltage_kv            double precision,
  grid_voltage_max_kv        double precision,
  grid_voltage_source        text,
  distribution_class_poi     boolean,
  tracking_type              text,
  tracking_type_share        double precision,
  module_tech                text,
  module_tech_share          double precision,
  module_type_confidence     text,
  bifacial_share             double precision,

  -- EIA-923
  cf_year                    integer,
  net_mwh                    double precision,
  net_ac_cf                  double precision,
  cf_series_resolution       text check (cf_series_resolution in ('monthly', 'annual')),
  months_reported            integer,
  cf_note                    text,

  -- load bookkeeping
  run_id                     text not null,
  loaded_at                  timestamptz not null default now()
);

create index plants_tier_idx on public.plants (tier) where filter_status = 'pass';
create index plants_county_idx on public.plants (county);
create index plants_geom_idx on public.plants using gist (geom);

alter table public.plants enable row level security;
create policy "allowlisted users read plants" on public.plants
  for select to authenticated
  using ((select public.is_allowlisted()));

comment on table public.plants is
  'Plant master (phase 1–2). pass + review plants; review = footprint unresolved until TxGIO parcels (phase 4). '
  'Written by the pipeline (pipeline/load_supabase.py) with the secret key; read by allowlisted users.';
