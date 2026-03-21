#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_pipeline.py — Generic OSDU demo pipeline runner.

Drives any dataset / decision-gate pipeline by auto-discovering generator
scripts from a directory, or from a built-in profile, or a JSON config.
Works cross-platform (unlike the .ps1 scripts).

Auto-discovery mode (preferred — works with any DG dataset):
    python demo/run_pipeline.py demo/drogon_dg2               # auto-discover from dir
    python demo/run_pipeline.py demo/my_field_dg3              # any dataset dir
    python demo/run_pipeline.py                                # default: demo/drogon_dg2

Built-in profiles:
    python demo/run_pipeline.py --profile drogon_dg1           # explicit profile
    python demo/run_pipeline.py --profile drogon_dg2

Options:
    python demo/run_pipeline.py demo/drogon_dg2 --skip-ingest  # generate only
    python demo/run_pipeline.py demo/drogon_dg2 --delay 5      # custom delay
    python demo/run_pipeline.py demo/drogon_dg2 --steps 1,2,3  # specific steps
    python demo/run_pipeline.py --config my_pipeline.json       # custom JSON config
    python demo/run_pipeline.py --list                          # list profiles
    python demo/run_pipeline.py --show demo/drogon_dg2          # show discovered steps

Expected directory layout for auto-discovery:
    demo/<dataset>_<gate>/
        _shared.py                          # shared helpers (optional)
        genparamsmanifest_*.py              # step 1: parameters
        genrawmanifest_*.py                 # step 2: raw volumes
        genstatmanifest_*.py                # step 3: statistics
        gen_activity_*.py                   # step 4: activity
        gen_risk_*.py                       # step 5: risks
        gen_documents_*.py                  # step 6: documents
        gen_devconcept_*.py                 # step 6.1: development concept
        gen_businessdecision_*.py           # step 7: business decision
        manifest2records*.py                # step 8: split manifests → records
        ingest_records_batch.py             # step 9: storage API ingestion
        records/                            # output dir for individual records
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent  # ores/
DEMO_DIR = Path(__file__).resolve().parent           # demo/
DEFAULT_DIR = "demo/drogon_dg2"                      # default dataset

# ─────────────────────────────────────────────────────────────────────
# Pipeline step definition
# ─────────────────────────────────────────────────────────────────────

class Step:
    """One pipeline step: run a Python script and optionally check a pre-requisite."""

    def __init__(
        self,
        number: int,
        label: str,
        script: str,
        *,
        prereq: Optional[str] = None,
        optional: bool = False,
        args: Optional[List[str]] = None,
    ):
        self.number = number
        self.label = label
        self.script = script          # relative to repo root
        self.prereq = prereq          # file that must exist before this step
        self.optional = optional
        self.args = args or []

    def __repr__(self) -> str:
        return f"Step({self.number}, {self.label!r}, {self.script!r})"


# ─────────────────────────────────────────────────────────────────────
# Built-in pipeline profiles
# ─────────────────────────────────────────────────────────────────────

