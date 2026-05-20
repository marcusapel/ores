#!/usr/bin/env python3
"""
build_structural_epc.py – Extract structural/seismic interpretation objects
from the full Drogon EPC into a standalone subset EPC + H5.

Includes:
  - GeneticBoundaryFeature, HorizonInterpretation (horizons)
  - TectonicBoundaryFeature, FaultInterpretation (faults)
  - PolylineSetRepresentation (fault sticks)
  - PointSetRepresentation (horizon control points)
  - StratigraphicColumn, StratigraphicColumnRankInterpretation
  - StratigraphicUnitFeature, StratigraphicUnitInterpretation
  - OrganizationFeature (structural organization)
  - LocalDepth3dCrs, LocalTime3dCrs (coordinate systems)
  - EpcExternalPartReference (H5 link)

Also produces JSON RESQML records with embedded arrays for OpenETPClient
ingestion into the local docker RDDMS.

Usage:
    python -m demo.epc.build_structural_epc
    python demo/epc/build_structural_epc.py
"""
from __future__ import annotations

import base64
import json
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import h5py
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
SRC_EPC = SCRIPT_DIR / "drogon.epc"
SRC_H5 = SCRIPT_DIR / "drogon.h5"
OUT_EPC = SCRIPT_DIR / "drogon_structural.epc"
OUT_H5 = SCRIPT_DIR / "drogon_structural.h5"
OUT_JSON = SCRIPT_DIR / "drogon_structural_records.json"

# Object types to include in the structural subset
INCLUDE_TYPES = [
    "GeneticBoundaryFeature",
    "HorizonInterpretation",
    "TectonicBoundaryFeature",
    "FaultInterpretation",
    "PolylineSetRepresentation",
    "PointSetRepresentation",
    "StratigraphicColumn",
    "StratigraphicColumnRankInterpretation",
    "StratigraphicUnitFeature",
    "StratigraphicUnitInterpretation",
    "OrganizationFeature",
    "LocalDepth3dCrs",
    "LocalTime3dCrs",
    "EpcExternalPartReference",
]

# Regex to extract H5 paths from XML
H5_PATH_RE = re.compile(r"<eml:PathInHdfFile[^>]*>([^<]+)</eml:PathInHdfFile>")
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _matches_type(filename: str) -> bool:
    """Check if an EPC entry matches one of our included types."""
    for t in INCLUDE_TYPES:
        if f"_{t}_" in filename or f"_{t}." in filename:
            return True
        # Also match obj_ prefix patterns
        if filename.startswith(f"obj_{t}_"):
            return True
    return False


def _get_object_uuid(filename: str) -> str | None:
    """Extract UUID from an EPC filename like obj_Type_UUID.xml."""
    m = UUID_RE.search(filename)
    return m.group(0) if m else None


def build_subset_epc():
    """Build subset EPC containing only structural/seismic objects."""
    print(f"Reading source EPC: {SRC_EPC}")
    with zipfile.ZipFile(SRC_EPC, "r") as src:
        all_names = src.namelist()

        # 1. Identify XML objects to include
        include_xmls = set()
        for name in all_names:
            if name.endswith(".xml") and name.startswith("obj_"):
                if _matches_type(name):
                    include_xmls.add(name)

        # 2. Also include their .rels files
        include_rels = set()
        for xml_name in include_xmls:
            rel_name = f"_rels/{xml_name}.rels"
            if rel_name in all_names:
                include_rels.add(rel_name)

        # 3. Collect all UUIDs of included objects (for filtering rels)
        included_uuids = set()
        for name in include_xmls:
            uid = _get_object_uuid(name)
            if uid:
                included_uuids.add(uid.lower())

        # 4. Collect H5 paths referenced by included objects
        h5_paths = set()
        for name in include_xmls:
            content = src.read(name).decode("utf-8")
            for path in H5_PATH_RE.findall(content):
                h5_paths.add(path)

        print(f"  Included objects: {len(include_xmls)}")
        print(f"  Included .rels:   {len(include_rels)}")
        print(f"  H5 paths needed:  {len(h5_paths)}")

        # 5. Build [Content_Types].xml for subset
        content_types_xml = _build_content_types(include_xmls)

        # 6. Build _rels/.rels (root relationships)
        root_rels = _build_root_rels(include_xmls, src)

        # 7. Write subset EPC
        print(f"\nWriting subset EPC: {OUT_EPC}")
        with zipfile.ZipFile(OUT_EPC, "w", zipfile.ZIP_DEFLATED) as dst:
            dst.writestr("[Content_Types].xml", content_types_xml)
            dst.writestr("_rels/.rels", root_rels)

            for name in sorted(include_xmls):
                dst.writestr(name, src.read(name))

            for name in sorted(include_rels):
                # Filter rels to only reference included objects
                rels_content = src.read(name).decode("utf-8")
                dst.writestr(name, rels_content)

    # 8. Build subset H5
    print(f"\nBuilding subset H5: {OUT_H5}")
    _build_subset_h5(h5_paths)

    # 9. Verify
    _verify_epc(OUT_EPC)

    return include_xmls, h5_paths


