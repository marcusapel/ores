# DevelopmentConcept - Schema, Data Model & OSDU Integration

> **Schema version**: `dev:wks:work-product-component--DevelopmentConcept:3.0.0`
> **Kind registered on**: `equinorswedev.energy.azure.com` (partition `dev`)
> **Schema source**: [`demo/drogon/schema_devconcept.json`](../demo/drogon/schema_devconcept.json)
> **Generators**: [`gen_devconcept_dg2.py`](../demo/drogon_dg2/gen_devconcept_dg2.py) (Drogon synthetic), GRAND populated from actual plan doc

---

## 1. Purpose

A **DevelopmentConcept** WPC captures exactly what a subsurface team proposes to build for a given decision gate: the facility layout, well plan, drainage strategy, reservoir target, and production technology choices.

It is deliberately a **pure leaf** - it does not carry economics, schedule, production forecast, risks, documents or activity history. Those items belong on the **BusinessDecision** (the hub record). The DevConcept is linked from the BD via `Parameters[]` with key `DevelopmentConcept`, making it fetchable at render time.

### What it is

- The **physical concept**: what gets built underwater and topside
- Structured enough for automated comparison across gates (DG1 sparse vs DG2 detailed vs DG3 final)
- A single self-describing record that different teams (facilities, wells, reservoir, production tech) can contribute to

### What it is not

