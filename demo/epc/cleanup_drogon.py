#!/usr/bin/env python3
"""
Clean up the Drogon EPC dataset for OSDU compliance.

Reads all XML files from demo/epc/drogon/, applies OSDU-compliant
transformations, and writes cleaned files to demo/epc/drogon_osdu/.

Transformations:
  1. Fix PropertyKind: map "absorbed dose" / "General continuous/discrete"
     to correct RESQML standard kinds
  2. Fix UOM: replace blanket "Euc" with correct physical units
  3. Remove FMU temporary objects (RFT/MLW wells, rescaling props, etc.)
  4. Remove intermediate surfaces (keep final interpreted/extracted)
  5. Remove vendor ExtraMetadata (pdgm/*, roxar/*)
  6. Fix well names: 55_33-X → 55/33-X (OSDU/NPD convention)
  7. Remove duplicate strat columns (keep Geogrid version)
  8. Clean CRS (add EPSG reference via ExtraMetadata)
  9. Remove UuidAuthority="pdgm" attributes

Usage:
    python demo/epc/cleanup_drogon.py              # dry-run (report only)
    python demo/epc/cleanup_drogon.py --apply       # write cleaned files
    python demo/epc/cleanup_drogon.py --apply --manifest  # also generate manifest
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

SRC_DIR = Path(__file__).resolve().parent / "drogon"
DST_DIR = Path(__file__).resolve().parent / "drogon_osdu"

# ── PropertyKind mapping: Title → (standard_kind, uom) ───────────────────────
# These correct the "absorbed dose" fallback and "General continuous/discrete"
# catch-all kinds assigned by Aspen RMS.

PROPERTY_KIND_MAP: dict[str, tuple[str, str]] = {
    # === Well log properties ===
    "VSH":          ("shale volume",               "v/v"),
    "VPHYL":        ("volume fraction",             "v/v"),
    "Vphyl":        ("volume fraction",             "v/v"),
    "PHIT":         ("porosity",                    "v/v"),
    "PHIT_orig":    ("porosity",                    "v/v"),
    "KLOGH":        ("rock permeability",           "mD"),
    "KLOGH_orig":   ("rock permeability",           "mD"),
    "KV":           ("rock permeability",           "mD"),
    "DENS":         ("mass per volume",             "kg/m3"),
    "VP":           ("velocity",                    "m/s"),
    "VS":           ("velocity",                    "m/s"),
    "VPVS":         ("dimensionless",               "Euc"),
    "AI":           ("Rock Impedance",              "kPa.s/m"),
    "AI_ed":        ("Rock Impedance",              "kPa.s/m"),
    "SI":           ("Rock Impedance",              "kPa.s/m"),
    "Sw":           ("saturation",                  "v/v"),
    "Sw_orig":      ("saturation",                  "v/v"),
    "SW":           ("saturation",                  "v/v"),
    "SO":           ("saturation",                  "v/v"),
    "SG":           ("saturation",                  "v/v"),
    "SGU":          ("saturation",                  "v/v"),
    "SWL":          ("saturation",                  "v/v"),
    "SWCR":         ("saturation",                  "v/v"),
    "SWATINIT":     ("saturation",                  "v/v"),
    "Swl":          ("saturation",                  "v/v"),
    "MDepth":       ("depth",                       "m"),

    # === Grid (IjkGrid / Grid2d) properties ===
    "PORO":         ("porosity",                    "v/v"),
    "PERMX":        ("rock permeability",           "mD"),
    "PERMY":        ("rock permeability",           "mD"),
    "PERMZ":        ("rock permeability",           "mD"),
    "Cell_Z":       ("depth",                       "m"),
    "temp":         ("thermodynamic temperature",   "degC"),
    "coalfraction": ("volume fraction",             "v/v"),
    "carbfraction": ("volume fraction",             "v/v"),
    "net_fraction": ("volume fraction",             "v/v"),
    "netfrac_pem":  ("volume fraction",             "v/v"),
    "poro_pem":     ("porosity",                    "v/v"),
    "ntg_pem":      ("net to gross ratio",          "v/v"),
    "sw_oil":       ("saturation",                  "v/v"),
    "sw_oil_H":     ("saturation",                  "v/v"),
    "sw_gas":       ("saturation",                  "v/v"),
    "sw_gas_H":     ("saturation",                  "v/v"),
    "GOC":          ("depth",                       "m"),
    "FWL":          ("depth",                       "m"),
    "FWL_WG":       ("depth",                       "m"),
    "Total_bulk":   ("volume",                      "m3"),
    "Total_pore":   ("volume",                      "m3"),
    "Oil_bulk":     ("volume",                      "m3"),
    "Oil_pore":     ("volume",                      "m3"),
    "Gas_bulk":     ("volume",                      "m3"),
    "Gas_pore":     ("volume",                      "m3"),

    # === Seismic attributes ===
    "seismic--amplitude_near_depth--20180101": ("amplitude", "Euc"),
    "seismic--relai_near_depth--20180101":     ("amplitude", "Euc"),

    # === Discrete properties ===
    "Zone":             ("index",  "Euc"),
    "Geogrid_FACIES":   ("index",  "Euc"),
    "FaultDistance_HUM": ("length", "m"),
    "Facies_Coal":      ("index",  "Euc"),
    "Facies_Calcite":   ("index",  "Euc"),
    "Facies":           ("index",  "Euc"),
    "FACIES":           ("index",  "Euc"),
    "PERF":             ("index",  "Euc"),
    "gridzones":        ("index",  "Euc"),
    "Region":           ("index",  "Euc"),
    "FaultBlock":       ("index",  "Euc"),
    "SATNUM":           ("index",  "Euc"),
    "Satnum":           ("index",  "Euc"),
    "FIPNUM":           ("index",  "Euc"),
    "FIPZON":           ("index",  "Euc"),
    "EQLNUM":           ("index",  "Euc"),
    "PVTNUM":           ("index",  "Euc"),
    "MULTNUM":          ("index",  "Euc"),
    "Satnum_rescaled":  ("index",  "Euc"),
}

# ── Well name correction: RMS underscores → OSDU/NPD slashes ─────────────────

WELL_NAME_MAP: dict[str, str] = {
    "55_33-1":     "55/33-1",
    "55_33-2":     "55/33-2",
    "55_33-3":     "55/33-3",
    "55_33-A-1":   "55/33-A-1",
    "55_33-A-2":   "55/33-A-2",
    "55_33-A-3":   "55/33-A-3",
    "55_33-A-4":   "55/33-A-4",
    "55_33-A-5":   "55/33-A-5",
    "55_33-A-6":   "55/33-A-6",
}

# ── FMU temporary objects: remove objects whose title matches these patterns ──

FMU_REMOVE_TITLE_PATTERNS = [
    r"^RFT_",                     # RFT pseudo-wells
    r"^MLW_",                     # Multi-lateral well artifacts
    r"^RescalingRow$",            # FMU rescaling properties
    r"^RescalingLayer$",
    r"^RescalingColumn$",
    r"^Satnum_weight$",           # FMU rescaling weight
    r"^cellForFaultFace$",        # GridConnectionSet internal
]

# ── FMU temporary surfaces: remove by surface name prefix ─────────────────────
# Keep: DS_interp, DS_velmod, DS_extract_geogrid, TS_interp
# Remove: all workflow-intermediate surfaces

FMU_REMOVE_SURFACE_PREFIXES = [
    "DS_extract_postprocess",
    "DS_extract_simgrid",
    "DS_gf_hum_extracted",
    "DS_gf_initial_extracted",
    "DS_hum_ert_ahm",
    "DS_hum_postiterate_extracted",
    "GS_velocity_dconv",
    "TS_filter",
    "TS_time_extracted",
]

# ── FMU temporary points/lines: remove by name prefix ────────────────────────

FMU_REMOVE_POINT_LINE_PREFIXES = [
    "DP_faults_",         # fault points workflow intermediates
    "DP_filter_",         # filtered depth points
    "DL_faultsticks",     # depth fault sticks (editing)
    "GL_",                # general lines (fault lines intermediate)
]

# ── Vendor ExtraMetadata prefixes to remove ──────────────────────────────────

VENDOR_METADATA_PREFIXES = [
    "pdgm/",
    "roxar/",
]

# ── Strat columns to remove (keep Geogrid, remove Simgrid duplicates) ────────

REMOVE_STRAT_TITLES = [
    "Strati column for Simgrid",
    "Structural model for Simgrid",
    "Earth model for Simgrid",
]


# ═══════════════════════════════════════════════════════════════════════════════
# XML Helpers
# ═══════════════════════════════════════════════════════════════════════════════

NS = {
    "resqml2": "http://www.energistics.org/energyml/data/resqmlv2",
    "eml":     "http://www.energistics.org/energyml/data/commonv2",
    "xsi":     "http://www.w3.org/2001/XMLSchema-instance",
    "xsd":     "http://www.w3.org/2001/XMLSchema",
}

def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _find(elem, *path):
    """Navigate XML by local names (namespace-agnostic)."""
    cur = elem
    for name in path:
        if cur is None:
            return None
        found = None
        for child in cur:
            if _strip_ns(child.tag) == name:
                found = child
                break
        cur = found
    return cur


def _findall(elem, name: str):
    if elem is None:
        return []
    return [c for c in elem if _strip_ns(c.tag) == name]


def _text(elem, *path, default=""):
    e = _find(elem, *path)
    return (e.text or default) if e is not None else default


def _get_title(root) -> str:
    return _text(root, "Citation", "Title")


def _get_uuid(root) -> str:
    return root.attrib.get("uuid", "")


def _get_type(root) -> str:
    tag = _strip_ns(root.tag)
    return tag


def _find_parent(root, target):
    """Find the parent element of 'target' within 'root'."""
    for p in root.iter():
        if target in list(p):
            return p
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Inventory
# ═══════════════════════════════════════════════════════════════════════════════

def inventory(src_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Parse all XML files and return a type→list-of-objects dict."""
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in sorted(src_dir.glob("obj_*.xml")):
        try:
            tree = ET.parse(f)
            root = tree.getroot()
        except ET.ParseError as e:
            print(f"  SKIP {f.name}: {e}", file=sys.stderr)
            continue
        otype = _get_type(root)
        uuid = _get_uuid(root)
        title = _get_title(root)
        by_type[otype].append({
            "file": f,
            "tree": tree,
            "root": root,
            "uuid": uuid,
            "title": title,
            "type": otype,
        })
    return dict(by_type)


