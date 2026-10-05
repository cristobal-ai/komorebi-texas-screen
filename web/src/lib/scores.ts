/** Phase 5 scores (public.plant_scores, supabase/migrations/*_plant_scores.sql) and client-side re-weighting. */

export type SectionId = "a" | "b" | "c" | "d" | "e" | "f";

export type Score = {
  eia_id: number;
  score_total: number;
  score_a: number;
  score_b: number;
  score_c: number;
  score_d: number;
  score_e: number;
  score_f: number;
  pts_capture: number | null;
  pts_curtailment: number | null;
  pts_cf_benchmark: number | null;
  pts_offtake: number | null;
  pts_poly: number | null;
  pts_monofacial: number | null;
  pts_tracker: number | null;
  pts_acres: number | null;
  pts_headroom: number | null;
  pts_land_control: number | null;
  pts_poi_kv: number | null;
  pts_dist_345: number | null;
  pts_load_pocket: number | null;
  pts_lambda: number | null;
  pts_drill: number | null;
  pts_hours25: number | null;
  pts_water: number | null;
  pts_fixed_cost: number | null;
  data_completeness: number | null;
  missing_inputs: string | null;
  rank_overall: number | null;
  rank_in_tier: number | null;
  region: string | null;
  cf_used: number | null;
  cf_source: string | null;
  cf_benchmark: number | null;
  cf_below_benchmark_pts: number | null;
  offtake_confidence: string | null;
  fixed_cost_est_usd: number | null;
  fixed_cost_includes_fiber: boolean | null;
  fixed_cost_per_kw_it: number | null;
  score_version: string | null;
  run_id: string;
};

/** Columns the plants table needs (the full row is read on the plant page). */
export const TABLE_SCORE_COLUMNS =
  "eia_id,score_total,score_a,score_b,score_c,score_d,score_e,score_f,data_completeness,missing_inputs,rank_overall,rank_in_tier";

export type TableScore = Pick<
  Score,
  | "eia_id" | "score_total" | "score_a" | "score_b" | "score_c" | "score_d" | "score_e" | "score_f"
  | "data_completeness" | "missing_inputs" | "rank_overall" | "rank_in_tier"
>;

type Component = { key: keyof Score; label: string; max: number };

/** Section maxima and components mirror pipeline/config.yaml `scoring` (brief §5). F is a penalty: max is its magnitude. */
export const SECTIONS: { id: SectionId; label: string; max: number; penalty?: boolean; components: Component[] }[] = [
  {
    id: "a", label: "A · Acquisition discount", max: 30,
    components: [
      { key: "pts_capture", label: "Capture rate (potential), fleet quartile", max: 12 },
      { key: "pts_curtailment", label: "Curtailment, fleet quartile", max: 8 },
      { key: "pts_cf_benchmark", label: "CF below regional benchmark", max: 6 },
      { key: "pts_offtake", label: "Offtake status", max: 4 },
    ],
  },
  {
    id: "b", label: "B · Repowering upside", max: 15,
    components: [
      { key: "pts_poly", label: "Polycrystalline (vintage proxy)", max: 6 },
      { key: "pts_monofacial", label: "Monofacial", max: 5 },
      { key: "pts_tracker", label: "Fixed tilt or vintage tracker", max: 4 },
    ],
  },
  {
    id: "c", label: "C · Physical envelope", max: 20,
    components: [
      { key: "pts_acres", label: "Host-parcel acres per MW AC", max: 8 },
      { key: "pts_headroom", label: "Expansion headroom (same owner)", max: 7 },
      { key: "pts_land_control", label: "Unified land control", max: 5 },
    ],
  },
  {
    id: "d", label: "D · Electrical", max: 20,
    components: [
      { key: "pts_poi_kv", label: "POI voltage", max: 10 },
      { key: "pts_dist_345", label: "Distance to 345 kV substation", max: 5 },
      { key: "pts_load_pocket", label: "Load-pocket proximity", max: 5 },
    ],
  },
  {
    id: "e", label: "E · Thermal & cooling", max: 15,
    components: [
      { key: "pts_lambda", label: "Soil thermal conductivity λ", max: 6 },
      { key: "pts_drill", label: "Drillability", max: 4 },
      { key: "pts_hours25", label: "Hours below 25 °C", max: 3 },
      { key: "pts_water", label: "Depth to water", max: 2 },
    ],
  },
  {
    id: "f", label: "F · Fixed-cost drag", max: 15, penalty: true,
    components: [{ key: "pts_fixed_cost", label: "Fixed cost per kW of firm IT", max: 15 }],
  },
];

export const COMPONENT_KEYS: Record<string, string> = Object.fromEntries(
  SECTIONS.flatMap((s) => s.components.map((c) => [String(c.key).replace(/^pts_/, ""), c.label])),
);

export type Weights = Record<SectionId, number>;
export const DEFAULT_WEIGHTS: Weights = Object.fromEntries(SECTIONS.map((s) => [s.id, s.max])) as Weights;

/** Re-weighted total: each section is rescaled from its default maximum to the slider's maximum. */
export function weightedScore(s: TableScore, w: Weights): number {
  let total = 0;
  for (const sec of SECTIONS) {
    const v = s[`score_${sec.id}` as keyof TableScore] as number;
    total += (v / sec.max) * w[sec.id];
  }
  return total;
}

export function isDefaultWeights(w: Weights): boolean {
  return SECTIONS.every((s) => w[s.id] === s.max);
}

/** Ranks (1 = best, ties share the lower number) of the given ids by value, descending. */
export function rankBy<T>(items: T[], value: (t: T) => number, id: (t: T) => number): Map<number, number> {
  const sorted = [...items].sort((a, b) => value(b) - value(a));
  const ranks = new Map<number, number>();
  sorted.forEach((t, i) => {
    const prev = i > 0 ? sorted[i - 1] : undefined;
    ranks.set(id(t), prev && value(prev) === value(t) ? ranks.get(id(prev))! : i + 1);
  });
  return ranks;
}

export function missingLabels(m: string | null | undefined): string[] {
  return (m ?? "").split(";").filter(Boolean).map((k) => COMPONENT_KEYS[k] ?? k);
}
