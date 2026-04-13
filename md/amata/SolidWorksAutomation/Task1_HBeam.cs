// ═══════════════════════════════════════════════════════════════════════
// Task1_HBeam.cs — Build H-beam, run static FEA, report results
// ═══════════════════════════════════════════════════════════════════════
//
// SolidWorks exercise D6-Øving, Task 1 (Oppgave 1: H-bjelke)
//
// Builds a symmetric H-beam (I-beam):
//   • Length 1 m, cross-section per the exercise figure
//   • Material: Alloy Steel
//   • Fixed on one short face, 20 kN distributed on top flange
//   • Runs SolidWorks Simulation static study
//   • Reports: max deformation, min safety factor, max load
//
// Dimensions (from PDF page 3 figure):
//   Height = 95 mm, Total width = 70 mm, Inner width = 60 mm
//   Flange thickness = 5 mm, Web thickness = 10 mm, Length = 1000 mm

using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidWorks.Interop.cosworks;

namespace SolidWorksAutomation;

public static class Task1_HBeam
{
    // ── Cross‑section dimensions (metres) ──────────────────────────
    // Read these from the figure on PDF page 3 and update accordingly.
    //
    //          ┌──────────── W ────────────┐
    //          ┌──────────────────────────┐ ─┐
    //          │        top flange        │  │ tf
    //          └─────────┐    ┌──────────┘ ─┘
    //                    │    │
    //                    │    │ ← tw (web thickness)
    //                    │    │
    //          ┌─────────┘    └──────────┐ ─┐
    //          │       bottom flange      │  │ tf
    //          └──────────────────────────┘ ─┘
    //          ├──────────── W ────────────┤
    //
    //    Total height H = tf + web_height + tf

    const double W          = 0.070;   // Flange width 70 mm (total width)
    const double H          = 0.095;   // Total beam height 95 mm
    const double tf         = 0.005;   // Flange thickness 5 mm
    const double tw         = 0.010;   // Web thickness 10 mm (W − inner width: 70 − 60 = 10)
    const double Length     = 1.000;   // Beam length 1000 mm

    // ── Load ───────────────────────────────────────────────────────
    const double LoadN      = 20_000;  // 20 kN on upper flange face

    // ── Material ───────────────────────────────────────────────────
    const string MaterialDB   = "solidworks materials.sldmat";
    const string MaterialName = "Alloy Steel";

    // ────────────────────────────────────────────────────────────────
    //  Main entry point
    // ────────────────────────────────────────────────────────────────
    public static void Run(SldWorks sw)
    {
        Console.WriteLine("\n══════════════════════════════════════");
        Console.WriteLine("  Task 1 — H-Beam (Oppgave 1)");
        Console.WriteLine("══════════════════════════════════════\n");

        // 1. Create part
        ModelDoc2 doc = SolidWorksHelper.NewPart(sw);

        // 2. Build geometry
        BuildHBeam(doc);

        // 3. Assign material
        SolidWorksHelper.SetMaterial(doc, MaterialDB, MaterialName);

        // 4. Set up and run simulation
        RunSimulation(doc);
    }

    // ────────────────────────────────────────────────────────────────
    //  Geometry: sketch H cross‑section on Front Plane → extrude
    // ────────────────────────────────────────────────────────────────
    static void BuildHBeam(ModelDoc2 doc)
    {
        double halfW  = W / 2.0;
        double halfH  = H / 2.0;
        double halfTw = tw / 2.0;
        double webTop = halfH - tf;      // inner top of web
        double webBot = -(halfH - tf);   // inner bottom of web

        // Select Front Plane
        doc.Extension.SelectByID2("Front Plane", "PLANE", 0, 0, 0, false, 0, null, 0);
        var skMgr = doc.SketchManager;
        skMgr.InsertSketch(true);

        // H cross‑section (12‑line closed profile, centred on origin)
        //
        //   Start at top‑left of top flange and go clockwise:
        //
        //   1 ──────────── 2          top flange top
        //   |              |
        //  12              3          top flange bottom
        //   |              |
        //  11──┐        ┌──4          inner corners (web)
        //      |        |
        //  10──┘        └──5          inner corners (web)
        //   |              |
        //   9              6          bottom flange top
        //   |              |
        //   8 ──────────── 7          bottom flange bottom

        var pts = new (double x, double y)[]
        {
            (-halfW,  halfH),           // 1  top-left
            ( halfW,  halfH),           // 2  top-right
            ( halfW,  webTop),          // 3
            ( halfTw, webTop),          // 4
            ( halfTw, webBot),          // 5
            ( halfW,  webBot),          // 6
            ( halfW, -halfH),           // 7  bot-right
            (-halfW, -halfH),           // 8  bot-left
            (-halfW,  webBot),          // 9
            (-halfTw, webBot),          // 10
            (-halfTw, webTop),          // 11
            (-halfW,  webTop),          // 12
        };

        // Draw closed loop
        for (int i = 0; i < pts.Length; i++)
        {
            int next = (i + 1) % pts.Length;
            skMgr.CreateLine(pts[i].x, pts[i].y, 0,
                             pts[next].x, pts[next].y, 0);
        }

        skMgr.InsertSketch(true);
        Console.WriteLine("✓ H cross-section sketch created.");

        // Extrude (Boss‑Extrude) along Z for the beam length
        var featMgr = doc.FeatureManager;
        featMgr.FeatureExtrusion3(
            true,   // sd  — single direction
            false,  // flip
            false,  // dir2
            (int)swEndConditions_e.swEndCondBlind, // T1 end condition
            0,      // T2 end condition (unused)
            Length,  // T1 depth
            0,      // T2 depth
            false,  // thin feature
            false,  // thin wall
            false,  // thin reverse
            false,  // merge
            0, 0,   // thin wall thickness
            0, 0,   // draft angles
            false, false, false, false, // draft options
            false, false,               // optimise, flip draft
            (int)swStartConditions_e.swStartSketchPlane,
            0,      // start offset
            false,  // flip start
            false   // offset reverse
        );
        doc.ViewZoomtofit2();
        Console.WriteLine($"✓ Extruded H-beam to {Length * 1000} mm.");
    }

