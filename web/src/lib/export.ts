/**
 * ercot_pv_conversion_screen.xlsx (brief §7 deliverable 1): every plant passing the hard filters, every computed metric as
 * a column, composite score, sorted by rank. Pure functions over rows already fetched, so the route stays thin.
 */
import ExcelJS from "exceljs";
import { DEFAULT_WEIGHTS, SECTIONS, isDefaultWeights, rankBy, weightedScore, type TableScore, type Weights } from "@/lib/scores";

type Row = Record<string, unknown>;

export type ExportData = {
  plants: Row[];
  scores: Row[];
  layers: { name: string; label: string; rows: Row[] }[];
  monthly: Row[];
};

export type ExportMeta = { generatedAt: Date; user: string | null; weights: Weights };

/** Supabase tables read by the export, in column-group order. */
export const LAYER_TABLES: { name: string; label: string }[] = [
  { name: "layers_transmission", label: "Transmission (OSM)" },
  { name: "layers_parcels", label: "Parcels (TxGIO)" },
  { name: "layers_gas_pipelines", label: "Gas pipelines (RRC)" },
  { name: "layers_flood", label: "Flood (FEMA NFHL)" },
  { name: "layers_fiber", label: "Fiber (proxy)" },
  { name: "layers_climate", label: "Climate (NSRDB)" },
  { name: "layers_wells", label: "Wells (TWDB)" },
  { name: "layers_soils", label: "Soils (SSURGO)" },
  { name: "layers_load_pocket", label: "Load pocket (manual table)" },
];

const LEAD = ["rank_overall", "rank_in_tier", "plant_name", "eia_id", "county", "tier", "filter_status", "ac_mw",
  "score_total", "score_a", "score_b", "score_c", "score_d", "score_e", "score_f", "data_completeness", "missing_inputs",
  "thermal_response_test_required"];
const MARKET = ["ercot_resources", "ercot_settlement_points", "ercot_resource_shared", "metrics_status", "sced_coverage",
  "metrics_window_months", "metrics_window_start", "metrics_window_end", "capture_rate_potential", "capture_rate",
  "shape_capture", "basis_ratio", "curtailment_pct", "hub_avg_spp", "node_gen_wtd_spp", "sced_net_cf", "sced_potential_cf",
  "peak_hsl_mw", "peak_hsl_ratio", "peak_hsl_ratio_recent"];
// pts_lambda: soil lambda is informative only (owner decision 5 Oct 2026), so the column is always empty
const DROP = new Set(["geom", "geometry", "run_id", "loaded_at", "pts_lambda"]);
const DATE_COLS = new Set(["cod_first", "cod_last"]);

/** Excel number format for a column, by naming convention (fractions are stored 0-1). */
export function numFmt(col: string): string | undefined {
  if (/^(rank|n_|eia_id$)|_year$|^months_|_id$/.test(col)) return "0";
  if (/(_usd|mkt_value_total|land_value_per_acre|_per_kw_it)$/.test(col)) return '"$"#,##0';
  if (/(_pct|_pct_host|_pct_unified|_share|_cf|^data_completeness|^cf_used|^cf_benchmark)$/.test(col)) return "0.0%";
  if (/^(score_|pts_)/.test(col)) return "0.0";
  if (/_mw$|_mi$|_kv$/.test(col)) return "#,##0.0";
  if (/^(capture_rate|shape_capture|basis_ratio|peak_hsl_ratio|ilr)/.test(col)) return "0.000";
  if (/(_mwh|_acres|acres_|_ft|_m)$/.test(col) || /^hours_/.test(col)) return "#,##0";
  return undefined;
}

function cell(col: string, v: unknown): ExcelJS.CellValue {
  if (v === null || v === undefined || v === "") return null;
  if (DATE_COLS.has(col) && typeof v === "string") {
    const d = new Date(`${v.slice(0, 10)}T00:00:00Z`);
    return Number.isNaN(d.getTime()) ? v : d;
  }
  if (typeof v === "number" || typeof v === "boolean" || typeof v === "string") return v;
  return JSON.stringify(v);
}

/** Pass plants by rank, then review plants (scored, unranked) by score. */
export function orderRows(rows: Row[], rankKey = "rank_overall", scoreKey = "score_total"): Row[] {
  const rank = (r: Row) => (typeof r[rankKey] === "number" ? (r[rankKey] as number) : Infinity);
  const score = (r: Row) => (typeof r[scoreKey] === "number" ? (r[scoreKey] as number) : -Infinity);
  return [...rows].sort((a, b) => rank(a) - rank(b) || score(b) - score(a));
}

