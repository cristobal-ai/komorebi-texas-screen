import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { num, pct, text, yearMonth, yesNo, type Plant } from "@/lib/plants";

export const dynamic = "force-dynamic";

type Row = [label: string, value: string, note?: string];

function Section({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <section className="rounded border border-neutral-200 p-4 dark:border-neutral-800">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">{title}</h2>
      <dl className="mt-2 grid grid-cols-[minmax(10rem,auto)_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.map(([label, value, note]) => (
          <div key={label} className="contents">
            <dt className="text-neutral-600 dark:text-neutral-400">{label}</dt>
            <dd className="tabular-nums">
              {value}
              {note && <span className="ml-2 text-xs text-neutral-500">{note}</span>}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default async function PlantPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const eiaId = Number(id);
  if (!Number.isInteger(eiaId)) notFound();

  const supabase = await createClient();
  const { data, error } = await supabase.from("plants").select("*").eq("eia_id", eiaId).maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) notFound();
  const p = data as Plant;

  const flags: string[] = [];
  if (p.sb6_review_required) flags.push(`SB6 co-location review: planned load ${num(p.planned_load_mw)} MW is at or above the large-load threshold`);
  if (p.tax_equity_consent_likely) flags.push("Tax-equity consent likely (COD ≥ 2019)");
  if (p.itc_recapture_open) flags.push("ITC recapture window may be open (COD within 5 years)");
  if (p.non_ercot_texas) flags.push(`Outside ERCOT balancing authority (${text(p.ba_code)})`);
  if (p.distribution_class_poi) flags.push("Distribution-class POI (at or below the config distribution-voltage limit)");
  if (p.filter_status === "review") flags.push(`Footprint unresolved: ${text(p.filter_reasons)}`);

  return (
    <main className="mx-auto max-w-5xl px-4 py-6">
      <Link href="/" className="text-sm text-neutral-500 hover:underline">
        ← All plants
      </Link>
      <h1 className="mt-2 text-2xl font-semibold">{p.plant_name}</h1>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        EIA {p.eia_id} · {text(p.county)} County · {text(p.operator)} · {text(p.tier)} ·{" "}
        <span className={p.filter_status === "pass" ? "" : "font-medium text-amber-700 dark:text-amber-400"}>
          {p.filter_status}
        </span>
      </p>

      {flags.length > 0 && (
        <ul className="mt-4 space-y-1 rounded border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950">
          {flags.map((f) => (
            <li key={f}>• {f}</li>
          ))}
        </ul>
      )}

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Section
          title="Capacity & COD"
          rows={[
            ["AC capacity", `${num(p.ac_mw)} MW`, `EIA-860M ${text(p.source_month)}; ${p.n_generators ?? "—"} PV generator(s)`],
            ["DC capacity", `${num(p.dc_mw)} MW`, `source: ${text(p.dc_mw_source)}`],
            ["ILR", num(p.ilr, 2), p.ilr_confidence === "assumed" ? "assumed (config default)" : "reported"],
            ["COD (first phase)", yearMonth(p.cod_first)],
            ["COD (last phase)", yearMonth(p.cod_last), p.cod_multi_phase ? "multi-phase" : undefined],
            ["Co-located storage", yesNo(p.has_colocated_storage)],
          ]}
        />
        <Section
          title="Compute envelope (config assumptions)"
          rows={[
            ["Tier", text(p.tier)],
            ["Firm IT", `${num(p.firm_it_mw)} MW`, "AC × ILR × IT per MWdc (pipeline/config.yaml)"],
            ["Planned facility load", `${num(p.planned_load_mw)} MW`, "firm IT × PUE (pipeline/config.yaml)"],
            ["SB6 review required", yesNo(p.sb6_review_required), "load ≥ SB6 threshold (pipeline/config.yaml)"],
          ]}
        />
        <Section
          title="Footprint (USPVDB v4.0)"
          rows={[
            ["Matched to USPVDB", yesNo(p.uspvdb_match)],
            ["Array area", `${num(p.array_acres, 0)} acres`, `${p.n_polygons ?? "—"} polygon(s)`],
            ["Array acres / MW AC", num(p.acres_per_mw_ac), "array, not site footprint — parcels in Phase 4"],
            ["USPVDB AC capacity", `${num(p.ac_mw_uspvdb)} MW`, `Δ vs EIA ${pct(p.ac_mw_delta_pct, 0)}`],
            ["USPVDB year", p.year_uspvdb ? String(p.year_uspvdb) : "—"],
            ["Footprint basis", text(p.footprint_basis)],
          ]}
        />
        <Section
          title={`Electrical & technology (EIA-860 ${p.eia860_year ?? ""})`}
          rows={[
            ["POI grid voltage", `${num(p.grid_voltage_kv, 0)} kV`, `source: ${text(p.grid_voltage_source)}`],
            ["Highest grid voltage", `${num(p.grid_voltage_max_kv, 0)} kV`],
            ["Tracking", text(p.tracking_type), p.tracking_type_share !== null ? `${pct(p.tracking_type_share, 0)} of MW` : undefined],
            ["Module technology", text(p.module_tech), p.module_tech_share !== null ? `${pct(p.module_tech_share, 0)} of MW` : undefined],
            ["Bifacial share", pct(p.bifacial_share, 0), p.bifacial_share === null ? "not reported to EIA" : undefined],
            ["Module class confidence", text(p.module_type_confidence)],
          ]}
        />
        <Section
          title={`Generation (EIA-923 ${p.cf_year ?? ""})`}
          rows={[
            ["Net generation", `${num(p.net_mwh, 0)} MWh`],
            ["Net AC capacity factor", pct(p.net_ac_cf), p.cf_note ?? undefined],
            ["Series resolution", text(p.cf_series_resolution)],
            ["Months reported", p.months_reported !== null ? String(p.months_reported) : "—"],
          ]}
        />
        <Section
          title="Location & provenance"
          rows={[
            ["Latitude, longitude", p.lat !== null && p.lon !== null ? `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}` : "—"],
            ["Balancing authority", text(p.ba_code)],
            ["Filter status", p.filter_status, p.filter_reasons ?? undefined],
            ["Data run", p.run_id],
          ]}
        />
      </div>
      <p className="mt-6 text-xs text-neutral-500">
        Not scored yet (Phases 3–5 add curtailment, capture rate, geo layers and the composite score).
      </p>
    </main>
  );
}
