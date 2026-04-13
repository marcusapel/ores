// ═══════════════════════════════════════════════════════════════════════
// SolidWorksHelper.cs — Shared utilities for connecting to SolidWorks
// ═══════════════════════════════════════════════════════════════════════
//
// References required (from your SolidWorks install directory):
//   SolidWorks.Interop.sldworks
//   SolidWorks.Interop.swconst
//   SolidWorks.Interop.cosworks

using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using System.Runtime.InteropServices;

namespace SolidWorksAutomation;

/// <summary>
/// Helper class to connect to a running SolidWorks instance or start one.
/// </summary>
public static class SolidWorksHelper
{
    /// <summary>
    /// Attach to a running SolidWorks or launch a new instance.
    /// </summary>
    public static SldWorks GetApplication()
    {
        SldWorks? sw = null;

        // Try to attach to running instance first
        try
        {
            sw = (SldWorks)Marshal.GetActiveObject("SldWorks.Application");
            Console.WriteLine("✓ Attached to running SolidWorks instance.");
        }
        catch (COMException)
        {
            Console.WriteLine("  SolidWorks not running — launching new instance…");
            var swType = Type.GetTypeFromProgID("SldWorks.Application")
                ?? throw new InvalidOperationException(
                    "SolidWorks is not installed or not registered on this machine.");
            sw = (SldWorks)(Activator.CreateInstance(swType)
                ?? throw new InvalidOperationException("Failed to create SolidWorks instance."));
            sw.Visible = true;
        }

        return sw;
    }

    /// <summary>
    /// Create a new Part document and return the ModelDoc2 handle.
    /// </summary>
    public static ModelDoc2 NewPart(SldWorks sw, string templatePath = "")
    {
        // Use default template if none supplied
        if (string.IsNullOrEmpty(templatePath))
            templatePath = sw.GetUserPreferenceStringValue(
                (int)swUserPreferenceStringValue_e.swDefaultTemplatePart);

        var doc = (ModelDoc2)sw.NewDocument(templatePath, 0, 0, 0);
        if (doc == null)
            throw new InvalidOperationException(
                "Could not create new part. Check that the template path is valid:\n" +
                templatePath);

        Console.WriteLine("✓ New Part document created.");
        return doc;
    }

    /// <summary>Set the active material on the part.</summary>
    public static void SetMaterial(ModelDoc2 doc, string database, string materialName)
    {
        var ext = doc.Extension;
        ext.SetUserPreferenceString(
            (int)swUserPreferenceStringValue_e.swFileSaveAsNameSuggestion,
            0, "");

        // Apply material
        bool ok = doc.SetMaterialPropertyName2(
            "", // configuration
            database,
            materialName);

        if (!ok)
            Console.WriteLine($"⚠ Could not set material '{materialName}' from '{database}'. " +
                              "Check spelling or database path.");
        else
            Console.WriteLine($"✓ Material set: {materialName} ({database})");
    }
}
