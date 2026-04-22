# Reservoir Estimated Volumes in OSDU

> **FMU context**: fmu-dataio exports in-place volumes as standard results (Parquet with BULK, NET, PORV, HCPV, STOIIP, GIIP keyed by ZONE, REGION, FACIES, REAL). This guide maps those to `ReservoirEstimatedVolumes` WPC in OSDU.
>
> **Links**: [fmu-dataio docs](https://fmu-dataio.readthedocs.io/en/latest/) · [Uncertainty guide](Uncertainty.md) · [FMU ↔ OSDU](FmuOsdu.md) · [BusinessDecision](BusinessDecision.md)

---

## 1) Why `ReservoirEstimatedVolumes`

| | `ReservoirEstimatedVolumes` | `ColumnBasedTable` |
|---|---|---|
| Domain semantics | Built-in property types, reservoir links | Generic; you enforce semantics yourself |
| Discoverability | Shows up in Reservoir Management context | Harder to find among many CBTs |
| Flexibility | Expects canonical properties | Any column structure |
| **Recommendation** | **Authoritative volumes store** | Raw/intermediate analysis tables |

Use `ReservoirEstimatedVolumes` for DG decision evidence. Use CBT only for wide or experimental tables.

---

## 2) Two flavours - raw realizations & aggregated statistics

### Raw realizations
- Keys: `Realisation` (int), `Zone` (string), `SegmentID` (string → `ReservoirSegment:2.0.0`)
- Columns: `Bulk`, `Net`, `Pore`, `HydrocarbonPore`, `Oil`, `AssociatedGas` - each with `PropertyTypeID` and `UnitOfMeasureID: m3`
- `ParentObjectID` → `master-data--Reservoir`

### Aggregated statistics
- Keys: `Zone`, `SegmentID` (no Realisation - aggregated across runs)
- Columns: dot-notation `<Property>.<Statistic>` - e.g. `Bulk.P10`, `Oil.ArithmeticMean`
- Each column carries `FacetIDs` with `FacetType:statistics` + `FacetRole:<P10|P50|P90|ArithmeticMean|Minimum|Maximum|StandardDeviation>`

---

## 3) fmu-dataio → OSDU column mapping

| fmu-dataio column | OSDU PropertyType | Notes |
|---|---|---|
| `BULK` | `ReservoirEstimatedVolumePropertyType:Bulk` | Bulk rock volume |
| `NET` | `...:Net` | Net rock volume |
| `PORV` | `...:Pore` | Pore volume |
| `HCPV` | `...:HydrocarbonPore` | HC pore volume |
| `STOIIP` | `...:Oil` | Stock tank oil in place |
| `GIIP` | `...:Gas` | Gas initially in place |
| `ASSOCIATEDGAS` | `...:AssociatedGas` | Associated gas |
| `ASSOCIATEDOIL` | `...:AssociatedOil` | Associated oil |
| `REAL` (key) | `Realisation` KeyColumn | fmu.realization.id |
| `ZONE` (key) | `Zone` KeyColumn | Stratigraphic zone |
| `REGION`/`FACIES` (key) | `SegmentID` KeyColumn | Maps to ReservoirSegment |

---

## 4) Example snippets

### 4.1 Raw realizations (excerpt)
```json
{
  "kind": "osdu:wks:work-product-component--ReservoirEstimatedVolumes:1.1.0",
  "data": {
    "ParentObjectID": "dev:master-data--Reservoir:...:1",
    "Volumes": {
      "KeyColumns": [
        {"ColumnName": "Realisation", "ColumnRole": "Key", "ValueType": "integer"},
        {"ColumnName": "Zone",        "ColumnRole": "Key", "ValueType": "string"},
        {"ColumnName": "SegmentID",   "ColumnRole": "Key", "ValueType": "string",
         "KindID": "osdu:wks:master-data--ReservoirSegment:2.0.0"}
      ],
      "Columns": [
        {"ColumnName": "Bulk", "ValueType": "number",
         "PropertyTypeID": "dev:reference-data--ReservoirEstimatedVolumePropertyType:Bulk:",
         "UnitOfMeasureID": "dev:reference-data--UnitOfMeasure:m3"},
        {"ColumnName": "Oil",  "ValueType": "number",
         "PropertyTypeID": "dev:reference-data--ReservoirEstimatedVolumePropertyType:Oil:",
         "UnitOfMeasureID": "dev:reference-data--UnitOfMeasure:m3"}
      ]
    }
  }
}
```

### 4.2 Aggregated statistics (excerpt)
```json
{
  "kind": "osdu:wks:work-product-component--ReservoirEstimatedVolumes:1.1.0",
  "data": {
    "ParentObjectID": "dev:master-data--Reservoir:...:1",
    "Volumes": {
      "KeyColumns": [
        {"ColumnName": "Zone",      "ColumnRole": "Key", "ValueType": "string"},
        {"ColumnName": "SegmentID", "ColumnRole": "Key", "ValueType": "string",
         "KindID": "osdu:wks:master-data--ReservoirSegment:2.0.0"}
      ],
      "Columns": [
        {"ColumnName": "Bulk.P10", "ValueType": "number",
         "PropertyTypeID": "dev:reference-data--ReservoirEstimatedVolumePropertyType:Bulk:",
         "UnitOfMeasureID": "dev:reference-data--UnitOfMeasure:m3",
         "FacetIDs": [{"FacetTypeID": "dev:reference-data--FacetType:statistics",
                       "FacetRoleID": "dev:reference-data--FacetRole:P10"}]},
        {"ColumnName": "Oil.ArithmeticMean", "ValueType": "number",
         "PropertyTypeID": "dev:reference-data--ReservoirEstimatedVolumePropertyType:Oil:",
         "UnitOfMeasureID": "dev:reference-data--UnitOfMeasure:m3",
         "FacetIDs": [{"FacetTypeID": "dev:reference-data--FacetType:statistics",
                       "FacetRoleID": "dev:reference-data--FacetRole:ArithmeticMean"}]}
      ]
    }
  }
}
```

---

## 5) Naming conventions

- Use `ArithmeticMean` (not Average), `StandardDeviation` (not StDev)
- Dot-notation for stats columns: `<Property>.<Statistic>`
- Units via `UnitOfMeasureID` - don't embed units in column names
- Consistent `m3` unless business rule requires `Mm3`

---

## 6) Quick comparison

| Aspect | Raw | Aggregated |
|---|---|---|
| Keys | `Realisation`, `Zone`, `SegmentID` | `Zone`, `SegmentID` |
| Columns | `Bulk`, `Net`, `Pore`, `HydrocarbonPore`, `Oil`, `AssociatedGas` | `Bulk.P10`, `Oil.P50`, `AssociatedGas.ArithmeticMean`, etc. |
| Use case | Full ensemble / Monte Carlo | DG reporting, dashboards |
| Links to BD | Input evidence (raw runs) | Decision evidence (statistical summary) |