# ═══════════════════════════════════════════════════════════════════════════════
# Transformation functions
# ═══════════════════════════════════════════════════════════════════════════════

def should_remove(obj: dict[str, Any], removed_uuids: set[str]) -> tuple[bool, str]:
    """Check if an object should be removed. Returns (remove, reason)."""
    title = obj["title"]
    otype = obj["type"]
    uuid = obj["uuid"]

    # FMU title patterns
    for pat in FMU_REMOVE_TITLE_PATTERNS:
        if re.match(pat, title):
            return True, f"FMU temp title: {title}"

    # FMU well-related: if any parent well was removed, remove children too
    # (MdDatum, WellboreInterpretation, Trajectory, etc. referencing RFT/MLW)
    root = obj["root"]
    # Check if this object references a removed UUID — but only for
    # structural references (SupportingRepresentation, RepresentedInterpretation,
    # Trajectory, etc.), NOT for PropertyKind references which we fix separately.
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in ("UUID", "Uuid"):
            ref_uuid = (elem.text or "").strip()
            if ref_uuid in removed_uuids:
                # Check if this is a PropertyKind reference — skip those
                parent = _find_parent(root, elem)
                if parent is not None:
                    parent_tag = _strip_ns(parent.tag)
                    gparent = _find_parent(root, parent)
                    gparent_tag = _strip_ns(gparent.tag) if gparent is not None else ""
                    if parent_tag == "LocalPropertyKind" or gparent_tag == "LocalPropertyKind":
                        continue  # PropertyKind refs are fixed, not cascade-removed
                    if parent_tag == "PropertyKind" or gparent_tag == "PropertyKind":
                        continue
                return True, f"references removed UUID {ref_uuid}"

    # FMU surfaces
    if otype == "Grid2dRepresentation":
        for prefix in FMU_REMOVE_SURFACE_PREFIXES:
            if title.startswith(prefix):
                return True, f"FMU surface: {title}"

    # FMU point/line representations
    if otype in ("PointSetRepresentation", "PolylineSetRepresentation"):
        for prefix in FMU_REMOVE_POINT_LINE_PREFIXES:
            if title.startswith(prefix):
                return True, f"FMU point/line: {title}"

    # Duplicate strat columns (keep Geogrid versions)
    if title in REMOVE_STRAT_TITLES:
        return True, f"Duplicate strat: {title}"

    # Remove custom PropertyKind objects (General continuous/discrete, cellForFaultFace)
    if otype == "PropertyKind":
        return True, f"Custom PropertyKind: {title}"

    return False, ""