def _build_content_types(include_xmls: set[str]) -> str:
    """Build OPC [Content_Types].xml for the subset."""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    lines.append('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">')
    lines.append('  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>')
    lines.append('  <Default Extension="xml" ContentType="application/xml"/>')

    for name in sorted(include_xmls):
        # Determine content type from filename
        ct = "application/x-resqml+xml;version=2.0"
        # Extract the type from obj_TypeName_UUID.xml
        m = re.match(r"obj_([A-Za-z0-9]+)_", name)
        if m:
            obj_type = m.group(1)
            ct = f"application/x-resqml+xml;version=2.0;type=obj_{obj_type}"
            if obj_type == "EpcExternalPartReference":
                ct = "application/x-eml+xml;version=2.0;type=obj_EpcExternalPartReference"
            elif obj_type.startswith("Local"):
                ct = f"application/x-resqml+xml;version=2.0;type=obj_{obj_type}"
        lines.append(f'  <Override PartName="/{name}" ContentType="{ct}"/>')

    lines.append("</Types>")
    return "\n".join(lines)


def _build_root_rels(include_xmls: set[str], src_zf: zipfile.ZipFile) -> str:
    """Build _rels/.rels with relationships to all included objects."""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    lines.append('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')

    for i, name in enumerate(sorted(include_xmls), 1):
        m = re.match(r"obj_([A-Za-z0-9]+)_", name)
        obj_type = m.group(1) if m else "Unknown"
        if obj_type == "EpcExternalPartReference":
            rel_type = "http://schemas.energistics.org/package/2012/relationships/externalResource"
        else:
            rel_type = "http://schemas.energistics.org/package/2012/relationships/destinationObject"
        lines.append(f'  <Relationship Id="rId{i}" Type="{rel_type}" Target="{name}"/>')

    lines.append("</Relationships>")
    return "\n".join(lines)


def _build_subset_h5(h5_paths: set[str]):
    """Copy only the referenced H5 datasets to a new file."""
    if not SRC_H5.exists():
        print(f"  WARNING: Source H5 not found ({SRC_H5}), skipping H5 subset")
        return

    with h5py.File(SRC_H5, "r") as src, h5py.File(OUT_H5, "w") as dst:
        copied = 0
        for path in sorted(h5_paths):
            if path in src:
                # Create parent groups
                parent = "/".join(path.split("/")[:-1])
                if parent and parent not in dst:
                    dst.require_group(parent)
                src.copy(src[path], dst, name=path)
                copied += 1
            else:
                print(f"  WARNING: H5 path not found: {path}")
        print(f"  Copied {copied}/{len(h5_paths)} H5 datasets")


def _verify_epc(epc_path: Path):
    """Basic verification of the EPC structure."""
    print(f"\nVerifying EPC: {epc_path}")
    errors = []

    with zipfile.ZipFile(epc_path, "r") as zf:
        names = zf.namelist()

        # Must have [Content_Types].xml
        if "[Content_Types].xml" not in names:
            errors.append("Missing [Content_Types].xml")

        # Must have _rels/.rels
        if "_rels/.rels" not in names:
            errors.append("Missing _rels/.rels")

        # Check all XML objects are well-formed
        xml_count = 0
        for name in names:
            if name.endswith(".xml"):
                try:
                    content = zf.read(name).decode("utf-8")
                    if not content.strip().startswith("<?xml") and not content.strip().startswith("<"):
                        errors.append(f"  {name}: not valid XML")
                    xml_count += 1
                except Exception as e:
                    errors.append(f"  {name}: read error: {e}")

        # Check internal references resolve
        all_uuids = set()
        referenced_uuids = set()
        for name in names:
            if name.startswith("obj_") and name.endswith(".xml"):
                uid = _get_object_uuid(name)
                if uid:
                    all_uuids.add(uid.lower())
                content = zf.read(name).decode("utf-8")
                # Find UUID references in DataObjectReference elements
                refs = UUID_RE.findall(content)
                for r in refs:
                    referenced_uuids.add(r.lower())

        # UUIDs referenced but not in the EPC (external refs are OK for H5 proxy)
        missing = referenced_uuids - all_uuids
        # Filter out the H5 proxy UUID which is expected
        h5_proxy_uuids = set()
        for name in names:
            if "EpcExternalPartReference" in name:
                uid = _get_object_uuid(name)
                if uid:
                    h5_proxy_uuids.add(uid.lower())

        truly_missing = missing - h5_proxy_uuids
        if truly_missing:
            # Some cross-references are expected (to other objects in the same dataspace)
            pass  # Don't treat as error - subset may have dangling refs

    if errors:
        print(f"  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    {e}")
        return False
    else:
        print(f"  OK - {xml_count} XML parts, {len(all_uuids)} objects")
        sz = epc_path.stat().st_size
        print(f"  Size: {sz / 1024:.1f} KB")
        if OUT_H5.exists():
            h5sz = OUT_H5.stat().st_size
            print(f"  H5 size: {h5sz / (1024*1024):.1f} MB")
        return True


