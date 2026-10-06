import { createClient } from "@/lib/supabase/server";
import { TABLE_COLUMNS, type TableRow } from "@/lib/plants";
import { TABLE_SCORE_COLUMNS, type TableScore } from "@/lib/scores";
import PlantTable, { type ScoredRow } from "./plant-table";

export const dynamic = "force-dynamic";

export default async function Home() {
  const supabase = await createClient();
  const [plants, scores, flood, meta] = await Promise.all([
    supabase.from("plants").select(TABLE_COLUMNS).order("ac_mw", { ascending: false }),
    supabase.from("plant_scores").select(TABLE_SCORE_COLUMNS),
    supabase.from("layers_flood").select("eia_id,flood_status,flood_flag"),
    supabase.from("plant_scores").select("run_id,loaded_at,score_version").limit(1).maybeSingle(),
  ]);
  const error = plants.error ?? scores.error ?? flood.error;
  const scoreById = new Map(((scores.data ?? []) as TableScore[]).map((s) => [s.eia_id, s]));
  const floodById = new Map(
    ((flood.data ?? []) as { eia_id: number; flood_status: string | null; flood_flag: boolean | null }[]).map((f) => [f.eia_id, f]),
  );
  const rows: ScoredRow[] = ((plants.data ?? []) as unknown as TableRow[]).map((p) => ({
    ...p,
    score: scoreById.get(p.eia_id) ?? null,
    flood_status: floodById.get(p.eia_id)?.flood_status ?? null,
    flood_flag: floodById.get(p.eia_id)?.flood_flag ?? null,
  }));

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">ERCOT PV plants — screen v0.3</h1>
      <p className="mt-1 max-w-3xl text-sm text-neutral-600 dark:text-neutral-400">
        Plants passing the hard filters (≥10 MW AC, COD 2015–2022, Texas), ranked on the brief’s scoring model. Poor
        generation performance is a price signal, not a defect: low capture, high curtailment and older modules score
        higher. Inputs not measured yet (load pockets, offtake) score half their points; “Data” shows how much of each
        score rests on real data. Move the weights to re-rank; Export XLSX downloads every metric at the current weights.
      </p>
      {error ? (
        <p className="mt-6 text-sm text-red-600">Could not load plants: {error.message}</p>
      ) : (
        <PlantTable rows={rows} />
      )}
      {meta.data && (
        <p className="mt-4 text-xs text-neutral-500">
          Scores run {meta.data.run_id} (config {meta.data.score_version}) · loaded{" "}
          {new Date(meta.data.loaded_at).toISOString().slice(0, 16).replace("T", " ")} UTC
        </p>
      )}
    </main>
  );
}