def fix_property_kind(root, title: str, stats: Counter) -> bool:
    """
    Fix PropertyKind: replace 'absorbed dose' and local 'General *' refs
    with the correct standard RESQML kind based on property title.
    """
    changed = False

    # Find PropertyKind element
    pk = _find(root, "PropertyKind")
    if pk is None:
        return False

    # Check current kind
    kind_elem = _find(pk, "Kind")
    local_pk = _find(pk, "LocalPropertyKind")

    mapping = PROPERTY_KIND_MAP.get(title)
    if not mapping:
        # No mapping defined for this title
        stats["kind_unmapped"] += 1
        return False

    new_kind, new_uom = mapping

    if kind_elem is not None:
        old_kind = kind_elem.text or ""
        if old_kind != new_kind:
            kind_elem.text = new_kind
            stats[f"kind: {old_kind} → {new_kind}"] += 1
            changed = True
    elif local_pk is not None:
        # Replace LocalPropertyKind with StandardPropertyKind
        pk_tag = pk.tag  # preserve namespace
        # Remove LocalPropertyKind
        pk.remove(local_pk)
        # Add StandardPropertyKind → Kind
        # The pk element type should change from LocalPropertyKind to StandardPropertyKind
        # We need to restructure: remove the old content and add Kind element
        # pk is typically: <PropertyKind xsi:type="..."><LocalPropertyKind>...</LocalPropertyKind></PropertyKind>
        # We want: <PropertyKind xsi:type="resqml2:StandardPropertyKind"><Kind xsi:type="...">new_kind</Kind></PropertyKind>
        pk.set(f"{{{NS['xsi']}}}type", "resqml2:StandardPropertyKind")
        new_kind_elem = ET.SubElement(pk, f"{{{NS['resqml2']}}}Kind")
        new_kind_elem.set(f"{{{NS['xsi']}}}type", "resqml2:ResqmlPropertyKind")
        new_kind_elem.text = new_kind
        stats[f"kind: Local→{new_kind}"] += 1
        changed = True

    return changed