/** One wide row per plant (pass + review) and the column groups, in display order. */
export function rankedTable(data: ExportData, weights: Weights) {
  const plants = data.plants.filter((p) => p.filter_status === "pass" || p.filter_status === "review");
  const byId = (rows: Row[]) => new Map(rows.map((r) => [r.eia_id as number, r]));
  const scores = byId(data.scores);
  const layers = data.layers.map((l) => ({ ...l, byId: byId(l.rows) }));

  const custom = !isDefaultWeights(weights);
  const rows: Row[] = plants.map((p) => {
    const s = scores.get(p.eia_id as number) ?? {};
    const row: Row = { ...p, ...s, plants_run_id: p.run_id, scores_run_id: s.run_id };
    for (const l of layers) {
      for (const [k, v] of Object.entries(l.byId.get(p.eia_id as number) ?? {})) if (!(k in row)) row[k] = v;
    }
    if (custom && typeof s.score_total === "number") row.score_custom = weightedScore(s as unknown as TableScore, weights);
    return row;
  });
  if (custom) {
    const ranked = rows.filter((r) => r.filter_status === "pass" && typeof r.score_custom === "number");
    const ranks = rankBy(ranked, (r) => r.score_custom as number, (r) => r.eia_id as number);
    rows.forEach((r) => (r.rank_custom = ranks.get(r.eia_id as number) ?? null));
  }

  const used = new Set<string>();
  const take = (cols: string[]) => cols.filter((c) => !DROP.has(c) && !used.has(c) && (used.add(c), true));
  const keysOf = (rs: Row[]) => [...new Set(rs.flatMap((r) => Object.keys(r)))];
  const groups: { label: string; cols: string[] }[] = [];
  groups.push({ label: "Rank & score", cols: take(custom ? ["rank_custom", "score_custom", ...LEAD] : LEAD) });
  groups.push({ label: "Score components", cols: take(keysOf(data.scores).filter((c) => c !== "fiber_lateral_miles")) });
  groups.push({ label: "Plant (EIA-860/860M/923, USPVDB)", cols: take(keysOf(data.plants).filter((c) => !MARKET.includes(c))) });
  groups.push({ label: "Market (ERCOT SCED + SPP)", cols: take(MARKET) });
  for (const l of data.layers) groups.push({ label: l.label, cols: take(keysOf(l.rows).filter((c) => c !== "eia_id")) });
  groups.push({ label: "Provenance", cols: take(["plants_run_id", "scores_run_id", "score_version"]) });
  return { rows: custom ? orderRows(rows, "rank_custom", "score_custom") : orderRows(rows),
    groups: groups.filter((g) => g.cols.length), custom };
}

function sheetFromTable(ws: ExcelJS.Worksheet, groups: { label: string; cols: string[] }[], rows: Row[], freezeCols: number) {
  const cols = groups.flatMap((g) => g.cols);
  ws.columns = cols.map((c) => ({ key: c, width: Math.min(Math.max(c.length + 2, 10), 34), style: numFmt(c) ? { numFmt: numFmt(c) } : {} }));
  // row 1: group band, row 2: column names
  const band = ws.getRow(1);
  let at = 1;
  for (const g of groups) {
    band.getCell(at).value = g.label;
    if (g.cols.length > 1) ws.mergeCells(1, at, 1, at + g.cols.length - 1);
    at += g.cols.length;
  }
  band.font = { bold: true, color: { argb: "FFFFFFFF" } };
  band.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF334155" } };
  const head = ws.getRow(2);
  cols.forEach((c, i) => (head.getCell(i + 1).value = c));
  head.font = { bold: true };
  head.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFE2E8F0" } };
  for (const r of rows) ws.addRow(cols.map((c) => cell(c, r[c])));
  DATE_COLS.forEach((c) => {
    if (cols.includes(c)) ws.getColumn(c).numFmt = "yyyy-mm-dd";
  });
  ws.views = [{ state: "frozen", xSplit: freezeCols, ySplit: 2 }];
  ws.autoFilter = { from: { row: 2, column: 1 }, to: { row: 2 + rows.length, column: cols.length } };
}

const DAYS_IN_MONTH = (ym: string) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
};

