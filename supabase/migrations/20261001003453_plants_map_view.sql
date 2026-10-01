-- W1 map: polygons as GeoJSON (simplified ~5 m) for MapLibre. security_invoker so plants' RLS applies to the caller.
create view public.plants_map
with (security_invoker = true) as
select
  eia_id,
  plant_name,
  county,
  tier,
  filter_status,
  ac_mw,
  net_ac_cf,
  lat,
  lon,
  extensions.st_asgeojson(extensions.st_simplifypreservetopology(geom, 0.00005), 6)::json as geojson
from public.plants;

revoke all on public.plants_map from anon;
grant select on public.plants_map to authenticated;

comment on view public.plants_map is 'W1 map layer: plants with geometry as simplified GeoJSON; RLS of public.plants applies (security_invoker).';
