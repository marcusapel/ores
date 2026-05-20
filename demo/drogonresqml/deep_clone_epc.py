#!/usr/bin/env python3
"""
Deep-clone an EPC file by remapping all UUIDs to fresh values.

Produces a new EPC with identical topology and metadata but globally
unique UUIDs - suitable for importing into the same RDDMS instance
as a truly independent dataspace.

Usage:
    python -m demo.epc.deep_clone_epc input.epc [output.epc]

If output is omitted, writes to <input>_clone.epc.

Algorithm:
  1. Scan all file contents (XML objects, .rels, Content_Types) for
     UUID patterns (8-4-4-4-12 hex).
  2. Build a deterministic old→new mapping (uuid4 per original).
  3. Text-replace every occurrence in every file's content.
  4. Rename filenames that embed UUIDs (obj_Type_UUID.xml, _rels/...).
  5. Re-pack into a new EPC (ZIP) archive.

No HDF5 rewriting needed - RDDMS EPC exports embed array data in
the binary store (no external .h5 files inside the archive).
"""

from __future__ import annotations

import re
import sys
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

# Regex: standard UUID pattern (lowercase or uppercase hex)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _collect_uuids(zf: zipfile.ZipFile) -> set[str]:
    """Scan all entries for UUID occurrences and return the unique set."""
    uuids: set[str] = set()
    for name in zf.namelist():
        try:
            content = zf.read(name).decode("utf-8")
        except (UnicodeDecodeError, KeyError):
            continue
        uuids.update(m.lower() for m in _UUID_RE.findall(content))
    # Also scan filenames themselves
    for name in zf.namelist():
        uuids.update(m.lower() for m in _UUID_RE.findall(name))
    return uuids


def _build_mapping(old_uuids: set[str]) -> dict[str, str]:
    """Create a mapping from every old UUID to a fresh uuid4."""
    return {old: str(uuid.uuid4()) for old in sorted(old_uuids)}


def _remap_text(text: str, mapping: dict[str, str]) -> str:
    """Replace all UUID occurrences in text using the mapping.

    Handles both lower and upper case occurrences by normalizing
    to lowercase before lookup, then preserving the new UUID in
    lowercase (EPC convention).
    """

    def _replacer(match: re.Match) -> str:
        old = match.group(0).lower()
        return mapping.get(old, match.group(0))

    return _UUID_RE.sub(_replacer, text)


def _remap_filename(name: str, mapping: dict[str, str]) -> str:
    """Replace UUID segments in a filename path."""

    def _replacer(match: re.Match) -> str:
        old = match.group(0).lower()
        return mapping.get(old, match.group(0))

    return _UUID_RE.sub(_replacer, name)


def deep_clone(src_path: Path, dst_path: Path) -> dict:
    """Clone an EPC, remapping all UUIDs. Returns stats dict."""
    with zipfile.ZipFile(src_path, "r") as zf:
        # Phase 1: collect all UUIDs
        old_uuids = _collect_uuids(zf)
        mapping = _build_mapping(old_uuids)

        # Phase 2: rewrite and repack
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for entry in zf.namelist():
                info = zf.getinfo(entry)
                raw = zf.read(entry)

                # Remap content (text files only)
                try:
                    text = raw.decode("utf-8")
                    new_text = _remap_text(text, mapping)
                    new_raw = new_text.encode("utf-8")
                except UnicodeDecodeError:
                    # Binary entry - pass through unchanged
                    new_raw = raw

                # Remap filename
                new_name = _remap_filename(entry, mapping)

                # Write with original metadata (except filename)
                new_info = zipfile.ZipInfo(new_name)
                new_info.compress_type = info.compress_type or zipfile.ZIP_DEFLATED
                new_info.external_attr = info.external_attr
                zout.writestr(new_info, new_raw)

        # Write output
        dst_path.write_bytes(buf.getvalue())

    return {
        "src": str(src_path),
        "dst": str(dst_path),
        "uuids_remapped": len(mapping),
        "entries": len(zipfile.ZipFile(src_path).namelist()),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        print("\nError: input EPC path required.", file=sys.stderr)
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"Error: {src} not found.", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) >= 3:
        dst = Path(sys.argv[2])
    else:
        dst = src.with_stem(src.stem + "_clone")

    print(f"Deep-cloning EPC: {src}")
    print(f"  Output: {dst}")
    stats = deep_clone(src, dst)
    print(f"  UUIDs remapped: {stats['uuids_remapped']}")
    print(f"  Entries: {stats['entries']}")
    print(f"  Size: {dst.stat().st_size / 1024:.0f} kB")
    print("Done.")


if __name__ == "__main__":
    main()
