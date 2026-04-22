#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manifest2records_seisint.py - Split the seisint manifest into individual
JSON record files under demo/seisint/records/.

One file per record, named from the record id, prefixed with a sequence
number that encodes dependency order (ref-data → master-data → WPC).

Storage API requires records one at a time in dependency order so that
parent records are indexed before their children.

Usage:
  python manifest2records_seisint.py
  python manifest2records_seisint.py --outdir records --namespace dev
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent  # demo/seisint


def _sanitize(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', s)[:200]


def _flatten_manifest(man: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract all records from a Manifest envelope, in dependency order."""
    out: List[Dict[str, Any]] = []

    # 1. Reference data first
    for r in man.get("ReferenceData", []):
        if isinstance(r, dict) and "data" in r:
            out.append(r)

    # 2. Master data
    for r in man.get("MasterData", []):
        if isinstance(r, dict) and "data" in r:
            out.append(r)

    data = man.get("Data", {})

    # 3. Datasets
    for r in data.get("Datasets", []):
        if isinstance(r, dict) and "data" in r:
            out.append(r)

    # 4. WorkProduct (single dict)
    wp = data.get("WorkProduct")
    if isinstance(wp, dict) and wp.get("data"):
        out.append(wp)

    # 5. WorkProductComponents - sorted by kind so that referenced
    #    entities (BinGrid, Interpretation) come before referencing
    #    ones (StructureMap, SeismicHorizon)
    wpcs = data.get("WorkProductComponents", [])
    kind_order = {
        "HorizonInterpretation": 0,
        "SeismicBinGrid": 1,
        "GenericBinGrid": 2,
        "SeismicHorizon": 3,
        "StructureMap": 4,
    }

    def _wpc_sort_key(r: dict) -> int:
        kind = r.get("kind", "")
        for frag, order in kind_order.items():
            if frag in kind:
                return order
        return 99

    wpcs_sorted = sorted(wpcs, key=_wpc_sort_key)
    for r in wpcs_sorted:
        if isinstance(r, dict) and "data" in r:
            out.append(r)

    return out


def split_manifest(manifest_path: Path, out_dir: Path, namespace: str) -> int:
    """Split a single manifest into numbered record files."""
    out_dir.mkdir(parents=True, exist_ok=True)

    text = manifest_path.read_text(encoding="utf-8")
    obj = json.loads(text)
    records = _flatten_manifest(obj) if isinstance(obj, dict) else obj

    count = 0
    for seq, rec in enumerate(records):
        if not isinstance(rec, dict) or "data" not in rec:
            continue

        # Optionally replace namespace prefix
        rid = rec.get("id", f"rec_{seq}")
        if isinstance(rid, str) and ":" in rid:
            parts = rid.split(":", 1)
            rid_ns = f"{namespace}:{parts[1]}"
            rec["id"] = rid_ns
        else:
            rid_ns = rid

        fname = f"{seq:03d}_{_sanitize(rid_ns)}.json"
        (out_dir / fname).write_text(
            json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        count += 1
        kind = rec.get("kind", "?")
        name = rec.get("data", {}).get("Name", "")
        print(f"  {fname:60s}  {kind}  {name}")

    print(f"\nTotal: {count} record files in {out_dir}/")
    return count


def main():
    ap = argparse.ArgumentParser(
        description="Split seisint manifest into individual record JSONs"
    )
    ap.add_argument(
        "--manifest",
        default=str(SCRIPT_DIR / "manifest_volantis_interp.json"),
        help="Path to the manifest JSON",
    )
    ap.add_argument("--outdir", default=str(SCRIPT_DIR / "records"))
    ap.add_argument("--namespace", default="dev")
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}")
        print("Run gen_volantis_interp.py first to generate it.")
        raise SystemExit(1)

    split_manifest(manifest_path, Path(args.outdir), args.namespace)


if __name__ == "__main__":
    main()