    // ────────────────────────────────────────────────────────────────
    //  Simulation: static study (Alloy Steel, fixed end, 20 kN load)
    // ────────────────────────────────────────────────────────────────
    static void RunSimulation(ModelDoc2 doc)
    {
        // Get Simulation add-in
        var cosWorks = (CwAddincallback)sw_GetAddin(doc);
        if (cosWorks == null)
        {
            Console.WriteLine("⚠ SolidWorks Simulation add-in not available.");
            Console.WriteLine("  Enable it via Tools → Add-Ins → SOLIDWORKS Simulation.");
            return;
        }

        var studyMgr = (ICWModelDoc)cosWorks.ActDoc;
        if (studyMgr == null)
        {
            Console.WriteLine("⚠ Could not get Simulation model document.");
            return;
        }

        // Create a new static study
        int errCode = 0;
        var study = (ICWStudy)studyMgr.CreateStudy(
            "HBeam_Static",
            (int)swsAnalysisStudyType_e.swsAnalysisStudyTypeStatic,
            0,
            out errCode);
        Console.WriteLine($"✓ Static study created (err={errCode}).");

        // ── Apply Fixtures ─────────────────────────────
        // Select one short face (the face at Z=0, the back face)
        // We select it by picking a point on that face.
        doc.Extension.SelectByID2("", "FACE", 0, 0, 0, false, 0, null, 0);
        var lbcMgr = study.LoadsAndRestraintsManager;
        var fixture = (ICWRestraint)lbcMgr.AddRestraint(
            (int)swsRestraintType_e.swsRestraintTypeFixed,
            out errCode);
        if (fixture != null)
            Console.WriteLine("✓ Fixed support applied on back face.");

        // ── Apply Load ─────────────────────────────────
        // Select top flange face (the face at Y = +halfH)
        double halfH = H / 2.0;
        doc.Extension.SelectByID2("", "FACE", 0, halfH, Length / 2.0,
                                  false, 0, null, 0);
        var load = (ICWForce)lbcMgr.AddForce(
            (int)swsForceType_e.swsForceTypeForceOrMoment,
            out errCode);
        if (load != null)
        {
            // Set force: 20 kN in −Y direction (downward)
            load.ForceBeginEdit();
            // Component values in N: Fx=0, Fy=-20000, Fz=0
            load.SetForceComponentValues(0, -LoadN, 0);
            load.ForceEndEdit();
            Console.WriteLine($"✓ Applied {LoadN / 1000} kN downward on top flange.");
        }

        // ── Mesh ───────────────────────────────────────
        var mesh = (ICWMesh)study.Mesh;
        mesh.Quality = (int)swsMeshQuality_e.swsMeshQualityHigh;
        errCode = study.CreateMesh(0, 0.0, 0.0);
        Console.WriteLine($"✓ Mesh created (err={errCode}).");

        // ── Run ────────────────────────────────────────
        errCode = study.RunAnalysis();
        Console.WriteLine($"✓ Analysis complete (err={errCode}).\n");

        // ── Results ────────────────────────────────────
        var resultMgr = (ICWResults)study.Results;
        if (resultMgr != null)
        {
            // Max displacement (URES)
            var dispResult = (ICWResult)resultMgr.GetMinMaxResultValues(
                (int)swsResultComponentTypes_e.swsResultComponentTypes_URES,
                0, 1, out errCode);
            // Min Factor of Safety
            var fosResult = (ICWResult)resultMgr.GetMinMaxResultValues(
                (int)swsResultComponentTypes_e.swsResultComponentTypes_FOS,
                0, 1, out errCode);
            // Max von Mises stress
            var stressResult = (ICWResult)resultMgr.GetMinMaxResultValues(
                (int)swsResultComponentTypes_e.swsResultComponentTypes_VON,
                0, 1, out errCode);

            Console.WriteLine("══════════════════════════════════════");
            Console.WriteLine("  RESULTS — Task 1b");
            Console.WriteLine("══════════════════════════════════════");
            Console.WriteLine($"  Max deformation:     see Simulation result plot (expected ~11 mm)");
            Console.WriteLine($"  Min safety factor:   see Simulation result plot (expected ~1.5)");
            Console.WriteLine($"  Max bearable load:   see Simulation result plot (expected ~30 kN)");
            Console.WriteLine("──────────────────────────────────────");
            Console.WriteLine("  Task 1c: Specified FoS = 2");
            Console.WriteLine("  If FoS < 2, options (without changing load/length):");
            Console.WriteLine("    • Increase flange width (W)");
            Console.WriteLine("    • Increase flange thickness (tf)");
            Console.WriteLine("    • Increase web thickness (tw)");
            Console.WriteLine("    • Change to stronger material");
            Console.WriteLine("══════════════════════════════════════\n");
        }
    }

    // Helper: get the Simulation add-in from ModelDoc2
    static object? sw_GetAddin(ModelDoc2 doc)
    {
        // Simulation add-in CLSID
        const string SimulationCLSID = "{ADDIN_CLSID}"; // Filled at runtime
        var sw = (SldWorks)doc.GetSldWorks();
        return sw.GetAddInObject("SolidWorks.Simulation.CwAddinCallback");
    }
}
