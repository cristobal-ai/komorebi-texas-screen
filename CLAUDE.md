# CLAUDE.md — Komorebi Texas Screen

This file is read by Claude Code at the start of every session, on every machine. Keep it current: when a decision changes or a phase completes, edit this file in the same commit.

## What this repo is

A screening pipeline plus a web app that ranks every utility-scale PV plant in ERCOT as an acquisition target for conversion into an AI compute campus (Komorebi cooling-first model: reuse the array as a ground-coupled cooling asset, put modular compute on the existing generation interconnect). Owner: Cristobal, Smart Investments (Houston). Outside users — JV partners, lenders, brokers — will log into the web app.

The thesis inversion matters everywhere in the code: **poor generation performance is a price signal, not a defect.** Never filter out low capture rate, high curtailment or old modules; they score *higher*.

## Source of truth, in order

1. `docs/build-plan-v2.md` — the current plan. Its §3 amends the brief; where they disagree, the plan wins.
2. `docs/brief.md` — the original specification (filters, tiers, data sources, scoring model, caveats).
3. `pipeline/config.yaml` — every weight, threshold and assumption. Never hard-code a number that belongs here.
4. `docs/adr/` — architecture decisions. Add one when you change stack, hosting or data-source strategy.

## Decisions already made (do not re-open without asking)

