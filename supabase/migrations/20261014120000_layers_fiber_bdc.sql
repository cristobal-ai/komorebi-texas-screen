-- Phase 4 layer 10: FCC BDC fiber-to-the-premises availability around each plant. One row per plant; written by the pipeline
-- (pipeline/phase_4_geo/load.py FIBER_BDC_COLUMNS, a test checks), read by allowlisted users. Display only, never scored.
create table public.layers_fiber_bdc (
  eia_id                    integer primary key references public.plants (eia_id) on delete cascade,
  bdc_status                text not null check (bdc_status in ('ok', 'no_bdc_data')),
  bdc_fiber_at_site         boolean,            -- an FTTP hexagon centre within site_cell_m (500 m) of the array
  bdc_fiber_nearest_mi      double precision,   -- array edge to the nearest FTTP hexagon centre (+/- one hexagon edge); null = none within bdc_search_mi
  bdc_nearest_brands        text,
  bdc_providers_near        integer,            -- distinct providers reporting FTTP within near_miles (5)
  bdc_brands_near           text,               -- top providers by hexagons
  bdc_cells_near            integer,            -- FTTP hexagons within near_miles (density)
  bdc_max_down_mbps_near    double precision,
  bdc_max_up_mbps_near      double precision,
  bdc_business_fiber_near   boolean,            -- business (B/X) FTTP service reported within near_miles
  bdc_search_mi             double precision,
  bdc_release               text,               -- e.g. 'J25 30jun2026' when the file name carries it
  bdc_source                text,
  bdc_fetched               date,
  bdc_confidence            text check (bdc_confidence in ('low', 'medium')),
  run_id                    text not null,
  loaded_at                 timestamptz not null default now()
);

alter table public.layers_fiber_bdc enable row level security;
create policy "allowlisted users read layers_fiber_bdc" on public.layers_fiber_bdc
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_fiber_bdc is
  'Phase 4 FCC BDC layer: provider-reported fiber-to-the-premises availability near the site (H3 res 8). Last-mile evidence of a nearby carrier, not a long-haul route.';
