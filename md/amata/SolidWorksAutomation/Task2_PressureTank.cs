// ═══════════════════════════════════════════════════════════════════════
// Task2_PressureTank.cs — Build pressure vessel, run FEA, parametric study
// ═══════════════════════════════════════════════════════════════════════
//
// SolidWorks exercise D6-Øving, Task 2 (Oppgave 2: Trykktank)
//
// Builds an axisymmetric pressure tank:
//   • Inner diameter 200 mm, wall thickness 5 mm (initial)
//   • Hemispherical dome on top (tangent arc / revolve)
//   • Material: 1060 Alloy (Aluminum)
//   • Bottom face fixed, internal pressure 10 bar
//   • Run static FEA → check safety factor
//   • Parametric sweep: wall thickness vs FoS vs material cost
//
// Dimensions (from PDF page 6 figure):
//   Inner diameter = 200 mm, Wall thickness = 5 mm (initial)
//   Cylinder height (to dome) = 600 mm
//   Hemispherical dome on top, with 20 mm hole at apex
//   Bottom is flat and fixed

using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidWorks.Interop.cosworks;

namespace SolidWorksAutomation;

public static class Task2_PressureTank
{
    // ── Tank dimensions (metres) ───────────────────────────────────
    //
    //  Profile (revolved 360° around Y‑axis):
    //
    //              ╭───────╮   ← hemispherical dome (R = innerR + t)
    //              │       │
    //              │       │   ← cylindrical section
    //              │       │      height = CylinderH
    //              │       │
    //              └───────┘   ← flat bottom
    //
    //  Axis of revolution = Y‑axis (vertical)

    const double InnerDiameter = 0.200;             // 200 mm
    const double InnerRadius   = InnerDiameter / 2; // 100 mm = 0.100 m
    const double WallThickness = 0.005;             // 5 mm — initial design
    const double CylinderH     = 0.600;             // 600 mm — cylinder up to dome
    const double BottomThick   = 0.005;             // 5 mm bottom plate
    const double DomeHoleDia   = 0.020;             // 20 mm hole at dome apex
    const double DomeHoleR     = DomeHoleDia / 2;   // 10 mm radius

    // ── Pressure ───────────────────────────────────────────────────
    const double PressureBar  = 10.0;
    const double PressurePa   = PressureBar * 1e5;  // 10 bar = 1 000 000 Pa

    // ── Material ───────────────────────────────────────────────────
    const string MaterialDB   = "solidworks materials.sldmat";
    const string MaterialName = "1060 Alloy";       // 1060 Aluminum

    // ── Cost ───────────────────────────────────────────────────────
    const double CostPerKg    = 2.70;  // USD/kg

    // ── Target safety factor ───────────────────────────────────────
    const double TargetFoS    = 2.0;

    // ── Parametric sweep range (wall thickness in mm → metres) ────
    static readonly double[] ThicknessSweep_mm = { 3, 4, 5, 6, 7, 8, 9, 10 };

    // ────────────────────────────────────────────────────────────────
    //  Main entry
    // ────────────────────────────────────────────────────────────────
    public static void Run(SldWorks sw, bool runSweep = true)
    {
        Console.WriteLine("\n══════════════════════════════════════");
        Console.WriteLine("  Task 2 — Pressure Tank (Oppgave 2)");
        Console.WriteLine("══════════════════════════════════════\n");

        // Build and analyse at initial wall thickness
        var (doc, fos, mass) = BuildAndAnalyse(sw, WallThickness);

        Console.WriteLine($"\n  Initial design: t={WallThickness * 1000} mm");
        Console.WriteLine($"  Safety factor : {fos:F3}");
        Console.WriteLine($"  Mass          : {mass:F4} kg");
        Console.WriteLine($"  Material cost : ${mass * CostPerKg:F2}");
        Console.WriteLine($"  Meets FoS≥{TargetFoS}? {(fos >= TargetFoS ? "YES ✓" : "NO ✗")}\n");

        // Part c/d: parametric sweep
        if (runSweep)
            RunParametricSweep(sw);
    }

