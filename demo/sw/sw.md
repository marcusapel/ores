# Trykktank (Pressure Tank) — SolidWorks Cookbook

---

## Dimensions

| Parameter | Value |
|-----------|-------|
| Inner diameter | 200 mm |
| Wall thickness | 5 mm |
| Cylinder height | 600 mm |
| Dome | Hemisphere on top |
| Hole at dome apex | Ø 20 mm |
| Bottom plate | 5 mm thick (flat) |
| Material | Aluminum 1060 Alloy |
| Pressure | 10 bar (= 1 MPa) |

---

## A. Build the Part

### A1 — New Part + Units

1. **File → New → Part** → OK
2. Bottom-right corner → set units to **MMGS**

### A2 — Sketch on the Right Plane

1. In the FeatureManager tree, click **Right Plane**
2. Right-click → **Sketch**

### A3 — Draw the Centreline (revolve axis)

1. **Tools → Sketch Entities → Centerline**
2. Click the **origin** → drag straight **up** → click to place
3. This vertical line is the revolve axis

### A4 — Draw the Outer Profile

Draw **3 lines + 1 arc** to the right of the axis:

```
          AXIS (centreline)
           |
           |
    (0,710)+ _ _
           |     ' .
           |         '.       quarter-circle arc
           |           \      centre = (0, 605)
           |            \     radius = 105 mm
           |             |
           |             | (105, 605)
           |             |
           |             |    vertical line
           |             |    605 mm tall
           |             |
           |             |
           |             |
    (0, 0) +-------------+ (105, 0)
         origin

           <-- 105 mm -->
```

1. **Line (L):** origin `(0, 0)` → right to `(105, 0)`
2. **Line:** `(105, 0)` → up to `(105, 605)`
3. **Tangent Arc:** `(105, 605)` → curve up-and-left to `(0, 710)`
   - **Tools → Sketch Entities → Tangent Arc**
   - Click at the top of the vertical line, drag upward and to the left
   - The arc **must end on the axis** (X = 0)

The profile closes automatically along the centreline.

### A5 — Add Dimensions

1. **Smart Dimension (D):**
   - Bottom line → `105`
   - Vertical line → `605`
   - Arc radius → `105`
2. All lines should turn **black** (fully defined)

### A6 — Revolve

1. **Features → Revolved Boss/Base**
2. Axis = the centreline (auto-detected)
3. Angle = **360°** → click ✓

Result: a **solid** cylinder with a hemisphere dome. Not hollow yet.

### A7 — Shell (hollow it out)

1. **Features → Shell**
2. **Faces to remove:** click the **flat bottom face** (disc at the bottom)
3. **Wall Thickness:** `5`
4. Click ✓

Result: the body is now a **5 mm hollow shell** — walls, dome, and bottom plate.

### A8 — Cut the 20 mm Hole

1. Click **Top Plane** → start a new **Sketch**
2. Draw a **Circle** at the origin, **radius 10 mm**
3. Exit the sketch
4. **Features → Extruded Cut**
5. Direction: **Up To Next** (cuts only the dome, not the bottom)
   - Arrow must point **downward**. If it points up → **Reverse Direction**
   - Do NOT use "Through All" — it cuts both top and bottom!
6. Click ✓

### A9 — Assign Material

1. Right-click **Material** → **Edit Material**
2. **Aluminum Alloys → 1060 Alloy** → Apply → Close

### A10 — Save

**File → Save As** → `PressureTank.SLDPRT`

---

## B. Run FEA Simulation (10 bar)

### B1 — New Study

1. **Simulation** tab → **New Study**
2. Name: `Pressure_10bar`, Type: **Static** → ✓

### B2 — Fix the Bottom

1. Right-click **Fixtures** → **Fixed Geometry…**
2. Click the **flat bottom face** → ✓

### B3 — Apply Pressure on Interior Faces

First, make the inside visible:

1. **View → Display → Section View** → pick **Right Plane** → ✓

Then apply pressure:

1. Right-click **External Loads** → **Pressure…**
2. Hold **Ctrl** and click all **inner faces**:
   - Inner cylinder wall
   - Inner dome
   - Inner bottom plate