export function buildWorkbook(data: ExportData, meta: ExportMeta): ExcelJS.Workbook {
  const wb = new ExcelJS.Workbook();
  wb.creator = "Komorebi Texas Screen";
  wb.created = meta.generatedAt;

  const { rows, groups, custom } = rankedTable(data, meta.weights);
  const ranked = wb.addWorksheet("Ranked");
  sheetFromTable(ranked, groups, rows, custom ? 5 : 3);

  const names = new Map(data.plants.map((p) => [p.eia_id as number, p.plant_name]));
  const monthly: Row[] = data.monthly
    .map((m): Row => ({ plant_name: names.get(m.eia_id as number) ?? null, ...m,
      full_month: typeof m.days === "number" && typeof m.month === "string" ? m.days / DAYS_IN_MONTH(m.month) >= 0.9 : null }))
    .sort((a, b) => String(a.plant_name).localeCompare(String(b.plant_name)) || String(a.month).localeCompare(String(b.month)));
  const mcols = ["plant_name", ...[...new Set(data.monthly.flatMap((m) => Object.keys(m)))].filter((c) => !DROP.has(c)), "full_month"];
  sheetFromTable(wb.addWorksheet("Monthly"), [{ label: "ERCOT monthly metrics (months under 90% of days are partial: filter full_month)", cols: mcols }], monthly, 2);

  const about = wb.addWorksheet("About");
  about.columns = [{ width: 34 }, { width: 110 }];
  const scoreRun = String(data.scores[0]?.run_id ?? "—");
  const lines: [string, string][] = [
    ["ERCOT PV conversion screen", "Komorebi Texas Screen — ranked acquisition targets for conversion into AI compute campuses"],
    ["Generated (UTC)", meta.generatedAt.toISOString().slice(0, 16).replace("T", " ")],
    ["Exported by", meta.user ?? "—"],
    ["Scores run / config", `${scoreRun} / ${String(data.scores[0]?.score_version ?? "—")}`],
    ["Plants", `${rows.length} (pass ${rows.filter((r) => r.filter_status === "pass").length}, review ${rows.filter((r) => r.filter_status === "review").length}: review plants are scored, not ranked)`],
    ["Weights", custom
      ? `custom — ${SECTIONS.map((s) => `${s.id.toUpperCase()} ${meta.weights[s.id]}`).join(", ")} (score_custom / rank_custom; score_total is at the defaults)`
      : `defaults — ${SECTIONS.map((s) => `${s.id.toUpperCase()} ${DEFAULT_WEIGHTS[s.id]}`).join(", ")} (F is a penalty)`],
    ["", ""],
    ["Thesis", "Poor generation performance is a price signal, not a defect: low capture, high curtailment and older modules score higher."],
    ["Missing inputs", "An input with no data scores half its points (neutral) and lowers data_completeness; missing_inputs lists them. A measured 'none within radius' is data."],
    ["Not scored", "Columns ending _confidence or _source, flood_flag, soil lambda (informative; sets thermal_response_test_required), gas and climate extras are displayed, never scored."],
    ["Fractions", "Columns formatted as % are stored as fractions (0.25 = 25%). capture_rate* are ratios to the HB_HUBAVG time average."],
    ["", ""],
    ["Caveat flags (every site)", "1. Load import capability is not screenable from public data: a generation interconnection agreement confers export rights only; import needs an ERCOT screening study."],
    ["", "2. SB6 large-load risk: ERCOT may curtail large loads before and during grid emergencies; sb6_review_required marks plants at or above the large-load threshold."],
    ["", "3. Mineral estate: in Texas the mineral estate is dominant; severed minerals without a surface waiver are a deal-killer in the Permian. Needs a title chain."],
    ["", "4. Behind-the-meter configuration is unsettled (metering, registration, SB6 net-metering changes): verify current ERCOT protocol status."],
    ["", "5. Chapter 313 / JETI property-tax abatement: check status and expiry per plant; the step-up at expiry is often mismodelled."],
    ["", ""],
    ["Sheets", "Ranked: one row per plant, column groups in row 1. Monthly: ERCOT SCED/SPP history per plant-month. Methodology: see the app's /methodology page."],
  ];
  lines.forEach(([k, v]) => {
    const r = about.addRow([k, v]);
    r.getCell(1).font = { bold: true };
    r.getCell(2).alignment = { wrapText: true, vertical: "top" };
  });
  about.getRow(1).font = { bold: true, size: 14 };
  return wb;
}

/** Weights from the query string (?a=30&b=15...), clamped to 0-100; anything missing keeps its default. */
export function weightsFromQuery(q: URLSearchParams): Weights {
  const w = { ...DEFAULT_WEIGHTS };
  for (const s of SECTIONS) {
    const v = Number(q.get(s.id));
    if (q.has(s.id) && Number.isFinite(v)) w[s.id] = Math.min(Math.max(v, 0), 100);
  }
  return w;
}