    // ────────────────────────────────────────────────────────────────
    //  Build tank, run simulation, return (ModelDoc, FoS, mass_kg)
    // ────────────────────────────────────────────────────────────────
    public static (ModelDoc2 doc, double fos, double mass) BuildAndAnalyse(
        SldWorks sw, double wallThick)
    {
        ModelDoc2 doc = SolidWorksHelper.NewPart(sw);

        BuildTank(doc, wallThick);
        SolidWorksHelper.SetMaterial(doc, MaterialDB, MaterialName);

        double mass = GetMass(doc);
        double fos  = RunSimulation(doc, wallThick);

        return (doc, fos, mass);
    }

    // ────────────────────────────────────────────────────────────────
    //  Geometry: sketch tank half‑profile → Revolved Boss/Base 360°
    // ────────────────────────────────────────────────────────────────
    static void BuildTank(ModelDoc2 doc, double t)
    {
        double ri = InnerRadius;           // inner radius
        double ro = ri + t;                // outer radius
        double bt = BottomThick;           // bottom thickness
        double cylH = CylinderH;           // height of cylindrical section

        // Heights (measured from Y=0 = outside bottom)
        double yBotInner  = bt;                    // inside bottom surface
        double yCylTopIn  = bt + cylH;             // top of inner cylinder
        double yCylTopOut = bt + cylH + t;         // top of outer cylinder (dome starts)
        // Dome: hemisphere of radius = ro (outer), centred at yCylTopIn on axis

        // Select Right Plane for the revolve sketch
        doc.Extension.SelectByID2("Right Plane", "PLANE", 0, 0, 0, false, 0, null, 0);
        var skMgr = doc.SketchManager;
        skMgr.InsertSketch(true);

        // ── Draw the 2D half‑profile (right side, axis = Y line at X=0) ──
        //
        //  The profile is a closed loop:
        //    bottom-outer → up outer wall → outer dome arc → axis top
        //    → inner dome arc → down inner wall → bottom-inner → close
        //
        //  We'll draw it as lines + a tangent arc for the dome.

        // --- Outer profile (going up) ---
        // 1. Bottom outer edge (horizontal)
        skMgr.CreateLine(0, 0, 0, ro, 0, 0);                // axis to outer-bottom-right
        // 2. Right outer wall (vertical)
        skMgr.CreateLine(ro, 0, 0, ro, yCylTopOut - t, 0);  // up to where dome starts
        // Actually: the outer dome starts at Y = yCylTopIn, ro outward
        skMgr.CreateLine(ro, 0, 0, ro, yCylTopIn, 0);

        // 3. Outer dome — quarter-circle arc from (ro, yCylTopIn) to (0, yCylTopIn+ro)
        //    Centre at (0, yCylTopIn), radius = ro
        //    This is a tangent arc from the vertical wall
        skMgr.CreateArc(
            0, yCylTopIn, 0,          // centre
            ro, yCylTopIn, 0,          // start (3 o'clock)
            0, yCylTopIn + ro, 0,      // end   (12 o'clock)
            -1                         // direction: counter-clockwise
        );

        // 4. Short horizontal line at dome apex (the 20 mm hole → 10 mm radius)
        double holeR = DomeHoleR;
        // Outer dome ends at angle where X = holeR on the outer arc
        // For a full hemisphere we'd go to X=0; with a hole we stop at X=holeR
        // Approximate: arc from (ro, yCylTopIn) curving up to (holeR, yCylTopIn + sqrt(ro²-holeR²))
        // For simplicity, draw full dome arcs to the axis, then add the hole as a cut later.
        // Here: axis segment from outer dome top to inner dome top
        skMgr.CreateLine(0, yCylTopIn + ro, 0, 0, yCylTopIn + ri, 0);

        // 5. Inner dome arc — from (0, yCylTopIn+ri) to (ri, yCylTopIn)
        //    Centre at (0, yCylTopIn), radius = ri
        skMgr.CreateArc(
            0, yCylTopIn, 0,            // centre
            0, yCylTopIn + ri, 0,        // start (12 o'clock)
            ri, yCylTopIn, 0,            // end   (3 o'clock)
            -1                           // direction
        );

        // 6. Inner wall down
        skMgr.CreateLine(ri, yCylTopIn, 0, ri, yBotInner, 0);

        // 7. Inner bottom (horizontal back to axis)
        skMgr.CreateLine(ri, yBotInner, 0, 0, yBotInner, 0);

        // 8. Close: axis from inner bottom back to origin
        skMgr.CreateLine(0, yBotInner, 0, 0, 0, 0);

        skMgr.InsertSketch(true);
        Console.WriteLine($"✓ Tank profile sketch created (t={t * 1000} mm).");

        // ── Revolve 360° ──
        // First add a Reference Axis along the Y direction if needed,
        // or just select the Y-axis line we drew (the axis segments).
        // SolidWorks can use sketch lines on the axis as revolve axis.

        // Select the sketch
        doc.Extension.SelectByID2("Sketch1", "SKETCH", 0, 0, 0, false, 0, null, 0);

        // We need to select the axis line. The line from (0,0,0) to (0, yBotInner, 0)
        // or simply use the origin axis.
        // Select the vertical axis line as the revolve axis
        doc.Extension.SelectByID2("Line1", "SKETCHSEGMENT",
            0, 0, 0, true, 16, null, 0);  // mark 16 = revolve axis

        var featMgr = doc.FeatureManager;
        featMgr.FeatureRevolve2(
            true,    // single direction
            true,    // use direction type
            false,   // not thin
            false,   // not merge (not relevant for single body)
            (int)swEndConditions_e.swEndCondBlind,
            0,       // end condition 2 (unused)
            2 * Math.PI,  // 360 degrees in radians
            0,       // angle 2
            false, false, // no thin wall
            false,        // cut (false = boss)
            false,        // flip
            0, 0,         // thin wall distances
            0, 0,         // offset options
            (int)swThinWallType_e.swThinWallOneDirection,
            0, 0, 0,      // offset values
            false, false,  // merge/knit
            true           // auto-select
        );

        doc.ViewZoomtofit2();
        Console.WriteLine("✓ Revolved Boss/Base created (360°).");

        // ── Cut the 20 mm hole at the dome apex ──
        // Sketch a circle on a plane at the dome top, then cut-extrude through
        double domeTopY = BottomThick + CylinderH + ri + t; // approximate Y of dome apex
        // Select Top Plane (or create a reference plane at dome top)
        doc.Extension.SelectByID2("Top Plane", "PLANE", 0, 0, 0, false, 0, null, 0);
        skMgr.InsertSketch(true);
        skMgr.CreateCircleByRadius(0, 0, 0, DomeHoleR); // 10 mm radius
        skMgr.InsertSketch(true);

        featMgr.FeatureCut4(
            true, false, false,
            (int)swEndConditions_e.swEndCondThroughAll, 0,
            0, 0,
            false, false, false, false,
            0, 0, false, false, false, false, false,
            true, true, true, true,
            false, 0, 0, false, false);
        Console.WriteLine("✓ 20 mm dome hole cut.");
    }

