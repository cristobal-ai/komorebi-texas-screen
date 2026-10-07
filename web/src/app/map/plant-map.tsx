"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Tier } from "@/lib/plants";

export type MapPlant = {
  eia_id: number;
  plant_name: string;
  county: string | null;
  tier: Tier | null;
  filter_status: "pass" | "review";
  ac_mw: number;
  net_ac_cf: number | null;
  lat: number | null;
  lon: number | null;
  geojson: GeoJSON.Geometry | null;
  score_total: number | null;
  rank_overall: number | null;
};

export type LoadPocketProject = {
  project_id: string;
  name: string;
  developer: string | null;
  kind: string | null;
  status: string | null;
  load_mw: number | null;
  lat: number;
  lon: number;
  location_confidence: string | null;
  qualifies: boolean;
  source_url: string | null;
};

export type Substation = { osm_id: string; name: string | null; operator: string | null; voltage_kv: number | null; lat: number; lon: number };

// Categorical tier colours, validated (dataviz validate_palette.js, --pairs all, light surface — the basemap is
// light in both app themes): worst CVD ΔE 9.2, normal-vision ΔE 16.3. Aqua is < 3:1 on white, so identity is
// never colour-alone: legend labels + popup text + the table view.
export const TIER_COLORS: Record<Tier, string> = {
  T1b: "#2a78d6",
  T1a: "#eb6834",
  T2: "#1baf7a",
  T3: "#4a3aa7",
};
const TIER_LABELS: Record<Tier, string> = {
  T1b: "T1b · Anchor, SB6 path",
  T1a: "T1a · Anchor, sub-threshold",
  T2: "T2 · Mid",
  T3: "T3 · Modular",
};
// Score: ordinal blue ramp (dataviz palette steps 250/350/450/550/650; validate_palette.js --ordinal on the light
// surface: monotone L, light end 2.06:1) — higher score, darker. Bins are score points (positive max 94, F penalty).
const SCORE_BINS: { min: number; label: string; color: string }[] = [
  { min: -Infinity, label: "< 40", color: "#86b6ef" },
  { min: 40, label: "40–50", color: "#5598e7" },
  { min: 50, label: "50–60", color: "#2a78d6" },
  { min: 60, label: "60–70", color: "#1c5cab" },
  { min: 70, label: "≥ 70", color: "#104281" },
];
const NO_SCORE = "#9b9a96";
// Overlays are told apart by SHAPE in neutral ink, not by hue: a fifth categorical hue failed the normal-vision floor
// against the tier orange (validate_palette.js --pairs all: magenta ΔE 12.9, yellow 13.7 < 15). Load pockets = diamonds
// (filled = operating / under construction, hollow = announced); substations = small grey dots; plants = coloured circles.
const POCKET_INK = "#2b2a27";
const SUBSTATION_COLOR = "#7a7974";

/** A diamond marker as image data (MapLibre symbol icon): filled or hollow, with a white halo. */
function diamond(size: number, filled: boolean): ImageData {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d")!;
  const m = size / 2;
  const path = (r: number) => {
    g.beginPath();
    g.moveTo(m, m - r);
    g.lineTo(m + r, m);
    g.lineTo(m, m + r);
    g.lineTo(m - r, m);
    g.closePath();
  };
  path(m - 1);
  g.fillStyle = "#ffffff";
  g.fill();
  path(m - 3.5);
  g.fillStyle = filled ? POCKET_INK : "#ffffff";
  g.fill();
  g.lineWidth = 2.5;
  g.strokeStyle = POCKET_INK;
  g.stroke();
  return g.getImageData(0, 0, size, size);
}

// Free vector basemap, no token (OpenFreeMap).
const BASEMAP = "https://tiles.openfreemap.org/styles/liberty";
// Bundled by Next, MapLibre cannot find its worker next to itself; scripts/copy-maplibre-worker.mjs publishes it here.
const WORKER_URL = "/maplibre/maplibre-gl-worker.mjs";
const TEXAS_BOUNDS: [number, number, number, number] = [-106.65, 25.84, -93.51, 36.5];

type ColorMode = "score" | "tier";
const E = (x: unknown) => x as maplibregl.ExpressionSpecification;

const tierColorExpr = E(["match", ["get", "tier"], ...Object.entries(TIER_COLORS).flat(), NO_SCORE]);
const scoreColorExpr = E([
  "case",
  ["==", ["get", "score"], null],
  NO_SCORE,
  ["step", ["get", "score"], SCORE_BINS[0].color, ...SCORE_BINS.slice(1).flatMap((b) => [b.min, b.color])],
]);
const colorExpr = (m: ColorMode) => (m === "score" ? scoreColorExpr : tierColorExpr);

const fmt = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });

function el(tag: string, text: string, className?: string) {
  const e = document.createElement(tag);
  e.textContent = text;
  if (className) e.className = className;
  return e;
}

