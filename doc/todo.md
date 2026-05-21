# WeCo Improvement Plan — Workflow, Diversity, Visualisation, OSDU

> Working document. Priority: P0 (immediate), P1 (next sprint), P2 (roadmap).

---

## 1. Workflow — Minimum Clicks to Best Results

### Current State
- **Demo GUI**: 2–3 clicks (select demo → run → view). Pre-canned datasets, hardcoded params.
- **Web client**: 6-tab wizard (Data → Logs → Parameters → Run → Results → Export).
  Optimal demo path: 4 clicks. Custom data: ~7 actions.

### Improvements

| ID | Task | Priority | Notes |
|----|------|----------|-------|
| W1 | **"Quick Run" mode** in web client: upload → auto-suggest → run → results in 2 clicks | P0 | Skip Logs/Parameters tabs entirely; use suggest-defaults + AI quality scoring to auto-select best |
| W2 | **Auto-run on demo select**: clicking a demo card should immediately run (not just load wells) | P1 | Already have `/run/demo` endpoint; wire "Run demo" click directly |
| W3 | **Iterative auto-refinement**: run → score quality → if quality < threshold, adjust gap-cost/min-dist → re-run (max 3 iterations) | P1 | New `/auto-run` endpoint; uses CorrelationQuality to decide when results are "good enough" |
| W4 | **Auto-detect deposit environment** from strat column metadata → apply environment preset → run | P2 | Wire `weco.depenv.detect_environment()` into suggest-defaults when OSDU metadata available |

---

## 2. Result Diversity — Avoid All Realisations Looking Alike

### Current State
- Engine has `min_dist` / `out_min_dist` (geometric path distance filter).
- Defaults are 0.0 → **no diversity** unless user sets them.
- Demo presets set `min_dist=0.1, out_min_dist=0.05` → helps but is purely geometric.
- Results sorted by cost only. No structural diversity guarantee.

### Improvements

| ID | Task | Priority | Notes |
|----|------|----------|-------|
| D1 | **Default diversity on**: set `min_dist=0.1, out_min_dist=0.05` as engine defaults (not just in suggest) | P0 | Simple change in RESET_OPTS or engine defaults |
| D2 | **Structural diversity filter** (API layer): cluster k-best results by topology (number of gaps, gap positions) and present one representative per cluster | P1 | Post-process in `_extract_results()`: compute topology signature → cluster → pick lowest-cost per cluster |
| D3 | **Diversity score column** in ranking table: show how different each result is from #1 | P1 | Use existing `path_distance` function, normalize to 0–1 |
| D4 | **Force-diverse mode**: guarantee at least one result with gap, one without; one with crossing removed, one with crossing kept | P2 | Run multiple configs internally (with/without gap-cost) → merge into single k-best set |
| D5 | **Interpretation scenarios**: present results as named geological scenarios ("Layer-cake", "Unconformity model", "Pinch-out model") based on gap/boundary ratio | P2 | Classification from topology + AI labelling |

---

## 3. Visualisation — Convincing Data-to-Result Causation

### Current State

**Demo GUI (matplotlib, static PNG):**
- Well columns with log traces (up to 3 curves, colored)
- Region/zone background bands (pastel, with zone ID labels)
- Correlation lines: red=boundary, blue=gap, gray=framework
- Bottom legend with line counts
- ❌ No facies track, no depth ticks, no log labels, no distance info

**Web client (canvas, interactive):**
- Multiple log tracks, discrete log tracks (auto-detected)
- Strat column strip (first region as colored zones)
- Depth labels (MD/TVDSS), align modes
- Correlation lines with marker dots
- Interactive toolbar (align, log select, discrete toggle)
- ❌ No zone name labels on strips, no uncertainty overlay, no log-scale

### Improvements