- Backend: **Supabase** (Postgres + magic-link auth with an invite-only allowlist + Storage). The database *is* the ranked table.
- Web: **Next.js 15** (App Router, TypeScript) in `web/`, deployed by **Vercel** (team "cristobal-ai's projects"; upgrade to Pro before the first outside invite — Hobby is non-commercial by policy). Auth lives in the app, not at the Vercel edge.
- Pipeline: **Python 3.11** in `pipeline/`, run monthly and on demand by **GitHub Actions** (`.github/workflows/refresh.yml`). It does not run on Vercel.
- Plant master spine: **USPVDB v4.0** polygons joined to EIA-860/860M/923 on `eia_id`. Parcels from **TxGIO StratMap**. POI voltage from the EIA-860 generator file.
- Tiers: T1b ≥ sb6_line (SB6 review path) · T1a 75–sb6_line · T2 25–75 · T3 10–25. **The T1a/T1b line is computed, not typed** (decision 30 Sep 2026): `sb6_line = 75 MW load ÷ (PUE 1.15 × 0.5 MW IT/MWdc × ILR 1.30) = 100.33 MW AC`, in `plant_master.sb6_line_ac_mw`; `config.yaml` tiers use the placeholder `sb6_line`. Tier uses the default ILR; `sb6_review_required` uses each plant's own ILR, so a plant with an unusual ILR can still sit on the "wrong" side of the line — display both.
- Footprint filter (decision 30 Sep 2026, amends plan §3.1): USPVDB `p_area` is array area, not site footprint, so with `hard_filters.footprint_basis: array_flag` the 5 ac/MW and 60-acre checks send a plant to `review` (reasons prefixed `array_`), not `fail`. Phase 4 switches to `parcel` and applies the same thresholds to TxGIO parcel area. Phases 3–4 carry `pass` + `review` plants; only `pass` is ranked. Section C acres/MW bands need rescaling at the same time.
- Validation: Pecos County first (Komorebi's published planning case), then one eastern county (Falls or Milam).

## Known data constraints

- ERCOT Public API history starts **2023-12-11**. A 36-month lookback from Sept 2026 reaches back to Oct 2023, so the SCED/SPP window is ~33 months unless backfilled from ERCOT MIS archives. Store `metrics_window_months` per plant.
- EIA-923 is monthly for a sample of ~1,900 plants and annual for the rest; many T3 plants will be annual-only. Populate `cf_series_resolution` ('monthly' | 'annual') and never let nulls reach the composite score.
- ≥10 MW AC does not guarantee SCED telemetry. Set `sced_coverage` per plant from what the disclosure actually returns.
- EIA-860 does not distinguish poly- from mono-crystalline; module type is a vintage proxy with `module_type_confidence = 'proxy'`.
- Long-haul fiber routes are not public; the fiber layer is a proxy with `fiber_confidence = 'low'`.
- ERCOT's large-load queue is not a downloadable dataset until the PUCT transparency rule lands (due Dec 2026); the load-pocket layer is a hand-maintained table marked `manual`.

## Working across machines

This repo is cloned on more than one computer (Windows, Mac, Mac Mini). Rules:

- Start every session with `git pull`. End every session with a commit and `git push` — even for work in progress; use a branch if it is not ready for `main`.
- Secrets never enter the repo. Local runs read `pipeline/.env` and `web/.env.local` (both gitignored); copy from `.env.example` on each machine. CI reads GitHub Actions secrets; Vercel reads its own environment variables.
- `data/` is gitignored except `data/crosswalk_eia_ercot.csv` and `data/README.md`. Raw downloads are cached locally per machine and, once the pipeline runs in CI, in Supabase Storage.
- The hand-verified crosswalk CSV is precious: commit it whenever it changes.
- The Claude.ai project "Komorebi TEXAS" holds the same two docs for Cowork sessions; the repo copies are the ones the code is built from. If you change the plan here, note it so the project copy can be refreshed.

## Conventions

- Python: `pandas`, `geopandas`, `shapely`, `pyarrow`; `gridstatus` for ERCOT; `rapidfuzz` for the crosswalk. Cache every raw download to Parquet under `data/raw/<source>/` before transforming. One module per phase under `pipeline/phase_N_*`; a phase reads the previous phase's Parquet and writes its own. Tests in `pipeline/tests/` with `pytest`.
- Every plant-level field that is estimated or hand-entered carries a sibling `<field>_confidence` or `<field>_source` column. Display these; never score them.
- Scores are stored as sub-scores (A–F) plus the composite, so the web app can re-rank client-side when a user moves a weight slider.
- Web: Tailwind + shadcn/ui, TanStack Table, MapLibre GL (free vector basemap; no Mapbox token), Recharts, `@supabase/ssr`, `exceljs` for export. Scaffold with `create-next-app`; do not hand-write the boilerplate.
- Commit messages: short imperative subject, body says what changed and why. Reference the phase (e.g. "phase 2: join USPVDB polygons to EIA-860M").

## Commands

```bash
# Pipeline (from repo root)
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r pipeline/requirements.txt
cp pipeline/.env.example pipeline/.env                # then fill in keys
python -m pipeline.check_env --live                   # Phase 0: which secrets are set, and do they work
python -m pipeline.run --phase 1-2 --county Pecos     # download → plant master → data/validation/pecos_handcheck.md
pytest

# Web
cd web && cp .env.example .env.local && npm install && npm run dev
```

## Status

- Phase 0 (accounts & keys): **in progress** — repo created 30 Sep 2026. Windows `pipeline/.env`: 7/7 secrets set, EIA / ERCOT / Supabase live ok (30 Sep). Supabase connector connected. Vercel team exists (no project linked; Pro upgrade unconfirmed). GitHub Actions secrets not yet added. Run `python -m pipeline.check_env --live` on each machine and add the same names as GitHub Actions secrets.
- Phase 1–2 (plant master): **first real run 30 Sep 2026 (Windows)** — USPVDB v4.0 (`uspvdb_v4_0_20260414.shp`, 6,611 polygons, 187 TX) + EIA-860M Aug 2026 (Sept not yet published) → 243 TX PV plants: 68 pass, 1 review, 174 fail; 2 TX polygons with no operating EIA plant. After the footprint/tier-line change (re-run 30 Sep): 68 pass / 9,218.8 MW (T1b 42 / 8,140.6 · T1a 6 / 561.1 · T2 9 / 394.9 · T3 11 / 122.2), 10 review / 1,853.0 MW (9 array-acreage, 1 no polygon), 165 fail. All 42 passing T1b carry SB6; 1 of 6 T1a carries SB6 (a 100.0 MW plant with ILR > 1.30). Pecos: 13 plants / 2,177.5 MW; 9 pass / 1,304.7; 1 review (Greasewood Solar, 255 MW, 4.9 ac/MW array); 3 fail on COD / 617.8; 13/13 matched to USPVDB, EIA vs USPVDB AC within ±5%. **Pecos signed off by owner 30 Sep 2026.**
- EIA-860 annual + EIA-923 (first real run 30 Sep, Windows; EIA-923 data year 2025): Pecos 13/13 have POI kV (4 × 345, 9 × 138), tracking, module and a 2025 CF; pass/review CF median 23.1%, range 18.3% (Greasewood Solar) – 27.0%. Open: `bifacial_share` empty for all 13 (column name or blank answers — check the run log); Alamo 6 reported dual-axis (verify). Fixed: blank Respondent Frequency cells no longer hide the M/A value (Barilla). `phase_1_ingest/eia_annual.py` fetches the newest `eia860{year}.zip` / `f923_{year}.zip` (year − 1, −2, −3; final, then ER, then archive) → `data/raw/eia860/{plant,solar}.parquet`, `data/raw/eia923/generation.parquet`. `phase_2_normalize/eia_annual.py` adds `grid_voltage_kv` (+ max of Grid Voltage 1–3, `distribution_class_poi`) from the **plant** file (EIA-860 carries grid voltage there, not in the generator file), MW-weighted `tracking_type` / `module_tech` (+ `_share`) and `bifacial_share` from 3_3 Solar, and `net_mwh`, `net_ac_cf`, `cf_year`, `cf_series_resolution` (from EIA-923 Respondent Frequency M/A), `cf_note` (`no_eia923_record` | `partial_year_cod` | `incomplete_months` | `outside_plausible_range`). CF uses actual hours in the year (8,784 in leap years) and only plants operating the whole calendar year. Column lookup is by normalized name; a missing column logs the available ones. `pipeline/phase_1_ingest/{uspvdb,eia860m}.py` cache downloads to `data/raw/`; `pipeline/phase_2_normalize/plant_master.py` rolls EIA-860M TX PV generators to plants, joins USPVDB on `eia_id`, applies hard filters (`filter_status` pass/review/fail + `filter_reasons`, nothing silently dropped), tiers, ILR, `planned_load_mw`, SB6 and COD caveat flags → `data/plants.parquet`; `county_check.py` writes the hand-check report. Claude Code cloud sandboxes cannot reach eia.gov / usgs.gov unless those hosts are allowed in the environment's network settings; run locally or via the `refresh` workflow (Actions → refresh → Run workflow, phase `1-2`, county `Pecos`; report is in the run's artifacts). If the USPVDB URL in `config.yaml` moves, drop the zip in `data/raw/uspvdb/manual/`.
- EIA-860/923 checked 30 Sep: 78/78 pass/review plants have grid voltage and 2025 CF, 77/78 tracking; no missing-column warnings (EIA-860 2025 final). `Bifacial?` exists but is blank for TX plants → `bifacial_share` null (B score uses the vintage proxy). Alamo 6 dual-axis is what EIA reports; verify on imagery.
- Supabase (30 Sep): **schema applied** to project `ouetbhexrewwsxomubdv` (4 migrations in `supabase/migrations/`, names match server versions); owner seeded as `admin` in `users_allowlist`; RLS on; advisors clean. **First load 30 Sep 2026 (run `20260930T220712Z`): 78 rows / 11,071.8 MW** — pass 68 / 9,218.8 (T1b 42 / 8,140.6, T1a 6 / 561.1, T2 9 / 394.9, T3 11 / 122.2), review 10 / 1,853.0; matches the parquet exactly. 77/78 have geometry (valid, centroids in place), 78/78 CF and POI kV. Pass CF median 23.1% (T3 median 16.1% — check in Phase 3). Barilla Solar's null `cf_series_resolution` was an `AM` Respondent Frequency code; `sources.eia923.frequency_map` now maps M→monthly, A/AM→annual (unknown codes warn) — re-run `--phase 2 --load` to update. `supabase/migrations/20260930210716_plants.sql` (postgis, `users_allowlist`, `is_allowlisted()`, `plants` with RLS read for allowlisted users) and `pipeline/load_supabase.py` (`--load` flag on `pipeline.run`).
- Phase W1 (web app v0.1, 1 Oct 2026): **built, not yet deployed.** `web/` scaffolded with `create-next-app@15` (Next 15.5.27, React 19.1, Tailwind 4); `@supabase/ssr` auth with allowlist-gated magic links, middleware redirect, `/` plants table (TanStack v8), `/map` (MapLibre 6 + OpenFreeMap, `plants_map` view — migration `20261001003453`), `/plants/[id]`, `/admin/users`. `npm run build` / lint / tsc clean; signed-out routes redirect to `/login` (smoke-tested). shadcn/ui and exceljs deferred (see `web/README.md`).
- Vercel (1 Oct 2026): project **`komorebi-texas-screen-web`** created by owner in team cristobal-ai's projects (Hobby), Root Directory `web`, linked to the GitHub repo. Production branch `main` fails until W1 is merged (no `web/` on `main` — expected); previews build from `claude/magical-hamilton-tfjm3y`. Preview URLs: `komorebi-texas-screen-web-git-<branch>-cristobal-ais-projects.vercel.app`. The Vercel connector in Claude sessions can read but not create projects (403).
- Next: confirm the branch preview builds, add `https://komorebi-texas-screen-web-*-cristobal-ais-projects.vercel.app/auth/callback` (+ localhost, + production) to Supabase Auth redirect URLs, log in end-to-end; then merge W1 to `main`. Before outside invites: GitHub Actions secrets, Vercel Pro.
- Nothing has been deployed yet.
