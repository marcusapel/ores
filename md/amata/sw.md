# SolidWorks GUI Tutorial — D6-Øving Step by Step

> A beginner-friendly walkthrough for solving all three tasks using the SolidWorks
> graphical interface. No coding required.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Task 1 — H-Beam](#task-1--h-beam-oppgave-1-h-bjelke)
   - [1a. Build the H-beam model](#1a-build-the-h-beam-model)
   - [1b. Run FEA simulation](#1b-run-fea-simulation)
   - [1c. Check and improve safety factor](#1c-check-and-improve-safety-factor)
3. [Task 2 — Pressure Tank](#task-2--pressure-tank-oppgave-2-trykktank)
   - [2a. Build the tank model](#2a-build-the-tank-model)
   - [2b. Run FEA simulation](#2b-run-fea-simulation)
   - [2c. Parametric study (thickness vs FoS vs cost)](#2c-parametric-study)
   - [2d. Cost comparison](#2d-cost-comparison)
4. [Task 3 — 3D Printing](#task-3--3d-printing)
5. [Tips & Troubleshooting](#tips--troubleshooting)

---

## Getting Started

### Launch SolidWorks

1. Open **SolidWorks** from the Start Menu
2. If prompted, choose **SolidWorks** (not Drawings or Assemblies)
3. Make sure the **Simulation add-in** is active:
   - Go to **Tools → Add-Ins…**
   - Check both boxes next to **SOLIDWORKS Simulation**
   - Click **OK**

### Key Concepts

| Concept | What it means |
|---------|---------------|
| **Sketch** | A 2D drawing on a plane — the starting point of any 3D shape |
| **Feature** | A 3D operation (extrude, revolve, cut, fillet, etc.) |
| **Plane** | A flat reference surface (Front, Top, Right, or custom) |
| **Extrude** | Push a 2D sketch into 3D by giving it depth |
| **Revolve** | Spin a 2D profile around an axis to create a 3D shape |
| **Fully defined sketch** | All lines are black (not blue) — every dimension and position is locked down |

### Units Setup

Before starting, set your units:

1. Bottom-right of the screen → click the **unit system** dropdown
2. Choose **MMGS** (millimetres, grams, seconds) — this matches the exercise
3. Alternatively: **Tools → Options → Document Properties → Units → MMGS**

---

## Task 1 — H-Beam (Oppgave 1: H-bjelke)

### 1a. Build the H-beam model

#### Step 1 — Create a New Part

1. **File → New → Part** → OK
2. You should see the three default planes in the graphics area (Front, Top, Right)

#### Step 2 — Open a Sketch on the Front Plane

1. In the **FeatureManager** tree (left panel), click **Front Plane**
2. Click the **Sketch** tab at the top, then click **Sketch** (pencil icon)
   - Or: right-click Front Plane → **Sketch**
3. The view will rotate to face the Front Plane head-on

#### Step 3 — Draw the H Cross-Section

You'll draw the H (I-beam) profile. The H is **wide and short** (flanges are the
long horizontal plates, web is the short vertical connector).

| Dimension | Value |
|-----------|-------|
| Total width (W) | **95 mm** (horizontal) |
| Total height (H) | **70 mm** (vertical) |
| Inner gap between flanges | **60 mm** (on each side of web) |
| Flange thickness (tf) | **5 mm** (top & bottom plates) |
| Web thickness (tw) | **5 mm** (centre vertical plate) |
| All plate thicknesses | **5 mm** (uniform) |
| Beam length | **1000 mm** |

```
    |<─────────────────── 95 mm ──────────────────>|

    ┌──────────────────────────────────────────────┐ ──
    │                 TOP FLANGE                    │  5 mm
    └──────────────────┐          ┌────────────────┘ ──
                       │          │
                       │          │
          45 mm        │  5 mm    │        45 mm
         (gap)         │  (web)   │       (gap)          60 mm
                       │          │
                       │          │
    ┌──────────────────┘          └────────────────┐ ──
    │                BOTTOM FLANGE                  │  5 mm
    └──────────────────────────────────────────────┘ ──

    |<─────────────────── 95 mm ──────────────────>|

    W  = 95 mm  (total horizontal width)
    H  = 70 mm  (total vertical height = 5 + 60 + 5)
    tf =  5 mm  (flange thickness, top & bottom)
    tw =  5 mm  (web thickness, centre vertical plate)
    Gap = 60 mm (vertical space between flanges)
    Overhang = 45 mm each side (flange extends past web)
```

**Drawing approach:**

1. Select the **Line** tool (keyboard shortcut: **L**)
2. Start at the **origin** (the orange crosshair). This will be the centre of the cross-section
3. Draw the outline of the H-shape as a **closed loop** of straight lines:
   - Draw the top-left corner of the top flange
   - Continue clockwise, making all the steps of the H shape
   - Close the loop by clicking back on the starting point

> **Tip:** Don't worry about exact dimensions yet — just get the shape roughly right.
> You'll add precise dimensions in the next step.

**Alternative (easier for beginners):**

Draw the H using **rectangles**:

1. Draw a large rectangle for the full bounding box (W × H)
2. Use **Sketch → Trim Entities** (or press **T**) to cut away the indentations
3. Or draw three overlapping rectangles and merge them

#### Step 4 — Add Dimensions (Smart Dimension)

1. Click **Smart Dimension** (keyboard: **D**)
2. Click on each line segment and type the correct dimension:
   - Click the **top edge** of the top flange → type the total width: `95`
   - Click the **left edge** of the whole cross-section → type the total height: `70`
   - Click the **top flange thickness** edge → type: `5`
   - Click the **web thickness** → type: `5`
   - Or: dimension the inner gap between flanges → type: `60`
3. Use **relations** to ensure symmetry:
   - Select the **origin point** and the vertical web → right-click → **Add Relation → Midpoint**
   - Or: select two symmetric edges → **Add Relation → Equal**

4. **Check:** All sketch lines should turn **black** (fully defined). Blue = under-defined.

#### Step 5 — Extrude the H-Beam

1. Click **Features** tab → **Extruded Boss/Base** (green arrow icon)
   - Or: **Insert → Boss/Base → Extrude**
2. In the left panel:
   - **Direction 1**: Blind
   - **Depth**: `1000` mm (= 1 m)
3. Click the **green check ✓**
4. You now have a 3D H-beam!

#### Step 6 — Assign Material

1. In the FeatureManager tree, right-click **Material \<not specified\>**
2. Click **Edit Material…**
3. Expand **Steel** in the material library
4. Select **Alloy Steel**
5. Click **Apply → Close**
6. The tree now shows **Material \<Alloy Steel\>**

#### Step 7 — Save

1. **File → Save As** → name it `HBeam.SLDPRT` → Save

---

### 1b. Run FEA Simulation

#### Step 8 — Create a New Static Study

1. Click the **Simulation** tab at the top
2. Click **New Study**
3. Name it `Static_20kN`
4. Select **Static** as the study type
5. Click **green check ✓**
6. The interface changes: you'll see a Simulation tree on the left with **Fixtures**, **External Loads**, **Mesh**, etc.

#### Step 9 — Apply Fixed Support (Fixture)

The beam is clamped on one short end.

1. Right-click **Fixtures** in the Simulation tree → **Fixed Geometry…**
2. Click on **one short face** of the beam (the flat end face at one side)
   - Rotate the model by holding the **middle mouse button** to see the end face
   - Click the flat face — it highlights in green
3. Click **green check ✓**
4. Green arrows appear on that face indicating it's fixed

#### Step 10 — Apply the 20 kN Load

1. Right-click **External Loads** → **Force…**
2. Click on the **top surface of the top flange** (the large flat face on top)
3. In the Force panel:
   - Select **Normal** (perpendicular to the face) — this pushes downward
   - If the arrows point upward, check **Reverse direction**
   - Type `20000` N (= 20 kN)
   - Make sure the unit is **N** (Newtons)
4. Click **green check ✓**
5. Purple arrows appear on the top flange

#### Step 11 — Create the Mesh

1. Right-click **Mesh** in the Simulation tree → **Create Mesh…**
2. Use the default settings (or set **Mesh Quality** to **High** for more accuracy)
3. Click **green check ✓**
4. Wait for meshing to complete — the model shows a wireframe mesh

#### Step 12 — Run the Analysis

1. Right-click the study name (`Static_20kN`) → **Run**
   - Or click the **Run** button (green play arrow) in the Simulation toolbar
2. Wait for the solver to finish
3. Three result plots appear automatically:
   - **Stress** (von Mises) — in MPa
   - **Displacement** (URES) — in mm
   - **Strain**

#### Step 13 — Read the Results

**Maximum deformation:**
1. Double-click the **Displacement** plot
2. Read the **max value** in the legend (colour scale on the right)
3. Expected: **≈ 11 mm**

**Maximum von Mises stress:**
1. Double-click the **Stress** plot
2. Read the max value from the legend

**Minimum safety factor:**
1. Right-click **Results** → **Define Factor of Safety Plot…**
2. Set the criterion (von Mises is default)
3. Click **green check ✓**
4. The min FoS is shown in the legend
5. Expected: **≈ 1.5**

**Maximum bearable load:**
- If FoS = 1.5 at 20 kN → max load ≈ 1.5 × 20 = **30 kN**
- (The structure fails when FoS drops to 1.0)

> **For the report:** Take screenshots (Snipping Tool or PrtScn) of each result
> plot showing the values.

---

### 1c. Check and Improve Safety Factor

The specification requires FoS ≥ 2.

**Does the beam meet it?**
- At 20 kN: FoS ≈ 1.5, so **NO**.

**How to improve (without changing load or length):**

| Option | How | Effect |
|--------|-----|--------|
| Increase flange thickness (tf) | Edit Sketch1 → change tf dimension → rebuild → re-run | More cross-section → lower stress |
| Increase web thickness (tw) | Same approach | Stiffer web |
| Increase flange width (W) | Same approach | Wider flanges resist bending more |
| Increase total height (H) | Same approach | Greater moment of inertia |
| Change material | Right-click Material → Edit → choose stronger steel | Higher yield strength |

**To re-run after changes:**

1. In the FeatureManager, double-click **Sketch1** (the cross-section)
2. Double-click the dimension you want to change → type new value → Enter
3. Click **Rebuild** (traffic-light icon, or Ctrl+Q)
4. Go back to Simulation tab → **Run** again
5. Check the new FoS

Iterate until FoS ≥ 2.0.

---

## Task 2 — Pressure Tank (Oppgave 2: Trykktank)

### 2a. Build the Tank Model

#### Step 1 — Create a New Part

1. **File → New → Part** → OK
2. Set units to **MMGS** (if not already)

#### Step 2 — Open a Sketch on the Right Plane

We'll draw the half-profile of the tank and revolve it.

1. Click **Right Plane** in the FeatureManager
2. Start a **Sketch** (right-click → Sketch, or Sketch tab → Sketch)

#### Step 3 — Draw the Axis of Revolution

1. Select the **Centerline** tool (under the Line dropdown, or shortcut: draw a line then set as construction)
   - Go to **Tools → Sketch Entities → Centerline**
2. Draw a **vertical centerline** through the **origin**, going up
   - Click the origin → drag straight up → click to place the end
3. This centerline is the axis of revolution (the tank's vertical centre axis)

#### Step 4 — Draw the Tank Half-Profile

The tank is axisymmetric. You only draw the right half of the cross-section.

| Dimension | Value |
|-----------|-------|
| Inner diameter | **200 mm** (inner radius = 100 mm) |
| Wall thickness (t) | **5 mm** (outer radius = 105 mm) |
| Cylinder height | **600 mm** (from bottom plate to where dome starts) |
| Dome | **hemisphere** on top (half-sphere, R = inner radius) |
| Dome hole | **20 mm** diameter opening at the apex |
| Bottom plate | **5 mm** thick |
| Total inner height | 600 + 100 = **700 mm** (cylinder + dome) |

The profile (looking at the right half):

```
          (axis)
            │  ○ ← 20 mm hole at apex
            │ ╲ ╱
            │╱     ╲  ← outer hemisphere dome (R = 105)
            │       │     (dome sits ON TOP of the cylinder)
            │╲     ╱  ← inner hemisphere dome (R = 100)
            │  ───      
            ├─── ro=105   dome meets cylinder at Y = 605
            │   │
            │   │  ← outer cylindrical wall
            │   │     600 mm tall
            │   │
            │   │
    ────────┤   │  ← outer bottom corner (Y = 0)
    ▒▒▒▒▒▒▒│   │  ← bottom plate (5 mm thick)
    ────────┤   │  ← inner bottom corner (Y = 5)
            │   │
            │   │  ← inner cylindrical wall
            │   │     600 mm tall
            │   │
            ├─── ri=100   inner wall meets dome at Y = 605
            │╲     ╱
            │  ───   ← inner dome
            │
```

**Drawing steps:**

1. **Line tool (L):** Draw the outer bottom edge — horizontal from axis to outer radius:
   - Start: origin `(0, 0)`
   - End: `(105, 0)` — outer radius = 100 + 5 mm wall = 105 mm

2. **Line:** Outer wall going **up** — the 600 mm cylinder:
   - From `(105, 0)` straight up to `(105, 605)` 
   - (605 = 5 mm bottom plate + 600 mm cylinder)

3. **Tangent Arc for the outer dome:**
   - Select **Tangent Arc**: **Tools → Sketch Entities → Tangent Arc**
   - Or: while using the Line tool, move your mouse in an arc direction — SolidWorks auto-switches
   - Start at the top of the outer wall `(105, 605)`
   - Drag the arc **up and to the left**
   - The arc is a **quarter circle** (90°) with radius 105 mm
   - It should end near the axis at `(10, 710)` — NOT exactly at the axis, because there's a **20 mm hole** at the top
   - The endpoint is at X = 10 mm (= hole radius) from the axis

4. **Line:** Short horizontal from the outer dome to the hole edge on the axis side:
   - If the arc doesn't land exactly at X=10, add a short horizontal segment
   - From the outer dome endpoint to `(10, 710)` (outer dome top at hole edge)

5. **Line:** Short vertical **down** along the hole edge:
   - From `(10, 710)` down to `(10, 705)` — this is the wall thickness (5 mm) at the hole
   - This creates the inner rim of the 20 mm hole

6. **Tangent Arc for the inner dome:**
   - Use **Tangent Arc** again
   - Start from the inner hole edge `(10, 705)`
   - Arc **down and to the right** (quarter circle, radius 100 mm)
   - End at `(100, 605)` — the inner wall meets the dome

7. **Line:** Inner wall going **down** — 600 mm cylinder:
   - From `(100, 605)` down to `(100, 5)`
   - (Y=5 because bottom plate is 5 mm thick)

8. **Line:** Inner bottom edge — horizontal **left** back to the axis:
   - From `(100, 5)` to `(0, 5)`

9. **Line:** Close the profile — down along the axis:
   - From `(0, 5)` back to origin `(0, 0)`

> **Important:** The profile must be a **closed loop** (all endpoints connected).

> **Note on the 20 mm hole:** The hole at the dome apex means the profile does NOT
> touch the axis at the top. Instead, it stops 10 mm away (hole radius = 10 mm).
> When revolved 360°, this creates a circular opening of 20 mm diameter at the top
> of the dome.

#### Step 5 — Add Dimensions

1. **Smart Dimension (D):** Add dimensions to each line/arc:
   - Bottom horizontal line: `105` mm (outer radius)
   - Outer vertical wall: `605` mm (5 bottom + 600 cylinder)
   - Wall thickness (distance between outer and inner wall): `5` mm
   - Inner vertical wall: `600` mm
   - Inner bottom horizontal line: `100` mm (inner radius)
   - Bottom plate (distance between Y=0 and Y=5): `5` mm
   - Hole gap at the top (distance from axis to dome edge): `10` mm on each side
   - Dome arcs: radius `105` (outer) and `100` (inner)

2. **Add Relations** to fully define:
   - Select outer arc → **Add Relation → Tangent** to the vertical cylinder wall
   - Select inner arc → **Tangent** to the inner vertical wall
   - If you used Tangent Arc tool, tangency is automatic
   - Select each arc centre → **Coincident** with a point at `(0, 605)` on the axis
     (this ensures both domes are true hemispheres centred at the cylinder top)
   - Inner wall and outer wall → **Parallel** or just dimension the gap to 5 mm

3. All lines should turn **black** when fully defined (no blue = no under-defined geometry)

#### Step 6 — Revolve the Profile (Revolved Boss/Base)

1. Click the **Features** tab
2. Click **Revolved Boss/Base** (circular arrow icon)
   - Or: **Insert → Boss/Base → Revolve**
3. SolidWorks should auto-detect the centerline as the revolve axis
   - If not: click the centerline when prompted for the axis
4. Set angle to **360°**
5. Click **green check ✓**
6. You now have a 3D pressure tank!

#### Step 7 — Assign Material

1. Right-click **Material** in the FeatureManager → **Edit Material…**
2. Expand **Aluminum Alloys**
3. Select **1060 Alloy**
4. Click **Apply → Close**

#### Step 8 — Save

1. **File → Save As** → `PressureTank.SLDPRT` → Save

---

### 2b. Run FEA Simulation

#### Step 9 — Create a New Static Study

1. **Simulation** tab → **New Study**
2. Name: `Pressure_10bar`
3. Type: **Static**
4. Click ✓

#### Step 10 — Fix the Bottom Face

The bottom sits on concrete → fixed.

1. Right-click **Fixtures** → **Fixed Geometry…**
2. Click the **flat outer bottom face** of the tank
   - This is the circular ring on the outside bottom
3. Click ✓

#### Step 11 — Apply Internal Pressure

This is the trickiest part — you need to select the **inside** faces of the tank.

**How to access interior faces (Section View):**

1. In the toolbar: **View → Display → Section View**
   - Or: click the **Section View** button in the Heads-Up toolbar
2. Choose a cutting plane (e.g. **Right Plane**, or **Front Plane**)
3. The model is now cut in half — you can see the inside!
4. **Keep track of which plane you used** (mentioned in the PDF hint)

**Apply the pressure:**

1. Right-click **External Loads** → **Pressure…**
2. Select all **interior surfaces**:
   - Click the **inner cylindrical wall** face
   - Hold **Ctrl** and click the **inner dome** face
   - Hold **Ctrl** and click the **inner bottom** face
   - All selected faces should highlight in green
3. In the Pressure panel:
   - The pressure acts **normally** inward on each face (pushing outward) — this is the default for internal pressure
   - **Value:** `1000000` Pa (since 10 bar = 1,000,000 Pa)
   - **Or** switch the unit dropdown to **bar** if available, then type `10`
4. Check the arrows point **outward** (away from the fluid, into the wall). If they point inward, click **Reverse direction**
5. Click ✓

> **Unit conversion:** 1 bar = 100,000 Pa = 0.1 MPa. So 10 bar = 1,000,000 Pa = 1 MPa.

6. Turn off Section View when done: **View → Display → Section View** (toggle off)

#### Step 12 — Mesh and Run

1. Right-click **Mesh** → **Create Mesh…** → High quality → ✓
2. Right-click user study name → **Run**
3. Wait for results

#### Step 13 — Check Factor of Safety

1. Right-click **Results** → **Define Factor of Safety Plot…**
2. Set:
   - **Criterion:** Max von Mises Stress (default)
   - **Step 1:** set the max stress criterion or accept defaults
3. Click ✓
4. Read the **minimum FoS** from the colour legend
5. **Question:** Is min FoS ≥ 2?
   - If yes → design is acceptable
   - If no → the wall is too thin for 10 bar at this safety margin

---

### 2c. Parametric Study

You need a graph showing **wall thickness vs. safety factor vs. material cost**.

#### Method — Manual (Straightforward)

Repeat the simulation for different wall thicknesses and record results in a table:

| Wall thickness (mm) | Min FoS | Mass (kg) | Cost (USD) at $2.70/kg |
|---------------------|---------|-----------|------------------------|
| 3 | ? | ? | ? |
| 4 | ? | ? | ? |
| 5 | ? | ? | ? |
| 6 | ? | ? | ? |
| 7 | ? | ? | ? |
| 8 | ? | ? | ? |
| 9 | ? | ? | ? |
| 10 | ? | ? | ? |

**For each thickness:**

1. Double-click **Sketch1** in the FeatureManager to edit it
2. Double-click the **wall thickness** dimension → type the new value → Enter
3. Press **Ctrl+Q** to rebuild the model
4. Check the mass:
   - **Evaluate** tab → **Mass Properties**
   - Or: **Tools → Evaluate → Mass Properties**
   - Read the **Mass** value (make sure 1060 Alloy is assigned!)
   - Compute cost: `mass × 2.70`
5. Go to **Simulation** tab → **Run** the study again
6. Check the new **Factor of Safety** plot
7. Record FoS, mass, cost in the table

#### Method — Design Study (Automated)

SolidWorks has a built-in design study feature:

1. Click the **Simulation** tab → at the bottom of the screen, click the **Design Study** tab
   (next to the Model/Motion tabs)
2. **New Design Study**
3. **Variables:** Add the wall thickness dimension as a variable
   - Click **Add Parameter** → select the thickness dimension from Sketch1
   - Set range: 3 mm to 10 mm, step size: 1 mm
4. **Constraints:** Add min FoS as a constraint (FoS ≥ 2.0)
5. **Goals:** Minimise mass (optional)
6. **Run** the design study
7. SolidWorks runs all the simulations automatically and shows results as graphs
8. Export the table/graph for your report

#### Creating the Graph

After collecting the data (either method):

1. Open **Excel** (or Google Sheets)
2. Enter your data: columns for Thickness, FoS, Cost
3. Insert a **combo chart**:
   - **Primary Y-axis:** Factor of Safety (line)
   - **Secondary Y-axis:** Material Cost (bars or line)
   - **X-axis:** Wall Thickness
4. Add a **horizontal reference line** at FoS = 2.0 to show the target
5. Screenshot or export the chart for your report

---

### 2d. Cost Comparison

From your data table:

1. Find the **cost at t = 5 mm** (original design) → `Cost_5mm`
2. Find the **minimum thickness** where FoS ≥ 2.0 → `t_safe`
3. Find the **cost at t_safe** → `Cost_safe`
4. Calculate: `% increase = (Cost_safe − Cost_5mm) / Cost_5mm × 100`

**For the report, write something like:**

> The original tank (t = 5 mm) has a material cost of $X.XX.
> To achieve a safety factor of 2.0, the wall thickness must be increased to Y mm,
> resulting in a material cost of $Z.ZZ.
> This represents a **W%** increase in material cost.

---

## Task 3 — 3D Printing

### Step 1 — Design Your Model

This is a creative task. Build any model you like, respecting the printer
constraints given in your first lecture (typically: max print volume, no
extreme overhangs without supports, minimum wall thickness, etc.).

**Ideas:**
- A phone stand
- A keychain with your name
- A small gear or bracket
- A miniature decoration

### Step 2 — Build in SolidWorks

Use what you've learned:

1. **File → New → Part**
2. **Sketch** on the appropriate plane
3. Use **Extrude**, **Revolve**, **Cut**, **Fillet**, **Chamfer** as needed
4. Remember: **Front Plane = the bottom of the STL print**
   - Build your geometry with the flat/base side on or parallel to the Front Plane

### Step 3 — Export as STL

1. **File → Save As**
2. Change the file type dropdown to **STL (*.stl)**
3. Click **Options** to check:
   - **Output as:** Binary
   - **Resolution:** Fine
   - **Deviation/Angle** tolerances: default is fine for most prints
4. Click **Save**

### Step 4 — Write the Report Section

In your report, include:
- What the model is
- Which SolidWorks features/principles you used (sketch, extrude, revolve, etc.)
- A screenshot of the model in SolidWorks

---

## Tips & Troubleshooting

### Sketch Won't Fully Define (Blue Lines)

- Add more **dimensions** (Smart Dimension, D)
- Add **relations**: select two entities → right-click → Add Relation
  - **Coincident** — points overlap
  - **Horizontal/Vertical** — line constrained to axis
  - **Equal** — two lines same length
  - **Symmetric** — mirror about a line
  - **Tangent** — arc meets line smoothly
- The **Fix** relation pins a point in place (but dimensions are better)

### Can't Select Interior Faces

- Use **Section View** to cut the model open
- You can also **rotate** inside the model by zooming in past the outer surface
- Another option: **right-click a face** → **Select Other…** to cycle through overlapping faces

### Simulation Won't Run

- Check all fixtures and loads have green check marks
- Make sure **material is assigned** (the solver needs density and yield strength)
- Try a coarser mesh first to verify the setup, then increase quality

### Mesh Fails

- Try **Standard** quality instead of High
- Check for very thin geometry or sharp angles — add small **fillets** to sharp corners
- Use **Mesh Control** to refine only certain areas

### Pressure Direction Seems Wrong

- After applying pressure, check the **arrows on the model**
- Arrows should point **outward** from the inner surfaces (pressure pushing the walls out)
- Use **Reverse direction** checkbox if needed

### Rebuilding After Dimension Changes

- **Ctrl+Q** = full rebuild
- **Ctrl+B** = rebuild (lighter version)
- If the model turns red/yellow, there's a rebuild error — check the FeatureManager for ⚠ icons

### Useful Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **S** | Shortcut bar (context-sensitive tools) |
| **L** | Line tool |
| **D** | Smart Dimension |
| **T** | Trim tool |
| **Ctrl+Z** | Undo |
| **Ctrl+Q** | Rebuild all |
| **Space** | View orientation dialog |
| **Middle mouse** | Rotate model |
| **Scroll wheel** | Zoom |
| **Shift + middle mouse** | Pan |

### Where Are My Planes?

If the Front/Top/Right planes are hidden:

1. In the FeatureManager, click the ▶ next to the part name to expand it
2. Right-click **Front Plane** → **Show**
3. Or: **View → Hide/Show → Planes**

---

## Summary Checklist

### Task 1 — H-Beam
- [ ] Part created with H cross-section, extruded 1 m
- [ ] Material: Alloy Steel
- [ ] Simulation: fixed end, 20 kN on top flange
- [ ] Report: max deformation (≈11 mm), min FoS (≈1.5), max load (≈30 kN)
- [ ] Report: does FoS meet 2.0? What changes would fix it?

### Task 2 — Pressure Tank
- [ ] Part created: revolved profile, hemisphere dome, 200 mm inner diameter
- [ ] Material: 1060 Alloy (Aluminum)
- [ ] Simulation: fixed bottom, 10 bar (1 MPa) internal pressure
- [ ] Report: does FoS meet 2.0?
- [ ] Graph: wall thickness vs FoS vs material cost
- [ ] Report: % cost increase for tank meeting FoS ≥ 2 vs. original (5 mm)

### Task 3 — 3D Print
- [ ] Model built in SolidWorks
- [ ] Screenshots in report
- [ ] STL file exported and uploaded

### Submission
- [ ] All questions answered with figures/screenshots
- [ ] Uploaded to Blackboard by **Sunday 27 April 23:59**
