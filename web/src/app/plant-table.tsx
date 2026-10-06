"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { TIERS, metricsStatusLabel, num, pct, text, yearMonth, type TableRow, type Tier } from "@/lib/plants";
import {
  DEFAULT_WEIGHTS,
  SECTIONS,
  isDefaultWeights,
  missingLabels,
  rankBy,
  weightedScore,
  type TableScore,
  type Weights,
} from "@/lib/scores";

/** A plants row with its Phase 5 score and flood flag (null when not scored / no flood row). */
export type ScoredRow = TableRow & {
  score: TableScore | null;
  flood_status: string | null;
  flood_flag: boolean | null;
};

/** ScoredRow plus the score and ranks under the current weights (pass plants only are ranked). */
type Row = ScoredRow & { w_score: number | null; w_rank: number | null; w_tier_rank: number | null };

const col = createColumnHelper<Row>();
const TIER_ORDER: Record<string, number> = { T1b: 0, T1a: 1, T2: 2, T3: 3 };

/** Market-metric column: a blank is never a zero, hover says why (behind the meter, not telemetered, not verified). */
function metric(
  id: string,
  header: string,
  pick: (r: Row) => number | null,
  format: (v: number) => string,
  flag?: (v: number) => boolean,
) {
  return col.accessor((r) => pick(r) ?? undefined, {
    id,
    header,
    sortUndefined: "last",
    meta: { numeric: true },
    cell: (c) => {
      const v = c.getValue();
      if (v === undefined) {
        return (
          <span title={metricsStatusLabel(c.row.original.metrics_status)} className="text-neutral-400">
            —
          </span>
        );
      }
      return flag?.(v) ? (
        <span title="Output ceiling well below nameplate over the last 3 months: possible derate or outage">
          {format(v)} ▼
        </span>
      ) : (
        format(v)
      );
    },
  });
}

const columns = [
  col.accessor((r) => r.w_rank ?? undefined, {
    id: "rank",
    header: "Rank",
    sortUndefined: "last",
    meta: { numeric: true },
    cell: (c) => {
      const v = c.getValue();
      return v === undefined ? (
        <span title="Review plants are scored but not ranked" className="text-neutral-400">
          —
        </span>
      ) : (
        v
      );
    },
  }),
  col.accessor((r) => r.w_score ?? undefined, {
    id: "score",
    header: "Score",
    sortUndefined: "last",
    meta: { numeric: true },
    cell: (c) => {
      const s = c.row.original.score;
      const v = c.getValue();
      if (v === undefined || !s) return <span className="text-neutral-400">—</span>;
      const parts = SECTIONS.map((sec) => `${sec.label}: ${num(s[`score_${sec.id}` as keyof TableScore] as number)}`).join("\n");
      return (
        <span title={`Default-weight sections\n${parts}`} className="font-medium">
          {num(v)}
        </span>
      );
    },
  }),
  col.accessor((r) => r.w_tier_rank ?? undefined, {
    id: "tier_rank",
    header: "In tier",
    sortUndefined: "last",
    meta: { numeric: true },
    cell: (c) => c.getValue() ?? "—",
  }),
  col.accessor((r) => r.score?.data_completeness ?? undefined, {
    id: "completeness",
    header: "Data",
    sortUndefined: "last",
    meta: { numeric: true },
    cell: (c) => {
      const v = c.getValue();
      if (v === undefined) return "—";
      const miss = missingLabels(c.row.original.score?.missing_inputs);
      return <span title={miss.length ? `Scored neutral (half points): ${miss.join("; ")}` : "All inputs measured"}>{pct(v, 0)}</span>;
    },
  }),
  col.accessor("plant_name", {
    header: "Plant",
    cell: (c) => (
      <Link href={`/plants/${c.row.original.eia_id}`} className="font-medium hover:underline">
        {c.getValue()}
      </Link>
    ),
  }),
  col.accessor("county", { header: "County", cell: (c) => text(c.getValue()) }),
  col.accessor("tier", {
    header: "Tier",
    sortingFn: (a, b) => (TIER_ORDER[a.original.tier ?? ""] ?? 9) - (TIER_ORDER[b.original.tier ?? ""] ?? 9),
    cell: (c) => text(c.getValue()),
  }),
  col.accessor("ac_mw", { header: "MW AC", cell: (c) => num(c.getValue()), meta: { numeric: true } }),
  col.accessor("ilr", {
    header: "ILR",
    cell: (c) => num(c.getValue(), 2) + (c.row.original.ilr_confidence === "assumed" ? "*" : ""),
    meta: { numeric: true },
  }),
  col.accessor("cod_first", { header: "COD", cell: (c) => yearMonth(c.getValue()) }),
  col.accessor("acres_per_mw_ac", { header: "Array ac/MW", cell: (c) => num(c.getValue()), meta: { numeric: true } }),
  col.accessor("grid_voltage_kv", { header: "POI kV", cell: (c) => num(c.getValue(), 0), meta: { numeric: true } }),
  col.accessor("net_ac_cf", {
    header: "Net AC CF",
    cell: (c) => pct(c.getValue()) + (c.row.original.cf_series_resolution === "annual" ? " (a)" : ""),
    meta: { numeric: true },
  }),
  metric("capture_rate_potential", "Capture (pot.)", (r) => r.capture_rate_potential, (v) => num(v, 2)),
  metric("curtailment_pct", "Curtail", (r) => r.curtailment_pct, (v) => pct(v)),
  metric("sced_net_cf", "SCED CF", (r) => r.sced_net_cf, (v) => pct(v)),
  metric("peak_hsl_ratio_recent", "Recent peak", (r) => r.peak_hsl_ratio_recent, (v) => num(v, 2), (v) => v < 0.75),
  col.accessor("planned_load_mw", { header: "Load MW", cell: (c) => num(c.getValue()), meta: { numeric: true } }),
  col.accessor("sb6_review_required", { header: "SB6", cell: (c) => (c.getValue() ? "Yes" : "No") }),
  col.accessor((r) => (r.flood_flag ? 2 : r.flood_status === "not_mapped" ? 1 : 0), {
    id: "flood",
    header: "Flood",
    sortDescFirst: true,
    cell: (c) => {
      const r = c.row.original;
      if (r.flood_flag)
        return <span title="10% or more of the array in a FEMA 1% annual-chance zone (shown, not scored)">⚠ SFHA</span>;
      if (r.flood_status === "not_mapped")
        return (
          <span title="No digital FEMA flood map covers this site: risk unknown, not zero" className="text-neutral-400">
            unmapped
          </span>
        );
      return r.flood_status ? "—" : "";
    },
  }),
  col.accessor("filter_status", { header: "Status", cell: (c) => c.getValue() }),
];

