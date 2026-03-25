#!/usr/bin/env python3
"""
RDDMS Roundtrip Test
====================

Push a stratigraphic column (from SMDA CSV) to Reservoir DDMS v2 dataspace
``maap/strat``, then fetch all objects back and verify identity including
metadata.

Steps:
  1. Load SMDA column from CSV → build RESQML 2.0.1 objects (same logic as app/strat.py)
  2. Push objects to RDDMS via transactional write (begin → PUT → commit)
  3. Fetch objects back by type+uuid
  4. Compare every field to verify roundtrip identity

Usage:
    python test_rddms_roundtrip.py                        # default CSV + dataspace
    python test_rddms_roundtrip.py --dataspace maap/test  # custom dataspace
    python test_rddms_roundtrip.py -v                     # verbose output

Requires:
    - config/.env or .env with REFRESH_TOKEN, AZURE_TENANT_ID, AZURE_CLIENT_ID, etc.
    - ``requests`` and ``authlib`` installed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Path setup ──────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "resqml"))
sys.path.insert(0, str(REPO_ROOT / "demo" / "strat"))

from resqml.rddms_client import RddmsSession, _make_base_url  # noqa: E402
from resqml.auth import load_env, get_access_token             # noqa: E402
from stratcolumnhandler import StratColumn                     # noqa: E402

# ── Constants ───────────────────────────────────────────────────────────
DEFAULT_CSV = str(Path(__file__).resolve().parent / "strat-column-1774434102505.csv")
DEFAULT_DATASPACE = "maap/strat"

# Namespace for deterministic UUID5 generation (same as app/strat.py)
_RESQML_NS = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
_CONTENT_TYPE_PREFIX = "application/x-resqml+xml;version=2.0;type="

# RESQML type keys
TYPES_IN_ORDER = [
    "resqml20.obj_RockVolumeFeature",
    "resqml20.obj_OrganizationFeature",
    "resqml20.obj_StratigraphicUnitInterpretation",
    "resqml20.obj_StratigraphicColumnRankInterpretation",
    "resqml20.obj_StratigraphicColumn",
]


# ── RESQML object builders (same logic as app/strat.py) ────────────────

def _det_uuid(seed: str) -> str:
    return str(_uuid.uuid5(_RESQML_NS, seed))


def _citation(title: str) -> dict:
    return {
        "$type": "eml20.Citation",
        "Title": title,
        "Originator": "ORES RDDMS Roundtrip Test",
        "Creation": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "Format": "ORES [test-roundtrip v1.0]",
    }


def _ref(typ_short: str, uid: str, title: str) -> dict:
    return {
        "$type": "eml20.DataObjectReference",
        "ContentType": f"{_CONTENT_TYPE_PREFIX}{typ_short}",
        "UUID": uid,
        "Title": title,
    }


def build_resqml_objects(col: StratColumn) -> Dict[str, List[dict]]:
    """Build RDDMS-format RESQML 2.0.1 objects from a StratColumn.

    Uses vendor metadata for a metadata-only column (header from CSV).
    For columns with ranks/units, converts the full structure.
    """
    col_name = col.name
    col_id = col_name

    by_type: Dict[str, List[dict]] = {t: [] for t in TYPES_IN_ORDER}

    # Vendor metadata → ExtraMetadata on the column object
    extra_meta: List[dict] = []
    if col.vendor:
        for k, v in col.vendor.items():
            if v is not None:
                extra_meta.append({"Name": f"smda.{k}", "Value": str(v)})

    if col.ranks:
        # Full column with ranks and units
        rank_refs: List[dict] = []
        for ri, rank in enumerate(col.ranks):
            rank_name = rank.name or f"Rank_{ri}"
            rank_uuid = _det_uuid(f"rank:{col_id}:{rank_name}")
            rank_feat_uuid = _det_uuid(f"rankfeat:{col_id}:{rank_name}")

            by_type["resqml20.obj_OrganizationFeature"].append({
                "$type": "resqml20.obj_OrganizationFeature",
                "SchemaVersion": "2.0",
                "Uuid": rank_feat_uuid,
                "Citation": _citation(rank_name),
                "OrganizationKind": "stratigraphic",
            })

            unit_refs: List[dict] = []
            for ui, unit in enumerate(rank.units):
                name = unit.name or f"Unit_{ui}"
                unit_uuid = _det_uuid(f"unit:{col_id}:{rank_name}:{name}:{ui}")
                feat_uuid = _det_uuid(f"feat:{col_id}:{rank_name}:{name}:{ui}")

                by_type["resqml20.obj_RockVolumeFeature"].append({
                    "$type": "resqml20.obj_RockVolumeFeature",
                    "SchemaVersion": "2.0",
                    "Uuid": feat_uuid,
                    "Citation": _citation(name),
                })

                unit_obj: Dict[str, Any] = {
                    "$type": "resqml20.obj_StratigraphicUnitInterpretation",
                    "SchemaVersion": "2.0",
                    "Uuid": unit_uuid,
                    "Citation": _citation(name),
                    "Domain": "depth",
                    "InterpretedFeature": _ref("obj_RockVolumeFeature", feat_uuid, name),
                }

                unit_extra: List[dict] = []
                if hasattr(unit, "top_age_ma") and unit.top_age_ma is not None:
                    unit_extra.append({"Name": "OlderPossibleAge_Ma", "Value": str(unit.top_age_ma)})
                if hasattr(unit, "base_age_ma") and unit.base_age_ma is not None:
                    unit_extra.append({"Name": "YoungerPossibleAge_Ma", "Value": str(unit.base_age_ma)})
                if hasattr(unit, "color_html") and unit.color_html:
                    unit_extra.append({"Name": "Colour", "Value": unit.color_html})
                if unit_extra:
                    unit_obj["ExtraMetadata"] = unit_extra

                by_type["resqml20.obj_StratigraphicUnitInterpretation"].append(unit_obj)
                unit_refs.append(_ref("obj_StratigraphicUnitInterpretation", unit_uuid, name))

            rank_obj = {
                "$type": "resqml20.obj_StratigraphicColumnRankInterpretation",
                "SchemaVersion": "2.0",
                "Uuid": rank_uuid,
                "Citation": _citation(rank_name),
                "Domain": "depth",
                "OrderingCriteria": "olderToYounger",
                "RankInStratigraphicColumn": ri,
                "InterpretedFeature": _ref("obj_OrganizationFeature", rank_feat_uuid, rank_name),
                "StratigraphicUnits": unit_refs,
            }
            by_type["resqml20.obj_StratigraphicColumnRankInterpretation"].append(rank_obj)
            rank_refs.append(_ref("obj_StratigraphicColumnRankInterpretation", rank_uuid, rank_name))

        col_obj: Dict[str, Any] = {
            "$type": "resqml20.obj_StratigraphicColumn",
            "SchemaVersion": "2.0",
            "Uuid": _det_uuid(f"col:{col_id}"),
            "Citation": _citation(col_name),
            "Ranks": rank_refs,
        }
        if extra_meta:
            col_obj["ExtraMetadata"] = extra_meta
        by_type["resqml20.obj_StratigraphicColumn"].append(col_obj)
    else:
        # Metadata-only column (no ranks) — just a StratigraphicColumn shell
        col_obj = {
            "$type": "resqml20.obj_StratigraphicColumn",
            "SchemaVersion": "2.0",
            "Uuid": _det_uuid(f"col:{col_id}"),
            "Citation": _citation(col_name),
            "Ranks": [],
        }
        if extra_meta:
            col_obj["ExtraMetadata"] = extra_meta
        by_type["resqml20.obj_StratigraphicColumn"].append(col_obj)

    return {k: v for k, v in by_type.items() if v}


# ── Comparison helpers ──────────────────────────────────────────────────

def _normalise_datetime(val: str) -> Optional[str]:
    """Parse a datetime string (ISO or JS Date.toString) into a canonical form.

    RDDMS rewrites ISO timestamps in ExtraMetadata values into JavaScript
    Date.toString() format, e.g. ``2022-03-17T10:16:57`` becomes
    ``Thu Mar 17 2022 10:16:57 GMT+0000 (Coordinated Universal Time)``.
    We normalise both to an ISO string for comparison.
    """
    if not val:
        return val
    from email.utils import parsedate_to_datetime
    import re
    # Try ISO first
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(val.strip(), fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    # Try JS Date.toString: "Thu Mar 17 2022 10:16:57 GMT+0000 (...)"
    m = re.match(
        r'\w{3}\s+(\w{3})\s+(\d{1,2})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})', val)
    if m:
        month_abbr, day, year, time_str = m.groups()
        try:
            from datetime import datetime as _dt
            dt = _dt.strptime(f"{day} {month_abbr} {year} {time_str}",
                              "%d %b %Y %H:%M:%S")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    return val  # can't parse — return as-is


def _compare_obj(sent: dict, received: dict, verbose: bool = False) -> List[str]:
    """Compare a sent RESQML object against its fetched counterpart.

    Returns list of mismatch descriptions.
    """
    errors: List[str] = []
    uid = sent.get("Uuid", "?")
    stype = sent.get("$type", "?")

    # Fields to verify
    checks = [
        ("$type", sent.get("$type"), received.get("$type")),
        ("Uuid", sent.get("Uuid"), received.get("Uuid")),
        ("SchemaVersion", sent.get("SchemaVersion"), received.get("SchemaVersion")),
    ]

    # Citation sub-fields
    s_cit = sent.get("Citation") or {}
    r_cit = received.get("Citation") or {}
    checks.append(("Citation.Title", s_cit.get("Title"), r_cit.get("Title")))
    checks.append(("Citation.Originator", s_cit.get("Originator"), r_cit.get("Originator")))
    checks.append(("Citation.Format", s_cit.get("Format"), r_cit.get("Format")))

    # Type-specific fields
    if "OrganizationKind" in sent:
        checks.append(("OrganizationKind", sent.get("OrganizationKind"),
                        received.get("OrganizationKind")))
    if "Domain" in sent:
        checks.append(("Domain", sent.get("Domain"), received.get("Domain")))
    if "OrderingCriteria" in sent:
        checks.append(("OrderingCriteria", sent.get("OrderingCriteria"),
                        received.get("OrderingCriteria")))
    if "RankInStratigraphicColumn" in sent:
        checks.append(("RankInStratigraphicColumn",
                        sent.get("RankInStratigraphicColumn"),
                        received.get("RankInStratigraphicColumn")))

    # InterpretedFeature reference
    if "InterpretedFeature" in sent:
        s_if = sent["InterpretedFeature"]
        r_if = received.get("InterpretedFeature") or {}
        checks.append(("InterpretedFeature.UUID", s_if.get("UUID"), r_if.get("UUID")))
        checks.append(("InterpretedFeature.Title", s_if.get("Title"), r_if.get("Title")))

    # ExtraMetadata (normalise datetime strings — RDDMS reformats them)
    s_em = {em["Name"]: em["Value"] for em in (sent.get("ExtraMetadata") or [])}
    r_em = {em["Name"]: em["Value"] for em in (received.get("ExtraMetadata") or [])}
    if s_em:
        for k, v in s_em.items():
            rv = r_em.get(k)
            # Normalise datetime values for comparison
            v_norm = _normalise_datetime(v) if "date" in k.lower() else v
            rv_norm = _normalise_datetime(rv) if rv and "date" in k.lower() else rv
            if (rv_norm or rv) != (v_norm or v):
                errors.append(f"ExtraMetadata[{k}]: sent={v!r} got={rv!r}")
            elif verbose:
                note = " (date normalised)" if v_norm != v or (rv and rv_norm != rv) else ""
                print(f"      ✓ ExtraMetadata[{k}] = {v!r}{note}")

    # StratigraphicUnits refs count
    if "StratigraphicUnits" in sent:
        s_units = sent.get("StratigraphicUnits") or []
        r_units = received.get("StratigraphicUnits") or []
        if len(s_units) != len(r_units):
            errors.append(f"StratigraphicUnits count: sent={len(s_units)} got={len(r_units)}")
        else:
            for i, (su, ru) in enumerate(zip(s_units, r_units)):
                if su.get("UUID") != ru.get("UUID"):
                    errors.append(f"StratigraphicUnits[{i}].UUID: sent={su.get('UUID')} got={ru.get('UUID')}")

    # Ranks refs count
    if "Ranks" in sent:
        s_ranks = sent.get("Ranks") or []
        r_ranks = received.get("Ranks") or []
        if len(s_ranks) != len(r_ranks):
            errors.append(f"Ranks count: sent={len(s_ranks)} got={len(r_ranks)}")
        else:
            for i, (sr, rr) in enumerate(zip(s_ranks, r_ranks)):
                if sr.get("UUID") != rr.get("UUID"):
                    errors.append(f"Ranks[{i}].UUID: sent={sr.get('UUID')} got={rr.get('UUID')}")

    for label, s_val, r_val in checks:
        if str(s_val) != str(r_val):
            errors.append(f"{label}: sent={s_val!r} got={r_val!r}")
        elif verbose:
            print(f"      ✓ {label} = {s_val!r}")

    return errors


# ── Main test ───────────────────────────────────────────────────────────

def run_roundtrip(
    csv_path: str,
    dataspace: str,
    max_columns: int = 3,
    verbose: bool = False,
) -> bool:
    """Run the full push → fetch → verify roundtrip.

    Returns True if all checks pass.
    """
    import asyncio

    print(f"\n{'='*72}")
    print(f"RDDMS Roundtrip Test")
    print(f"  CSV:       {csv_path}")
    print(f"  Dataspace: {dataspace}")
    print(f"{'='*72}")

    # ── 1. Token ──
    print("\n[1/5] Acquiring access token ...")
    load_env()
    token = asyncio.run(get_access_token())
    if not token:
        print("  FAIL: Could not acquire access token.")
        print("        Ensure .env / config/.env has REFRESH_TOKEN, AZURE_TENANT_ID, AZURE_CLIENT_ID")
        return False
    print(f"  OK: token length = {len(token)}")

    # ── 2. Load columns from CSV ──
    print("\n[2/5] Loading strat columns from CSV ...")
    columns = StratColumn.from_smda_column_csv(csv_path)
    if not columns:
        print("  FAIL: No columns found in CSV")
        return False
    # Limit to first N for test speed
    test_cols = columns[:max_columns]
    print(f"  OK: {len(columns)} columns total, testing first {len(test_cols)}")
    for c in test_cols:
        print(f"    - {c.name}")

    # ── 3. Build RESQML objects ──
    print("\n[3/5] Building RESQML 2.0.1 objects ...")
    all_objects: List[dict] = []
    # Track objects keyed by (type, uuid) for later verification
    sent_index: Dict[tuple, dict] = {}

    for col in test_cols:
        by_type = build_resqml_objects(col)
        for typ in TYPES_IN_ORDER:
            for obj in by_type.get(typ, []):
                uid = obj["Uuid"]
                all_objects.append(obj)
                sent_index[(obj["$type"], uid)] = obj

    print(f"  OK: {len(all_objects)} objects to push")
    type_counts = {}
    for obj in all_objects:
        t = obj["$type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, n in type_counts.items():
        print(f"    {t}: {n}")

    # ── 4. Push to RDDMS ──
    print(f"\n[4/5] Pushing to RDDMS dataspace '{dataspace}' ...")
    base_url = _make_base_url(os.getenv("OSDU_BASE_URL", ""))
    partition = os.getenv("DATA_PARTITION_ID", "dev")
    sess = RddmsSession(base_url, token, partition)

    # Ensure dataspace exists
    try:
        sess.create_dataspace(dataspace)
        print(f"  Created dataspace '{dataspace}'")
    except Exception as e:
        if "400" in str(e) or "409" in str(e):
            print(f"  Dataspace '{dataspace}' already exists")
        else:
            print(f"  WARN: create_dataspace: {e}")

    # Transactional write
    try:
        tx_id = sess.begin_transaction(dataspace)
        print(f"  Transaction started: {tx_id}")
    except Exception as e:
        print(f"  FAIL: begin_transaction: {e}")
        return False

    try:
        sess.put_resources(dataspace, all_objects, tx_id)
        print(f"  PUT {len(all_objects)} objects — OK")
    except Exception as e:
        print(f"  FAIL: put_resources: {e}")
        try:
            sess.cancel_transaction(dataspace, tx_id)
            print(f"  Transaction rolled back")
        except Exception:
            pass
        return False

    try:
        sess.commit_transaction(dataspace, tx_id)
        print(f"  Transaction committed — OK")
    except Exception as e:
        print(f"  FAIL: commit_transaction: {e}")
        return False

    # ── 5. Fetch back & verify ──
    print(f"\n[5/5] Fetching objects back and verifying ...")
    total_checked = 0
    total_pass = 0
    total_fail = 0
    all_errors: List[tuple] = []

    for (obj_type, uid), sent_obj in sent_index.items():
        total_checked += 1
        title = (sent_obj.get("Citation") or {}).get("Title", "?")
        short_type = obj_type.replace("resqml20.obj_", "")

        try:
            fetched = sess.get_object(dataspace, obj_type, uid, as_json=True)
        except Exception as e:
            total_fail += 1
            err_msg = f"fetch error: {e}"
            all_errors.append((short_type, uid, title, [err_msg]))
            if verbose:
                print(f"    FAIL {short_type}/{uid} ({title}): {err_msg}")
            continue

        # The fetched object may be wrapped in a list or have extra envelope
        if isinstance(fetched, list):
            fetched = fetched[0] if fetched else {}

        errs = _compare_obj(sent_obj, fetched, verbose=verbose)
        if errs:
            total_fail += 1
            all_errors.append((short_type, uid, title, errs))
            if not verbose:
                print(f"    FAIL {short_type} '{title}' ({uid[:8]}...): {len(errs)} mismatches")
        else:
            total_pass += 1
            if verbose:
                print(f"    OK   {short_type} '{title}' ({uid[:8]}...)")

    # ── Summary ──
    print(f"\n{'='*72}")
    print(f"RESULT: {total_checked} objects checked, "
          f"{total_pass} passed, {total_fail} failed")
    print(f"{'='*72}")

    if all_errors:
        print(f"\nMismatches ({len(all_errors)} objects):")
        for short_type, uid, title, errs in all_errors[:20]:
            print(f"\n  {short_type} / {title} ({uid[:12]}...):")
            for e in errs:
                print(f"    - {e}")
        if len(all_errors) > 20:
            print(f"  ... and {len(all_errors) - 20} more")
        return False
    else:
        print("\nAll objects verified — roundtrip identity confirmed.")
        return True


def main():
    ap = argparse.ArgumentParser(description="RDDMS Roundtrip Test")
    ap.add_argument("--csv", default=DEFAULT_CSV,
                    help=f"Path to SMDA column CSV (default: {DEFAULT_CSV})")
    ap.add_argument("--dataspace", default=DEFAULT_DATASPACE,
                    help=f"RDDMS dataspace (default: {DEFAULT_DATASPACE})")
    ap.add_argument("--max-columns", type=int, default=3,
                    help="Max columns from CSV to test (default: 3)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Show every field comparison")
    ns = ap.parse_args()

    ok = run_roundtrip(
        csv_path=ns.csv,
        dataspace=ns.dataspace,
        max_columns=ns.max_columns,
        verbose=ns.verbose,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