| ID | Task | Priority | Notes |
|----|------|----------|-------|
| V1 | **Facies track** in PyQt GUI: discrete colored strip alongside log traces | P0 | Like web client's discrete tracks but for matplotlib; use FACIES/LITH region data |
| V2 | **Zone name labels** on strat strips (both GUIs): print zone names rotated/centered in each band | P1 | Requires facies dictionary (see F1 below) for human-readable names |
| V3 | **Uncertainty overlay**: draw top-3 results simultaneously with decreasing opacity (alpha=1.0, 0.4, 0.2) | P1 | Shows where correlation is certain (all agree) vs uncertain (lines diverge) |
| V4 | **Composite result view**: side-by-side panels showing 3 diverse results at once (no clicking) | P1 | Single image/canvas with 3 sub-panels, each labelled with scenario name |
| V5 | **Depth axis ticks** and **log scale labels** on both GUIs | P0 | Restore x-tick labels for at least GR (0–150 API), add depth ticks every N metres |
| V6 | **Log-scale option** for resistivity (RT) in web client | P2 | Add toggle; log10 transform before drawing |
| V7 | **Export plot as PNG** from web client (`canvas.toBlob()`) | P1 | "Download Plot" button next to View toggle |
| V8 | **Well spacing** reflects actual distance (scale bar) | P2 | Use well X/Y coordinates to set column widths proportionally |

---

## 4. Facies Dictionary & OSDU Reference Data

### Current State
- `Well.region` stores `(zone_id, start_index, length)` — integer IDs only.
- No mapping from zone_id → name/color/description.
- `doc/rddms_stratcolumn.md` documents StratColumn model (Column → Rank → Unit → Horizon).
- 11 depositional environment presets exist (mapping OSDU vocabulary → engine params).
- No `FaciesDictionary` class. No chrono/litho column integration at plot time.

### OSDU Reference Model

```
GlobalStratColumn (no gaps, complete)
  └── Rank (e.g. "Stage", "Formation")
       └── Unit (e.g. "Draupne Fm", "Hugin Fm")
            ├── chronostratigraphic age
            ├── lithostratigraphic classification
            └── Horizon (boundary between units)

SingleWellStratColumn (has gaps = non-penetrated units)
  └── Picks referencing GlobalStratColumn units
```

**OSDU schema entities:**
- `LithostratigraphicUnit`: name, age, parent, color_code
- `ChronostratigraphicUnit`: era, period, epoch, stage, numerical_age
- `DepositionalFacies`: lithology, grain_size, texture, color

### Improvements

| ID | Task | Priority | Notes |
|----|------|----------|-------|
| F1 | **FaciesDictionary class** (`weco/facies_dict.py`): maps `zone_id` → `{name, color, lithology, description}` | P0 | Used at plot time for legend + facies track colouring |
| F2 | **Standard facies colour palette**: define default colours for common lithologies (sandstone=yellow, shale=gray, coal=black, limestone=blue, etc.) | P0 | USGS pattern-based; embedded in FaciesDictionary defaults |
| F3 | **Auto-detect facies from region values**: if region has values 1–10, attempt to match against standard litho codes | P1 | Heuristic: count distinct values, check naming patterns |
| F4 | **OSDU facies lookup**: given a `LithostratigraphicUnit` record bundle, build FaciesDictionary automatically | P1 | Parse OSDU `kind=osdu:wks:master-data--LithostratigraphicUnit:1.0.0` records |
| F5 | **Global StratColumn integration**: display chronostrat column alongside wells (absolute time axis) | P2 | Requires age model; map zone depths → global column positions |
| F6 | **Lithostratigraphic column from OSDU**: populate named formations, members, groups from OSDU hierarchy | P2 | Auto-build no-crossing constraints from formation boundaries |
| F7 | **SingleWell → GlobalColumn gap display**: show which global units are missing in each well (non-penetrated / eroded) | P2 | Wheeler diagram concept — highlight stratigraphic gaps explicitly |

---

## 5. Auto-Suggest → Auto-Run → Auto-Present

