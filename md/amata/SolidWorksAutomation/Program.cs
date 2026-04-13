// ═══════════════════════════════════════════════════════════════════════
// Program.cs — Entry point — runs Task 1 and/or Task 2
// ═══════════════════════════════════════════════════════════════════════

using SolidWorks.Interop.sldworks;

namespace SolidWorksAutomation;

class Program
{
    static void Main(string[] args)
    {
        Console.WriteLine("╔══════════════════════════════════════════╗");
        Console.WriteLine("║  D6-Øving SolidWorks Automation (C#)    ║");
        Console.WriteLine("║  Tasks 1 & 2 — H-Beam + Pressure Tank  ║");
        Console.WriteLine("╚══════════════════════════════════════════╝\n");

        // Parse arguments: --task1  --task2  --all (default)
        bool runTask1 = args.Contains("--task1") || args.Contains("--all") || args.Length == 0;
        bool runTask2 = args.Contains("--task2") || args.Contains("--all") || args.Length == 0;

        try
        {
            SldWorks sw = SolidWorksHelper.GetApplication();

            if (runTask1) Task1_HBeam.Run(sw);
            if (runTask2) Task2_PressureTank.Run(sw, runSweep: true);

            Console.WriteLine("\n✓ All tasks completed.");
        }
        catch (Exception ex)
        {
            Console.ForegroundColor = ConsoleColor.Red;
            Console.WriteLine($"\n✗ Error: {ex.Message}");
            Console.WriteLine(ex.StackTrace);
            Console.ResetColor();
        }

        Console.WriteLine("\nPress Enter to exit…");
        Console.ReadLine();
    }
}
