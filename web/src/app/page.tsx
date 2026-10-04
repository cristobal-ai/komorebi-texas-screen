import { createClient } from "@/lib/supabase/server";
import { TABLE_COLUMNS, type TableRow } from "@/lib/plants";
import PlantTable from "./plant-table";

export const dynamic = "force-dynamic";

export default async function Home() {
  const supabase = await createClient();
  const { data, error } = await supabase.from("plants").select(TABLE_COLUMNS).order("ac_mw", { ascending: false });
  const { data: meta } = await supabase.from("plants").select("run_id,loaded_at").limit(1).maybeSingle();

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">ERCOT PV plants — screen v0.1</h1>
      <p className="mt-1 max-w-3xl text-sm text-neutral-600 dark:text-neutral-400">
        Plants passing the hard filters (≥10 MW AC, COD 2015–2022, Texas) from USPVDB v4.0 + EIA-860M/860/923. Not
        scored yet: sort by any column, including the new market columns from the SCED backfill. Low capacity factor is a price signal, not a defect. “Review” = footprint
        unresolved until parcel data (array area alone is below the site-footprint thresholds).
      </p>
      {error ? (
        <p className="mt-6 text-sm text-red-600">Could not load plants: {error.message}</p>
      ) : (
        <PlantTable rows={(data ?? []) as unknown as TableRow[]} />
      )}
      {meta && (
        <p className="mt-4 text-xs text-neutral-500">
          Data run {meta.run_id} · loaded {new Date(meta.loaded_at).toISOString().slice(0, 16).replace("T", " ")} UTC
        </p>
      )}
    </main>
  );
}