def fix_uom(root, title: str, stats: Counter) -> bool:
    """Fix UOM for ContinuousProperty objects."""
    mapping = PROPERTY_KIND_MAP.get(title)
    if not mapping:
        return False

    _, correct_uom = mapping

    uom = _find(root, "UOM")
    if uom is not None:
        old_uom = uom.text or ""
        if old_uom != correct_uom:
            uom.text = correct_uom
            stats[f"uom: {old_uom} → {correct_uom}"] += 1
            return True
    return False


def fix_well_names(root, stats: Counter) -> bool:
    """Fix well names in Citation.Title and any text referencing well names."""
    changed = False

    for elem in root.iter():
        if elem.text:
            new_text = elem.text
            for old, new in WELL_NAME_MAP.items():
                if old in new_text:
                    new_text = new_text.replace(old, new)
            if new_text != elem.text:
                elem.text = new_text
                changed = True
                stats["well_name_fixed"] += 1

    return changed


def remove_vendor_metadata(root, stats: Counter) -> bool:
    """Remove vendor-specific ExtraMetadata entries (pdgm/*, roxar/*)."""
    changed = False
    to_remove = []

    for em in _findall(root, "ExtraMetadata"):
        name = _text(em, "Name")
        if any(name.startswith(prefix) for prefix in VENDOR_METADATA_PREFIXES):
            to_remove.append(em)

    for em in to_remove:
        root.remove(em)
        changed = True
        stats["vendor_metadata_removed"] += 1

    return changed


def remove_uuid_authority(root, stats: Counter) -> bool:
    """Remove UuidAuthority elements (vendor-specific, not OSDU)."""
    changed = False
    for elem in root.iter():
        if _strip_ns(elem.tag) == "UuidAuthority":
            parent = None
            # Find parent by iterating (ElementTree doesn't have parent refs)
            for p in root.iter():
                if elem in list(p):
                    parent = p
                    break
            if parent is not None:
                parent.remove(elem)
                changed = True
                stats["uuid_authority_removed"] += 1
    return changed


