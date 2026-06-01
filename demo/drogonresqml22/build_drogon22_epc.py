#!/usr/bin/env python3
"""
build_drogon22_epc.py – Convert the RESQML 2.0.1 Drogon EPC to RESQML 2.2.

Transforms:
  1. schemaVersion "2.0" → "2.2"
  2. Remove obj_ prefix from type names (in filenames, xsi:type, ContentType refs)
  3. Rename types: GeneticBoundaryFeature/TectonicBoundaryFeature → BoundaryFeature
                   StratigraphicUnitFeature → RockVolumeFeature
                   OrganizationFeature → Model
  4. Update content-type version: version=2.0 → version=2.2
  5. Add missing xmlns:xsd declaration (fixes Geosiris validation)
  6. Remove problematic CustomData/DisabledMarkers blocks

Usage:
    python demo/drogonresqml22/build_drogon22_epc.py
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SRC_EPC = SCRIPT_DIR.parent / "drogonresqml" / "drogon_demo.epc"
OUT_EPC = SCRIPT_DIR / "drogon_demo_22.epc"

# ── RESQML 2.0.1 → 2.2 type renames ─────────────────────────────────────
TYPE_RENAMES = {
    "GeneticBoundaryFeature": "BoundaryFeature",
    "TectonicBoundaryFeature": "BoundaryFeature",
    "StratigraphicUnitFeature": "RockVolumeFeature",
    "OrganizationFeature": "Model",
}

# All 2.0.1 types (just drop obj_ prefix for those not in renames)
# The RDDMS will register them as resqml22.TypeName


def _convert_type_name(name_201: str) -> str:
    """Convert a 2.0.1 type name (with or without obj_ prefix) to 2.2."""
    bare = name_201.replace("obj_", "")
    return TYPE_RENAMES.get(bare, bare)


def _fix_namespace_decl(root_line: str) -> str:
    """Add xmlns:xsd if missing."""
    if 'xmlns:xsd=' not in root_line:
        # Insert before the first xsi: or before schemaVersion
        root_line = root_line.replace(
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema"',
        )
    return root_line


def _remove_disabled_markers(xml: str) -> str:
    """Remove the CustomData/DisabledMarkers block that fails validation."""
    # Remove entire CustomData block containing DisabledMarkers
    xml = re.sub(
        r'\s*<eml:CustomData[^>]*>.*?DisabledMarkers.*?</eml:CustomData>',
        '', xml, flags=re.DOTALL)
    return xml


def _convert_content(xml: str, old_type: str, new_type: str) -> str:
    """Apply all 2.0.1→2.2 transformations to XML content."""

    # 1. Fix namespace declarations
    xml = _fix_namespace_decl(xml)

    # 2. schemaVersion="2.0" → "2.2"
    xml = xml.replace('schemaVersion="2.0"', 'schemaVersion="2.2"')

    # 3. Update root element tag: resqml2:obj_Type → resqml2:Type
    xml = re.sub(
        r'(</?resqml2:)obj_' + re.escape(old_type),
        r'\g<1>' + new_type,
        xml)
    # Also handle cases where the type appears without obj_ already
    if old_type != new_type:
        xml = re.sub(
            r'(</?resqml2:)' + re.escape(old_type) + r'(?=[\s>])',
            r'\g<1>' + new_type,
            xml)

    # 4. xsi:type="resqml2:obj_Type" → xsi:type="resqml2:Type"
    xml = re.sub(r'xsi:type="resqml2:obj_(\w+)"',
                 lambda m: f'xsi:type="resqml2:{_convert_type_name(m.group(1))}"', xml)

    # 5. ContentType version=2.0;type=obj_Type → version=2.2;type=Type
    xml = re.sub(
        r'(application/x-resqml\+xml;version=)2\.0(;type=)obj_(\w+)',
        lambda m: f'{m.group(1)}2.2{m.group(2)}{_convert_type_name(m.group(3))}',
        xml)
    # EML content types
    xml = re.sub(
        r'(application/x-eml\+xml;version=)2\.0(;type=)obj_(\w+)',
        lambda m: f'{m.group(1)}2.3{m.group(2)}{m.group(3).replace("obj_", "")}',
        xml)

    # 6. Remove DisabledMarkers CustomData
    xml = _remove_disabled_markers(xml)

    # 7. Update Format citation to indicate 2.2
    xml = xml.replace(
        'RESQML v2.0 (Drogon Demo)',
        'RESQML v2.2 (Drogon Demo)')

    return xml


def _convert_filename(old_name: str) -> str:
    """Convert filename: obj_Type_uuid.xml → Type_uuid.xml"""
    if not old_name.startswith("obj_"):
        return old_name
    # Extract type and uuid
    m = re.match(r'obj_(\w+?)_([0-9a-f-]{36})\.xml', old_name)
    if m:
        old_type = m.group(1)
        uuid = m.group(2)
        new_type = _convert_type_name(old_type)
        return f"{new_type}_{uuid}.xml"
    return old_name


def main():
    if not SRC_EPC.exists():
        sys.exit(f"Source EPC not found: {SRC_EPC}")

    print(f"Converting RESQML 2.0.1 → 2.2")
    print(f"  Source: {SRC_EPC}")
    print(f"  Output: {OUT_EPC}")

    from collections import Counter
    type_counts = Counter()
    renamed_count = 0

    with zipfile.ZipFile(SRC_EPC, "r") as src:
        with zipfile.ZipFile(OUT_EPC, "w", zipfile.ZIP_DEFLATED) as dst:
            for old_name in src.namelist():
                content_bytes = src.read(old_name)

                if old_name.endswith(".xml") and old_name.startswith("obj_"):
                    # Convert object XML
                    xml = content_bytes.decode("utf-8")

                    # Determine types
                    m = re.match(r'obj_(\w+?)_[0-9a-f-]{36}\.xml', old_name)
                    old_type = m.group(1) if m else ""
                    new_type = _convert_type_name(old_type)
                    type_counts[new_type] += 1
                    if old_type != new_type:
                        renamed_count += 1

                    # Transform content
                    xml = _convert_content(xml, old_type, new_type)
                    content_bytes = xml.encode("utf-8")

                    # Rename file
                    new_name = _convert_filename(old_name)
                    dst.writestr(new_name, content_bytes)

                elif old_name == "[Content_Types].xml":
                    # Update content types
                    ct_xml = content_bytes.decode("utf-8")
                    ct_xml = re.sub(r'obj_(\w+?)_', lambda m: f'{_convert_type_name(m.group(1))}_', ct_xml)
                    ct_xml = ct_xml.replace("version=2.0", "version=2.2")
                    dst.writestr(old_name, ct_xml.encode("utf-8"))

                elif old_name.startswith("_rels/") and old_name.endswith(".rels"):
                    # Update .rels file (rename targets)
                    rels_xml = content_bytes.decode("utf-8")
                    rels_xml = re.sub(r'obj_(\w+?)_', lambda m: f'{_convert_type_name(m.group(1))}_', rels_xml)
                    # Also update the new .rels filename
                    new_rels_name = re.sub(r'obj_(\w+?)_', lambda m: f'{_convert_type_name(m.group(1))}_', old_name)
                    dst.writestr(new_rels_name, rels_xml.encode("utf-8"))

                else:
                    # Copy as-is (e.g. _rels/.rels)
                    dst.writestr(old_name, content_bytes)

    print(f"\n  Converted {sum(type_counts.values())} objects ({renamed_count} type renames)")
    print(f"  Types:")
    for t, c in sorted(type_counts.items()):
        print(f"    {t:45s} {c:4d}")
    print(f"\n  Output: {OUT_EPC} ({OUT_EPC.stat().st_size / 1024:.0f} KB)")

    # Verify
    print("\n  Verifying...")
    with zipfile.ZipFile(OUT_EPC) as z:
        names = [n for n in z.namelist() if n.endswith('.xml') and not n.startswith('[') and not n.startswith('_')]
        has_obj = any(n.startswith('obj_') for n in names)
        first_xml = z.read(names[0]).decode()
        has_22 = 'schemaVersion="2.2"' in first_xml
        has_xsd = 'xmlns:xsd=' in first_xml
        has_disabled = 'DisabledMarkers' in first_xml
        print(f"    obj_ prefix remaining: {'YES ⚠' if has_obj else 'NO ✓'}")
        print(f"    schemaVersion 2.2: {'YES ✓' if has_22 else 'NO ⚠'}")
        print(f"    xmlns:xsd declared: {'YES ✓' if has_xsd else 'NO ⚠'}")
        marker_files = [n for n in names if 'MarkerFrame' in n]
        if marker_files:
            mc = z.read(marker_files[0]).decode()
            print(f"    DisabledMarkers removed: {'NO ⚠' if 'DisabledMarkers' in mc else 'YES ✓'}")

    print("\n  Done ✓")


if __name__ == "__main__":
    main()
