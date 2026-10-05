-- Phase 4 layer 2: natural gas pipelines (Texas RRC QPipelines, layer 12). One row per plant; written by the pipeline
-- with the secret key (pipeline/phase_4_geo/load.py), read by allowlisted users. Column list must match
-- pipeline/phase_4_geo/load.py GAS_PIPELINES_COLUMNS (a test checks). Display only: not scored.
create table public.layers_gas_pipelines (
  eia_id                                 integer primary key references public.plants (eia_id) on delete cascade,
  dist_gas_transmission_mi               double precision,   -- edge-to-edge miles to the nearest in-service gas transmission line
  gas_transmission_operator              text,
  gas_transmission_system                text,
  gas_transmission_diameter_in           double precision,   -- null when RRC records 0 / blank
  gas_transmission_interstate            boolean,
  dist_gas_any_mi                        double precision,   -- nearest in-service natural gas line of any type (gathering included)
  n_gas_transmission_near                integer,            -- gas transmission segments within near_miles (config)
  max_gas_transmission_diameter_near_in  double precision,
  gas_search_mi                          double precision,   -- coverage radius: a null distance means none within this many miles
  gas_source                             text,
  gas_fetched                            date,
  gas_confidence                         text check (gas_confidence in ('low', 'medium')),
  run_id                                 text not null,
  loaded_at                              timestamptz not null default now()
);

alter table public.layers_gas_pipelines enable row level security;
create policy "allowlisted users read layers_gas_pipelines" on public.layers_gas_pipelines
  for select to authenticated
  using ((select private.is_allowlisted()));

comment on table public.layers_gas_pipelines is
  'Phase 4 gas layer: distance to in-service natural gas transmission and gathering lines from Texas RRC QPipelines (generalized). A screen for bridge/backup generation, not scored.';
