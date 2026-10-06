"use client";

import { useEffect, useRef } from "react";
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
};

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
// Free vector basemap, no token (OpenFreeMap).
const BASEMAP = "https://tiles.openfreemap.org/styles/liberty";
// Bundled by Next, MapLibre cannot find its worker next to itself; scripts/copy-maplibre-worker.mjs publishes it here.
const WORKER_URL = "/maplibre/maplibre-gl-worker.mjs";
const TEXAS_BOUNDS: [number, number, number, number] = [-106.65, 25.84, -93.51, 36.5];

const tierColorExpr = [
  "match",
  ["get", "tier"],
  ...Object.entries(TIER_COLORS).flat(),
  "#888888",
] as unknown as maplibregl.ExpressionSpecification;

export default function PlantMap({ plants }: { plants: MapPlant[] }) {
  const container = useRef<HTMLDivElement>(null);

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
    });
    const polygons: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: plants
        .filter((p) => p.geojson)
        .map((p) => ({ type: "Feature", geometry: p.geojson!, properties: props(p) })),
    };
    const points: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: plants
        .filter((p) => p.lat !== null && p.lon !== null)
        .map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon!, p.lat!] }, properties: props(p) })),
    };

    maplibregl.setWorkerUrl(WORKER_URL);
    const map = new maplibregl.Map({ container: container.current, style: BASEMAP, bounds: TEXAS_BOUNDS });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");

    map.on("load", () => {
      map.addSource("polygons", { type: "geojson", data: polygons });
      map.addSource("points", { type: "geojson", data: points });
      map.addLayer({
        id: "plant-fill",
        type: "fill",
        source: "polygons",
        minzoom: 9,
        paint: { "fill-color": tierColorExpr, "fill-opacity": 0.55 },
      });
      map.addLayer({
        id: "plant-outline",
        type: "line",
        source: "polygons",
        minzoom: 9,
        paint: { "line-color": tierColorExpr, "line-width": 2 },
      });
      map.addLayer({
        id: "plant-points",
        type: "circle",
        source: "points",
        maxzoom: 11,
        paint: {
          // area ∝ MW: radius ∝ √MW, floor 4 px (8 px marker)
          "circle-radius": ["max", 4, ["*", 0.6, ["sqrt", ["get", "mw"]]]],
          "circle-color": tierColorExpr,
          "circle-opacity": ["case", ["==", ["get", "status"], "review"], 0.45, 0.9],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });

      const popup = (e: maplibregl.MapLayerMouseEvent) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as Record<string, string | number | null>;
        const cf = p.cf === null || p.cf === undefined || p.cf === "" ? "—" : `${(Number(p.cf) * 100).toFixed(1)}%`;
        const el = document.createElement("div");
        el.className = "text-sm text-neutral-900";
        const a = document.createElement("a");
        a.href = `/plants/${p.eia_id}`;
        a.className = "font-semibold underline";
        a.textContent = String(p.name);
        const info = document.createElement("div");
        info.textContent = `${p.tier} · ${Number(p.mw).toLocaleString("en-US", { maximumFractionDigits: 1 })} MW AC · CF ${cf}${p.status === "review" ? " · review" : ""}`;
        const county = document.createElement("div");
        county.className = "text-xs text-neutral-600";
        county.textContent = `${p.county} County`;
        el.append(a, info, county);
        new maplibregl.Popup({ closeButton: true }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
      };
      for (const layer of ["plant-fill", "plant-points"]) {
        map.on("click", layer, popup);
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
    });

    return () => map.remove();
  }, [plants]);

  return (
    <div className="mt-4">
      <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label="Tier legend">
        {(Object.keys(TIER_COLORS) as Tier[]).map((t) => (
          <li key={t} className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-full ring-2 ring-white" style={{ background: TIER_COLORS[t] }} />
            {TIER_LABELS[t]}
          </li>
        ))}
        <li className="text-neutral-500">dot area ∝ MW AC · faded = review</li>
      </ul>
      <div ref={container} className="h-[70vh] w-full rounded border border-neutral-200 dark:border-neutral-800" />
    </div>
  );
}
