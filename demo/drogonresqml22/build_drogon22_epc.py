#!/usr/bin/env python3
"""
build_drogon22_epc.py – Convert the RESQML 2.0.1 Drogon EPC to valid RESQML 2.2.

Transforms:
  1. schemaVersion "2.0" → "2.2"
  2. Remove obj_ prefix from type names
  3. Rename types: GeneticBoundaryFeature/TectonicBoundaryFeature → BoundaryFeature
                   StratigraphicUnitFeature → RockVolumeFeature
                   OrganizationFeature → Model
  4. DataObjectReference: ContentType/UUID → QualifiedType/Uuid
  5. Properties: Count→ValueCountPerIndexableElement, PatchOfValues→ValuesForPatch, UOM→Uom
  6. PropertyKind: StandardPropertyKind/Kind → PropertyKind DOR
  7. Grid2dRepresentation: unwrap Grid2dPatch → FastestAxisCount/SlowestAxisCount/Geometry
  8. Representations: RepresentedInterpretation → RepresentedObject
  9. Interpretations: RepresentedInterpretation → InterpretedFeature
  10. WellboreTrajectory: StartMd/FinishMd/MdUom → MdInterval
  11. Remove DisabledMarkers CustomData blocks

Usage:
    python build_drogon22_epc.py
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

# Types that are Interpretations (use InterpretedFeature for their DOR to feature)
INTERPRETATION_TYPES = {
    "FaultInterpretation", "HorizonInterpretation",
    "StratigraphicUnitInterpretation", "StratigraphicColumnRankInterpretation",
    "StructuralOrganizationInterpretation", "WellboreInterpretation",
    "GeobodyInterpretation", "GeobodyBoundaryInterpretation",
    "EarthModelInterpretation",
}

# Types that are Representations (use RepresentedObject)
REPRESENTATION_TYPES = {
    "Grid2dRepresentation", "PolylineSetRepresentation", "PointSetRepresentation",
    "WellboreTrajectoryRepresentation", "WellboreFrameRepresentation",
    "IjkGridRepresentation", "TriangulatedSetRepresentation",
    "UnstructuredGridRepresentation",
}


def _convert_type_name(name_201: str) -> str:
    """Convert a 2.0.1 type name (with or without obj_ prefix) to 2.2."""
    bare = name_201.replace("obj_", "")
    return TYPE_RENAMES.get(bare, bare)


def _content_type_to_qualified_type(content_type: str) -> str:
    """Convert ContentType string to QualifiedType.

    'application/x-resqml+xml;version=2.0;type=obj_FaultInterpretation'
    -> 'resqml22.FaultInterpretation'
    """
    m = re.match(r'application/x-(\w+)\+xml;version=[\d.]+;type=(?:obj_)?(\w+)', content_type)
    if not m:
        return content_type

    domain = m.group(1)
    type_name = _convert_type_name(m.group(2))

    if domain == "resqml":
        return f"resqml22.{type_name}"
    elif domain == "eml":
        return f"eml23.{type_name}"
    elif domain == "witsml":
        return f"witsml21.{type_name}"
    else:
        return f"{domain}22.{type_name}"


def _convert_dor(dor_xml: str) -> str:
    """Convert all DataObjectReferences: ContentType/UUID -> QualifiedType/Uuid."""

    # ContentType -> QualifiedType
    def _replace_ct(m):
        indent = m.group(1)
        ct_value = m.group(2)
        qt = _content_type_to_qualified_type(ct_value)
        return f'{indent}<eml:QualifiedType xsi:type="xsd:string">{qt}</eml:QualifiedType>'

    dor_xml = re.sub(
        r'(\s*)<eml:ContentType[^>]*>([^<]+)</eml:ContentType>',
        _replace_ct, dor_xml)

    # UUID -> Uuid
    dor_xml = re.sub(
        r'<eml:UUID([^>]*)>([^<]+)</eml:UUID>',
        r'<eml:Uuid\1>\2</eml:Uuid>',
        dor_xml)

    return dor_xml


def _convert_property_kind(xml: str) -> str:
    """Keep PropertyKind as-is (v2.0 StandardPropertyKind/Kind).
    
    While RESQML 2.2 schema expects a DOR for PropertyKind,
    keeping the inline enum avoids needing to create PropertyKind objects
    and their .rels entries in the EPC. The ETP server and cxml parser
    handle both formats.
    """
    return xml


def _convert_grid2d_patch(xml: str) -> str:
    """Unwrap Grid2dPatch: move FastestAxisCount, SlowestAxisCount, Geometry up."""

    def _replace_patch(m):
        inner = m.group(1)
        # Remove PatchIndex
        inner = re.sub(r'\s*<resqml2:PatchIndex[^>]*>[^<]*</resqml2:PatchIndex>\s*', '\n', inner)
        # Dedent one level
        lines = inner.split('\n')
        dedented = []
        for line in lines:
            if line.startswith('\t\t'):
                dedented.append(line[1:])
            elif line.strip():
                dedented.append(line)
        return '\n'.join(dedented)

    xml = re.sub(
        r'\s*<resqml2:Grid2dPatch[^>]*>(.*?)</resqml2:Grid2dPatch>',
        _replace_patch, xml, flags=re.DOTALL)

    return xml




def _convert_lattice_offset_to_dimension(xml: str) -> str:
    """Convert Point3dLatticeArray Offset to Dimension (v2.0 -> v2.2).

    v2.0: <resqml2:Offset xsi:type="resqml2:Point3dOffset">
              <resqml2:Offset xsi:type="resqml2:Point3d">...</resqml2:Offset>
              <resqml2:Spacing ...>...</resqml2:Spacing>
          </resqml2:Offset>

    v2.2: <resqml2:Dimension xsi:type="resqml2:Point3dLatticeDimension">
              <resqml2:Direction xsi:type="resqml2:Point3d">...</resqml2:Direction>
              <resqml2:Spacing ...>...</resqml2:Spacing>
          </resqml2:Dimension>
    """
    # First rename inner Offset (point3d direction) -> Direction
    # The inner offset is: <resqml2:Offset xsi:type="resqml2:Point3d">
    xml = re.sub(
        r'<resqml2:Offset(\s+xsi:type="resqml2:Point3d")>',
        r'<resqml2:Direction\1>',
        xml)
    xml = re.sub(
        r'</resqml2:Offset>(\s*<resqml2:Spacing)',
        r'</resqml2:Direction>\1',
        xml)

    # Then rename outer Offset (Point3dOffset) -> Dimension (Point3dLatticeDimension)
    xml = xml.replace(
        'xsi:type="resqml2:Point3dOffset"',
        'xsi:type="resqml2:Point3dLatticeDimension"')
    xml = re.sub(
        r'<resqml2:Offset(\s+xsi:type="resqml2:Point3dLatticeDimension")>',
        r'<resqml2:Dimension\1>',
        xml)
    # Fix closing tags - find </resqml2:Offset> that comes after Dimension opening
    # This is tricky - use a simpler approach: replace remaining Offset open/close that 
    # contain Dimension/Direction children
    xml = re.sub(
        r'</resqml2:Offset>(\s*</resqml2:(?:SupportingGeometry|Point3dLatticeArray))',
        r'</resqml2:Dimension>\1',
        xml)
    # Also fix closing Offset that appears before another Dimension
    xml = re.sub(
        r'</resqml2:Offset>(\s*<resqml2:Dimension)',
        r'</resqml2:Dimension>\1',
        xml)
    
    return xml

def _convert_wellbore_trajectory(xml: str) -> str:
    """Convert StartMd/FinishMd/MdUom to MdInterval."""

    start_m = re.search(r'<resqml2:StartMd[^>]*>([^<]+)</resqml2:StartMd>', xml)
    finish_m = re.search(r'<resqml2:FinishMd[^>]*>([^<]+)</resqml2:FinishMd>', xml)
    uom_m = re.search(r'<resqml2:MdUom[^>]*>([^<]+)</resqml2:MdUom>', xml)

    if start_m and finish_m:
        start_val = start_m.group(1)
        finish_val = finish_m.group(1)
        uom_val = uom_m.group(1) if uom_m else "m"

        md_interval = (
            '\t<resqml2:MdInterval xsi:type="resqml2:MdInterval">\n'
            f'\t\t<eml:MdMin xsi:type="xsd:double">{start_val}</eml:MdMin>\n'
            f'\t\t<eml:MdMax xsi:type="xsd:double">{finish_val}</eml:MdMax>\n'
            f'\t\t<eml:Uom xsi:type="eml:LengthUom">{uom_val}</eml:Uom>\n'
            '\t</resqml2:MdInterval>\n'
        )

        # Remove old elements
        xml = re.sub(r'\s*<resqml2:StartMd[^>]*>[^<]*</resqml2:StartMd>', '', xml)
        xml = re.sub(r'\s*<resqml2:FinishMd[^>]*>[^<]*</resqml2:FinishMd>', '', xml)
        xml = re.sub(r'\s*<resqml2:MdUom[^>]*>[^<]*</resqml2:MdUom>', '', xml)

        # Remove MdDatum DOR if present
        xml = re.sub(
            r'\s*<resqml2:MdDatum[^>]*>.*?</resqml2:MdDatum>',
            '', xml, flags=re.DOTALL)

        # Insert MdInterval after RepresentedObject or after Citation
        if '</resqml2:RepresentedObject>' in xml:
            xml = re.sub(
                r'(</resqml2:RepresentedObject>)',
                r'\1\n' + md_interval, xml, count=1)
        elif '</eml:Citation>' in xml:
            xml = re.sub(
                r'(</eml:Citation>)',
                r'\1\n' + md_interval, xml, count=1)

    return xml


def _fix_namespace_decl(xml: str) -> str:
    """Add xmlns:xsd if missing."""
    if 'xmlns:xsd=' not in xml:
        xml = xml.replace(
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema"',
        )
    return xml


def _remove_disabled_markers(xml: str) -> str:
    """Remove the CustomData/DisabledMarkers block."""
    xml = re.sub(
        r'\s*<eml:CustomData[^>]*>.*?DisabledMarkers.*?</eml:CustomData>',
        '', xml, flags=re.DOTALL)
    return xml


def _determine_object_category(type_name: str) -> str:
    """Determine if a type is an interpretation or representation."""
    bare = _convert_type_name(type_name.replace("obj_", ""))
    if bare in INTERPRETATION_TYPES:
        return "interpretation"
    if bare in REPRESENTATION_TYPES:
        return "representation"
    return "other"


def _convert_content(xml: str, old_type: str, new_type: str) -> str:
    """Apply all 2.0.1->2.2 transformations to XML content."""

    # 1. Fix namespace declarations
    xml = _fix_namespace_decl(xml)

    # 2. schemaVersion="2.0" -> "2.2"
    xml = xml.replace('schemaVersion="2.0"', 'schemaVersion="2.2"')

    # 3. Update root element tag: resqml2:obj_Type -> resqml2:Type
    xml = re.sub(
        r'(</?resqml2:)obj_' + re.escape(old_type),
        r'\g<1>' + new_type,
        xml)
    if old_type != new_type:
        xml = re.sub(
            r'(</?resqml2:)' + re.escape(old_type) + r'(?=[\s>])',
            r'\g<1>' + new_type,
            xml)

    # 4. xsi:type="resqml2:obj_Type" -> xsi:type="resqml2:Type"
    xml = re.sub(r'xsi:type="resqml2:obj_(\w+)"',
                 lambda m: f'xsi:type="resqml2:{_convert_type_name(m.group(1))}"', xml)

    # 5. Convert all DataObjectReferences (ContentType/UUID -> QualifiedType/Uuid)
    xml = _convert_dor(xml)

    # 6. RepresentedInterpretation -> InterpretedFeature or RepresentedObject
    category = _determine_object_category(old_type)
    if category == "interpretation":
        xml = xml.replace("RepresentedInterpretation", "InterpretedFeature")
    elif category == "representation":
        xml = xml.replace("RepresentedInterpretation", "RepresentedObject")

    # 7. Property-specific conversions
    if "Property" in new_type:
        # Count -> ValueCountPerIndexableElement
        xml = re.sub(
            r'<resqml2:Count([^>]*)>([^<]+)</resqml2:Count>',
            r'<resqml2:ValueCountPerIndexableElement\1>\2</resqml2:ValueCountPerIndexableElement>',
            xml)
        # PatchOfValues -> ValuesForPatch
        xml = xml.replace('<resqml2:PatchOfValues', '<resqml2:ValuesForPatch')
        xml = xml.replace('</resqml2:PatchOfValues>', '</resqml2:ValuesForPatch>')
        # UOM -> Uom (standalone element, not part of other words)
        xml = re.sub(
            r'<resqml2:UOM([^>]*)>([^<]+)</resqml2:UOM>',
            r'<resqml2:Uom\1>\2</resqml2:Uom>',
            xml)
        # PropertyKind inline -> DOR
        xml = _convert_property_kind(xml)

    # 8. Grid2d-specific: unwrap Grid2dPatch + Offset->Dimension
    if "Grid2d" in new_type:
        xml = _convert_grid2d_patch(xml)
        xml = _convert_lattice_offset_to_dimension(xml)

    # 9. WellboreTrajectory-specific: StartMd/FinishMd -> MdInterval
    if "WellboreTrajectory" in new_type:
        xml = _convert_wellbore_trajectory(xml)

    # 10. Remove DisabledMarkers
    xml = _remove_disabled_markers(xml)

    # 11. Update Format citation
    xml = xml.replace(
        'RESQML v2.0 (Drogon Demo)',
        'RESQML v2.2 (Drogon Demo)')

    return xml


def _convert_filename(old_name: str) -> str:
    """Convert filename: obj_Type_uuid.xml -> Type_uuid.xml"""
    if not old_name.startswith("obj_"):
        return old_name
    m = re.match(r'obj_(\w+?)_([0-9a-f-]{36})\.xml', old_name)
    if m:
        old_type = m.group(1)
        uuid = m.group(2)
        new_type = _convert_type_name(old_type)
        return f"{new_type}_{uuid}.xml"
    return old_name


def _generate_property_kind_objects_from_names(kinds: set) -> dict:
    """Generate PropertyKind XML objects for the given kind names."""
    import uuid as _uuid
    _NS = _uuid.UUID("a48c9c25-1e3a-43c8-be6a-044224cc69cb")
    
    pk_objects = {}
    for kind_name in sorted(kinds):
        pk_uuid = str(_uuid.uuid5(_NS, kind_name))
        pk_xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<eml:PropertyKind xmlns:eml="http://www.energistics.org/energyml/data/commonv2" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" schemaVersion="2.2" uuid="{pk_uuid}" xsi:type="eml:PropertyKind">
\t<eml:Citation xsi:type="eml:Citation">
\t\t<eml:Title xsi:type="eml:DescriptionString">{kind_name}</eml:Title>
\t\t<eml:Originator xsi:type="eml:NameString">Energistics</eml:Originator>
\t\t<eml:Creation xsi:type="xsd:dateTime">2025-01-01T00:00:00Z</eml:Creation>
\t\t<eml:Format xsi:type="eml:DescriptionString">RESQML v2.2 Standard PropertyKind</eml:Format>
\t</eml:Citation>
\t<eml:QuantityClass xsi:type="eml:QuantityClassKind">{kind_name}</eml:QuantityClass>
\t<eml:IsAbstract xsi:type="xsd:boolean">false</eml:IsAbstract>
</eml:PropertyKind>"""
        filename = f"PropertyKind_{pk_uuid}.xml"
        pk_objects[filename] = pk_xml
    
    return pk_objects


