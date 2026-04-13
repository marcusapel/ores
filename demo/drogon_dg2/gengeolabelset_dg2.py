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
  manifest_geolabelset_dg2.json        — Manifest envelope (consumed by
                                         manifest2records_dg2.py)

Usage:
  python demo/drogon_dg2/gengeolabelset_dg2.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent       # demo/drogon_dg2
DG1_DIR    = SCRIPT_DIR.parent / "drogon"           # demo/drogon

GLS_UUID    = "e4b7a1c3-5f28-4d9e-8a61-7c3d9e0f2b85"
GLS_ID      = f"dev:work-product-component--GeoLabelSet:{GLS_UUID}:1"
MANIFEST_OUT = SCRIPT_DIR / "manifest_geolabelset_dg2.json"


def main() -> None:
    # Generate the raw record via the DG1 script into a temp file
    with tempfile.NamedTemporaryFile(
        suffix=".json", dir=str(SCRIPT_DIR), delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)

    cmd = [
        sys.executable,
        str(DG1_DIR / "gengeolabelset_drogon.py"),
        "--gate", "DG2",
        "--stat-manifest", str(SCRIPT_DIR / "manifest_wpcstat_dg2.json"),
        "--output", str(tmp_path),
        "--record-id", GLS_ID,
        "--parent-wp", "dev:work-product:Drogon-DG2-ConceptSelect:1",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR.parent.parent))
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        sys.exit(result.returncode)

    # Wrap the record in a Manifest envelope
    record = json.loads(tmp_path.read_text(encoding="utf-8"))
    tmp_path.unlink(missing_ok=True)

    manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": [],
        "MasterData": [],
        "Data": {
            "Datasets": [],
            "WorkProductComponents": [record],
            "WorkProducts": [],
        },
    }

    MANIFEST_OUT.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"DG2 GeoLabelSet manifest written → {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