3. Value: `1000000` Pa (= 10 bar = 1 MPa)
4. Arrows should point **outward**. If inward → **Reverse direction**
5. Click ✓
6. Turn off Section View: **View → Display → Section View** (toggle)

### B4 — Mesh + Run

1. Right-click **Mesh** → **Create Mesh** → ✓
2. Right-click study name → **Run**

### B5 — Read Results

| Result | How to read |
|--------|-------------|
| Max stress | Double-click **Stress** plot → read max from legend |
| Max displacement | Double-click **Displacement** plot → read max |
| Min Factor of Safety | Right-click **Results** → **Define Factor of Safety Plot** → ✓ → read min from legend |

**Question:** Is min FoS ≥ 2? If not, the wall is too thin.

---

## C. Parametric Study (thickness vs FoS vs cost)

### C1 — Fill this Table

Repeat the simulation for each wall thickness:

| Thickness (mm) | Max Stress (MPa) | Min FoS | Mass (kg) | Cost ($) at $2.70/kg |
|:-:|:-:|:-:|:-:|:-:|
| 3 | 33.3 | 0.83 | 3.89 | 10.51 |
| 4 | 25.0 | 1.10 | 5.23 | 14.11 |
| **5** | **20.0** | **1.38** | **6.58** | **17.75** |
| 6 | 16.7 | 1.66 | 7.94 | 21.44 |
| 7 | 14.3 | 1.93 | 9.33 | 25.18 |
| **8** | **12.5** | **2.21** | **10.73** | **28.97** |
| 9 | 11.1 | 2.48 | 12.15 | 32.80 |
| 10 | 10.0 | 2.76 | 13.59 | 36.68 |

> Values computed using thin-wall pressure vessel theory: σ = p×r/t (cylinder hoop stress).
> Yield strength of 1060 Alloy ≈ 27.6 MPa. FEA results may differ slightly due to
> stress concentrations at the dome-cylinder junction and the hole.
>
> **Key rows bolded:** t=5 mm (original design) and t=8 mm (first thickness where FoS ≥ 2.0).

### C2 — How to Change Thickness

For each row:

1. Go to the **Model** tab (exit Simulation)
2. In the FeatureManager, expand **Shell1**
3. Double-click it → change **Wall Thickness** to the new value → ✓
4. **Ctrl+Q** to rebuild
5. Check mass: **Tools → Evaluate → Mass Properties** → read **Mass**
6. Cost = mass × $2.70
7. Go to **Simulation** tab → **Run**
8. Read the new **FoS** from the Factor of Safety plot
9. Record in the table

### C3 — Make the Graph

1. Open **Excel**
2. Enter: Thickness, FoS, Cost
3. Insert a **combo chart**:
   - X-axis: Wall Thickness
   - Left Y-axis: Factor of Safety (line)
   - Right Y-axis: Cost (line or bars)
4. Add a horizontal line at **FoS = 2.0**

### C4 — Cost Comparison

| | Original design | Safe design |
|---|:-:|:-:|
| Wall thickness | 5 mm | 8 mm |
| Min FoS | 1.38 | 2.21 |
| Mass | 6.58 kg | 10.73 kg |
| Material cost | $17.75 | $28.97 |

**% cost increase** = (28.97 − 17.75) / 17.75 × 100 = **63.2%**

> The original tank (t = 5 mm) does **not** meet the FoS ≥ 2.0 requirement (FoS = 1.38).
> The minimum wall thickness for FoS ≥ 2.0 is **8 mm** (FoS = 2.21),
> which increases material cost by **63%** — from $17.75 to $28.97.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Body is solid, not hollow | You forgot **Shell** (step A7). Features → Shell → select bottom face → 5 mm |
| Hole goes through both sides | You used "Through All". Undo → redo with **Up To Next** |
| Cut arrow points wrong way | Click **Reverse Direction** |
| Can't select inner faces | Use **Section View** (View → Display → Section View) |
| Pressure arrows point inward | Click **Reverse direction** in the Pressure dialog |
| Sketch lines are blue | Add more dimensions (**D**) until all lines are black |
| Arc won't connect to axis | Add a **Coincident** relation between arc endpoint and centreline |