def fix_hdf5_title(root, stats: Counter) -> bool:
    """Fix HDF5 reference title (remove local filesystem path)."""
    changed = False
    otype = _strip_ns(root.tag)

    if otype == "EpcExternalPartReference":
        title_elem = _find(root, "Citation", "Title")
        if title_elem is not None and title_elem.text:
            old = title_elem.text
            if "/" in old and old.startswith("/"):
                title_elem.text = "drogon.h5"
                stats["hdf5_title_fixed"] += 1
                changed = True

    # Also fix HDF5 title references in property objects
    for elem in root.iter():
        if _strip_ns(elem.tag) == "HdfProxy":
            hdf_title = _find(elem, "Title")
            if hdf_title is not None and hdf_title.text and hdf_title.text.startswith("/"):
                hdf_title.text = "drogon.h5"
                stats["hdf5_ref_title_fixed"] += 1
                changed = True

    return changed


def add_crs_epsg_metadata(root, stats: Counter) -> bool:
    """Add EPSG reference to CRS ExtraMetadata if missing."""
    otype = _strip_ns(root.tag)
    if otype not in ("LocalDepth3dCrs", "LocalTime3dCrs"):
        return False

    # Check if already has EPSG metadata
    for em in _findall(root, "ExtraMetadata"):
        name = _text(em, "Name")
        if "EPSG" in name or "epsg" in name:
            return False

    # Add EPSG metadata (UTM37S/ED50 = EPSG:23037)
    em = ET.SubElement(root, f"{{{NS['resqml2']}}}ExtraMetadata")
    em.set(f"{{{NS['xsi']}}}type", "resqml2:NameValuePair")
    nm = ET.SubElement(em, f"{{{NS['resqml2']}}}Name")
    nm.set(f"{{{NS['xsi']}}}type", "xsd:string")
    nm.text = "EPSG"
    vl = ET.SubElement(em, f"{{{NS['resqml2']}}}Value")
    vl.set(f"{{{NS['xsi']}}}type", "xsd:string")
    vl.text = "23037"  # UTM zone 37S, ED50

    stats["crs_epsg_added"] += 1
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Main processing
# ═══════════════════════════════════════════════════════════════════════════════

