import { Fragment } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import {
  MONTHLY_COLUMNS,
  fullMonths,
  metricsStatusLabel,
  num,
  pct,
  text,
  yearMonth,
  yesNo,
  type MonthlyMetric,
  type Plant,
} from "@/lib/plants";
import { SECTIONS, missingLabels, type Score } from "@/lib/scores";
import MonthlyCharts from "./monthly-charts";
import PrintButton from "@/components/print-button";

type Layers = {
  transmission: Record<string, string | number | boolean | null> | null;
  parcels: Record<string, string | number | boolean | null> | null;
  gas: Record<string, string | number | boolean | null> | null;
  flood: Record<string, string | number | boolean | null> | null;
  fiber: Record<string, string | number | boolean | null> | null;
  climate: Record<string, string | number | boolean | null> | null;
  wells: Record<string, string | number | boolean | null> | null;
  soils: Record<string, string | number | boolean | null> | null;
};

const n = (v: unknown) => (typeof v === "number" ? v : null);
const t = (v: unknown) => (typeof v === "string" ? v : null);

function ScoreSection({ s }: { s: Score }) {
  return (
    <section className="mt-4 rounded border border-neutral-200 p-4 dark:border-neutral-800">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Score (brief §5, default weights)</h2>
      <p className="mt-1 text-sm">
        <span className="text-2xl font-semibold tabular-nums">{num(s.score_total)}</span>
        <span className="ml-2 text-neutral-600 dark:text-neutral-400">
          {s.rank_overall ? `rank ${s.rank_overall} overall · ${s.rank_in_tier} in tier` : "review plant: scored, not ranked"} · data{" "}
          {pct(s.data_completeness, 0)}
        </span>
      </p>
      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="text-left text-neutral-500">
            <th scope="col" className="py-1 font-medium">Component</th>
            <th scope="col" className="py-1 text-right font-medium">Points</th>
            <th scope="col" className="py-1 text-right font-medium">Max</th>
          </tr>
        </thead>
        <tbody>
          {SECTIONS.map((sec) => (
            <Fragment key={sec.id}>
              <tr className="border-t border-neutral-200 font-medium dark:border-neutral-800">
                <td className="py-1">{sec.label}</td>
                <td className="py-1 text-right tabular-nums">{num(s[`score_${sec.id}` as keyof Score] as number)}</td>
                <td className="py-1 text-right tabular-nums">{sec.penalty ? `−${sec.max}` : sec.max}</td>
              </tr>
              {sec.components.map((c) => {
                const missing = (s.missing_inputs ?? "").split(";").includes(String(c.key).replace(/^pts_/, ""));
                return (
                  <tr key={String(c.key)} className="text-neutral-600 dark:text-neutral-400">
                    <td className="py-0.5 pl-4">
                      {c.label}
                      {missing && <span className="ml-2 text-xs text-neutral-500">not measured: neutral half points</span>}
                    </td>
                    <td className="py-0.5 text-right tabular-nums">{num(s[c.key] as number | null)}</td>
                    <td className="py-0.5 text-right tabular-nums">{sec.penalty ? `−${c.max}` : c.max}</td>
                  </tr>
                );
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-neutral-500">
        CF used {pct(s.cf_used)} ({text(s.cf_source)}) vs {text(s.region)} benchmark {pct(s.cf_benchmark)}. Fixed cost
        ${num((s.fixed_cost_est_usd ?? 0) / 1e6, 1)}M → ${num(s.fixed_cost_per_kw_it, 0)}/kW firm IT
        {s.fixed_cost_includes_fiber ? "" : " (fiber lateral not included yet: a floor)"}. Offtake {text(s.offtake_confidence)}.
        {missingLabels(s.missing_inputs).length > 0 && ` Neutral inputs: ${missingLabels(s.missing_inputs).join("; ")}.`}
      </p>
    </section>
  );
}

export const dynamic = "force-dynamic";

type Row = [label: string, value: string, note?: string];

type Caveat = { title: string; status: "applies" | "required" | "check" | "not flagged"; text: string };

/** Brief §6 caveat flags (config `caveat_flags`, every dossier) plus the two plant-level tax flags: seven in all. */
function caveats(p: Plant, s: Score | null): Caveat[] {
  const west = s?.region === "west_texas";
  return [
    {
      title: "Load import capability",
      status: "applies",
      text: "Not screenable from public data: a generation interconnection agreement confers export rights only. Import at this POI needs an ERCOT screening study.",
    },
    {
      title: "SB6 large-load risk",
      status: p.sb6_review_required ? "required" : "applies",
      text: `Planned facility load ${num(p.planned_load_mw)} MW (firm IT ${num(p.firm_it_mw)} MW × PUE, at the plant's ILR ${num(p.ilr, 2)}); ${p.sb6_review_required ? "at or above" : "below"} the large-load threshold in pipeline/config.yaml. ERCOT may curtail large loads before and during grid emergencies: an underwriting variable, not a footnote.`,
    },
    {
      title: "Mineral estate",
      status: west ? "required" : "check",
      text: `${west ? "West Texas / Permian site: " : ""}the mineral estate is dominant in Texas. Severed minerals without a recorded surface waiver or subordination keep implied surface access — a deal-killer that only a title chain can clear.`,
    },
    {
      title: "Behind-the-meter configuration",
      status: "applies",
      text: "Metering and registration for load behind an existing generator's POI are unsettled and SB6 may reshape them: verify current ERCOT protocol status at the time of use.",
    },
    {
      title: "Chapter 313 / JETI abatement",
      status: "check",
      text: `${text(p.county)} County: abatement status, taxing units and expiry are not screened. The step-up at expiry is often mismodelled by sellers.`,
    },
    {
      title: "Tax-equity consent",
      status: p.tax_equity_consent_likely ? "applies" : "not flagged",
      text: p.tax_equity_consent_likely
        ? `COD ${yearMonth(p.cod_first)} (2019 or later): a tax-equity partnership is likely still in place; its consent is needed for a sale or a change of use.`
        : `COD ${yearMonth(p.cod_first)}: before the 2019 cut-off used for this flag; confirm in diligence.`,
    },
    {
      title: "ITC recapture window",
      status: p.itc_recapture_open ? "applies" : "not flagged",
      text: p.itc_recapture_open
        ? "COD within the last 5 years: a disposition or change of use may recapture part of the investment tax credit."
        : "COD more than 5 years ago: the 5-year ITC recapture window has closed (confirm the placed-in-service dates).",
    },
  ];
}

const CAVEAT_STYLE: Record<Caveat["status"], string> = {
  required: "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300",
  applies: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300",
  check: "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300",
  "not flagged": "border-neutral-300 bg-neutral-50 text-neutral-600 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-400",
};

function CaveatsSection({ items }: { items: Caveat[] }) {
  return (
    <section className="mt-4 rounded border border-neutral-200 p-4 dark:border-neutral-800">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Caveats (brief §6, every site)</h2>
      <ul className="mt-2 space-y-2 text-sm">
        {items.map((c) => (
          <li key={c.title} className="grid grid-cols-[minmax(12rem,auto)_6rem_1fr] items-baseline gap-x-3">
            <span className="font-medium">{c.title}</span>
            <span className={`rounded border px-1.5 text-center text-xs ${CAVEAT_STYLE[c.status]}`}>{c.status}</span>
            <span className="text-neutral-700 dark:text-neutral-300">{c.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

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

  const { data: monthlyData, error: monthlyError } = await supabase
    .from("plant_metrics_monthly")
    .select(MONTHLY_COLUMNS)
    .eq("eia_id", eiaId)
    .order("month", { ascending: true });
  if (monthlyError) throw new Error(monthlyError.message);
  const allMonths = (monthlyData ?? []) as unknown as MonthlyMetric[];
  const months = fullMonths(allMonths);

  const [scoreRes, trRes, pcRes, gasRes, flRes, fbRes, clRes, wlRes, soRes] = await Promise.all([
    supabase.from("plant_scores").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_transmission").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_parcels").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_gas_pipelines").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_flood").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_fiber").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_climate").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_wells").select("*").eq("eia_id", eiaId).maybeSingle(),
    supabase.from("layers_soils").select("*").eq("eia_id", eiaId).maybeSingle(),
  ]);
  const score = (scoreRes.data ?? null) as Score | null;
  const L: Layers = {
    transmission: trRes.data,
    parcels: pcRes.data,
    gas: gasRes.data,
    flood: flRes.data,
    fiber: fbRes.data,
    climate: clRes.data,
    wells: wlRes.data,
    soils: soRes.data,
  };
  const C = L.climate;
  const W = L.wells;
  const S = L.soils;

  const flags: string[] = [];
  if (L.flood?.flood_flag)
    flags.push(
      `Flood: ${pct(n(L.flood.sfha_share), 0)} of the array (${num(n(L.flood.sfha_acres), 0)} acres) in FEMA 1% annual-chance zones (${text(t(L.flood.flood_zones))}); the brief’s kill criterion — shown, not scored`,
    );
  if (score?.thermal_response_test_required)
    flags.push(
      `Soil thermal conductivity estimate ${num(n(S?.soil_lambda_w_mk), 2)} W/m·K is below 1.0: a thermal response test is required before sizing the loop field`,
    );
  if (p.sb6_review_required) flags.push(`SB6 co-location review: planned load ${num(p.planned_load_mw)} MW is at or above the large-load threshold`);
  if (p.tax_equity_consent_likely) flags.push("Tax-equity consent likely (COD ≥ 2019)");
  if (p.itc_recapture_open) flags.push("ITC recapture window may be open (COD within 5 years)");
  if (p.non_ercot_texas) flags.push(`Outside ERCOT balancing authority (${text(p.ba_code)})`);
  if (p.distribution_class_poi) flags.push("Distribution-class POI (at or below the config distribution-voltage limit)");
  if (p.metrics_status !== "ok") flags.push(`Market metrics unavailable: ${metricsStatusLabel(p.metrics_status)}`);
  if (p.peak_hsl_ratio_recent !== null && p.peak_hsl_ratio_recent < 0.75)
    flags.push(
      `Recent output ceiling is ${pct(p.peak_hsl_ratio_recent, 0)} of nameplate over the last 3 full months: possible derate or outage (a price signal, not a defect)`,
    );
  if (p.filter_status === "review") flags.push(`Footprint unresolved: ${text(p.filter_reasons)}`);

  return (
    <main className="mx-auto max-w-5xl px-4 py-6">
      <div className="flex items-center justify-between print:hidden">
        <Link href="/" className="text-sm text-neutral-500 hover:underline">
          ← All plants
        </Link>
        <PrintButton />
      </div>
      <p className="hidden text-xs text-neutral-500 print:block">
        Komorebi Texas Screen · site dossier · printed {new Date().toISOString().slice(0, 10)} · data run {p.run_id}
        {score ? ` · scores ${score.run_id}` : ""} · screening estimates from public data, not diligence findings
      </p>
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

      {score && <ScoreSection s={score} />}
      <CaveatsSection items={caveats(p, score)} />

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
          title={`Market (SCED + real-time SPP${p.metrics_window_start ? `, ${p.metrics_window_start} to ${p.metrics_window_end}` : ""})`}
          rows={[
            ["Status", metricsStatusLabel(p.metrics_status), p.metrics_window_months ? `${p.metrics_window_months} full months` : undefined],
            ["Capture rate (potential)", num(p.capture_rate_potential, 2), "HSL-weighted node price ÷ hub average; the scored measure"],
            ["Capture rate (delivered)", num(p.capture_rate, 2), "flattered by curtailment"],
            ["Shape × basis", `${num(p.shape_capture, 2)} × ${num(p.basis_ratio, 2)}`, "solar-hours discount × location discount (delivered)"],
            ["Curtailment", pct(p.curtailment_pct), "share of available output dispatched down"],
            ["Capacity factor", `${pct(p.sced_net_cf)} net · ${pct(p.sced_potential_cf)} potential`, `EIA-923 ${p.cf_year ?? ""}: ${pct(p.net_ac_cf)}`],
            ["Peak HSL ÷ nameplate", `${num(p.peak_hsl_ratio, 2)} window · ${num(p.peak_hsl_ratio_recent, 2)} last 3 months`, "low = derated, part-built or out"],
            ["ERCOT resource(s)", text(p.ercot_resources), p.ercot_resource_shared ? "unit shared with another EIA plant" : undefined],
            ["Settlement point", text(p.ercot_settlement_points)],
          ]}
        />
        <Section
          title="Site (Phase 4 layers)"
          rows={[
            [
              "345 kV substation",
              L.transmission ? (n(L.transmission.dist_345kv_sub_mi) !== null ? `${num(n(L.transmission.dist_345kv_sub_mi))} mi` : `none within ${num(n(L.transmission.transmission_search_mi), 0)} mi`) : "—",
              text(t(L.transmission?.nearest_345kv_sub_name)),
            ],
            ["Voltages within 3 mi", text(t(L.transmission?.kv_classes_within_near)), L.transmission ? `source: ${text(t(L.transmission.transmission_source))}` : undefined],
            [
              "Host parcels",
              L.parcels ? `${num(n(L.parcels.parcel_acres_host), 0)} acres · ${num(n(L.parcels.acres_per_mw_parcel))} ac/MW` : "—",
              L.parcels ? `status ${text(t(L.parcels.parcel_status))}; headroom ${pct(n(L.parcels.headroom_pct_unified), 0)} with same-owner land` : undefined,
            ],
            ["Land control", L.parcels ? yesNo(L.parcels.unified_land_control as boolean | null) : "—", text(t(L.parcels?.host_owners))],
            [
              "Host parcel IDs",
              text(t(L.parcels?.host_parcel_ids)?.split(";").join(", ") ?? null),
              L.parcels ? `${text(t(L.parcels.county ?? p.county))} County appraisal district, largest first; StratMap ${text(t(L.parcels.parcel_vintage))}` : undefined,
            ],
            [
              "Gas transmission line",
              L.gas ? (n(L.gas.dist_gas_transmission_mi) !== null ? `${num(n(L.gas.dist_gas_transmission_mi))} mi` : `none within ${num(n(L.gas.gas_search_mi), 0)} mi`) : "—",
              L.gas && n(L.gas.dist_gas_transmission_mi) !== null
                ? `${text(t(L.gas.gas_transmission_operator))} · ${num(n(L.gas.gas_transmission_diameter_in), 0)}″${L.gas.gas_transmission_interstate ? " · interstate" : ""}`
                : undefined,
            ],
            ["Any gas line", L.gas ? (n(L.gas.dist_gas_any_mi) !== null ? `${num(n(L.gas.dist_gas_any_mi))} mi` : "—") : "—", "gathering included; RRC QPipelines (display only)"],
            [
              "Fiber lateral (proxy)",
              L.fiber ? (n(L.fiber.fiber_lateral_miles) !== null ? `${num(n(L.fiber.fiber_lateral_miles))} mi to ${L.fiber.fiber_corridor === "rail" ? `${text(t(L.fiber.nearest_rail_owner))} mainline` : text(t(L.fiber.nearest_interstate))}` : `no corridor within ${num(n(L.fiber.fiber_search_mi), 0)} mi`) : "—",
              L.fiber
                ? `Class I rail ${n(L.fiber.dist_class1_rail_mi) !== null ? `${num(n(L.fiber.dist_class1_rail_mi))} mi` : "none near"} · interstate ${n(L.fiber.dist_interstate_mi) !== null ? `${num(n(L.fiber.dist_interstate_mi))} mi` : "none near"} · confidence low (routes are not public)`
                : undefined,
            ],
            [
              "Carrier hotel latency (est.)",
              L.fiber && n(L.fiber.latency_rtt_ms_est) !== null ? `~${num(n(L.fiber.latency_rtt_ms_est))} ms RTT to ${text(t(L.fiber.nearest_carrier_hotel))}` : "—",
              L.fiber ? `${text(t(L.fiber.carrier_hotel_rtt_ms))} (estimated route from great circle × config route factor; light in glass only)` : undefined,
            ],
            [
              "FEMA flood zones",
              !L.flood ? "—" : L.flood.flood_status === "not_mapped" ? "No digital flood map: unknown" : `${pct(n(L.flood.sfha_share), 1)} of array in SFHA`,
              L.flood && L.flood.flood_status === "ok"
                ? `zones ${text(t(L.flood.flood_zones))}; 0.2%: ${pct(n(L.flood.x500_share), 1)}; floodway ${pct(n(L.flood.floodway_share), 1)}`
                : undefined,
            ],
          ]}
        />
        <Section
          title={`Climate (NSRDB${C ? ` ${text(t(C.climate_years))}` : ""})`}
          rows={[
            [
              "Hours below 25 °C dry-bulb",
              C ? `${num(n(C.hours_below_25c_drybulb), 0)} h/yr` : "—",
              C ? `mean of the years (scored); worst year ${num(n(C.hours_below_25c_drybulb_min), 0)} · TMY ${num(n(C.hours_below_25c_drybulb_tmy), 0)}` : undefined,
            ],
            ["By year", text(t(C?.hours_below_25c_by_year))],
            [
              "Hours below 20 / 15 °C",
              C ? `${num(n(C.hours_below_20c_drybulb), 0)} / ${num(n(C.hours_below_15c_drybulb), 0)} h/yr` : "—",
            ],
            ["Hours below 20 °C wet-bulb", C ? `${num(n(C.hours_below_20c_wetbulb), 0)} h/yr` : "—", "Stull (2011) from temperature and humidity"],
            ["Hours above 35 °C", C ? `${num(n(C.hours_above_35c_drybulb), 0)} h/yr` : "—", "dry-cooler derate hours"],
            [
              "Design dry-bulb / wet-bulb (0.4%)",
              C ? `${num(n(C.design_drybulb_0p4_c))} / ${num(n(C.design_wetbulb_0p4_c))} °C` : "—",
              C ? `max ${num(n(C.max_drybulb_c))} °C` : undefined,
            ],
            ["Mean annual temperature", C ? `${num(n(C.mean_annual_temp_c))} °C` : "—", "≈ undisturbed ground temperature below ~10 m"],
            [
              "NSRDB cell",
              C ? `${num(n(C.nsrdb_lat), 2)}, ${num(n(C.nsrdb_lon), 2)} · ${num(n(C.nsrdb_elevation_m), 0)} m` : "—",
              C ? `confidence ${text(t(C.climate_confidence))} (modelled MERRA-2 temperature, ~4 km cell)` : undefined,
            ],
          ]}
        />
        <Section
          title="Ground & water (SSURGO soils, TWDB well records)"
          rows={[
            [
              "Soil thermal conductivity λ",
              S && n(S.soil_lambda_w_mk) !== null ? `${num(n(S.soil_lambda_w_mk), 2)} W/m·K` : S ? "no soil data" : "—",
              S && n(S.soil_lambda_w_mk) !== null
                ? `at field capacity, top 2 m (scored); dry ${num(n(S.soil_lambda_dry_w_mk), 2)} · saturated ${num(n(S.soil_lambda_sat_w_mk), 2)}; estimate (Côté & Konrad), a thermal response test decides`
                : undefined,
            ],
            [
              "Soil",
              S ? text(t(S.dominant_soil)) : "—",
              S && n(S.soil_sand_pct) !== null
                ? `sand ${num(n(S.soil_sand_pct), 0)}% · clay ${num(n(S.soil_clay_pct), 0)}% · ${num(n(S.soil_bulk_density), 2)} g/cm³; ${text(t(S.dominant_mapunit))}`
                : undefined,
            ],
            [
              "Restrictive layers (top 2 m)",
              S ? (t(S.restriction_kinds) ? `${text(t(S.restriction_kinds))}` : "none mapped") : "—",
              S && t(S.restriction_kinds)
                ? `${pct(n(S.restriction_share), 0)} of the array; shallowest ${num(n(S.restriction_min_depth_cm), 0)} cm (petrocalcic = caliche hardpan)`
                : undefined,
            ],
            [
              "Thick caliche / gypsum",
              W && n(W.thick_hard_layer_share) !== null ? `${pct(n(W.thick_hard_layer_share), 0)} of driller logs` : "—",
              W
                ? n(W.logs_radius_mi) !== null
                  ? `≥ 20 ft in the top 500 ft (scored); ${num(n(W.n_logs), 0)} logs within ${num(n(W.logs_radius_mi), 0)} mi, median depth ${num(n(W.median_log_depth_ft), 0)} ft`
                  : `only ${num(n(W.n_logs), 0)} logs within 10 mi: no data, scored neutral`
                : undefined,
            ],
            [
              "Any caliche / any gypsum",
              W && n(W.caliche_log_share) !== null ? `${pct(n(W.caliche_log_share), 0)} / ${pct(n(W.gypsum_log_share), 0)} of logs` : "—",
              W && n(W.hard_layer_ft_median) !== null ? `median ${num(n(W.hard_layer_ft_median), 0)} ft, 90th pct ${num(n(W.hard_layer_ft_p90), 0)} ft per log` : undefined,
            ],
            [
              "Hard rock · lost circulation",
              W && n(W.hard_rock_log_share) !== null ? `${pct(n(W.hard_rock_log_share), 0)} · ${pct(n(W.lost_circulation_log_share), 0)} of logs` : "—",
              "≥ 20 ft limestone/dolomite/igneous · lost returns or cavities (display only)",
            ],
            [
              "Depth to water",
              W && n(W.depth_to_water_ft) !== null ? `${num(n(W.depth_to_water_ft), 0)} ft` : "—",
              W && n(W.depth_to_water_ft) !== null
                ? `median (scored); IQR ${num(n(W.depth_to_water_ft_p25), 0)}–${num(n(W.depth_to_water_ft_p75), 0)} ft; ${text(t(W.water_level_sources))} within ${num(n(W.water_radius_mi), 0)} mi; latest ${text(String(W.latest_water_level_year ?? "—"))}`
                : undefined,
            ],
            ["Closed-loop geothermal bores nearby", W ? num(n(W.n_geothermal_bores), 0) : "—", "SDR wells with that proposed use within 10 mi"],
            ["Aquifer · GCD", W ? `${text(t(W.aquifer_majority))} · ${text(t(W.gcd_majority))}` : "—", "most common among nearby TWDB monitored wells"],
            ["Confidence", text(t(W?.wells_confidence)), "free-text driller logs: a screen, not a geotechnical finding"],
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
      <MonthlyCharts rows={months} excluded={allMonths.length - months.length} />
      <p className="mt-6 text-xs text-neutral-500">
        Low capture rate, high curtailment and older modules are price signals and score higher; they are never filtered
        out. Inputs whose layers are not built yet (soil λ, drillability, climate, water table, load pockets) and the
        offtake status score half their points until measured.
      </p>
    </main>
  );
}