def main():
    if not SRC_EPC.exists():
        sys.exit(f"Source EPC not found: {SRC_EPC}")

    print(f"Converting RESQML 2.0.1 -> 2.2 (full schema transformation)")
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
                    xml = content_bytes.decode("utf-8")

                    m = re.match(r'obj_(\w+?)_[0-9a-f-]{36}\.xml', old_name)
                    old_type = m.group(1) if m else ""
                    new_type = _convert_type_name(old_type)
                    type_counts[new_type] += 1
                    if old_type != new_type:
                        renamed_count += 1

                    xml = _convert_content(xml, old_type, new_type)
                    content_bytes = xml.encode("utf-8")

                    new_name = _convert_filename(old_name)
                    dst.writestr(new_name, content_bytes)

                elif old_name.endswith(".xml") and not old_name.startswith("[") and not old_name.startswith("_"):
                    # Non-obj_ XML files (e.g. EpcExternalPartReference)
                    xml = content_bytes.decode("utf-8")
                    xml = _fix_namespace_decl(xml)
                    xml = xml.replace('schemaVersion="2.0"', 'schemaVersion="2.2"')
                    # Fix obj_ in xsi:type
                    xml = re.sub(r'xsi:type="eml:obj_(\w+)"', r'xsi:type="eml:\1"', xml)
                    xml = re.sub(r'xsi:type="resqml2:obj_(\w+)"', lambda m2: f'xsi:type="resqml2:{_convert_type_name(m2.group(1))}"', xml)
                    xml = xml.replace('RESQML v2.0 (Drogon Demo)', 'RESQML v2.2 (Drogon Demo)')
                    content_bytes = xml.encode("utf-8")
                    dst.writestr(old_name, content_bytes)

                elif old_name == "[Content_Types].xml":
                    ct_xml = content_bytes.decode("utf-8")
                    ct_xml = re.sub(r'obj_(\w+?)_', lambda m2: f'{_convert_type_name(m2.group(1))}_', ct_xml)
                    ct_xml = ct_xml.replace("version=2.0", "version=2.2")
                    # Strip obj_ from ContentType values: type=obj_Foo -> type=Foo
                    ct_xml = re.sub(r'type=obj_(\w+)', lambda m2: f'type={_convert_type_name(m2.group(1))}', ct_xml)
                    # No PropertyKind entries needed (kept as inline enum)
                    dst.writestr(old_name, ct_xml.encode("utf-8"))

                elif old_name.startswith("_rels/") and old_name.endswith(".rels"):
                    rels_xml = content_bytes.decode("utf-8")
                    rels_xml = re.sub(r'obj_(\w+?)_', lambda m2: f'{_convert_type_name(m2.group(1))}_', rels_xml)
                    new_rels_name = re.sub(r'obj_(\w+?)_', lambda m2: f'{_convert_type_name(m2.group(1))}_', old_name)
                    dst.writestr(new_rels_name, rels_xml.encode("utf-8"))

                else:
                    dst.writestr(old_name, content_bytes)

    print(f"\n  Converted {sum(type_counts.values())} objects ({renamed_count} type renames)")
    print(f"  Types:")
    for t, c in sorted(type_counts.items()):
        print(f"    {t:45s} {c:4d}")
    print(f"\n  Output: {OUT_EPC} ({OUT_EPC.stat().st_size / 1024:.0f} KB)")

    # Quick verification
    print("\n  Verifying sample...")
    with zipfile.ZipFile(OUT_EPC) as z:
        names = [n for n in z.namelist() if n.endswith('.xml') and not n.startswith('[') and not n.startswith('_')]
        has_obj = any(n.startswith('obj_') for n in names)
        sample_issues = []
        for n in names[:20]:
            content = z.read(n).decode()
            if '<eml:ContentType' in content:
                sample_issues.append(f"  {n}: still has <eml:ContentType>")
            if '<eml:UUID' in content:
                sample_issues.append(f"  {n}: still has <eml:UUID>")
            if 'schemaVersion="2.0"' in content:
                sample_issues.append(f"  {n}: still has schemaVersion 2.0")

        print(f"    obj_ prefix remaining: {'YES !!' if has_obj else 'NO ok'}")
        if sample_issues:
            print(f"    Issues found ({len(sample_issues)}):")
            for i in sample_issues[:5]:
                print(f"      {i}")
        else:
            print(f"    Sample check: OK")

    print("\n  Done")


if __name__ == "__main__":
    main()
