# D6-Øving — SolidWorks Automation (C#)

Automated solution for the SolidWorks FEA exercises (Tasks 1 & 2).

## Prerequisites

1. **Windows** with **SolidWorks 2020+** installed
2. **SolidWorks Simulation** add-in enabled (Tools → Add-Ins)
3. **.NET 8 SDK** — [download](https://dotnet.microsoft.com/download)

## Setup

### 1. Add SolidWorks COM References

The SolidWorks API DLLs are **not** available via NuGet. You must reference them
from your local SolidWorks installation.

**Option A — Visual Studio:**
1. Open `SolidWorksAutomation.csproj` in Visual Studio
2. Right-click the project → Add → COM Reference
3. Add these:
   - `SolidWorks.Interop.sldworks`
   - `SolidWorks.Interop.swconst`
   - `SolidWorks.Interop.cosworks` (Simulation API)

**Option B — Manual:**
Find the DLLs in `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist\` and
add `<Reference>` entries to the `.csproj`.

### 2. Fill in Dimensions

The cross-section dimensions are embedded in the PDF figures and must be read
manually. Open `D6-Øving.pdf` and update:

| File | Constants | PDF page |
|------|-----------|----------|
| `Task1_HBeam.cs` | `W=70`, `H=95`, `tf=5`, `tw=10` (filled in) | Page 3 |
| `Task2_PressureTank.cs` | `CylinderH=600`, `DomeHoleDia=20`, `BottomThick=5` (filled in) | Page 6 |

## Build & Run

```bash
dotnet build
dotnet run              # runs both tasks
dotnet run -- --task1   # H-beam only
dotnet run -- --task2   # pressure tank only
```

## What It Does

### Task 1 — H-Beam
1. Creates H cross-section sketch → extrudes to 1 m
2. Assigns Alloy Steel material
3. Applies fixed support on one end
4. Applies 20 kN distributed load on top flange
5. Meshes and runs static FEA
6. Reports max deformation, min FoS, max load

### Task 2 — Pressure Tank
1. Creates half-profile sketch (cylinder + hemisphere dome)
2. Revolved Boss/Base 360°
3. Assigns 1060 Alloy (Aluminum)
4. Fixes bottom face, applies 10 bar internal pressure
5. Runs static FEA → checks if FoS ≥ 2
6. **Parametric sweep** (Task 2c): loops wall thickness 3–10 mm,
   records FoS + mass → computes cost at $2.70/kg
7. **Cost comparison** (Task 2d): prints % increase vs. 5 mm original

## Output Example

```
══════════════════════════════════════
  Task 2c — Parametric Sweep
══════════════════════════════════════
  t (mm)   FoS        Mass (kg)    Cost (USD)   Meets FoS≥2?
──────────────────────────────────────────────────────────────
  3.0      0.832      0.1823       $0.49        NO ✗
  4.0      1.195      0.2431       $0.66        NO ✗
  5.0      1.523      0.3039       $0.82        NO ✗
  6.0      1.884      0.3647       $0.98        NO ✗
  7.0      2.211      0.4255       $1.15        YES ✓   ← first to meet spec
  ...

  Task 2d: Cost increase for FoS≥2 vs. original 5mm design:
           40.0% higher material cost.
```

## Notes

- **Face selection** for pressure loads may need manual adjustment. The code
  picks faces by coordinate; if the geometry changes you may need to tweak the
  pick points or use `SelectByRay` for more robust selection.
- The Simulation results extraction API varies between SolidWorks versions. The
  code prints guidance messages where exact API calls are version-dependent.
- Task 3 (3D printing) is creative and not automated.

## File Structure

```
SolidWorksAutomation/
├── Program.cs               # Entry point
├── SolidWorksHelper.cs      # SW connection + material utilities
├── Task1_HBeam.cs           # Oppgave 1: H-beam
├── Task2_PressureTank.cs    # Oppgave 2: Pressure tank + sweep
├── SolidWorksAutomation.csproj
└── README.md
```