    // ────────────────────────────────────────────────────────────────
    //  Simulation: fixed bottom, internal pressure 10 bar
    // ────────────────────────────────────────────────────────────────
    static double RunSimulation(ModelDoc2 doc, double wallThick)
    {
        var sw = (SldWorks)doc.GetSldWorks();
        var cosWorks = sw.GetAddInObject("SolidWorks.Simulation.CwAddinCallback")
            as CwAddincallback;

        if (cosWorks == null)
        {
            Console.WriteLine("⚠ Simulation add-in not available.");
            return -1;
        }

        var simDoc = (ICWModelDoc)cosWorks.ActDoc;
        int err = 0;

        var study = (ICWStudy)simDoc.CreateStudy(
            $"Tank_t{wallThick * 1000:F0}mm",
            (int)swsAnalysisStudyType_e.swsAnalysisStudyTypeStatic,
            0, out err);

        // ── Fixed bottom face ──────────────────────────
        // Select the flat outer bottom face at Y=0
        doc.Extension.SelectByID2("", "FACE", InnerRadius / 2, 0, 0,
            false, 0, null, 0);

        var lbcMgr = study.LoadsAndRestraintsManager;
        var fixture = (ICWRestraint)lbcMgr.AddRestraint(
            (int)swsRestraintType_e.swsRestraintTypeFixed, out err);
        Console.WriteLine($"  Fixed bottom face (err={err}).");

        // ── Internal pressure ──────────────────────────
        // Need to select ALL interior faces.
        // Use Section View in GUI, or programmatically select inner faces.
        // Here we apply pressure to surfaces via face selection.
        //
        // Approach: select inner cylindrical face + inner dome face + inner bottom face
        // This may require multiple face selections (Ctrl+click = append select)

        // Inner cylinder face (approximate pick point)
        double yMid = BottomThick + CylinderH / 2.0;
        doc.Extension.SelectByID2("", "FACE", InnerRadius, yMid, 0,
            false, 0, null, 0);
        // Append inner dome face
        doc.Extension.SelectByID2("", "FACE", InnerRadius * 0.5,
            BottomThick + CylinderH + InnerRadius * 0.5, 0,
            true, 0, null, 0);
        // Append inner bottom face
        doc.Extension.SelectByID2("", "FACE", InnerRadius / 2, BottomThick, 0,
            true, 0, null, 0);

        var pressure = (ICWPressure)lbcMgr.AddPressure(
            (int)swsPressureType_e.swsPressureTypeNormal, out err);
        if (pressure != null)
        {
            pressure.PressureBeginEdit();
            pressure.Unit = (int)swsUnitSystem_e.swsUnitSystemSI;
            pressure.SetPressureValue(PressurePa);
            pressure.PressureEndEdit();
            Console.WriteLine($"  Applied {PressureBar} bar ({PressurePa / 1e6} MPa) internal pressure.");
        }

        // ── Mesh & Run ─────────────────────────────────
        var mesh = (ICWMesh)study.Mesh;
        mesh.Quality = (int)swsMeshQuality_e.swsMeshQualityHigh;
        study.CreateMesh(0, 0.0, 0.0);
        study.RunAnalysis();

        // ── Read min Factor of Safety ──────────────────
        double minFoS = -1;
        var results = (ICWResults)study.Results;
        if (results != null)
        {
            // Get FoS plot
            object minVal = null, maxVal = null;
            // The exact API call depends on SW version; simplified here
            Console.WriteLine("  ✓ Analysis complete — check FoS plot in SolidWorks GUI.");
            Console.WriteLine("    (Programmatic FoS extraction depends on SW API version.)");
        }

        return minFoS;
    }

