-- Phase 4 layer 5: fiber PROXY (distance to Class I rail mainline / interstate right-of-way, carrier-hotel latency).
-- Long-haul routes are proprietary: fiber_confidence is always 'low'. One row per plant; written by the pipeline
-- (pipeline/phase_4_geo/load.py FIBER_COLUMNS, a test checks), read by allowlisted users.
create table public.layers_fiber (
  eia_id                      integer primary key references public.plants (eia_id) on delete cascade,
  dist_class1_rail_mi         double precision,   -- nearest UP / BNSF / CPKC mainline (BTS NTAD); null = none within search radius
  nearest_rail_owner          text,
  nearest_rail_subdiv         text,
  dist_interstate_mi          double precision,   -- nearest interstate motorway/trunk (OpenStreetMap)
  nearest_interstate          text,
  fiber_lateral_miles         double precision,   -- min of the two: the lateral priced in Section F
  fiber_corridor              text check (fiber_corridor in ('rail', 'interstate')),
  nearest_carrier_hotel       text,
  carrier_hotel_gc_mi         double precision,   -- great circle from the plant's EIA point
  carrier_hotel_route_mi_est  double precision,   -- great circle × route_factor (config)
  latency_rtt_ms_est          double precision,   -- route miles × rtt_ms_per_route_mile (light in glass only)
  carrier_hotel_rtt_ms        text,               -- every configured hotel, 'name ~x.x ms; ...'
  fiber_search_mi             double precision,
  fiber_source                text,
  fiber_fetched               date,
  fiber_confidence            text check (fiber_confidence in ('low', 'proxy', 'measured')),
  run_id                      text not null,
  loaded_at                   timestamptz not null default now()
);

alter table public.layers_fiber enable row level security;
create policy "allowlisted users read layers_fiber" on public.layers_fiber
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_fiber is
  'Phase 4 fiber proxy: corridors long-haul fiber is usually laid in (Class I mainline, interstates) and carrier-hotel latency. Not a route map.';

-- Section F now prices the fiber lateral where the proxy found one.
alter table public.plant_scores add column fiber_lateral_miles double precision;   -- fiber proxy lateral used in fixed_cost_est_usd
