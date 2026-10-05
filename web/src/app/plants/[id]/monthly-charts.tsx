"use client";

import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { num, pct, type MonthlyMetric } from "@/lib/plants";

/**
 * Monthly SCED / SPP history for one plant. Three single-axis charts (never dual-axis), colours from the --viz-*
 * tokens in globals.css (validated for light and dark), crosshair tooltip on every chart, and a table view with the
 * same numbers. Low capture rate and high curtailment are price signals here, not defects.
 */

type Series = { key: keyof MonthlyMetric; name: string; color: string };
type Fmt = (v: number | null | undefined) => string;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (m: string) => `${MONTHS[Number(m.slice(5, 7)) - 1]} ’${m.slice(2, 4)}`;

type TipProps = {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ name?: unknown; value?: unknown; color?: string }>;
};

function makeTooltip(fmt: Fmt) {
  return function Tip({ active, label, payload }: TipProps) {
    if (!active || !payload || payload.length === 0) return null;
    return (
      <div
        className="rounded border px-3 py-2 text-xs shadow-sm"
        style={{ background: "var(--viz-surface)", borderColor: "var(--viz-axis)", color: "var(--viz-ink)" }}
      >
        <div style={{ color: "var(--viz-ink-2)" }}>{monthLabel(String(label))}</div>
        {payload.map((p) => (
          <div key={String(p.name)} className="mt-1 flex items-center gap-2">
            <span aria-hidden className="inline-block h-0.5 w-3" style={{ background: p.color }} />
            <span className="font-semibold tabular-nums">{fmt(typeof p.value === "number" ? p.value : null)}</span>
            <span style={{ color: "var(--viz-ink-2)" }}>{String(p.name ?? "")}</span>
          </div>
        ))}
      </div>
    );
  };
}

function Legend({ series }: { series: Series[] }) {
  return (
    <ul className="mt-1 flex flex-wrap gap-x-4 text-xs" style={{ color: "var(--viz-ink-2)" }}>
      {series.map((s) => (
        <li key={s.name} className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-0.5 w-4" style={{ background: s.color }} />
          {s.name}
        </li>
      ))}
    </ul>
  );
}