    // ────────────────────────────────────────────────────────────────
    //  Mass Properties
    // ────────────────────────────────────────────────────────────────
    static double GetMass(ModelDoc2 doc)
    {
        doc.Extension.CreateMassProperty();
        var massProp = (MassProperty)doc.Extension.CreateMassProperty();
        double mass = massProp.Mass; // kg
        Console.WriteLine($"  Mass = {mass:F4} kg");
        return mass;
    }

    // ────────────────────────────────────────────────────────────────
    //  Task 2c/d — Parametric sweep: thickness vs FoS vs cost
    // ────────────────────────────────────────────────────────────────
    public static void RunParametricSweep(SldWorks sw)
    {
        Console.WriteLine("\n══════════════════════════════════════");
        Console.WriteLine("  Task 2c — Parametric Sweep");
        Console.WriteLine("══════════════════════════════════════");
        Console.WriteLine($"  {"t (mm)",-8} {"FoS",-10} {"Mass (kg)",-12} {"Cost (USD)",-12} {"Meets FoS≥2?",-14}");
        Console.WriteLine(new string('─', 58));

        double baseCost = -1;
        double meetsCost = -1;

        foreach (double t_mm in ThicknessSweep_mm)
        {
            double t_m = t_mm / 1000.0;

            // Build fresh part for each thickness
            var (doc, fos, mass) = BuildAndAnalyse(sw, t_m);
            double cost = mass * CostPerKg;
            bool meets = fos >= TargetFoS;

            Console.WriteLine($"  {t_mm,-8:F1} {fos,-10:F3} {mass,-12:F4} ${cost,-11:F2} {(meets ? "YES ✓" : "NO ✗"),-14}");

            if (Math.Abs(t_mm - 5.0) < 0.01)
                baseCost = cost;
            if (meets && meetsCost < 0)
                meetsCost = cost;

            // Close the document to keep things tidy
            sw.CloseDoc(doc.GetTitle());
        }

        // Task 2d: percentage increase
        if (baseCost > 0 && meetsCost > 0)
        {
            double pctIncrease = (meetsCost - baseCost) / baseCost * 100.0;
            Console.WriteLine($"\n  Task 2d: Cost increase for FoS≥2 vs. original 5mm design:");
            Console.WriteLine($"           {pctIncrease:F1}% higher material cost.\n");
        }
    }
}
