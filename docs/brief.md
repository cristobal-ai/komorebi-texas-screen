# Texas PV-to-Datacenter Conversion Screen — Build Brief

**Prepared for:** Cristobal / Smart Investments
**Purpose:** Hand this document to Claude Cowork as the specification for building a screening pipeline.
**Date:** August 2026

> Repo note (30 Sep 2026): this is the original brief, copied verbatim from the Claude project "Komorebi TEXAS". It is amended by `docs/build-plan-v2.md` §3 — where the two disagree, the build plan wins.

---

## 1. Objective

Build a repeatable data pipeline that ranks every utility-scale PV plant in ERCOT as an **acquisition target for conversion to an AI compute campus**, following the Komorebi cooling-first model (dual-use the existing array as a 24/7 cooling asset, drop modular compute onto the existing generation interconnect).

**Output:** a ranked table of Texas PV plants with a composite suitability score, plus one-page site dossiers for the top 20–25.

### What "good" looks like

The ideal target is a plant that is **cheap to buy** (financially underperforming) but **expensive to replicate** (large contiguous land, energized high-voltage interconnect, favorable soils and climate for ground-coupled cooling).

Critically: **poor generation performance is a price signal, not a defect.** Do not filter it out. A congested West Texas plant with a 55% capture rate is a *better* target than a fully contracted plant at 100% of P50, because the acquisition basis is lower and the value being purchased is the interconnect, land, and thermal mass — not the energy revenue.

### Thesis inversion to encode

| Variable | Generation thesis | Conversion thesis (use this) |
|---|---|---|
| Old polycrystalline / monofacial modules | Liability | **Asset** — repowering + module recovery upside |
| Vintage 2016–2021 | Aging | **Target zone** |
| Low capture rate / high curtailment | Deal killer | **Discount driver** |
| Long-term investment-grade PPA | Premium | **Encumbrance** — must be bought out or served |
| Merchant exposure | Risk | **Preferred** — cleaner to restructure |
| Remote / low-load-pocket siting | Bad | **Neutral to good** if fiber and gas exist |

---

## 2. Hard filters (kill criteria — apply first)

Drop any plant failing these:

1. **Capacity ≥ 10 MW AC.** The thermal asset is valued as a fraction of compute-side cooling load, not in absolute terms, so the ratio holds at small scale. At Komorebi's stated 50 MW firm IT per 100 MWdc (0.5 MW IT per MWdc), a 10 MW AC plant at 1.30 ILR ≈ 13 MWdc ≈ **6.5 MW of firm IT** — a viable single modular hall.

   *Why 10 MW AC and not lower:* this approximates the ERCOT threshold above which a plant registers as a dispatchable Generation Resource. Below it, plants are typically Settlement-Only Generators, which are not SCED-dispatched and therefore carry **no Base Point / HSL telemetry** — no curtailment metric, no resource-node capture rate. The data floor and the economic floor coincide. Verify the current threshold in ERCOT Nodal Protocols §16 before finalizing.

2. **Commercial operation date between 2015-01-01 and 2022-12-31.** Older = degraded infrastructure. Newer = seller won't discount, and ITC recapture window may still be open (5-year vesting from placed-in-service).
3. **Located in ERCOT.** Exclude El Paso Electric territory and the SPP portions of the Panhandle. Flag but do not auto-drop.
4. **Contiguous site footprint ≥ 5 acres per MW AC, minimum 60 acres.** Use a ratio rather than an absolute, since land use scales with array area (Texas single-axis-tracker PV typically runs 6–9 acres/MW AC). The array *is* the cooling asset, so acreage and thermal capacity scale together — an absolute acreage floor would double-count the capacity filter.
5. **Technology = utility-scale PV.** Exclude CSP, rooftop, community solar aggregations.

### 2.1 Size tiers

Do not rank a 12 MW plant against a 200 MW plant on one undifferentiated list. Assign a tier and report rankings **within tier** as well as overall.

| Tier | AC capacity | Approx. firm IT | Primary use case |
|---|---|---|---|
| **T1 — Anchor** | ≥75 MW AC | ≥48 MW | Hyperscale offtake, multi-hall campus |
| **T2 — Mid** | 25–75 MW AC | 16–48 MW | Single-tenant enterprise or regional AI |
| **T3 — Modular** | 10–25 MW AC | 6.5–16 MW | Single hall, edge inference, pilot / proof site |

