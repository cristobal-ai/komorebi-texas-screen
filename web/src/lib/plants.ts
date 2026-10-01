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
  run_id: string;
  loaded_at: string;
};

export type Tier = "T1b" | "T1a" | "T2" | "T3";

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
  "cf_series_resolution,sb6_review_required,planned_load_mw,tracking_type,module_tech,non_ercot_texas";

export type TableRow = Pick<
  Plant,
  | "eia_id" | "plant_name" | "operator" | "county" | "ac_mw" | "dc_mw" | "dc_mw_source" | "ilr" | "ilr_confidence"
  | "cod_first" | "cod_last" | "tier" | "filter_status" | "filter_reasons" | "array_acres" | "acres_per_mw_ac"
  | "grid_voltage_kv" | "net_ac_cf" | "cf_year" | "cf_series_resolution" | "sb6_review_required"
  | "planned_load_mw" | "tracking_type" | "module_tech" | "non_ercot_texas"
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