PROFILES: Dict[str, Dict[str, Any]] = {
    "drogon_dg1": {
        "name": "Drogon DG1 (Explore / BOD)",
        "base_dir": "demo/drogon",
        "records_dir": "demo/drogon/records",
        "steps": [
            Step(0,  "Split CSV",
                 "demo/drogon/split_valysar.py",
                 optional=True),
            Step(1,  "Reference data (PropertyTypes + FacetRoles)",
                 "demo/drogon/genrefpropertytypes_drogon.py"),
            Step(1.1, "Reference data (FacetRoles)",
                 "demo/drogon/genreffacetrole_drogon.py"),
            Step(2,  "Master data (Reservoir + Segments + WP)",
                 "demo/drogon/genmaster_drogon.py"),
            Step(3,  "RAW volumes WPC",
                 "demo/drogon/genrawmanifest_drogon.py"),
            Step(4,  "Statistics WPC",
                 "demo/drogon/genstatmanifest_drogon.py"),
            Step(5,  "Parameters WPC",
                 "demo/drogon/genparamsmanifest_drogon.py"),
            Step(6,  "Activity",
                 "demo/drogon/gen_activity_drogon.py"),
            Step(6.1, "Risk",
                 "demo/drogon/gen_risk_drogon.py"),
            Step(6.2, "DevelopmentConcept WPC",
                 "demo/drogon/gen_devconcept_drogon.py"),
            Step(7,  "Business Decision",
                 "demo/drogon/gen_businessdecision_drogon.py"),
            Step(8,  "Manifests → records",
                 "demo/drogon/manifest2records_drogon.py"),
            Step(9,  "Storage API ingestion",
                 "demo/drogon/ingest_records_batch.py"),
        ],
    },
    "drogon_dg2": {
        "name": "Drogon DG2 (Concept Select)",
        "base_dir": "demo/drogon_dg2",
        "records_dir": "demo/drogon_dg2/records",
        "depends_on": "drogon_dg1",
        "prereqs": [
            "demo/drogon/manifest_masterwp_drogon.json",
        ],
        "steps": [
            Step(1,  "DG2 Parameters (porosity ×0.8)",
                 "demo/drogon_dg2/genparamsmanifest_dg2.py"),
            Step(2,  "DG2 Raw Volumes (×0.8)",
                 "demo/drogon_dg2/genrawmanifest_dg2.py"),
            Step(3,  "DG2 Statistics",
                 "demo/drogon_dg2/genstatmanifest_dg2.py"),
            Step(4,  "DG2 Activity",
                 "demo/drogon_dg2/gen_activity_dg2.py"),
            Step(5,  "DG2 Risks",
                 "demo/drogon_dg2/gen_risk_dg2.py"),
            Step(6,  "DG2 Documents",
                 "demo/drogon_dg2/gen_documents_dg2.py"),
            Step(6.1, "DG2 DevelopmentConcept WPC",
                 "demo/drogon_dg2/gen_devconcept_dg2.py"),
            Step(7,  "DG2 Business Decision",
                 "demo/drogon_dg2/gen_businessdecision_dg2.py"),
            Step(8,  "Manifests → records",
                 "demo/drogon_dg2/manifest2records_dg2.py"),
            Step(9,  "Storage API ingestion",
                 "demo/drogon_dg2/ingest_records_batch.py"),
        ],
    },
}

# ─────────────────────────────────────────────────────────────────────
# Pipeline runner
# ─────────────────────────────────────────────────────────────────────