T3 sites are strategically useful as **low-cost pilots** — cheap enough to acquire and prove the Komorebi Phase 1 thermal architecture before committing capital to a T1 asset. Score them on that basis, not on absolute compute value.

---

## 3. Data sources

### 3.1 Generation and asset fundamentals

| Source | What it gives | Access |
|---|---|---|
| **EIA-860M** (monthly) | AC nameplate, COD, county, operator, status | `eia.gov/electricity/data/eia860m/` — direct XLSX download |
| **EIA-860** (annual) | **DC capacity, tracking type (fixed/1-axis/2-axis), tilt, azimuth, module technology (crystalline Si / thin film), bifaciality** | `eia.gov/electricity/data/eia860/` — Schedule 3 solar table |
| **EIA-923** (monthly) | Net generation MWh by plant | `eia.gov/electricity/data/eia923/` |
| **EIA API v2** | Programmatic access to the above | `api.eia.gov` — requires free API key registration |

> EIA-860 Schedule 3 is the single most important file for this thesis and the one most people skip. It is where module technology and tracking type live. Without it you cannot identify repowering candidates or normalize capacity factor by inverter loading ratio.

> **Small-plant data caveat — verify before building Phase 3.** EIA-923 monthly generation reporting applies only to plants above a size threshold; smaller plants report on an annual cycle. With the filter at 10 MW AC, a large share of T3 candidates will likely have **annual generation only**, not a monthly series. Confirm the current threshold against EIA-923 survey instructions, and if it bites: degrade gracefully — compute annual capacity factor for those plants, populate `cf_series_resolution = 'annual'`, and exclude them from any month-over-month or seasonal analysis rather than letting nulls propagate into the composite score. The same applies to ERCOT SCED coverage for Settlement-Only Generators (see §2, filter 1).

**Derived fields:**
- `ilr = dc_capacity_mw / ac_capacity_mw`
- `net_ac_cf = annual_mwh / (ac_capacity_mw * 8760)`
- `cf_delta = net_ac_cf - regional_benchmark_cf`

### 3.2 ERCOT market data

| Source | What it gives | Access |
|---|---|---|
| **60-Day SCED Disclosure Reports** | Per-resource Base Point and HSL at 5-min. Curtailment = HSL − Base Point | `ercot.com/mp/data-products` — ERCOT Public API at `api.ercot.com` requires free registration |
| **Settlement Point Prices (Resource Node)** | Realized price per plant | Same API |
| **Monthly GIS Report** | Interconnection queue, POI substation names, voltage class, COD history | `ercot.com` public reports (XLSX) |
| **GridStatus.io** | Convenience wrapper over ERCOT data, easier than raw API | `gridstatus.io` — has a Python client, free tier available |

**Derived fields:**
- `curtailment_pct = sum(HSL - BasePoint) / sum(HSL)`
- `capture_rate = (generation-weighted realized price) / (simple average hub price, same period)`

Benchmark: standalone ERCOT solar captured ~57% of average locational price in H1 2025; co-located sites captured ~72%. Score anything below 60% as high-discount.

### 3.3 Datacenter suitability layers (the differentiating work)

| Layer | Source | Notes |
|---|---|---|
| **Soil thermal conductivity** | USDA NRCS **SSURGO** database — `websoilsurvey.nrcs.usda.gov`, or `soilDB` R package / Python via SDA REST API | Extract texture class, bulk density, % clay/sand, available water capacity. Convert to estimated λ (W/m·K) — see §5. |
| **Depth to groundwater / drillability** | **TWDB Groundwater Database** and Submitted Driller's Reports — `twdb.texas.gov/groundwater/data/` | Well logs give lithology and depth to water directly. Also identifies caliche and gypsum layers. |
| **Aquifer + GCD boundaries** | TWDB major/minor aquifer GIS; Groundwater Conservation District boundaries | Permitting constraint even at low WUE |
| **Climate / TMY** | **NREL NSRDB** — `nsrdb.nrel.gov`, free API | Pull dry-bulb and wet-bulb hourly. Komorebi's Pecos case uses 2024 NSRDB. Compute annual hours below key dry-bulb thresholds. |
| **Transmission voltage class** | ERCOT GIS Report + **HIFLD** transmission line shapefiles (`hifld-geoplatform.opendata.arcgis.com`) | 345 kV strongly preferred over 138 kV for load import |
| **Long-haul fiber** | FCC National Broadband Map; HIFLD; carrier route maps (Lumen, Zayo, Cogent, Uniti) | Compute great-circle and routed distance to nearest long-haul route |
| **Natural gas pipelines** | **Texas RRC GIS** — `rrc.texas.gov/resource-center/research/gis-viewer/` | Intrastate pipeline proximity for bridge/backup generation |
| **Flood risk** | FEMA National Flood Hazard Layer | Kill criterion if substantial footprint in Zone A/AE |
| **Latency** | Compute routed distance to Dallas (Infomart), Houston, San Antonio carrier hotels | Proxy: fiber-route miles × 1.5 → approx. ms RTT |
| **Parcel / acreage** | County Appraisal District GIS (Pecos, Ector, Ward, Andrews, Crane, Concho, Tom Green, Falls, Milam, Hill, Fort Bend, Brazoria) | Also gives assessed value trend, protests, delinquencies |