export default function PlantMap({
  plants,
  pockets,
  substations,
}: {
  plants: MapPlant[];
  pockets: LoadPocketProject[];
  substations: Substation[];
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [mode, setMode] = useState<ColorMode>("score");
  const [showPockets, setShowPockets] = useState(true);
  const [showSubs, setShowSubs] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!container.current) return;
    const props = (p: MapPlant) => ({
      eia_id: p.eia_id,
      name: p.plant_name,
      county: p.county ?? "",
      tier: p.tier ?? "",
      status: p.filter_status,
      mw: p.ac_mw,
      cf: p.net_ac_cf,
      score: p.score_total,
      rank: p.rank_overall,
    });
    const polygons: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: plants
        .filter((p) => p.geojson)
        .map((p) => ({ type: "Feature", geometry: p.geojson!, properties: props(p) })),
    };
    const points: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      // higher scores drawn last (on top) where dots overlap
      features: plants
        .filter((p) => p.lat !== null && p.lon !== null)
        .sort((a, b) => (a.score_total ?? -1) - (b.score_total ?? -1))
        .map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon!, p.lat!] }, properties: props(p) })),
    };
    const pocketFc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: pockets
        .filter((q) => q.qualifies)
        .map((q) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [q.lon, q.lat] },
          properties: { ...q, firm: q.status === "operating" || q.status === "under_construction" },
        })),
    };
    const subFc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: substations.map((s) => ({ type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: s })),
    };

    maplibregl.setWorkerUrl(WORKER_URL);
    const map = new maplibregl.Map({ container: container.current, style: BASEMAP, bounds: TEXAS_BOUNDS });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");

    map.on("load", () => {
      map.addSource("polygons", { type: "geojson", data: polygons });
      map.addSource("points", { type: "geojson", data: points });
      map.addSource("pockets", { type: "geojson", data: pocketFc });
      map.addSource("substations", { type: "geojson", data: subFc });
      // substations under the plants; load-pocket diamonds above them (small, and they must stay visible)
      map.addLayer({
        id: "substations",
        type: "circle",
        source: "substations",
        layout: { visibility: "none" },
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 2.5, 10, 5],
          "circle-color": SUBSTATION_COLOR,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
        },
      });
      map.addImage("pocket-firm", diamond(20, true), { pixelRatio: 1.25 });
      map.addImage("pocket-announced", diamond(20, false), { pixelRatio: 1.25 });
      map.addLayer({
        id: "pockets",
        type: "symbol",
        source: "pockets",
        layout: {
          "icon-image": ["case", ["get", "firm"], "pocket-firm", "pocket-announced"],
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
      });
      map.addLayer({
        id: "plant-fill",
        type: "fill",
        source: "polygons",
        minzoom: 9,
        paint: { "fill-color": scoreColorExpr, "fill-opacity": 0.6 },
      });
      map.addLayer({
        id: "plant-outline",
        type: "line",
        source: "polygons",
        minzoom: 9,
        paint: { "line-color": scoreColorExpr, "line-width": 2 },
      });
      map.addLayer({
        id: "plant-points",
        type: "circle",
        source: "points",
        maxzoom: 11,
        paint: {
          // area ∝ MW: radius ∝ √MW, floor 4 px (8 px marker)
          "circle-radius": ["max", 4, ["*", 0.6, ["sqrt", ["get", "mw"]]]],
          "circle-color": scoreColorExpr,
          "circle-opacity": ["case", ["==", ["get", "status"], "review"], 0.45, 0.95],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });
      map.moveLayer("pockets");   // diamonds on top of the plant dots

      const show = (lngLat: maplibregl.LngLat, nodes: HTMLElement[]) => {
        const box = document.createElement("div");
        box.className = "text-sm text-neutral-900";
        box.append(...nodes);
        new maplibregl.Popup({ closeButton: true }).setLngLat(lngLat).setDOMContent(box).addTo(map);
      };
      const plantPopup = (e: maplibregl.MapLayerMouseEvent) => {
        const p = e.features?.[0]?.properties as Record<string, string | number | null> | undefined;
        if (!p) return;
        const a = el("a", String(p.name), "font-semibold underline") as HTMLAnchorElement;
        a.href = `/plants/${p.eia_id}`;
        const score =
          p.score === null || p.score === undefined || p.score === ""
            ? "not scored"
            : `score ${fmt(Number(p.score), 1)}${p.rank ? ` · rank ${p.rank}` : " · review (unranked)"}`;
        const cf = p.cf === null || p.cf === undefined || p.cf === "" ? "—" : `${(Number(p.cf) * 100).toFixed(1)}%`;
        show(e.lngLat, [
          a,
          el("div", score),
          el("div", `${p.tier} · ${fmt(Number(p.mw), 1)} MW AC · CF ${cf}`),
          el("div", `${p.county} County`, "text-xs text-neutral-600"),
        ]);
      };
      const pocketPopup = (e: maplibregl.MapLayerMouseEvent) => {
        const q = e.features?.[0]?.properties as Record<string, string | number | boolean | null> | undefined;
        if (!q) return;
        const nodes = [
          el("div", String(q.name), "font-semibold"),
          el("div", `${String(q.kind ?? "").replace("_", " ")} · ${String(q.status ?? "").replace("_", " ")}${q.load_mw ? ` · ${fmt(Number(q.load_mw))} MW` : ""}`),
          el("div", `${q.developer ? `${q.developer} · ` : ""}location: ${q.location_confidence} (approximate unless 'site')`, "text-xs text-neutral-600"),
        ];
        if (q.source_url) {
          const a = el("a", "source", "text-xs underline") as HTMLAnchorElement;
          a.href = String(q.source_url);
          a.target = "_blank";
          a.rel = "noreferrer";
          nodes.push(a);
        }
        show(e.lngLat, nodes);
      };
      const subPopup = (e: maplibregl.MapLayerMouseEvent) => {
        const s = e.features?.[0]?.properties as Record<string, string | number | null> | undefined;
        if (!s) return;
        show(e.lngLat, [
          el("div", String(s.name ?? "Unnamed substation"), "font-semibold"),
          el("div", `${fmt(Number(s.voltage_kv))} kV${s.operator ? ` · ${s.operator}` : ""}`),
          el("div", "OpenStreetMap", "text-xs text-neutral-600"),
        ]);
      };
      const handlers: [string, (e: maplibregl.MapLayerMouseEvent) => void][] = [
        ["plant-fill", plantPopup],
        ["plant-points", plantPopup],
        ["pockets", pocketPopup],
        ["substations", subPopup],
      ];
      for (const [layer, fn] of handlers) {
        map.on("click", layer, fn);
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
      setReady(true);
    });

    return () => {
      mapRef.current = null;
      setReady(false);
      map.remove();
    };
  }, [plants, pockets, substations]);

  // colour mode and overlay toggles restyle the live map (no reload)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    for (const [layer, prop] of [
      ["plant-points", "circle-color"],
      ["plant-fill", "fill-color"],
      ["plant-outline", "line-color"],
    ] as const)
      map.setPaintProperty(layer, prop, colorExpr(mode));
    map.setLayoutProperty("pockets", "visibility", showPockets ? "visible" : "none");
    map.setLayoutProperty("substations", "visibility", showSubs ? "visible" : "none");
  }, [mode, showPockets, showSubs, ready]);

  const nPockets = pockets.filter((q) => q.qualifies).length;
  return (
    <div className="mt-4">
      <div className="mb-2 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <fieldset className="flex items-center gap-2">
          <legend className="sr-only">Colour plants by</legend>
          <span className="text-neutral-600 dark:text-neutral-400">Colour by</span>
          {(["score", "tier"] as const).map((m) => (
            <label key={m} className="flex items-center gap-1">
              <input type="radio" name="colour" checked={mode === m} onChange={() => setMode(m)} />
              {m === "score" ? "Score" : "Tier"}
            </label>
          ))}
        </fieldset>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showPockets} onChange={(e) => setShowPockets(e.target.checked)} />
          Load pockets ({nPockets})
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showSubs} onChange={(e) => setShowSubs(e.target.checked)} />
          345 kV substations ({substations.length})
        </label>
      </div>
      <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label="Map legend">
        {mode === "score"
          ? SCORE_BINS.map((b) => (
              <li key={b.label} className="flex items-center gap-1.5">
                <span className="inline-block h-3 w-3 rounded-full ring-2 ring-white" style={{ background: b.color }} />
                {b.label}
              </li>
            ))
          : (Object.keys(TIER_COLORS) as Tier[]).map((t) => (
              <li key={t} className="flex items-center gap-1.5">
                <span className="inline-block h-3 w-3 rounded-full ring-2 ring-white" style={{ background: TIER_COLORS[t] }} />
                {TIER_LABELS[t]}
              </li>
            ))}
        {showPockets && (
          <li className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rotate-45" style={{ background: POCKET_INK }} />
            load pocket (operating / building)
            <span className="ml-1 inline-block h-2.5 w-2.5 rotate-45 border-2 bg-white" style={{ borderColor: POCKET_INK }} />
            announced
          </li>
        )}
        {showSubs && (
          <li className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: SUBSTATION_COLOR }} />
            345 kV substation
          </li>
        )}
        <li className="text-neutral-500">
          {mode === "score" ? "score points (darker = higher)" : "tier"} · dot area ∝ MW AC · faded = review
        </li>
      </ul>
      <div ref={container} className="h-[70vh] w-full rounded border border-neutral-200 dark:border-neutral-800" />
    </div>
  );
}
