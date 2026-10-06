/**
 * Local check of the XLSX export against the live database, without a browser session:
 *   cd web && npx tsx scripts/export-check.ts <out.xlsx> [a=30 b=15 ...]
 * Reads the Supabase URL and SUPABASE_SECRET_KEY as scripts/script-env.ts describes (read-only queries).
 */
import { buildWorkbook, weightsFromQuery } from "../src/lib/export";
import { loadExportData } from "../src/lib/export-data";
import { scriptClient } from "./script-env";

async function main() {
  const [out, ...w] = process.argv.slice(2);
  const data = await loadExportData(scriptClient());
  const wb = buildWorkbook(data, { generatedAt: new Date(), user: "export-check", weights: weightsFromQuery(new URLSearchParams(w.join("&"))) });
  await wb.xlsx.writeFile(out);
  console.log(`wrote ${out}: plants ${data.plants.length}, scores ${data.scores.length}, monthly ${data.monthly.length}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
