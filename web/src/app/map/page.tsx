import { createClient } from "@/lib/supabase/server";
import PlantMap, { type LoadPocketProject, type MapPlant, type Substation } from "./plant-map";

export const dynamic = "force-dynamic";

export default async function MapPage() {
  const supabase = await createClient();
  const [plantsRes, scoresRes, pocketsRes, subsRes] = await Promise.all([
    supabase.from("plants_map").select("eia_id,plant_name,county,tier,filter_status,ac_mw,net_ac_cf,lat,lon,geojson"),
    supabase.from("plant_scores").select("eia_id,score_total,rank_overall"),
    supabase
      .from("ref_load_pocket_projects")
      .select("project_id,name,developer,kind,status,load_mw,lat,lon,location_confidence,qualifies,source_url"),
    supabase.from("ref_substations_345kv").select("osm_id,name,operator,voltage_kv,lat,lon").range(0, 4999),
  ]);
  const error = plantsRes.error ?? scoresRes.error ?? pocketsRes.error ?? subsRes.error;
  const scores = new Map((scoresRes.data ?? []).map((s) => [s.eia_id as number, s]));
  const plants = (plantsRes.data ?? []).map((p) => ({
    ...p,
    score_total: scores.get(p.eia_id)?.score_total ?? null,
    rank_overall: scores.get(p.eia_id)?.rank_overall ?? null,
  })) as MapPlant[];
  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">Map</h1>
      <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
        Plants coloured by score (or tier); dots at state scale, USPVDB array polygons when zoomed in. Load pockets are the
        hand-maintained table behind the Section D score (locations approximate); 345 kV substations are from
        OpenStreetMap. Click anything for details; a plant opens its page.
      </p>
      {error ? (
        <p className="mt-6 text-sm text-red-600">Could not load map data: {error.message}</p>
      ) : (
        <PlantMap
          plants={plants}
          pockets={(pocketsRes.data ?? []) as LoadPocketProject[]}
          substations={(subsRes.data ?? []) as Substation[]}
        />
      )}
    </main>
  );
}
