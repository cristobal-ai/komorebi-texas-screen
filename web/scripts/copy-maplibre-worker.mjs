// Copies MapLibre's worker (and the shared module it imports) into public/maplibre/. MapLibre 6 finds its worker at
// new URL("./maplibre-gl-worker.mjs", import.meta.url); once Next bundles maplibre into its own chunks that URL points
// nowhere and the worker never loads ("Worker failed to load"): no vector tiles, no GeoJSON, no plants on the map.
// plant-map.tsx calls setWorkerUrl("/maplibre/maplibre-gl-worker.mjs"). Runs before dev and build, so the copy always
// matches the installed version; public/maplibre/ is gitignored.
import { copyFileSync, mkdirSync } from "node:fs";

const dist = new URL("../node_modules/maplibre-gl/dist/", import.meta.url);
const out = new URL("../public/maplibre/", import.meta.url);
mkdirSync(out, { recursive: true });
for (const f of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) copyFileSync(new URL(f, dist), new URL(f, out));
console.log("copy-maplibre-worker: public/maplibre/ ready");