export default function PlantTable({ rows }: { rows: ScoredRow[] }) {
  const [tiers, setTiers] = useState<Set<Tier>>(new Set(TIERS.map((t) => t.id)));
  const [showReview, setShowReview] = useState(false);
  const [query, setQuery] = useState("");
  const [weights, setWeights] = useState<Weights>(DEFAULT_WEIGHTS);
  const [showWeights, setShowWeights] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([{ id: "rank", desc: false }]);

  // Score and rank every plant under the current weights; ranks are over all pass plants, not just the visible ones.
  const scored = useMemo<Row[]>(() => {
    const withScore = rows.map((r) => ({ ...r, w_score: r.score ? weightedScore(r.score, weights) : null }));
    const ranked = withScore.filter((r) => r.filter_status === "pass" && r.w_score !== null);
    const overall = rankBy(ranked, (r) => r.w_score!, (r) => r.eia_id);
    const inTier = new Map<number, number>();
    for (const t of TIERS) {
      rankBy(ranked.filter((r) => r.tier === t.id), (r) => r.w_score!, (r) => r.eia_id).forEach((v, k) => inTier.set(k, v));
    }
    return withScore.map((r) => ({ ...r, w_rank: overall.get(r.eia_id) ?? null, w_tier_rank: inTier.get(r.eia_id) ?? null }));
  }, [rows, weights]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return scored.filter(
      (r) =>
        (r.tier ? tiers.has(r.tier) : false) &&
        (showReview || r.filter_status === "pass") &&
        (!q || `${r.plant_name} ${r.county ?? ""} ${r.operator ?? ""}`.toLowerCase().includes(q)),
    );
  }, [scored, tiers, showReview, query]);

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    enableMultiSort: true,
  });

  const totalMw = filtered.reduce((s, r) => s + r.ac_mw, 0);

  return (
    <section className="mt-5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {TIERS.map((t) => {
          const on = tiers.has(t.id);
          return (
            <button
              key={t.id}
              aria-pressed={on}
              onClick={() => {
                const next = new Set(tiers);
                if (on) next.delete(t.id);
                else next.add(t.id);
                setTiers(next);
              }}
              className={`rounded-full border px-3 py-1 ${on ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900" : "border-neutral-300 dark:border-neutral-700"}`}
            >
              {t.label}
            </button>
          );
        })}
        <label className="ml-2 flex items-center gap-1">
          <input type="checkbox" checked={showReview} onChange={(e) => setShowReview(e.target.checked)} />
          include review
        </label>
        <input
          type="search"
          placeholder="Search plant, county, operator"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="ml-auto w-64 rounded border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
        />
      </div>
      <div className="mt-3 text-sm">
        <button
          onClick={() => setShowWeights(!showWeights)}
          aria-expanded={showWeights}
          className="rounded border border-neutral-300 px-3 py-1 dark:border-neutral-700"
        >
          Weights{isDefaultWeights(weights) ? " (brief defaults)" : " (custom)"} {showWeights ? "▴" : "▾"}
        </button>
        <a
          href={`/export${isDefaultWeights(weights) ? "" : `?${new URLSearchParams(Object.entries(weights).map(([k, v]) => [k, String(v)]))}`}`}
          className="ml-2 inline-block rounded border border-neutral-300 px-3 py-1 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
          title="Every pass and review plant with every metric, the monthly ERCOT history and an About sheet; custom weights add score_custom and rank_custom"
        >
          Export XLSX
        </a>
        {showWeights && (
          <div className="mt-2 rounded border border-neutral-200 p-3 dark:border-neutral-800">
            <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
              {SECTIONS.map((sec) => (
                <label key={sec.id} className="flex items-center gap-2">
                  <span className="w-44 shrink-0">{sec.label}</span>
                  <input
                    type="range"
                    min={0}
                    max={40}
                    step={1}
                    value={weights[sec.id]}
                    onChange={(e) => setWeights({ ...weights, [sec.id]: Number(e.target.value) })}
                    className="w-full"
                  />
                  <span className="w-10 text-right tabular-nums">
                    {sec.penalty ? "−" : ""}
                    {weights[sec.id]}
                  </span>
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-neutral-500">
              Each slider sets a section’s maximum points (F: the maximum penalty). Positive maximum now{" "}
              {SECTIONS.filter((s) => !s.penalty).reduce((t, s) => t + weights[s.id], 0)} (default 94: the brief’s 100 less soil λ, shown but not scored). Ranks recompute
              over all pass plants.{" "}
              <button onClick={() => setWeights(DEFAULT_WEIGHTS)} className="underline">
                Reset to brief defaults
              </button>
            </p>
          </div>
        )}
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        {filtered.length} plants · {num(totalMw)} MW AC · click headers to sort (shift-click for a second key) · *
        ILR assumed (no reported DC) · (a) CF from an annual EIA-923 respondent
      </p>
      <p className="mt-1 text-xs text-neutral-500">
        Market columns (SCED + real-time prices, last 30 full months): Capture (pot.) = HSL-weighted node price ÷ hub
        average · Curtail = share of available output dispatched down · Recent peak = highest output ceiling over the
        last 3 months ÷ nameplate (▼ below 0.75). — = no metrics; hover for the reason. Low capture and high
        curtailment are price signals, not defects.
      </p>
      <div className="mt-2 overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800">
        <table className="w-full min-w-[1500px] text-sm">
          <thead className="bg-neutral-50 dark:bg-neutral-900">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const numeric = (h.column.columnDef.meta as { numeric?: boolean } | undefined)?.numeric;
                  const dir = h.column.getIsSorted();
                  return (
                    <th
                      key={h.id}
                      scope="col"
                      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
                      className={`whitespace-nowrap px-3 py-2 font-medium ${numeric ? "text-right" : "text-left"}`}
                    >
                      <button onClick={h.column.getToggleSortingHandler()} className="hover:underline">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {dir === "asc" ? " ▲" : dir === "desc" ? " ▼" : ""}
                      </button>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="border-t border-neutral-100 dark:border-neutral-800">
                {row.getVisibleCells().map((cell) => {
                  const numeric = (cell.column.columnDef.meta as { numeric?: boolean } | undefined)?.numeric;
                  return (
                    <td key={cell.id} className={`whitespace-nowrap px-3 py-1.5 ${numeric ? "text-right tabular-nums" : ""}`}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
