/** Reads every table the XLSX export needs. Shared by the /export route and the scripts in web/scripts. */
import type { SupabaseClient } from "@supabase/supabase-js";
import { LAYER_TABLES, type ExportData } from "@/lib/export";

type Row = Record<string, unknown>;

/** Storage bucket for the XLSX written by each pipeline run (scripts/export-upload.ts). */
export const EXPORT_BUCKET = "exports";
export const LATEST_EXPORT = "ercot_pv_conversion_screen_latest.xlsx";

/** Every row of a table (PostgREST returns at most 1,000 per request). */
async function all(sb: SupabaseClient, table: string): Promise<Row[]> {
  const out: Row[] = [];
  for (let from = 0; ; from += 1000) {
    const { data, error } = await sb.from(table).select("*").order("eia_id").range(from, from + 999);
    if (error) throw new Error(`${table}: ${error.message}`);
    out.push(...(data ?? []));
    if (!data || data.length < 1000) return out;
  }
}

export async function loadExportData(sb: SupabaseClient): Promise<ExportData> {
  const [plants, scores, monthly, ...layerRows] = await Promise.all([
    all(sb, "plants"),
    all(sb, "plant_scores"),
    all(sb, "plant_metrics_monthly"),
    ...LAYER_TABLES.map((l) => all(sb, l.name)),
  ]);
  return { plants, scores, monthly, layers: LAYER_TABLES.map((l, i) => ({ ...l, rows: layerRows[i] })) };
}
