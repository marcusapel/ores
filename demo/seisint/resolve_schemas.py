#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resolve_schemas.py - Resolve Generated schemas into the platform-ready
format that the OSDU Schema Service expects.

The Generated schemas from the Data Definitions repo use file-path
``$ref`` links:

    ``$ref: "../abstract/AbstractLegalTags.1.0.0.json"``

The Schema Service expects local definition refs:

    ``$ref: "#/definitions/osdu:wks:AbstractLegalTags:1.0.0"``

This script:
  1. Reads each concrete schema from ``schemas/*.json``
  2. Loads all abstract schemas from ``schemas/abstract/*.json``
  3. Recursively collects every abstract dependency
  4. Builds a ``definitions`` block with the abstracted schemas
  5. Rewrites all ``$ref`` pointers to ``#/definitions/...`` form
  6. Writes the resolved schema to ``schemas/resolved/``
"""

import json
import re
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = SCRIPT_DIR / "schemas"
ABSTRACT_DIR = SCHEMAS_DIR / "abstract"
RESOLVED_DIR = SCHEMAS_DIR / "resolved"


def load_abstract_index():
    """Load all abstract schemas and build filename → (kind, body) map."""
    index = {}
    for path in ABSTRACT_DIR.glob("*.json"):
        with open(path) as f:
            data = json.load(f)
        kind = data.get("x-osdu-schema-source", "")
        index[path.name] = (kind, data)
    return index


def collect_deps(schema_text, abstract_index, collected=None):
    """Recursively collect all abstract dependencies from $ref links."""
    if collected is None:
        collected = {}

    refs = set(re.findall(r'"\$ref"\s*:\s*"([^"]+)"', schema_text))
    for ref in refs:
        # Only process abstract file refs
        if "../abstract/" in ref or (ref.endswith(".json") and ref.startswith("Abstract")):
            fname = ref.split("/")[-1]
            if fname in collected:
                continue
            if fname not in abstract_index:
                print(f"  WARNING: abstract {fname} not found locally")
                continue
            kind, body = abstract_index[fname]
            collected[fname] = (kind, body)
            # Recurse into this abstract's own deps
            collect_deps(json.dumps(body), abstract_index, collected)

    return collected


def rewrite_refs(obj, abstract_index):
    """Deep-walk a JSON object, rewriting $ref file paths to #/definitions/kind."""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "$ref" and isinstance(v, str) and ("../abstract/" in v or v.startswith("Abstract")):
                fname = v.split("/")[-1]
                if fname in abstract_index:
                    kind = abstract_index[fname][0]
                    result[k] = f"#/definitions/{kind}"
                else:
                    result[k] = v  # leave untransformed
            else:
                result[k] = rewrite_refs(v, abstract_index)
        return result
    elif isinstance(obj, list):
        return [rewrite_refs(item, abstract_index) for item in obj]
    else:
        return obj


def resolve_schema(schema_path, abstract_index):
    """Resolve a single concrete schema → platform-ready form."""
    with open(schema_path) as f:
        schema = json.load(f)

    # Collect all abstract dependencies (transitive)
    deps = collect_deps(json.dumps(schema), abstract_index)

    # Also collect deps from the abstracts themselves
    changed = True
    while changed:
        changed = False
        for fname in list(deps.keys()):
            _, body = deps[fname]
            new_deps = collect_deps(json.dumps(body), abstract_index)
            for k, v in new_deps.items():
                if k not in deps:
                    deps[k] = v
                    changed = True

    # Build definitions block: each abstract → rewritten body
    definitions = {}
    for fname, (kind, body) in deps.items():
        resolved_body = rewrite_refs(body, abstract_index)
        definitions[kind] = resolved_body

    # Rewrite the main schema refs
    resolved = rewrite_refs(schema, abstract_index)

    # Insert definitions
    if definitions:
        resolved["definitions"] = definitions

    return resolved


def main():
    RESOLVED_DIR.mkdir(parents=True, exist_ok=True)

    abstract_index = load_abstract_index()
    print(f"Loaded {len(abstract_index)} abstract schemas")

    # Find concrete schemas (not in abstract/ or resolved/)
    concrete_files = sorted(
        p for p in SCHEMAS_DIR.glob("*.json")
        if p.parent == SCHEMAS_DIR
    )

    # Only resolve the 4 that need it
    targets = [
        "StructureMap.1.0.0.json",
        "GenericBinGrid.1.0.0.json",
        "SeismicHorizon.2.1.0.json",
        "HorizonControlPoints.1.0.0.json",
    ]
    concrete_files = [p for p in concrete_files if p.name in targets]

    print(f"Resolving {len(concrete_files)} schemas:\n")

    for path in concrete_files:
        print(f"  {path.name}:")
        resolved = resolve_schema(path, abstract_index)

        # Count definitions
        ndefs = len(resolved.get("definitions", {}))
        out_path = RESOLVED_DIR / path.name
        with open(out_path, "w") as f:
            json.dump(resolved, f, indent=2)
        size = out_path.stat().st_size
        print(f"    → {out_path.name}  ({ndefs} definitions, {size:,} bytes)")

    # Verify: check all $refs point to #/definitions/
    print("\nVerifying resolved schemas...")
    ok = True
    for path in RESOLVED_DIR.glob("*.json"):
        with open(path) as f:
            text = f.read()
        bad_refs = [r for r in re.findall(r'"\$ref"\s*:\s*"([^"]+)"', text)
                    if not r.startswith("#/definitions/")]
        if bad_refs:
            print(f"  ✗ {path.name}: {len(bad_refs)} unresolved refs: {bad_refs[:3]}")
            ok = False
        else:
            print(f"  ✓ {path.name}")
    if ok:
        print("\nAll schemas fully resolved ✓")


if __name__ == "__main__":
    main()