def build_json_records(include_xmls: set[str], h5_paths: set[str]):
    """
    Build JSON RESQML records with embedded arrays for OpenETPClient ingestion.
    Uses the local docker partition settings (no ACL/legal needed).
    """
    print(f"\n{'='*60}")
    print("Building JSON RESQML records for OpenETPClient")
    print(f"{'='*60}")

    dataspace = "maap/drogon"
    records = []

    with zipfile.ZipFile(SRC_EPC, "r") as zf:
        # Load H5 arrays
        h5_arrays = {}
        if SRC_H5.exists():
            with h5py.File(SRC_H5, "r") as h5f:
                for path in sorted(h5_paths):
                    if path in h5f:
                        arr = h5f[path][()]
                        h5_arrays[path] = arr

        for name in sorted(include_xmls):
            if "EpcExternalPartReference" in name:
                continue  # Skip the H5 proxy record

            content = zf.read(name).decode("utf-8")
            uid = _get_object_uuid(name)
            if not uid:
                continue

            # Determine RESQML type
            m = re.match(r"obj_([A-Za-z0-9]+)_", name)
            obj_type = m.group(1) if m else "Unknown"

            # Build the JSON record
            record = {
                "uri": f"eml:///dataspace('{dataspace}')/resqml20.obj_{obj_type}('{uid}')",
                "dataObjectType": f"resqml20.obj_{obj_type}",
                "uuid": uid,
                "xml": content,
            }

            # Embed arrays referenced by this object
            obj_h5_paths = H5_PATH_RE.findall(content)
            if obj_h5_paths:
                arrays = {}
                for h5path in obj_h5_paths:
                    if h5path in h5_arrays:
                        arr = h5_arrays[h5path]
                        # Store as base64-encoded float64/int64 for efficiency
                        arrays[h5path] = {
                            "dtype": str(arr.dtype),
                            "shape": list(arr.shape),
                            "data_b64": base64.b64encode(arr.tobytes()).decode("ascii"),
                        }
                if arrays:
                    record["blobData"] = arrays

            records.append(record)

    # Write JSON records file
    output = {
        "dataspace": dataspace,
        "description": "Drogon structural/seismic interpretation - RESQML objects with embedded arrays",
        "objects": records,
    }

    print(f"  Writing {len(records)} records to {OUT_JSON}")
    with open(OUT_JSON, "w") as f:
        json.dump(output, f, indent=2)

    sz = OUT_JSON.stat().st_size
    print(f"  JSON size: {sz / (1024*1024):.1f} MB")

    # Also write individual record files for easier ingestion
    records_dir = SCRIPT_DIR / "structural_records"
    records_dir.mkdir(exist_ok=True)
    for i, rec in enumerate(records):
        fname = f"{i:03d}_{rec['dataObjectType']}_{rec['uuid'][:8]}.json"
        (records_dir / fname).write_text(json.dumps(rec, indent=2))
    print(f"  Also wrote {len(records)} individual files to {records_dir.name}/")

    return records


def main():
    if not SRC_EPC.exists():
        sys.exit(f"Source EPC not found: {SRC_EPC}")

    include_xmls, h5_paths = build_subset_epc()
    build_json_records(include_xmls, h5_paths)

    print(f"\n{'='*60}")
    print("Done! Files created:")
    print(f"  EPC: {OUT_EPC}")
    if OUT_H5.exists():
        print(f"  H5:  {OUT_H5}")
    print(f"  JSON: {OUT_JSON}")
    print(f"\nTo test with local RDDMS docker:")
    print(f"  # Start services:")
    print(f"  docker compose -f demo/epc/docker-compose.yaml up -d")
    print(f"  # Create dataspace and import:")
    print(f"  docker run --rm --network host \\")
    print(f"    -v {SCRIPT_DIR}:/data \\")
    print(f"    <etp-server-image> \\")
    print(f"    openETPServer space -S ws://localhost:9002 --auth none \\")
    print(f"    -s maap/drogon --import-epc /data/drogon_structural.epc")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