- Not a cost estimate (that's on the BD or a separate economics WPC)
- Not a schedule (that's on the BD `ext.equinor.Schedule`)
- Not a risk register (those are `master-data--Risk` records linked from the BD)
- Not a duplication of reservoir properties (those live on `ReservoirSegment` records)


## 2. Architecture - BD Hub + DevConcept Leaf

```
BusinessDecision (master-data, the hub)
  |
  +-- Parameters[]:
  |     [0] REV-raw          -> ReservoirEstimatedVolumes WPC (raw per-realisation)
  |     [1] REV-stats        -> ReservoirEstimatedVolumes WPC (P10/P50/P90)
  |     [2] GeoLabelSet      -> GeoLabelSet WPC (headline volumes per segment)
  |     [3] ProductionForecast -> ColumnBasedTable WPC (20-year forecast)
  |     [4] DevelopmentConcept -> DevelopmentConcept WPC  <-- this record
  |     [5] PersistedCollection -> Evidence Package WPC
  |     [6..N] Documents, prior gate, reservoir scope, dataspace, etc.
  |
  +-- ext.equinor.Risks[]    -> Risk master-data IDs
  +-- ext.equinor.Economics   -> CAPEX/OPEX/NPV/IRR
  +-- ext.equinor.Schedule    -> milestones, first oil, plateau
  +-- ext.equinor.Authors[]   -> team members
  +-- ext.equinor.Alternatives[] -> rejected concepts
```

At render time, `app/main.py::_enrich_bd_developmentconcept()` fetches the DevConcept WPC from Storage API and injects its data into `ext.equinor.DevelopmentConcept` on the BD, so templates can render it inline.


## 3. Schema Structure (v3.0.0)

The schema has 5 structured sub-objects plus top-level scalars. All fields are optional except `Name`.

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `Name` | string (required) | Short human-readable name |
| `Description` | string | Full description of concept and context |
| `Summary` | string | Executive summary |
| `DecisionGate` | enum | `DG0` / `DG1` / `DG2` / `DG3` / `DG4` |
| `ConceptID` | string | OSDU ID of a related DevConcept (lineage to prior gate) |
| `ParentObjectID` | string | Reservoir or field master-data ID |
| `ancestry.parents[]` | string[] | OSDU ancestry (typically the Reservoir ID) |

### 3.1 FacilityConcept

What is being built or modified on the facility side.

| Field | Type | Notes |
|---|---|---|
| `FacilityType` | enum | `SubseaTieback`, `StandaloneFPSO`, `FixedPlatform`, `SubseaToShore`, `TLP`, etc. |
| `FacilityTypeID` | ref-data ID | -> `reference-data--FacilityType` |
| `HostFacility` | string | Name of host facility for tie-back |
| `HostFacilityID` | master-data ID | -> `master-data--GenericFacility` |
| `HostModifications` | string | Brownfield modifications narrative |
| `TemplateCount` | integer | Number of subsea templates |
| `SlotsPerTemplate` | integer | Well slots per template |
| `TotalSlots` | integer | Total well slots |
| `Flowlines[]` | array | Type, Diameter_in, Length_km, Count, Material |
| `SubseaBoostingPump` | boolean | |
| `ArtificialLift` | enum | `GasLift`, `ESP`, `SRP`, `PCP`, `JetPump`, `None` |
| `ArtificialLiftTypeID` | ref-data ID | -> `reference-data--ArtificialLiftType` |
| `ProcessingCapacity` | object | OilRate_Sm3d, GasRate_MSm3d, WaterTreatment_m3d, etc. |
| `WaterDepth_m` | number | |
| `DistanceToHost_km` | number | |
| `Provisions` | string | Future expansion provisions |

### 3.2 WellPlan

Drilling and completion parameters.

| Field | Type | Notes |
|---|---|---|
| `Producers` | integer | |
| `Injectors` | integer | |
| `ObservationWells` | integer | |
| `ContingentWells` | integer | Provisioned but uncommitted |
| `TotalTargets` | integer | May exceed well count for multilaterals |
| `MultilateralWells` | integer | |
| `WellTypes[]` | array | Type, Count, Names[], TargetZone, AvgLength_mMD |
| `AvgWellDepth_mMD` | number | |
| `DrillingDuration_days` | number | Average per-well |
| `CompletionType` | string | |
| `SandControl` | string | |
| `InflowControl` | string | ICD / AICD / autonomous |
| `PilotStrategy` | string | Landing pilot, reservoir pilot |

### 3.3 DrainageStrategy

Reservoir drainage and injection approach.

| Field | Type | Notes |
|---|---|---|
| `PrimaryRecoveryMechanism` | enum | `WaterInjection`, `GasInjection`, `WAG`, `NaturalDepletion`, etc. |
| `ReservoirDriveMechanismTypeID` | ref-data ID | -> `reference-data--ReservoirDriveMechanismType` |
| `InjectionType` | enum | `Water`, `Gas`, `WAG`, `Polymer`, `LowSalinityWater`, `CO2`, `Steam`, `None` |
| `InjectionStrategy` | string | Narrative |
| `IORStrategy` | string | Improved Oil Recovery options |
| `DevelopmentPhases[]` | array | Phase, Description, Wells, StartDate |
| `AquiferSupport` | string | |
| `DepletionPlan` | string | |

### 3.4 ReservoirTarget

Which reservoir interval and structure this concept targets.

| Field | Type | Notes |
|---|---|---|
| `FormationName` | string | |
| `GroupName` | string | |
| `Age` | string | Geological age |
| `FieldArea` | string | |
| `FieldID` | master-data ID | -> `master-data--Field` |
| `DepthRange_mTVDMSL` | object | `{ Min, Max }` |
| `Zones[]` | string[] | Reservoir zone names |
| `ReservoirSegmentIDs[]` | master-data IDs | -> `master-data--ReservoirSegment` (OWC/GOC/fault properties live there) |

### 3.5 ProductionTechnology

Well chemistry and production management.

| Field | Type | Notes |
|---|---|---|
| `SandManagement` | string | |
| `ScaleRisk` | string | |
| `EmulsionRisk` | string | |
| `WaxRisk` | string | |
| `CorrosionStrategy` | string | |
| `MeteringStrategy` | string | |
| `WellAutomation` | string | |
| `WaterManagement` | string | |


## 4. OSDU Reference-Data Integration

The schema provides `*ID` fields alongside human-readable labels for cross-referencing canonical OSDU reference-data types. This dual approach means the record is both human-readable and machine-linkable.

### 4.1 Reference-data types used

| Field | OSDU Entity | Example ID |
|---|---|---|
| `FacilityTypeID` | `reference-data--FacilityType` | `dev:reference-data--FacilityType:SubseaTieback:` |
| `ArtificialLiftTypeID` | `reference-data--ArtificialLiftType` | `dev:reference-data--ArtificialLiftType:GasLift:` |
| `ReservoirDriveMechanismTypeID` | `reference-data--ReservoirDriveMechanismType` | `dev:reference-data--ReservoirDriveMechanismType:WaterDrive:` |

### 4.2 Master-data references

| Field | OSDU Entity | Purpose |
|---|---|---|
| `HostFacilityID` | `master-data--GenericFacility` | Links to the actual host platform/FPSO record |
| `FieldID` | `master-data--Field` | Links to the OSDU field record |
| `ReservoirSegmentIDs[]` | `master-data--ReservoirSegment` | Links to fault-bounded segments with OWC/GOC/seal properties |
| `ParentObjectID` | `master-data--Reservoir` | The parent reservoir (also in `ancestry.parents[]`) |

### 4.3 Why both label + ID?

- **Label** (e.g. `FacilityType: "SubseaTieback"`) - immediately readable in templates, search results, and raw JSON without requiring extra lookups
- **ID** (e.g. `FacilityTypeID: "dev:reference-data--FacilityType:SubseaTieback:"`) - enables machine-to-machine linking, schema validation, and relationship graph traversal
- **Schema enums** on the label fields constrain values to a known set, acting as lightweight validation even without the ID


## 5. Design Decisions

### 5.1 Leaf WPC, not embedded in BD

**Choice**: DevConcept is a standalone WPC record, linked FROM the BD via `Parameters[]`.

**Alternatives considered**:
- **Embed in BD `ext.*`**: Simpler (no extra record), but OSDU silently drops custom `ext.*` keys during ingestion. Requires the app to re-merge from local manifests at render time - fragile.
- **Embed in BD `data.*`**: BD schema is `master-data--BusinessDecision:1.0.0` (OSDU-owned). Can't add custom properties without schema modification.
- **Separate WPC with `additionalProperties: true`**: What we chose. The DevConcept lives on its own record with its own schema. The BD is the hub that links to it.

**Benefits**: schema-validated, independently versionable, survives OSDU ingestion, discoverable via search, can evolve its own schema version without touching the BD.

### 5.2 Structured sub-objects vs flat keys

**Choice**: 5 nested sub-objects (`FacilityConcept`, `WellPlan`, etc.) rather than flat top-level keys.

**Earlier version** (v1): Had flat keys like `WellCount`, `TemplateSlots`, `WaterDepth_m` at the top level. This was simple but became unwieldy as the schema grew, and made it hard to tell which team owned which fields.

**Benefits**: Clear domain ownership (facilities team fills FacilityConcept, wells team fills WellPlan), natural grouping for UI rendering, easier partial population (an early DG1 record might only have FacilityConcept and ReservoirTarget, with WellPlan sparse).

### 5.3 ReservoirSegmentIDs instead of inline faults/contacts

**Choice**: Reference `master-data--ReservoirSegment` IDs rather than embedding OWC/GOC/fault properties.

**Rationale**: Segment properties (OWC depth, GOC depth, fault seal factor, GRV, HCPV) are shared across multiple records (REV, BD, DevConcept, GeoLabelSet). Keeping them on the Segment master-data record avoids duplication and ensures consistency.

### 5.4 DecisionGate enum vs DecisionLevel ref-data

The schema has a `DecisionGate` enum (`DG0`-`DG4`) for the display label. A formal `DecisionLevelID` referencing `reference-data--DecisionLevel` could be added but wasn't needed for the current use case - the BD already carries the gate reference.

### 5.5 ConceptID for gate lineage

`ConceptID` links to a prior gate's DevConcept (e.g. DG3 concept references DG2 concept). This enables delta comparison across gates: what changed in the concept between DG2 and DG3?

For the first gate (DG2 in the Drogon case), ConceptID is absent - there is no prior concept. DG1 typically doesn't have a development concept at all (it's the "Identify & Assess" phase).


## 6. Worked Examples

### 6.1 Drogon DG2 (synthetic)

Based on the public `equinor/fmu-drogon` tutorial model. Purely synthetic but structurally realistic.

- **Facility**: Subsea tie-back to converted FPSO, 2x4-slot templates, dual 10" production + 6" gas lift
- **Wells**: 4 horizontal producers (A1-A4) + 2 water injectors (A5-A6) + 2 contingent
- **Drainage**: Water injection, IOR options under evaluation (low-salinity, polymer)
- **Target**: Volantis Group (Valysar, Therys, Volon), 1650-1690 m TVD MSL, 7 fault-bounded segments
- **Technology**: Frac-pack + ICD, subsea MPFM, gas lift

Record ID: `dev:work-product-component--DevelopmentConcept:Drogon-DG2:1`

### 6.2 GRAND DG2 (plan-based)

Based on the actual Grane Northern Area Development (GRAND) plan document (PM398-DD-200-001).

- **Facility**: Subsea tie-back to existing Grane platform, 4x6-slot templates (24 slots), dual 16"/14" production + 8" gas lift
- **Wells**: 19 producers + 4 injectors (23 total), 2 multilateral, from 4 drilling centres
- **Drainage**: Water injection with 4 dedicated injectors
- **Target**: Heimdal Formation (Palaeocene), Grane Northern Area, 1750-1850 m TVD MSL
- **Technology**: Frac-pack, 13Cr CRA tubing, BaSO4 scale squeeze programme

Record ID: `dev:work-product-component--DevelopmentConcept:GRAND-DG2:1`


## 7. UI Rendering

### 7.1 Search page (search.html)

When a BusinessDecision is displayed, the app:

1. Finds the `DevelopmentConcept` parameter in `Parameters[]`
2. Fetches the WPC from Storage API (`_enrich_bd_developmentconcept()`)
3. Injects into `ext.equinor.DevelopmentConcept`
4. Renders a **headline grid** with 6 key metrics (Facility type, Well count, Recovery mechanism, Target formation, Depth, Water depth)
5. A collapsible **"Full Concept Details"** section shows all sub-objects in detail

### 7.2 Analyse page (analyse.html)

The `buildDevConceptSection()` function uses dot-path resolution (`FacilityConcept.FacilityType`, `WellPlan.Producers`, etc.) to render a structured table for gate-to-gate comparison.


## 8. Possible OSDU Improvements

### 8.1 Community-standard DevelopmentConcept schema

Currently, OSDU has no community-standard `DevelopmentConcept` WPC schema. The closest is the generic `WorkProduct` envelope pattern. A proposal to the OSDU Data Definitions working group for a standard `osdu:wks:work-product-component--DevelopmentConcept:1.0.0` would benefit the community, as every operator goes through concept selection.

**Suggested scope**: Facility concept, well plan, drainage strategy, reservoir target - these are universal across operators. Production technology specifics may be more operator-dependent and could go in `ext.*`.

### 8.2 Better reference-data coverage

Some concept aspects lack canonical OSDU reference-data types:

| Concept | Current approach | Desired OSDU type |
|---|---|---|
| Completion type (frac-pack, ICD, gravel pack) | Free-text `CompletionType` | `reference-data--CompletionType` |
| Injection fluid type (water, gas, WAG, polymer) | Enum on schema | `reference-data--InjectionFluidType` |
| Subsea infrastructure type (template, manifold, PLET) | Free-text / integer counts | `reference-data--SubseaInfrastructureType` |
| Well type (horizontal producer, multilateral, injector) | Free-text `Type` in `WellTypes[]` | `reference-data--WellType` (beyond existing `WellTypeAcronym`) |

### 8.3 Schema extension mechanism

OSDU `ext.*` keys are silently dropped during manifest-based ingestion (the Workflow API / `Osdu_ingest` DAG). This makes it impossible to store operator-specific extensions reliably through the standard ingestion path. The workaround (direct Storage API PUT) works but bypasses manifest validation.

**Improvement**: Either preserve `ext.*` through the manifest pipeline, or provide a documented "schema extension" mechanism that survives ingestion.

### 8.4 Concept versioning across gates

A standardised pattern for linking DevConcept records across gates (DG1 -> DG2 -> DG3) would enable automated delta reports. Currently we use `ConceptID` as a simple reference, but a first-class `ConceptLineage` pattern (similar to `ancestry`) with support for branching (multiple alternatives at the same gate) would be more powerful.

### 8.5 Parameters[] key standardisation

The BD `Parameters[]` array uses `Keys[].StringParameterKey` for artifact typing (e.g. `"DevelopmentConcept"`, `"GeoLabelSet"`, `"ProductionForecast"`). These key strings are not formalised anywhere - each operator invents their own. A standard set of `ParameterKey` values for common artifact roles would improve interoperability.

### 8.6 Alternatives as first-class records

Currently, rejected concept alternatives are stored as `ext.equinor.Alternatives[]` on the BD. These could be full DevConcept WPC records with a status field (`Selected`, `Rejected`, `Deferred`), enabling richer comparison between the chosen concept and its alternatives.


## 9. Schema Evolution History

| Version | Changes |
|---|---|
| 1.0.0 | Flat top-level keys (WellCount, TemplateSlots, etc.) |
| 2.0.0 | Restructured into 5 sub-objects. Added `PriorConceptID` for gate lineage. Added OSDU reference-data IDs. Moved segment/fault properties to ReservoirSegment master-data records. |
| 3.0.0 | Renamed `PriorConceptID` to `ConceptID` (breaking). Clarified that ConceptID can reference any related concept, not just a prior gate. |

> **Note**: The ConceptID rename from v2 to v3 is a breaking change. Re-registration as v4.0.0 is recommended if the v3.0.0 schema was already ingested with the old field name.


## 10. File Inventory

| File | Purpose |
|---|---|
| `demo/drogon/schema_devconcept.json` | JSON Schema definition (v3.0.0) |
| `demo/drogon/register_schema_devconcept.py` | Registers schema with OSDU Schema Service |
| `demo/drogon_dg2/gen_devconcept_dg2.py` | Generator for Drogon DG2 DevConcept manifest |
| `demo/drogon_dg2/manifest_devconcept_dg2.json` | Generated manifest (Drogon DG2) |
| `app/main.py` | `_enrich_bd_developmentconcept()` - runtime fetch and injection |
| `app/templates/search.html` | DevConcept headline grid + collapsible details |
| `app/templates/analyse.html` | `buildDevConceptSection()` - gate comparison rendering |
