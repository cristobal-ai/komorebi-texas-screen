-- Phase 4 layer 3: TxGIO parcels under each plant (site footprint, headroom, land control). One row per plant; written by
-- the pipeline with the secret key (pipeline/phase_4_geo/load.py), read by allowlisted users. Column list must match
-- pipeline/phase_4_geo/load.py PARCELS_COLUMNS (a test checks).
create table public.layers_parcels (
  eia_id                     integer primary key references public.plants (eia_id) on delete cascade,
  parcel_status              text not null check (parcel_status in ('ok', 'no_parcel_data', 'no_parcel_overlap')),
  n_host_parcels             integer,
  parcel_acres_host          double precision,   -- area of the parcels the array sits on (from geometry, EPSG:5070)
  parcel_acres_unified       double precision,   -- host parcels + same-owner parcels touching them (within the read neighbourhood)
  adjacent_same_owner_acres  double precision,
  host_cover_share           double precision,   -- array area / host-parcel area (low = small leased corner of a large parcel)
  headroom_pct_host          double precision,   -- (parcel acres - array acres) / array acres
  headroom_pct_unified       double precision,
  acres_per_mw_parcel        double precision,   -- parcel_acres_host / AC MW (replaces array acres/MW in the footprint filter)
  host_owners                text,               -- top owners by area, e.g. 'ACME SOLAR 84%; J DOE 16%'
  largest_owner_share        double precision,
  unified_land_control       boolean,            -- one owner holds >= unified_min_owner_share of the host acres
  land_use_codes             text,
  mkt_value_total            double precision,
  land_value_per_acre        double precision,
  parcel_vintage             text,               -- tax/data year of the parcel file when it says so
  parcel_source              text,
  parcels_confidence         text check (parcels_confidence in ('low', 'medium', 'high')),
  run_id                     text not null,
  loaded_at                  timestamptz not null default now()
);

alter table public.layers_parcels enable row level security;
create policy "allowlisted users read layers_parcels" on public.layers_parcels
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_parcels is
  'Phase 4 parcel layer from TxGIO StratMap Land Parcels (annual county appraisal snapshots). Owner names are matched after stripping LLC/Inc words; sponsor LLC families are not merged.';