---

## 4. Pipeline architecture

Build in phases. Do not attempt the whole thing in one script.

```
phase_1_ingest/       # download + cache raw sources to local parquet
phase_2_normalize/    # join EIA 860/860M/923 into one plant master table
phase_3_market/       # ERCOT SCED + SPP → curtailment, capture rate per plant
phase_4_geo/          # geospatial joins: soils, fiber, gas, transmission, flood
phase_5_score/        # composite scoring model
phase_6_report/       # ranked XLSX + per-site markdown dossiers
```

**Recommended stack:** Python 3.11+, `pandas`, `geopandas`, `shapely`, `requests`, `pyarrow`, `openpyxl`. Cache every raw download to disk — the EIA and ERCOT files are large and the APIs rate-limit.

**Plant identity join key problem (expect to spend real time here):** EIA plant codes do not map cleanly to ERCOT resource names. Build a crosswalk table using county + nameplate capacity + COD as a fuzzy match, then hand-verify. Persist the crosswalk as a CSV that survives re-runs. This is the single most likely place the pipeline breaks.

---

## 5. Scoring model

Composite score, 0–100. Weights are a starting point — expose them as config so they can be tuned.

### A. Acquisition discount (30 pts) — lower performance scores higher

| Metric | Points | Logic |
|---|---|---|
| Capture rate | 12 | <55% → 12 pts; 55–65% → 8; 65–75% → 4; >75% → 0 |
| Curtailment % | 8 | >20% → 8 pts; 10–20% → 5; <10% → 2 |
| CF vs regional benchmark | 6 | More than 4 pts below → 6; 2–4 below → 4; at/above → 1 |
| Offtake status | 4 | Merchant → 4; short PPA (<5 yr remaining) → 3; long IG PPA → 0 |

### B. Repowering / module recovery upside (15 pts)

| Metric | Points |
|---|---|
| Polycrystalline modules | 6 |
| Monofacial | 5 |
| Fixed-tilt or single-axis vintage tracker | 4 |

### C. Physical envelope (20 pts)

Score acreage on **surplus above the array footprint**, not absolute size — this is what makes the metric tier-neutral.

| Metric | Points |
|---|---|
| Acres per MW AC: >9 → 8; 7–9 → 6; 5–7 → 3 | 8 |
| Expansion headroom: adjacent uncommitted contiguous land under same or acquirable ownership (>50% of array footprint → 7; 20–50% → 4; none → 0) | 7 |
| Single ownership / unified land control | 5 |

### D. Electrical (20 pts)

| Metric | Points |
|---|---|
| POI voltage: 345 kV → 10; 138 kV → 6; 69 kV → 2; ≤34.5 kV (distribution-class) → 0 | 10 |
| Distance to nearest 345 kV substation (<5 mi → 5; 5–15 mi → 2) | 5 |
| Load-pocket proximity (near existing large load or announced DC development) | 5 |

> **Tier interaction:** T3 plants frequently interconnect at 34.5 kV or 69 kV distribution class. Import capability at a distribution-class POI is materially more constrained than at transmission voltage, and the upgrade cost to reach transmission-class service can exceed the acquisition price of the plant itself. Add a hard flag `distribution_class_poi = True` and surface it at the top of any T3 dossier. A cheap T3 site behind 34.5 kV is usually not a bargain.

### F. Fixed-cost drag (penalty, −15 to 0 pts)

Site-development costs are near-constant regardless of plant size, so they fall hardest on T3. Compute and subtract:

```
fixed_cost_est = ercot_study + title_and_mineral + substation_mod
               + (fiber_lateral_miles × $100k)      # range $50–150k/mi
               + legal_and_transaction
fixed_cost_per_kw_it = fixed_cost_est / (firm_it_mw × 1000)
```

