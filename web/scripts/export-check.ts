/**
 * Local check of the XLSX export against the live database, without a browser session:
 *   cd web && npx tsx scripts/export-check.ts <out.xlsx> [a=30 b=15 ...]
 * Reads the Supabase URL and SUPABASE_SECRET_KEY from .env.local, else ../pipeline/.env (read-only queries).
 */
import { existsSync, readFileSync } from "node:fs";
import { createClient } from "@supabase/supabase-js";
import { LAYER_TABLES, buildWorkbook, weightsFromQuery } from "../src/lib/export";

const envFile = existsSync(".env.local") ? ".env.local" : "../pipeline/.env";
const env = Object.fromEntries(
  readFileSync(envFile, "utf8").split(/\r?\n/).filter((l) => l.includes("=") && !l.startsWith("#"))
    .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]),
);
const sb = createClient(env.NEXT_PUBLIC_SUPABASE_URL ?? env.SUPABASE_URL, env.SUPABASE_SECRET_KEY, { auth: { persistSession: false } });

async function all(table: string) {
  const out: Record<string, unknown>[] = [];
  for (let from = 0; ; from += 1000) {
    const { data, error } = await sb.from(table).select("*").order("eia_id").range(from, from + 999);
    if (error) throw new Error(`${table}: ${error.message}`);
    out.push(...(data ?? []));
    if (!data || data.length < 1000) return out;
  }
}

async function main() {
  const [out, ...w] = process.argv.slice(2);
  const [plants, scores, monthly, ...layers] = await Promise.all([all("plants"), all("plant_scores"), all("plant_metrics_monthly"),
    ...LAYER_TABLES.map((l) => all(l.name))]);
  const wb = buildWorkbook({ plants, scores, monthly, layers: LAYER_TABLES.map((l, i) => ({ ...l, rows: layers[i] })) },
    { generatedAt: new Date(), user: "export-check", weights: weightsFromQuery(new URLSearchParams(w.join("&"))) });
  await wb.xlsx.writeFile(out);
  console.log(`wrote ${out}: plants ${plants.length}, scores ${scores.length}, monthly ${monthly.length}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