def process(src_dir: Path, dst_dir: Path, dry_run: bool = True, gen_manifest: bool = False):
    """Process all EPC XML files with OSDU-compliant transformations."""

    print(f"{'DRY RUN' if dry_run else 'APPLYING'} — Drogon EPC OSDU Cleanup")
    print(f"  Source: {src_dir}")
    print(f"  Target: {dst_dir}")
    print()

    # Register namespaces for clean output
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    ET.register_namespace("SOAP-ENV", "http://schemas.xmlsoap.org/soap/envelope/")
    ET.register_namespace("SOAP-ENC", "http://schemas.xmlsoap.org/soap/encoding/")
    ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
    ET.register_namespace("gts", "http://www.isotc211.org/2005/gts")
    ET.register_namespace("gsr", "http://www.isotc211.org/2005/gsr")
    ET.register_namespace("dc", "http://purl.org/dc/terms/")
    ET.register_namespace("resqml1", "http://www.resqml.org/schemas/1series")
    ET.register_namespace("witsml1", "http://www.witsml.org/schemas/1series")
    ET.register_namespace("gml", "http://www.opengis.net/gml/3.2")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    ET.register_namespace("gmd", "http://www.isotc211.org/2005/gmd")
    ET.register_namespace("gco", "http://www.isotc211.org/2005/gco")
    ET.register_namespace("ptm", "http://www.f2i-consulting.com/PropertyTypeMapping")
    ET.register_namespace("abstract", "http://www.energistics.org/schemas/abstract")

    # ── Phase 1: Inventory ────────────────────────────────────────────
    print("Phase 1: Inventory")
    by_type = inventory(src_dir)
    total = sum(len(v) for v in by_type.values())
    print(f"  Found {total} objects across {len(by_type)} types\n")

    for otype in sorted(by_type):
        print(f"  {otype}: {len(by_type[otype])}")
    print()

    # ── Phase 2: Determine removals (multi-pass for dependency cascade) ──
    print("Phase 2: Identify FMU temporary objects to remove")
    removed_uuids: set[str] = set()
    removed_files: set[str] = set()
    remove_reasons: dict[str, str] = {}
    all_objects = [obj for objs in by_type.values() for obj in objs]

    # Multi-pass: removing an object may trigger removal of dependents
    for pass_num in range(5):
        new_removals = 0
        for obj in all_objects:
            if obj["uuid"] in removed_uuids:
                continue
            remove, reason = should_remove(obj, removed_uuids)
            if remove:
                removed_uuids.add(obj["uuid"])
                removed_files.add(obj["file"].name)
                remove_reasons[obj["uuid"]] = reason
                new_removals += 1
        if new_removals == 0:
            break
        print(f"    Pass {pass_num + 1}: {new_removals} objects marked for removal")

    print(f"  Total removals: {len(removed_uuids)} objects")

    # Show removals by type
    remove_by_type: Counter = Counter()
    for obj in all_objects:
        if obj["uuid"] in removed_uuids:
            remove_by_type[obj["type"]] += 1
    for otype, count in remove_by_type.most_common():
        print(f"    {otype}: {count}")
    print()

    # Show specific removes
    if removed_uuids:
        print("  Removed objects:")
        for obj in all_objects:
            if obj["uuid"] in removed_uuids:
                reason = remove_reasons.get(obj["uuid"], "")
                print(f"    ✗ {obj['type']}/{obj['title']} ({reason})")
        print()

    # ── Phase 3: Transformations ──────────────────────────────────────
    print("Phase 3: Apply OSDU-compliant transformations")
    stats: Counter = Counter()
    kept_objects: list[dict[str, Any]] = []
    modified_files: list[str] = []

    for obj in all_objects:
        if obj["uuid"] in removed_uuids:
            stats["removed"] += 1
            continue

        root = obj["root"]
        title = obj["title"]
        otype = obj["type"]
        changed = False

        # 3a. Fix PropertyKind (ContinuousProperty, DiscreteProperty)
        if otype in ("ContinuousProperty", "DiscreteProperty"):
            changed |= fix_property_kind(root, title, stats)

        # 3b. Fix UOM (ContinuousProperty only)
        if otype == "ContinuousProperty":
            changed |= fix_uom(root, title, stats)

        # 3c. Fix well names
        changed |= fix_well_names(root, stats)

        # 3d. Remove vendor ExtraMetadata
        changed |= remove_vendor_metadata(root, stats)

        # 3e. Remove UuidAuthority
        changed |= remove_uuid_authority(root, stats)

        # 3f. Fix HDF5 reference title
        changed |= fix_hdf5_title(root, stats)

        # 3g. Add EPSG to CRS
        changed |= add_crs_epsg_metadata(root, stats)

        if changed:
            modified_files.append(obj["file"].name)
            stats["modified"] += 1
        else:
            stats["unchanged"] += 1

        kept_objects.append(obj)

    print(f"  Kept: {len(kept_objects)}, Modified: {stats['modified']}, "
          f"Unchanged: {stats['unchanged']}")
    print()

    print("  Transformation statistics:")
    for key in sorted(stats):
        if key not in ("modified", "unchanged", "removed"):
            print(f"    {key}: {stats[key]}")
    print()

    # ── Phase 4: Summary ──────────────────────────────────────────────
    kept_by_type: Counter = Counter()
    for obj in kept_objects:
        kept_by_type[obj["type"]] += 1

    print("Phase 4: Output Summary")
    print(f"  Input:  {total} objects")
    print(f"  Output: {len(kept_objects)} objects ({total - len(kept_objects)} removed)")
    print()
    print("  Output breakdown by type:")
    for otype, count in kept_by_type.most_common():
        orig = len(by_type.get(otype, []))
        removed = orig - count
        suffix = f" (-{removed})" if removed > 0 else ""
        print(f"    {otype}: {count}{suffix}")
    print()

    # ── Phase 5: Write output ─────────────────────────────────────────
    if dry_run:
        print("DRY RUN complete. Use --apply to write cleaned files.\n")
        return

    print(f"Phase 5: Writing output to {dst_dir}")

    # Create output directory
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True)

    # Copy non-object files (Content_Types.xml, _rels/, docProps/)
    for item in src_dir.iterdir():
        if item.name.startswith("obj_"):
            continue
        dst_path = dst_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dst_path)
        else:
            shutil.copy2(item, dst_path)

    # Write cleaned objects
    written = 0
    for obj in kept_objects:
        tree = obj["tree"]
        dst_path = dst_dir / obj["file"].name
        tree.write(dst_path, xml_declaration=True, encoding="UTF-8")
        written += 1

    print(f"  Written {written} XML files to {dst_dir}")

    # Update [Content_Types].xml to remove entries for deleted files
    ct_path = dst_dir / "[Content_Types].xml"
    if ct_path.exists():
        try:
            ct_tree = ET.parse(ct_path)
            ct_root = ct_tree.getroot()
            to_remove = []
            for override in ct_root:
                part_name = override.get("PartName", "")
                # Check if this references a removed file
                fname = part_name.lstrip("/")
                if fname in removed_files:
                    to_remove.append(override)
            for elem in to_remove:
                ct_root.remove(elem)
            ct_tree.write(ct_path, xml_declaration=True, encoding="UTF-8")
            print(f"  Updated [Content_Types].xml (removed {len(to_remove)} entries)")
        except Exception as e:
            print(f"  Warning: Could not update [Content_Types].xml: {e}")

    # Update _rels/.rels to remove entries for deleted files
    rels_path = dst_dir / "_rels" / ".rels"
    if rels_path.exists():
        try:
            rels_tree = ET.parse(rels_path)
            rels_root = rels_tree.getroot()
            to_remove = []
            for rel in rels_root:
                target = rel.get("Target", "")
                if target in removed_files:
                    to_remove.append(rel)
            for elem in to_remove:
                rels_root.remove(elem)
            rels_tree.write(rels_path, xml_declaration=True, encoding="UTF-8")
            print(f"  Updated _rels/.rels (removed {len(to_remove)} entries)")
        except Exception as e:
            print(f"  Warning: Could not update _rels/.rels: {e}")

    print()

    # ── Phase 6: Generate manifest (optional) ─────────────────────────
    if gen_manifest:
        print("Phase 6: Generating OSDU ingest manifest")
        manifest = generate_manifest(kept_objects)
        manifest_path = dst_dir.parent / "manifest_drogon_osdu.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"  Written manifest to {manifest_path}")
        print(f"  {len(manifest.get('resources', []))} resources in manifest")
        print()

    print("DONE ✓\n")


