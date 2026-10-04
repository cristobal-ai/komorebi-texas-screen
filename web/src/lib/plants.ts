/** Shape of public.plants rows used by the app (see supabase/migrations/*_plants.sql). */
export type Plant = {
  eia_id: number;
  plant_name: string;
  operator: string | null;
  county: string | null;
  ba_code: string | null;
  lat: number | null;
  lon: number | null;
  ac_mw: number;
  n_generators: number | null;
  cod_first: string | null;
  cod_last: string | null;
  cod_multi_phase: boolean | null;
  has_colocated_storage: boolean | null;
  source_month: string | null;
  dc_mw: number | null;
  dc_mw_source: "eia860m" | "uspvdb" | "ilr_default" | null;
  ilr: number | null;
  ilr_confidence: "reported" | "assumed" | null;
  uspvdb_match: boolean | null;
  ac_mw_uspvdb: number | null;
  ac_mw_delta_pct: number | null;
  year_uspvdb: number | null;
  tracking_uspvdb: string | null;
  n_polygons: number | null;
  array_acres: number | null;
  array_acres_calc: number | null;
  acres_per_mw_ac: number | null;
  footprint_basis: "array_flag" | "parcel" | null;
  filter_status: "pass" | "review";
  filter_reasons: string | null;
  non_ercot_texas: boolean | null;
  tier: Tier | null;
  firm_it_mw: number | null;
  planned_load_mw: number | null;
  sb6_review_required: boolean | null;
  tax_equity_consent_likely: boolean | null;
  itc_recapture_open: boolean | null;
  eia860_year: number | null;
  grid_voltage_kv: number | null;
  grid_voltage_max_kv: number | null;
  grid_voltage_source: string | null;
  distribution_class_poi: boolean | null;
  tracking_type: string | null;
  tracking_type_share: number | null;
  module_tech: string | null;
  module_tech_share: number | null;
  module_type_confidence: string | null;
  bifacial_share: number | null;
  cf_year: number | null;
  net_mwh: number | null;
  net_ac_cf: number | null;
  cf_series_resolution: "monthly" | "annual" | null;
  months_reported: number | null;
  cf_note: string | null;
  // Phase 3b (SCED + real-time SPP, see supabase/migrations/*_plant_metrics.sql); null until the plant is in the crosswalk
  ercot_resources: string | null;
  ercot_settlement_points: string | null;
  ercot_resource_shared: boolean | null;
  metrics_status: MetricsStatus | null;
  sced_coverage: "full" | "none" | null;
  metrics_window_months: number | null;
  metrics_window_start: string | null;
  metrics_window_end: string | null;
  curtailment_pct: number | null;
  capture_rate: number | null;
  capture_rate_potential: number | null;
  shape_capture: number | null;
  basis_ratio: number | null;
  hub_avg_spp: number | null;
  node_gen_wtd_spp: number | null;
  sced_net_cf: number | null;
  sced_potential_cf: number | null;
  peak_hsl_mw: number | null;
  peak_hsl_ratio: number | null;
  peak_hsl_ratio_recent: number | null;
  run_id: string;
  loaded_at: string;
};

export type Tier = "T1b" | "T1a" | "T2" | "T3";

export type MetricsStatus =
  | "ok"
  | "no_ercot_resource"
  | "no_sced_data"
  | "no_full_months"
  | "no_settlement_point"
  | "too_little_generation";

/** Why a plant has no market metrics. A blank is never a zero: say which reason it is. */
export function metricsStatusLabel(s: MetricsStatus | null | undefined): string {
  switch (s) {
    case "ok":
      return "Metrics available";
    case "no_ercot_resource":
      return "No ERCOT resource (behind the meter)";
    case "no_sced_data":
      return "Not a telemetered PV resource (no SCED data)";
    case "no_full_months":
      return "Fewer than one full month of SCED data";
    case "no_settlement_point":
      return "No settlement point: curtailment only";
    case "too_little_generation":
      return "Too little generation to price";
    default:
      return "Crosswalk not verified yet";
  }
}

/** One row of public.plant_metrics_monthly. */
export type MonthlyMetric = {
  eia_id: number;
  month: string; // YYYY-MM, US/Central
  n_units: number | null;
  days: number | null;
  gen_mwh: number | null;
  hsl_mwh: number | null;
  curtailed_mwh: number | null;
  curtailment_pct: number | null;
  capture_rate: number | null;
  capture_rate_potential: number | null;
  shape_capture: number | null;
  basis_ratio: number | null;
  hub_avg_spp: number | null;
  node_gen_wtd_spp: number | null;
  sced_net_cf: number | null;
  sced_potential_cf: number | null;
  peak_hsl_mw: number | null;
};

export const MONTHLY_COLUMNS =
  "month,days,gen_mwh,hsl_mwh,curtailed_mwh,curtailment_pct,capture_rate,capture_rate_potential,shape_capture," +
  "basis_ratio,hub_avg_spp,sced_net_cf,sced_potential_cf,peak_hsl_mw";

/** Same rule as the pipeline summary (market_metrics.min_month_coverage): partial months are not charted. */
export const MIN_MONTH_COVERAGE = 0.9;

export function daysInMonth(month: string): number {
  const [y, m] = month.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

export function fullMonths<T extends { month: string; days: number | null }>(rows: T[]): T[] {
  return rows.filter((r) => r.days !== null && r.days / daysInMonth(r.month) >= MIN_MONTH_COVERAGE);
}

// Display order and labels follow pipeline/config.yaml `tiers`; the MW bounds live there, not here.
export const TIERS: { id: Tier; label: string }[] = [
  { id: "T1b", label: "T1b · Anchor, SB6 path" },
  { id: "T1a", label: "T1a · Anchor, sub-threshold" },
  { id: "T2", label: "T2 · Mid" },
  { id: "T3", label: "T3 · Modular" },
];

export const TABLE_COLUMNS =
  "eia_id,plant_name,operator,county,ac_mw,dc_mw,dc_mw_source,ilr,ilr_confidence,cod_first,cod_last,tier," +
  "filter_status,filter_reasons,array_acres,acres_per_mw_ac,grid_voltage_kv,net_ac_cf,cf_year," +
  "cf_series_resolution,sb6_review_required,planned_load_mw,tracking_type,module_tech,non_ercot_texas," +
  "metrics_status,capture_rate_potential,curtailment_pct,sced_net_cf,peak_hsl_ratio_recent";

export type TableRow = Pick<
  Plant,
  | "eia_id" | "plant_name" | "operator" | "county" | "ac_mw" | "dc_mw" | "dc_mw_source" | "ilr" | "ilr_confidence"
  | "cod_first" | "cod_last" | "tier" | "filter_status" | "filter_reasons" | "array_acres" | "acres_per_mw_ac"
  | "grid_voltage_kv" | "net_ac_cf" | "cf_year" | "cf_series_resolution" | "sb6_review_required"
  | "planned_load_mw" | "tracking_type" | "module_tech" | "non_ercot_texas"
  | "metrics_status" | "capture_rate_potential" | "curtailment_pct" | "sced_net_cf" | "peak_hsl_ratio_recent"
>;

// ---- formatting: blanks render as an em dash, never as 0 -----------------------------------------------------
const DASH = "—";

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  return `${(v * 100).toFixed(digits)}%`;
}

export function yearMonth(v: string | null | undefined): string {
  return v ? v.slice(0, 7) : DASH;
}

export function text(v: string | null | undefined): string {
  return v && v.trim() ? v : DASH;
}

export function yesNo(v: boolean | null | undefined): string {
  return v === null || v === undefined ? DASH : v ? "Yes" : "No";
}
