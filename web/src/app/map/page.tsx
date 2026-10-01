import { createClient } from "@/lib/supabase/server";
import PlantMap, { type MapPlant } from "./plant-map";

export const dynamic = "force-dynamic";

export default async function MapPage() {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("plants_map")
    .select("eia_id,plant_name,county,tier,filter_status,ac_mw,net_ac_cf,lat,lon,geojson");
  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">Map</h1>
      <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
        USPVDB v4.0 array polygons; dots mark each plant at state scale. Click a plant for its page. The same data is in
        the table on the Plants page.
      </p>
      {error ? (
        <p className="mt-6 text-sm text-red-600">Could not load map data: {error.message}</p>
      ) : (
        <PlantMap plants={(data ?? []) as MapPlant[]} />
      )}
    </main>
  );
}
