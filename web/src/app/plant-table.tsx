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

const col = createColumnHelper<TableRow>();
const TIER_ORDER: Record<string, number> = { T1b: 0, T1a: 1, T2: 2, T3: 3 };

/** Market-metric column: a blank is never a zero, hover says why (behind the meter, not telemetered, not verified). */
function metric(
  id: string,
  header: string,
  pick: (r: TableRow) => number | null,
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
  col.accessor("filter_status", { header: "Status", cell: (c) => c.getValue() }),
];

export default function PlantTable({ rows }: { rows: TableRow[] }) {
  const [tiers, setTiers] = useState<Set<Tier>>(new Set(TIERS.map((t) => t.id)));
  const [showReview, setShowReview] = useState(false);
  const [query, setQuery] = useState("");
  const [sorting, setSorting] = useState<SortingState>([
    { id: "tier", desc: false },
    { id: "ac_mw", desc: true },
  ]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter(
      (r) =>
        (r.tier ? tiers.has(r.tier) : false) &&
        (showReview || r.filter_status === "pass") &&
        (!q || `${r.plant_name} ${r.county ?? ""} ${r.operator ?? ""}`.toLowerCase().includes(q)),
    );
  }, [rows, tiers, showReview, query]);

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
        <table className="w-full min-w-[1240px] text-sm">
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
