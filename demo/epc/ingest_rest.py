#!/usr/bin/env python3
"""
ingest_rest.py – Import cleaned Drogon OSDU RESQML objects into swedev
RDDMS via the REST transactional API (PUT /resources).

Converts each XML file to RDDMS-compatible JSON (with $type annotations)
and pushes objects in batches within a transaction.

Usage:
    cd demo/epc && python3 ingest_rest.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────── #
BASE = "https://equinorswedev.energy.azure.com/api/reservoir-ddms/v2"
DS = "maap/drogon"
DS_ENC = "maap%2Fdrogon"
SCRIPT_DIR = Path(__file__).resolve().parent
XML_DIR = SCRIPT_DIR / "drogon_osdu"
BATCH_SIZE = 10  # conservative - the server may reject large payloads

TENANT = "3aa4a235-b6e2-48d5-9195-7fcf05b459b0"
CLIENT_ID = "ebd2bfee-ecba-47b7-a33c-017d0131879d"
SCOPE = "7daee810-3f78-40c4-84c2-7a199428de18/.default openid offline_access"


# ── Auth ──────────────────────────────────────────────────────────────────── #
def mint_token() -> str:
    """Mint access token from SWEDEV_REFRESH_TOKEN env or k8s/secret.yaml."""
    rt = os.environ.get("SWEDEV_REFRESH_TOKEN", "")
    if not rt:
        # Fall back to k8s/secret.yaml
        secret = SCRIPT_DIR.parent.parent / "k8s" / "secret.yaml"
        for line in secret.read_text().splitlines():
            s = line.strip()
            if s.startswith("INSTANCE_EQNDEV_REFRESH_TOKEN:"):
                rt = s.split(":", 1)[1].strip().strip('"').strip("'")
                break
    if not rt:
        sys.exit("No refresh token found (set SWEDEV_REFRESH_TOKEN or update k8s/secret.yaml)")
    print(f"  RT: {len(rt)} chars")
    r = httpx.post(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data={"grant_type": "refresh_token", "client_id": CLIENT_ID,
              "refresh_token": rt, "scope": SCOPE},
        timeout=30,
    )
    data = r.json()
    if "error" in data:
        sys.exit(f"Auth error: {data['error']}: {data.get('error_description','')[:200]}")
    token = data["access_token"]
    print(f"  Token: {len(token)} chars, TTL={data.get('expires_in')}s")
    return token


def hdrs(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "data-partition-id": "dev",
            "Content-Type": "application/json"}


# ── XML to RDDMS JSON conversion ─────────────────────────────────────────── #

# Namespace URI to $type prefix
NS_PREFIX = {
    "http://www.energistics.org/energyml/data/resqmlv2": "resqml20",
    "http://www.energistics.org/energyml/data/commonv2": "eml20",
}

# XSD namespace for xsi:type
XSI = "http://www.w3.org/2001/XMLSchema-instance"

# Known complex element names that need $type annotation
# (derived from actual swedev /resources/all JSON output)
TYPED_NAMES = {
    # eml20 namespace
    "Citation", "DataObjectReference", "Hdf5Dataset", "PlaneAngleMeasure",
    "LengthMeasure", "VerticalUnknownCrs", "ProjectedUnknownCrs",
    "VerticalCoordinateUom", "ProjectedCoordinateUom",
    "VerticalCrsEpsgCode", "ProjectedCrsEpsgCode",
    "ObjectAlias",
    # resqml20 namespace
    "NameValuePair", "Point3d",
    "DoubleHdf5Array", "IntegerHdf5Array", "BooleanHdf5Array", "StringHdf5Array",
    "FloatingPointExternalArray", "IntegerExternalArray",
    "IntegerConstantArray", "DoubleConstantArray", "BooleanConstantArray",
    "IntegerLatticeArray", "DoubleLatticeArray",
    "IntegerRangeArray",
    "Point3dHdf5Array", "Point3dParametricArray",
    "Point3dFromRepresentationLatticeArray", "Point3dZValueArray",
    "PatchOfPoints", "PatchOfValues",
    "ParametricLineGeometry", "ParametricLineFromRepresentationGeometry",
    "IjkGridGeometry", "UnstructuredGridGeometry",
    "SplitNodePatch", "SplitColumnEdges",
    "KGaps",
    "WellboreMarker",
    "Seismic3dCoordinates", "Seismic2dCoordinates",
    "LocalPropertyKind", "StandardPropertyKind",
    "ExternalDatasetPart",
}


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _ns(tag: str) -> str:
    return tag.split("}", 1)[0][1:] if tag.startswith("{") else ""


def _prefix(ns_uri: str) -> str:
    return NS_PREFIX.get(ns_uri, "resqml20")


def _try_num(s: str | None):
    if s is None:
        return None
    t = s.strip()
    if not t:
        return None
    if t.lower() == "true":
        return True
    if t.lower() == "false":
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _elem_type(elem: ET.Element) -> str | None:
    """Determine the $type for an element, or None if not typed."""
    local = _local(elem.tag)
    ns = _ns(elem.tag)

    # xsi:type overrides (polymorphic elements like Values, Coordinates, etc.)
    xsi_type = elem.attrib.get(f"{{{XSI}}}type")
    if xsi_type:
        if ":" in xsi_type:
            _, xsi_local = xsi_type.split(":", 1)
        else:
            xsi_local = xsi_type
        prefix = _prefix(ns)
        return f"{prefix}.{xsi_local}"

    # Known typed names
    if local in TYPED_NAMES:
        prefix = _prefix(ns)
        return f"{prefix}.{local}"

    return None


def _elem_to_json(elem: ET.Element) -> dict | str | int | float | bool | None:
    """Recursively convert an XML element to RDDMS JSON."""
    children = list(elem)

    # Non-xsi attributes
    attrs = {k: v for k, v in elem.attrib.items() if not k.startswith(f"{{{XSI}")}

    # Leaf element
    if not children:
        val = _try_num(elem.text)
        if attrs:
            d: dict = {}
            # Add $type if this is a typed element
            t = _elem_type(elem)
            if t:
                d["$type"] = t
            for k, v in attrs.items():
                ak = _local(k) if "}" in k else k
                d[ak] = _try_num(v)
            if elem.text and elem.text.strip():
                d["_"] = val
            return d if d else val
        return val

    # Complex element
    d = {}

    # $type
    t = _elem_type(elem)
    if t:
        d["$type"] = t

    # Non-xsi attributes
    for k, v in attrs.items():
        ak = _local(k) if "}" in k else k
        d[ak] = _try_num(v)

    # Group children by local name
    child_groups: dict[str, list] = {}
    for c in children:
        cn = _local(c.tag)
        child_groups.setdefault(cn, []).append(c)

    for cn, group in child_groups.items():
        if len(group) == 1:
            d[cn] = _elem_to_json(group[0])
        else:
            d[cn] = [_elem_to_json(c) for c in group]

    return d


def xml_to_rddms_json(filepath: Path, rddms_type: str) -> dict:
    """Convert an XML RESQML file to RDDMS JSON with proper $type."""
    tree = ET.parse(filepath)
    root = tree.getroot()
    obj = _elem_to_json(root)
    if isinstance(obj, dict):
        obj["$type"] = rddms_type
    return obj


# ── Dependency ordering ──────────────────────────────────────────────────── #
TYPE_ORDER = {
    "resqml20.obj_EpcExternalPartReference": 0,
    "resqml20.obj_LocalDepth3dCrs": 1,
    "resqml20.obj_LocalTime3dCrs": 1,
    "resqml20.obj_PropertyKind": 2,
    "resqml20.obj_StringTableLookup": 2,
    "resqml20.obj_ActivityTemplate": 2,
    "resqml20.obj_Activity": 3,
    "resqml20.obj_OrganizationFeature": 4,
    "resqml20.obj_TectonicBoundaryFeature": 4,
    "resqml20.obj_GeneticBoundaryFeature": 4,
    "resqml20.obj_BoundaryFeature": 4,
    "resqml20.obj_FrontierFeature": 4,
    "resqml20.obj_StratigraphicUnitFeature": 4,
    "resqml20.obj_WellboreFeature": 4,
    "resqml20.obj_FaultInterpretation": 5,
    "resqml20.obj_HorizonInterpretation": 5,
    "resqml20.obj_GenericFeatureInterpretation": 5,
    "resqml20.obj_StratigraphicUnitInterpretation": 5,
    "resqml20.obj_WellboreInterpretation": 5,
    "resqml20.obj_StratigraphicOccurrenceInterpretation": 6,
    "resqml20.obj_StratigraphicColumnRankInterpretation": 6,
    "resqml20.obj_StratigraphicColumn": 7,
    "resqml20.obj_MdDatum": 7,
    "resqml20.obj_WellboreTrajectoryRepresentation": 8,
    "resqml20.obj_DeviationSurveyRepresentation": 8,
    "resqml20.obj_Grid2dRepresentation": 9,
    "resqml20.obj_IjkGridRepresentation": 9,
    "resqml20.obj_PointSetRepresentation": 9,
    "resqml20.obj_PolylineSetRepresentation": 9,
    "resqml20.obj_TriangulatedSetRepresentation": 9,
    "resqml20.obj_GridConnectionSetRepresentation": 9,
    "resqml20.obj_WellboreFrameRepresentation": 10,
    "resqml20.obj_WellboreMarkerFrameRepresentation": 10,
    "resqml20.obj_ContinuousProperty": 11,
    "resqml20.obj_DiscreteProperty": 11,
    "resqml20.obj_CategoricalProperty": 11,
    "resqml22.obj_GraphicalInformationSet": 12,
}


# ── Main ──────────────────────────────────────────────────────────────────── #
def main():
    print("=== 1. Authenticate ===")
    token = mint_token()
    h = hdrs(token)

    # Load and convert XML files
    print(f"\n=== 2. Convert XML to JSON ===")
    files = sorted(f for f in os.listdir(XML_DIR) if f.startswith("obj_") and f.endswith(".xml"))
    print(f"  Found {len(files)} XML files in {XML_DIR.name}/")

    objects: list[dict] = []
    skipped = 0
    for fn in files:
        m = re.match(r"obj_(.+?)_([0-9a-f-]{36})\.xml", fn)
        if not m:
            skipped += 1
            continue
        obj_type = m.group(1)
        rddms_type = f"resqml20.obj_{obj_type}"
        try:
            obj = xml_to_rddms_json(XML_DIR / fn, rddms_type)
            objects.append(obj)
        except Exception as e:
            print(f"  ERROR {fn}: {e}")
            skipped += 1

    print(f"  Converted: {len(objects)}, skipped: {skipped}")

    # Sort by dependency order
    objects.sort(key=lambda o: TYPE_ORDER.get(o.get("$type", ""), 99))

    # Show type distribution
    type_counts: dict[str, int] = {}
    for o in objects:
        t = o.get("$type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"    {t}: {c}")

    # Test: try putting one simple object first
    print(f"\n=== 3. Test single PUT ===")
    test_obj = None
    for o in objects:
        if "LocalDepth3dCrs" in o.get("$type", ""):
            test_obj = o
            break
    if not test_obj:
        test_obj = objects[0]

    # Begin a test transaction
    r = httpx.post(f"{BASE}/dataspaces/{DS_ENC}/transactions", headers=h, timeout=30)
    if r.status_code != 201:
        print(f"  FAIL begin tx: {r.status_code} {r.text[:300]}")
        sys.exit(1)
    tx_id = r.text.strip().strip('"')
    print(f"  tx: {tx_id}")

    # PUT the test object
    r = httpx.put(
        f"{BASE}/dataspaces/{DS_ENC}/resources",
        headers=h, params={"transactionId": tx_id},
        json=[test_obj], timeout=60,
    )
    print(f"  PUT test ({test_obj['$type']}/{test_obj.get('Uuid','?')}): {r.status_code}")
    if r.status_code >= 400:
        print(f"    Response: {r.text[:500]}")
        # Show what we sent
        print(f"    Sent: {json.dumps(test_obj, indent=2)[:1000]}")
        # Cancel transaction
        httpx.delete(f"{BASE}/dataspaces/{DS_ENC}/transactions/{tx_id}", headers=h, timeout=30)
        sys.exit(1)
    print(f"  Test PUT succeeded!")

    # Cancel test transaction (don't commit yet)
    httpx.delete(f"{BASE}/dataspaces/{DS_ENC}/transactions/{tx_id}", headers=h, timeout=30)
    print(f"  Test tx cancelled")

    # Full import
    print(f"\n=== 4. Full import ({len(objects)} objects) ===")
    r = httpx.post(f"{BASE}/dataspaces/{DS_ENC}/transactions", headers=h, timeout=30)
    if r.status_code != 201:
        print(f"  FAIL begin tx: {r.status_code} {r.text[:300]}")
        sys.exit(1)
    tx_id = r.text.strip().strip('"')
    print(f"  tx: {tx_id}")

    success = 0
    errors = 0
    for i in range(0, len(objects), BATCH_SIZE):
        batch = objects[i:i + BATCH_SIZE]
        batch_types = set(o["$type"] for o in batch)
        try:
            r = httpx.put(
                f"{BASE}/dataspaces/{DS_ENC}/resources",
                headers=h, params={"transactionId": tx_id},
                json=batch, timeout=120,
            )
            if r.status_code < 300:
                success += len(batch)
                print(f"  PUT [{i}:{i+len(batch)}] OK  ({', '.join(sorted(batch_types))})")
            else:
                print(f"  PUT [{i}:{i+len(batch)}] FAIL {r.status_code}: {r.text[:300]}")
                # Try one-by-one
                for obj in batch:
                    r2 = httpx.put(
                        f"{BASE}/dataspaces/{DS_ENC}/resources",
                        headers=h, params={"transactionId": tx_id},
                        json=[obj], timeout=60,
                    )
                    if r2.status_code < 300:
                        success += 1
                        print(f"    ok {obj['$type']}/{obj.get('Uuid','?')}")
                    else:
                        errors += 1
                        print(f"    FAIL {obj['$type']}/{obj.get('Uuid','?')}: {r2.status_code} {r2.text[:200]}")
        except Exception as e:
            errors += len(batch)
            print(f"  PUT [{i}:{i+len(batch)}] EXCEPTION: {e}")

    print(f"\n  Results: {success} success, {errors} errors")

    if errors == 0 or success > errors:
        print(f"\n=== 5. Commit ===")
        r = httpx.put(f"{BASE}/dataspaces/{DS_ENC}/transactions/{tx_id}", headers=h, timeout=300)
        if r.status_code < 300:
            print(f"  COMMITTED ({r.status_code})")
        else:
            print(f"  COMMIT FAILED: {r.status_code} {r.text[:500]}")
    else:
        print(f"\n=== 5. Rollback (too many errors) ===")
        httpx.delete(f"{BASE}/dataspaces/{DS_ENC}/transactions/{tx_id}", headers=h, timeout=30)
        print(f"  Transaction rolled back")
        sys.exit(1)

    # Verify
    print(f"\n=== 6. Verify ===")
    r = httpx.get(f"{BASE}/dataspaces/{DS_ENC}/resources", headers=h, timeout=30)
    if r.status_code == 200:
        total = sum(t["count"] for t in r.json())
        print(f"  Total: {total} objects across {len(r.json())} types")
        for t in r.json():
            print(f"    {t['name']}: {t['count']}")
    print("\nDone.")


if __name__ == "__main__":
    main()