class PipelineRunner:
    """Execute a list of Steps with optional filtering."""

    def __init__(
        self,
        profile: Dict[str, Any],
        *,
        skip_ingest: bool = False,
        skip_optional: bool = False,
        only_steps: Optional[set] = None,
        delay: int = 3,
        dry_run: bool = False,
    ):
        self.profile = profile
        self.skip_ingest = skip_ingest
        self.skip_optional = skip_optional
        self.only_steps = only_steps
        self.delay = delay
        self.dry_run = dry_run
        self.results: List[Dict[str, Any]] = []

    # ── colour helpers (ANSI) ───────────────────────────────────────
    @staticmethod
    def _c(text: str, code: int) -> str:
        return f"\033[{code}m{text}\033[0m"

    def _banner(self, step: Step, status: str = "") -> str:
        tag = f"Step {step.number}"
        colour = 36  # cyan
        if status == "skip":
            colour = 90  # grey
        elif status == "fail":
            colour = 31  # red
        elif status == "ok":
            colour = 32  # green
        return self._c(f"═══ {tag}: {step.label} ═══", colour)

    # ── step execution ──────────────────────────────────────────────
    def _should_run(self, step: Step) -> bool:
        # Ingest step (always number 9 by convention)
        if step.number == 9 and self.skip_ingest:
            return False
        if step.optional and self.skip_optional:
            return False
        if self.only_steps is not None:
            return step.number in self.only_steps
        return True

    def _run_step(self, step: Step) -> bool:
        if not self._should_run(step):
            print(self._banner(step, "skip") + " (skipped)")
            self.results.append({"step": step.number, "label": step.label, "status": "skipped"})
            return True

        # Pre-requisite check
        if step.prereq:
            path = REPO_ROOT / step.prereq
            if not path.exists():
                print(self._banner(step, "fail"))
                print(f"  Pre-requisite missing: {step.prereq}")
                self.results.append({"step": step.number, "label": step.label, "status": "prereq_missing"})
                return False

        print(self._banner(step))

        script_path = REPO_ROOT / step.script
        if not script_path.exists():
            print(f"  Script not found: {step.script}")
            self.results.append({"step": step.number, "label": step.label, "status": "not_found"})
            return False

        # Build command
        cmd = [sys.executable, str(script_path)] + step.args
        if step.number == 9:  # ingestion step — pass delay
            cmd.extend(["--delay", str(self.delay)])

        if self.dry_run:
            print(f"  [dry-run] {' '.join(cmd)}")
            self.results.append({"step": step.number, "label": step.label, "status": "dry_run"})
            return True

        result = subprocess.run(cmd, cwd=str(REPO_ROOT))
        if result.returncode != 0:
            print(self._banner(step, "fail") + f" (exit {result.returncode})")
            self.results.append({"step": step.number, "label": step.label, "status": "failed", "rc": result.returncode})
            return False

        print(self._banner(step, "ok"))
        self.results.append({"step": step.number, "label": step.label, "status": "ok"})
        return True

    # ── pre-requisite checks (profile-level) ────────────────────────
    def _check_prereqs(self) -> bool:
        prereqs = self.profile.get("prereqs", [])
        if not prereqs:
            return True
        print(self._c("═══ Pre-check: required files ═══", 36))
        for p in prereqs:
            path = REPO_ROOT / p
            if not path.exists():
                dep = self.profile.get("depends_on", "?")
                print(f"  MISSING: {p}")
                print(f"  → Run the '{dep}' pipeline first")
                return False
            print(f"  OK {p}")
        return True

    # ── clean records dir ────────────────────────────────────────────
    def _clean_records(self) -> None:
        records_dir = REPO_ROOT / self.profile.get("records_dir", "")
        if not records_dir.is_dir():
            return
        for f in records_dir.glob("*.json"):
            f.unlink()
            print(f"  removed {f.name}")

    # ── main execution ──────────────────────────────────────────────
    def run(self) -> bool:
        name = self.profile.get("name", "Pipeline")
        print(self._c(f"\n{'='*60}", 1))
        print(self._c(f"  {name}", 1))
        print(self._c(f"{'='*60}\n", 1))

        if not self._check_prereqs():
            return False

        steps: List[Step] = self.profile["steps"]
        failed = False

        for step in steps:
            # Clean records dir before manifest→records step
            if step.number == 8 and self._should_run(step):
                self._clean_records()

            if not self._run_step(step):
                failed = True
                break

        # Summary
        print(self._c(f"\n{'='*60}", 1))
        if failed:
            print(self._c("  Pipeline FAILED", 31))
        else:
            print(self._c(f"  {name} — complete", 32))
        print(self._c(f"{'='*60}\n", 1))

        if self.skip_ingest:
            base = self.profile.get("base_dir", ".")
            print(f"  Ingestion skipped. Run manually:")
            print(f"  python {base}/ingest_records_batch.py --delay {self.delay}")

        return not failed


# ─────────────────────────────────────────────────────────────────────
# Custom config loader (JSON)
# ─────────────────────────────────────────────────────────────────────