def generate_manifest(kept_objects: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a minimal OSDU-style ingest manifest for the cleaned dataset."""
    resources = []
    for obj in kept_objects:
        otype = obj["type"]
        uuid = obj["uuid"]
        title = obj["title"]

        typ_path = f"resqml20.obj_{otype}"

        resource = {
            "uuid": uuid,
            "type": typ_path,
            "title": title,
        }

        # Add supporting representation reference if present
        root = obj["root"]
        support = _find(root, "SupportingRepresentation")
        if support is not None:
            ref_uuid = _text(support, "UUID") or _text(support, "Uuid")
            ref_ct = _text(support, "ContentType")
            if ref_uuid:
                resource["supportingRepresentation"] = {
                    "uuid": ref_uuid,
                    "contentType": ref_ct,
                }

        resources.append(resource)

    return {
        "_comment": "OSDU-compliant Drogon demo dataset manifest",
        "dataspace": "maap/drogon",
        "schemaVersion": "2.0",
        "resources": resources,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Clean up Drogon EPC dataset for OSDU compliance",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write cleaned files to drogon_osdu/ (default: dry-run report only)",
    )
    parser.add_argument(
        "--manifest", action="store_true",
        help="Generate an OSDU ingest manifest JSON (requires --apply)",
    )
    parser.add_argument(
        "--src", type=Path, default=SRC_DIR,
        help=f"Source directory (default: {SRC_DIR})",
    )
    parser.add_argument(
        "--dst", type=Path, default=DST_DIR,
        help=f"Output directory (default: {DST_DIR})",
    )
    args = parser.parse_args()

    if not args.src.is_dir():
        print(f"Error: source directory not found: {args.src}", file=sys.stderr)
        sys.exit(1)

    process(args.src, args.dst, dry_run=not args.apply, gen_manifest=args.manifest)


if __name__ == "__main__":
    main()