function Chart({
  title,
  note,
  rows,
  series,
  fmt,
  axisFmt,
  domain,
  reference,
}: {
  title: string;
  note: string;
  rows: MonthlyMetric[];
  series: Series[];
  fmt: Fmt;
  axisFmt: (v: number) => string;
  domain: [number, number | ((max: number) => number)];
  reference?: { y: number; label: string };
}) {
  const last = rows.length - 1;
  const ticks = rows.filter((r) => /-(01|07)$/.test(r.month)).map((r) => r.month);
  const Tip = makeTooltip(fmt);
  return (
    <figure className="rounded border border-neutral-200 p-4 dark:border-neutral-800" style={{ background: "var(--viz-surface)" }}>
      <figcaption>
        <div className="text-sm font-semibold" style={{ color: "var(--viz-ink)" }}>
          {title}
        </div>
        <div className="text-xs" style={{ color: "var(--viz-ink-2)" }}>
          {note}
        </div>
        {series.length > 1 && <Legend series={series} />}
      </figcaption>
      <div className="mt-2 h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 72, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
            <XAxis
              dataKey="month"
              ticks={ticks}
              tickFormatter={monthLabel}
              tick={{ fill: "var(--viz-muted)", fontSize: 11 }}
              stroke="var(--viz-axis)"
              tickLine={false}
            />
            <YAxis
              domain={domain}
              tickCount={5}
              tickFormatter={axisFmt}
              tick={{ fill: "var(--viz-muted)", fontSize: 11 }}
              stroke="var(--viz-axis)"
              tickLine={false}
              axisLine={false}
              width={44}
            />
            <Tooltip content={Tip} cursor={{ stroke: "var(--viz-axis)", strokeWidth: 1 }} isAnimationActive={false} />
            {reference && (
              <ReferenceLine
                y={reference.y}
                stroke="var(--viz-axis)"
                strokeDasharray="4 4"
                label={{ value: reference.label, position: "insideTopRight", fill: "var(--viz-muted)", fontSize: 11 }}
              />
            )}
            {series.map((s) => (
              <Line
                key={s.name}
                name={s.name}
                dataKey={s.key as string}
                stroke={s.color}
                strokeWidth={2}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
                activeDot={{ r: 4, stroke: "var(--viz-surface)", strokeWidth: 2, fill: s.color }}
                label={(p: { x?: number | string; y?: number | string; index?: number }) =>
                  p.index === last && p.x !== undefined && p.y !== undefined ? (
                    <text key={s.name} x={Number(p.x) + 8} y={Number(p.y)} dy={4} fontSize={11} fill="var(--viz-ink-2)">
                      {s.name.split(" ")[0]}
                    </text>
                  ) : (
                    <g key={`${s.name}-${p.index}`} />
                  )
                }
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

const S1 = "var(--viz-series-1)";
const S2 = "var(--viz-series-2)";

export default function MonthlyCharts({ rows, excluded }: { rows: MonthlyMetric[]; excluded: number }) {
  const [table, setTable] = useState(false);
  if (rows.length === 0) return null;
  return (
    <section className="mt-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          Monthly market history ({rows[0].month} to {rows[rows.length - 1].month})
        </h2>
        <button
          onClick={() => setTable(!table)}
          aria-pressed={table}
          className="rounded border border-neutral-300 px-2 py-0.5 text-xs hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          {table ? "Show charts" : "Show table"}
        </button>
        <span className="text-xs text-neutral-500">
          SCED + 15-minute real-time prices
          {excluded > 0 ? ` · ${excluded} partial month${excluded > 1 ? "s" : ""} left out (under 90% of days)` : ""}
        </span>
      </div>

      {table ? (
        <div className="mt-2 overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="bg-neutral-50 dark:bg-neutral-900">
              <tr>
                {["Month", "Curtailment", "Capture (potential)", "Capture (delivered)", "Shape", "Basis", "SCED CF", "Potential CF", "Gen MWh"].map(
                  (h, i) => (
                    <th key={h} scope="col" className={`whitespace-nowrap px-3 py-2 font-medium ${i ? "text-right" : "text-left"}`}>
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.month} className="border-t border-neutral-100 tabular-nums dark:border-neutral-800">
                  <td className="px-3 py-1.5">{r.month}</td>
                  <td className="px-3 py-1.5 text-right">{pct(r.curtailment_pct)}</td>
                  <td className="px-3 py-1.5 text-right">{num(r.capture_rate_potential, 2)}</td>
                  <td className="px-3 py-1.5 text-right">{num(r.capture_rate, 2)}</td>
                  <td className="px-3 py-1.5 text-right">{num(r.shape_capture, 2)}</td>
                  <td className="px-3 py-1.5 text-right">{num(r.basis_ratio, 2)}</td>
                  <td className="px-3 py-1.5 text-right">{pct(r.sced_net_cf)}</td>
                  <td className="px-3 py-1.5 text-right">{pct(r.sced_potential_cf)}</td>
                  <td className="px-3 py-1.5 text-right">{num(r.gen_mwh, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="mt-2 grid gap-4 lg:grid-cols-2">
          <Chart
            title="Curtailment"
            note="Share of available output (HSL) dispatched down below its base point"
            rows={rows}
            series={[{ key: "curtailment_pct", name: "Curtailment", color: S1 }]}
            fmt={(v) => pct(v)}
            axisFmt={(v) => `${Math.round(v * 100)}%`}
            domain={[0, (max) => Math.max(0.1, Math.ceil(max * 10) / 10)]}
          />
          <Chart
            title="Capture rate vs hub average"
            note="Generation-weighted node price ÷ average HB_HUBAVG. Delivered is flattered by curtailment; potential is not."
            rows={rows}
            series={[
              { key: "capture_rate_potential", name: "Potential capture", color: S1 },
              { key: "capture_rate", name: "Delivered capture", color: S2 },
            ]}
            fmt={(v) => num(v, 2)}
            axisFmt={(v) => v.toFixed(1)}
            domain={[0, (max) => Math.max(1.2, Math.ceil(max * 10) / 10)]}
            reference={{ y: 1, label: "hub average" }}
          />
          <Chart
            title="Capacity factor (AC)"
            note="Net = telemetered output; potential = what the plant could have delivered (HSL). The gap is curtailment."
            rows={rows}
            series={[
              { key: "sced_potential_cf", name: "Potential CF", color: S1 },
              { key: "sced_net_cf", name: "Net CF", color: S2 },
            ]}
            fmt={(v) => pct(v)}
            axisFmt={(v) => `${Math.round(v * 100)}%`}
            domain={[0, (max) => Math.max(0.3, Math.ceil(max * 10) / 10)]}
          />
        </div>
      )}
    </section>
  );
}