### Vision: Zero-Click Best Result

```
User uploads wells (or selects demo)
  → auto-detect logs, regions, metadata
  → auto-detect depositional environment
  → apply environment preset + suggest params
  → run correlation (with diversity: min_dist=0.1)
  → AI quality scoring + anomaly flagging
  → cluster results into 3 diverse scenarios
  → present composite view: "Here are 3 geological interpretations"
     with named scenario labels and quality scores
```

### Implementation Steps

| ID | Task | Priority | Notes |
|----|------|----------|-------|
| A1 | **Unified `/auto` endpoint**: import → suggest → run → score → cluster → return top-3 diverse | P1 | Chains existing endpoints internally |
| A2 | **Environment detection from logs**: GR range + facies vocabulary → infer "fluvial" / "marine" / "carbonate" | P1 | Use `weco.ai` + log statistics |
| A3 | **Scenario labelling**: classify results as "Layer-cake", "Erosional", "Pinch-out" etc. based on gap/boundary ratio | P2 | Simple rule-based first; ML later |
| A4 | **Quality threshold gate**: if best-result quality < 0.5, auto-adjust params and re-run | P1 | Iterative loop in `/auto` endpoint |
| A5 | **Facies-guided parameter suggestion**: if FACIES region exists → auto-enable distality cost | P0 | Simple check in `_suggest_defaults_for_wells` |

---

## 6. Priority Summary

### P0 — Immediate (this week)
- ~~D1: Default diversity on (`min_dist=0.1` always)~~ ✅ Done — RESET_OPTS + suggest-defaults
- ~~V1: Facies track in PyQt GUI~~ ✅ Done — discrete coloured strip from FaciesDictionary
- ~~V5: Depth axis ticks + log labels~~ ✅ Done — MaxNLocator + log name headers with units
- ~~F1: FaciesDictionary class~~ ✅ Done — `weco/facies_dict.py`
- ~~F2: Standard facies colour palette~~ ✅ Done — STANDARD_LITHO_PALETTE + ZONE_COLORS
- ~~A5: Auto-enable distality when FACIES region present~~ ✅ Done — in `_suggest_defaults_for_wells`

### P1 — Next Sprint
- ~~V3: Uncertainty overlay~~ ✅ Done — top-N results drawn with decreasing alpha (show_uncertainty=True)
- ~~V7: Export plot as PNG~~ ✅ Done — "📥 PNG" button on web client toolbar (canvas.toBlob)
- ~~D2: Structural diversity filter~~ ✅ Done — `_diverse_results()` clusters by topology signature
- W1: Quick Run mode
- W3: Iterative auto-refinement
- D3: Diversity score column
- V2: Zone name labels
- V4: Composite 3-result view
- F3: Auto-detect facies from region values
- F4: OSDU facies lookup
- A1: Unified `/auto` endpoint
- A2: Environment detection from logs
- A4: Quality threshold gate

### P2 — Roadmap
- W4: Deposit environment detection from strat metadata
- D4: Force-diverse mode
- D5: Interpretation scenario naming
- V6: Log-scale for RT
- V8: Well spacing from coordinates
- F5: Global StratColumn integration
- F6: Lithostratigraphic column from OSDU
- F7: SingleWell→Global gap display
- A3: Scenario labelling

---

## Notes

- The engine's diversity mechanism (`path_distance` + `min_dist`) is powerful but inactive by default.
  Turning it on (D1) is the single highest-impact change for result quality.
- The web client already has discrete log tracks and strat strips — much more advanced than the PyQt demo.
  The PyQt demo is for offline/developer use; web client is the production UI.
- OSDU integration (F4–F7) requires the RDDMS connection to be active. For demos and offline use,
  FaciesDictionary (F1) with hardcoded standard palettes (F2) is sufficient.
- The "zero-click" vision (A1) is achievable by chaining existing components.
  Each piece exists; they just need orchestration.
