/**
 * Writes the XLSX export (default weights) to Supabase Storage, bucket `exports` (plan §2 deliverable 3):
 *   snapshots/ercot_pv_conversion_screen_<YYYY-MM-DD>_<label>.xlsx   one per run, kept
 *   ercot_pv_conversion_screen_latest.xlsx                            overwritten each run (/export/latest serves it)
 *   cd web && npx tsx scripts/export-upload.ts [--label L] [--dry-run out.xlsx]
 * The label defaults to the GitHub Actions run id, else "local". Run by the `refresh` workflow after the pipeline.
 */
import { writeFileSync } from "node:fs";
import { buildWorkbook } from "../src/lib/export";
import { EXPORT_BUCKET, LATEST_EXPORT, loadExportData } from "../src/lib/export-data";
import { DEFAULT_WEIGHTS } from "../src/lib/scores";
import { scriptClient } from "./script-env";

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

async function main() {
  const label = (arg("--label") ?? process.env.GITHUB_RUN_ID ?? "local").replace(/[^\w.-]/g, "_");
  const dryRun = arg("--dry-run");
  const sb = scriptClient();
  const data = await loadExportData(sb);
  if (!data.plants.length) throw new Error("plants table is empty: refusing to overwrite the snapshot");
  const now = new Date();
  const user = process.env.GITHUB_RUN_ID ? `refresh workflow run ${process.env.GITHUB_RUN_ID}` : "export-upload (local)";
  const buf = Buffer.from(await buildWorkbook(data, { generatedAt: now, user, weights: { ...DEFAULT_WEIGHTS } }).xlsx.writeBuffer());
  const counts = `plants ${data.plants.length}, scores ${data.scores.length}, monthly ${data.monthly.length}, ${buf.length} bytes`;
  if (dryRun) {
    writeFileSync(dryRun, buf);
    console.log(`dry run: wrote ${dryRun} (${counts})`);
    return;
  }
  const snapshot = `snapshots/ercot_pv_conversion_screen_${now.toISOString().slice(0, 10)}_${label}.xlsx`;
  for (const path of [snapshot, LATEST_EXPORT]) {
    const { error } = await sb.storage.from(EXPORT_BUCKET).upload(path, buf, { contentType: XLSX, upsert: true, cacheControl: "0" });
    if (error) throw new Error(`upload ${EXPORT_BUCKET}/${path}: ${error.message}`);
    console.log(`uploaded ${EXPORT_BUCKET}/${path}`);
  }
  console.log(counts);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
