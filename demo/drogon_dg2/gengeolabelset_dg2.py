#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gengeolabelset_dg2.py — Generate DG2 GeoLabelSet from the DG2 stat REV.

Thin wrapper around the DG1 ``gengeolabelset_drogon.py`` which already
supports ``--gate DG2`` via CLI arguments.  This script just supplies the
correct defaults for the DG2 pipeline.

Reads:
  manifest_wpcstat_dg2.json            — DG2 stat REV manifest
  ../drogon/manifest_masterwp_drogon.json — Reservoir ID, acl, legal

Output:
  records/026_dev_work-product-component--GeoLabelSet_<uuid>_1.json

Usage:
  python demo/drogon_dg2/gengeolabelset_dg2.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent       # demo/drogon_dg2
DG1_DIR    = SCRIPT_DIR.parent / "drogon"           # demo/drogon

GLS_UUID    = "e4b7a1c3-5f28-4d9e-8a61-7c3d9e0f2b85"
GLS_ID      = f"dev:work-product-component--GeoLabelSet:{GLS_UUID}:1"
OUTPUT_FILE = (
    SCRIPT_DIR / "records" /
    f"026_dev_work-product-component--GeoLabelSet_{GLS_UUID}_1.json"
)


def main() -> None:
    cmd = [
        sys.executable,
        str(DG1_DIR / "gengeolabelset_drogon.py"),
        "--gate", "DG2",
        "--stat-manifest", str(SCRIPT_DIR / "manifest_wpcstat_dg2.json"),
        "--output", str(OUTPUT_FILE),
        "--record-id", GLS_ID,
        "--parent-wp", "dev:work-product:Drogon-DG2-ConceptSelect:1",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR.parent.parent))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
