#!/usr/bin/env python3
"""
SMDA → RESQML → SMDA roundtrip verification
=============================================

Reads the three SMDA strat-column test files (1 CSV + 2 XLSX), converts each
column header through **two** roundtrip paths:

  Path A:  SMDA  →  RESQML JSON  →  SMDA dict
  Path B:  SMDA  →  OSDU bundle  →  SMDA dict       (via RESQML intermediate)

Then compares every field of the output dict against the original input and
reports any mismatches.

Usage:
    python test_roundtrip.py          # run all three files
    python test_roundtrip.py -v       # verbose: show every field comparison
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure the handler is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stratcolumnhandler import StratColumn  # noqa: E402

HERE = Path(__file__).resolve().parent

# ── Test-file inventory ──────────────────────────────────────────────────
TEST_FILES = [
    ("strat-column-1774434102505.csv",   "csv"),
    ("strat-column-1774434129404.xlsx",  "xlsx"),
    ("strat-column-1774434148583.xlsx",  "xlsx"),
]

SMDA_FIELDS = StratColumn.SMDA_COLUMN_FIELDS


# ── Helpers ──────────────────────────────────────────────────────────────
def _norm(val: Any) -> str | None:
    """Normalise a value for comparison: None / string (preserving whitespace)."""
    if val is None:
        return None
    s = str(val)
    return s if s else None


def load_columns(path: Path, fmt: str) -> List[StratColumn]:
    if fmt == "csv":
        return StratColumn.from_smda_column_csv(str(path))
    else:
        return StratColumn.from_smda_column_xlsx(str(path))


def roundtrip_resqml(col: StratColumn) -> StratColumn:
    """SMDA → RESQML JSON → StratColumn."""
    resqml_objs = col.to_resqml_json()
    # Write to temp file so from_resqml_json can read it
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(resqml_objs, f, indent=2)
        tmp = f.name
    try:
        return StratColumn.from_resqml_json(tmp)
    finally:
        os.unlink(tmp)


def roundtrip_osdu(col: StratColumn) -> StratColumn:
    """SMDA → OSDU bundle → StratColumn."""
    bundle = col.to_osdu_bundle(partition="test")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(bundle, f, indent=2)
        tmp = f.name
    try:
        return StratColumn.from_osdu_bundle(tmp)
    finally:
        os.unlink(tmp)


def compare_column(
    original: StratColumn,
    restored: StratColumn,
    label: str,
    verbose: bool = False,
) -> List[str]:
    """Compare all 15 SMDA fields.  Returns list of mismatch descriptions."""
    orig_d = original.to_smda_column_dict()
    rest_d = restored.to_smda_column_dict()

    errors: List[str] = []
    for fld in SMDA_FIELDS:
        a = _norm(orig_d.get(fld))
        b = _norm(rest_d.get(fld))
        if a != b:
            errors.append(f"  {fld}: {a!r} → {b!r}")
        elif verbose:
            print(f"    ✓ {fld}: {a!r}")
    return errors


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="SMDA ↔ RESQML roundtrip test")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Show every field comparison, not just failures")
    ns = ap.parse_args()

    total_columns = 0
    total_pass = 0
    total_fail = 0
    all_errors: List[Tuple[str, str, List[str]]] = []

    for fname, fmt in TEST_FILES:
        fpath = HERE / fname
        if not fpath.exists():
            print(f"SKIP  {fname}  (file not found)")
            continue

        columns = load_columns(fpath, fmt)
        print(f"\n{'='*72}")
        print(f"FILE: {fname}  ({len(columns)} column headers)")
        print(f"{'='*72}")

        for col in columns:
            total_columns += 1
            cname = col.name

            # ── Path A: SMDA → RESQML → SMDA ──
            try:
                rt_resqml = roundtrip_resqml(col)
                errs_a = compare_column(col, rt_resqml, "RESQML", verbose=ns.verbose)
            except Exception as e:
                errs_a = [f"  EXCEPTION: {e}"]

            # ── Path B: SMDA → OSDU → SMDA ──
            try:
                rt_osdu = roundtrip_osdu(col)
                errs_b = compare_column(col, rt_osdu, "OSDU", verbose=ns.verbose)
            except Exception as e:
                errs_b = [f"  EXCEPTION: {e}"]

            if errs_a or errs_b:
                total_fail += 1
                status = "FAIL"
            else:
                total_pass += 1
                status = "ok"

            if errs_a or errs_b or ns.verbose:
                print(f"\n  [{status}] {cname}")
                if errs_a:
                    print(f"    RESQML roundtrip mismatches:")
                    for e in errs_a:
                        print(f"      {e}")
                elif ns.verbose:
                    print(f"    RESQML roundtrip: all fields match")
                if errs_b:
                    print(f"    OSDU roundtrip mismatches:")
                    for e in errs_b:
                        print(f"      {e}")
                elif ns.verbose:
                    print(f"    OSDU roundtrip: all fields match")

                if errs_a:
                    all_errors.append((fname, f"{cname} [RESQML]", errs_a))
                if errs_b:
                    all_errors.append((fname, f"{cname} [OSDU]", errs_b))

    # ── Summary ──
    print(f"\n{'='*72}")
    print(f"SUMMARY: {total_columns} columns tested, "
          f"{total_pass} passed, {total_fail} failed")
    print(f"{'='*72}")

    if all_errors:
        print(f"\nFailed roundtrips ({len(all_errors)}):")
        for fname, label, errs in all_errors[:20]:  # cap output
            print(f"  {fname} / {label}")
            for e in errs:
                print(f"    {e}")
        if len(all_errors) > 20:
            print(f"  ... and {len(all_errors) - 20} more")
        sys.exit(1)
    else:
        print("\nAll roundtrips passed — input and output SMDA data are identical.")
        sys.exit(0)


if __name__ == "__main__":
    main()