def load_config_file(path: str) -> Dict[str, Any]:
    """Load a pipeline config from a JSON file.

    Expected format:
    {
        "name": "My Dataset DG3",
        "base_dir": "demo/my_dataset_dg3",
        "records_dir": "demo/my_dataset_dg3/records",
        "depends_on": "drogon_dg2",
        "prereqs": ["demo/drogon_dg2/manifest_bd_dg2.json"],
        "steps": [
            {"number": 1, "label": "Generate params", "script": "demo/my_dataset_dg3/gen_params.py"},
            {"number": 2, "label": "Generate volumes", "script": "demo/my_dataset_dg3/gen_volumes.py"},
            ...
            {"number": 8, "label": "Manifests to records", "script": "demo/my_dataset_dg3/manifest2records.py"},
            {"number": 9, "label": "Ingest", "script": "demo/my_dataset_dg3/ingest_records_batch.py"}
        ]
    }
    """
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Convert step dicts to Step objects
    steps = []
    for s in cfg.get("steps", []):
        steps.append(Step(
            number=s["number"],
            label=s["label"],
            script=s["script"],
            prereq=s.get("prereq"),
            optional=s.get("optional", False),
            args=s.get("args", []),
        ))
    cfg["steps"] = steps
    return cfg


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generic OSDU demo pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Built-in profiles:
  drogon_dg1   Drogon DG1 (Explore / BOD) — full pipeline from CSV
  drogon_dg2   Drogon DG2 (Concept Select) — requires DG1 first

Custom configs:
  python demo/run_pipeline.py --config my_pipeline.json

Example pipeline config JSON:
  {
    "name": "Johan Sverdrup DG3",
    "base_dir": "demo/sverdrup_dg3",
    "records_dir": "demo/sverdrup_dg3/records",
    "steps": [
      {"number": 1, "label": "Parameters", "script": "demo/sverdrup_dg3/gen_params.py"},
      {"number": 8, "label": "Split records", "script": "demo/sverdrup_dg3/manifest2records.py"},
      {"number": 9, "label": "Ingest",        "script": "demo/sverdrup_dg3/ingest_records_batch.py"}
    ]
  }
""",
    )
    parser.add_argument(
        "profile",
        nargs="?",
        choices=list(PROFILES.keys()),
        help="Built-in pipeline profile to run",
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to a custom pipeline config JSON file (overrides profile)",
    )
    parser.add_argument(
        "--skip-ingest", action="store_true",
        help="Generate manifests and records but don't ingest",
    )
    parser.add_argument(
        "--skip-optional", action="store_true",
        help="Skip optional steps (e.g. CSV split)",
    )
    parser.add_argument(
        "--steps",
        help="Comma-separated step numbers to run (e.g. 1,2,3)",
    )
    parser.add_argument(
        "--delay", type=int, default=3,
        help="Seconds between Storage API PUTs (default: 3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print commands without executing",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List built-in profiles and exit",
    )

    args = parser.parse_args()

    if args.list:
        print("\nBuilt-in pipeline profiles:\n")
        for key, prof in PROFILES.items():
            dep = f" (requires: {prof['depends_on']})" if prof.get("depends_on") else ""
            n_steps = len(prof["steps"])
            print(f"  {key:20s}  {prof['name']}{dep}  [{n_steps} steps]")
        print()
        return

    # Resolve config
    if args.config:
        profile = load_config_file(args.config)
    elif args.profile:
        profile = PROFILES[args.profile]
    else:
        parser.error("Specify a profile name or --config file. Use --list to see options.")
        return

    # Parse step filter
    only_steps = None
    if args.steps:
        only_steps = set()
        for s in args.steps.split(","):
            try:
                only_steps.add(float(s) if "." in s else int(s))
            except ValueError:
                parser.error(f"Invalid step number: {s!r}")

    runner = PipelineRunner(
        profile,
        skip_ingest=args.skip_ingest,
        skip_optional=args.skip_optional,
        only_steps=only_steps,
        delay=args.delay,
        dry_run=args.dry_run,
    )

    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