| `fixed_cost_per_kw_it` | Penalty |
|---|---|
| <$100/kW | 0 |
| $100–250/kW | −5 |
| $250–450/kW | −10 |
| >$450/kW | −15 |

Illustrative: a $3M fixed-cost load against 6.5 MW firm IT is ~$460/kW; the same $3M against 50 MW is ~$60/kW. That ~8x spread is the real reason small sites must clear a higher bar on every other axis, and it should be visible in the score rather than buried in a footnote.

### E. Thermal & cooling suitability (15 pts) — the Komorebi-specific layer

| Metric | Points |
|---|---|
| Estimated soil thermal conductivity λ (see below) | 6 |
| Drillability — absence of thick caliche/gypsum refusal layers per TWDB driller logs | 4 |
| Annual hours below 25 °C dry-bulb (NSRDB) | 3 |
| Depth to water table (deeper = better for dry loop fields, but check drilling cost) | 2 |

**Estimating λ from SSURGO** (no lab data needed for a screen): moist sandy/loamy soils typically run λ ≈ 1.5–2.5 W/m·K; dry sands drop to ≈ 0.3–0.8; clays with retained moisture ≈ 1.0–1.6. Use SSURGO `sandtotal_r`, `claytotal_r`, `dbthirdbar_r`, and `awc_r` as inputs. Score higher λ better. Flag any site where λ estimate < 1.0 W/m·K as requiring a thermal response test before any bid — the loop field sizing is highly sensitive to this and it is the largest single capex swing in Phase 1.

---

## 6. Explicit non-goals — do not let the model imply these

The script produces **candidates**, not qualified sites. Encode these as caveat fields in the output, not as scores:

1. **Load import capability is NOT screenable from public data.** A generation interconnection agreement confers export rights only. Import capability at a POI requires an ERCOT screening study. Every dossier must carry this warning.
2. **SB6 large-load risk.** Texas Senate Bill 6 (signed June 2025) established new interconnection requirements for large loads and gave ERCOT authority to curtail large load consumption before and during declared grid emergencies. This is a material underwriting variable, not a footnote.
3. **Mineral estate.** In Texas the mineral estate is dominant over the surface. If minerals were severed without a recorded surface waiver or subordination, the mineral owner retains implied surface access rights. This is a deal-killer on Permian Basin sites and cannot be screened — it requires a title chain. Add a `mineral_severance_check_required` flag to every West Texas dossier.
4. **Behind-the-meter configuration is unsettled.** Developers currently face complex metering and registration requirements pairing technologies behind a single point of interconnection. SB6 may reshape net metering arrangements between existing generation and large loads as early as 2026 — verify current ERCOT protocol status at time of use.
5. **Chapter 313 / JETI property tax expiry.** Check abatement status and expiry date per plant; the step-up at expiry is often mismodeled by sellers.

---

## 7. Deliverables

1. **`ercot_pv_conversion_screen.xlsx`** — full ranked table, all plants passing hard filters, every computed metric as a column, composite score, sorted descending.
2. **`dossiers/`** — one markdown file per top-25 site containing: plant identity and ownership chain, capacity (AC/DC/ILR), COD, module and tracker technology, 36-month capacity factor / curtailment / capture rate history, acreage and parcel IDs, POI substation and voltage, distance to fiber / gas / 345 kV, soils summary and λ estimate, NSRDB climate summary, county and taxing jurisdictions, and the five caveat flags from §6.
3. **`crosswalk_eia_ercot.csv`** — the hand-verified plant identity mapping, so re-runs are cheap.
4. **`config.yaml`** — all scoring weights and thresholds, externalized.
5. A short **methodology note** documenting every assumption, so it can be handed to a lender or a JV partner without rework.

---

## 8. Suggested build order for Cowork

1. Get EIA API key and ERCOT API registration first — both are free but take time to provision.
2. Build Phase 1–2 (EIA ingest and plant master) and validate against a known plant by hand before going further.
3. Build the EIA↔ERCOT crosswalk. Budget real time. Verify by hand.
4. Add Phase 3 (market data). Sanity-check capture rate against the ~57% standalone benchmark.
5. Add Phase 4 geo layers one at a time, easiest first: transmission → gas → fiber → flood → soils. SSURGO is the hardest; do it last.
6. Scoring and reporting.

Validate the whole pipeline against **Pecos County** first, since that is Komorebi's published planning case and gives a reference point for whether the outputs are sane.
