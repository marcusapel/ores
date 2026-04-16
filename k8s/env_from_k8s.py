#!/usr/bin/env python3
"""
k8s/env_from_k8s.py — Export env vars from k8s ConfigMap + Secret YAML.

Usage (local development — replaces .env):

    # Print export statements (inspect what will be set):
    python k8s/env_from_k8s.py

    # Source into current shell, then run the app:
    eval "$(python k8s/env_from_k8s.py)"
    uvicorn app.main:app --reload

    # Or as a one-liner:
    eval "$(python k8s/env_from_k8s.py)" && uvicorn app.main:app --reload

This reads k8s/configmap.yaml and k8s/secret.yaml (gitignored),
extracts the data/stringData fields, and prints shell export statements.
No .env file needed — the k8s YAMLs are the single source of truth.
"""
from __future__ import annotations

import sys
import pathlib

try:
    import yaml
except ImportError:
    # Minimal YAML parser for simple k8s manifests (flat string maps only).
    # Avoids forcing PyYAML as a dev dependency.
    yaml = None  # type: ignore


def _parse_simple_yaml(text: str) -> dict:
    """Minimal parser for flat k8s YAML — handles data:/stringData: blocks."""
    result: dict[str, str] = {}
    in_data_block = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            if in_data_block and not raw_line.startswith(" ") and not raw_line.startswith("\t"):
                in_data_block = False
            continue
        # Detect top-level data: / stringData: keys
        if stripped in ("data:", "stringData:"):
            in_data_block = True
            continue
        # Detect any other top-level key (no leading whitespace)
        if not raw_line[0].isspace():
            in_data_block = False
            continue
        if in_data_block and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and not key.startswith("#"):
                result[key] = val
    return result


def load_k8s_yaml(path: pathlib.Path) -> dict[str, str]:
    """Load env vars from a k8s ConfigMap or Secret YAML file."""
    if not path.exists():
        return {}
    text = path.read_text()
    if yaml:
        doc = yaml.safe_load(text) or {}
        return {**(doc.get("data") or {}), **(doc.get("stringData") or {})}
    return _parse_simple_yaml(text)


def main():
    k8s_dir = pathlib.Path(__file__).resolve().parent
    config = load_k8s_yaml(k8s_dir / "configmap.yaml")
    secrets = load_k8s_yaml(k8s_dir / "secret.yaml")

    if not config and not secrets:
        print("# ⚠  No config found. Is k8s/secret.yaml present?", file=sys.stderr)
        print("#    Copy the template:  cp k8s/secret.yaml.template k8s/secret.yaml", file=sys.stderr)
        sys.exit(1)

    # Secrets override config (same key → secret wins)
    merged = {**config, **secrets}

    for key, val in sorted(merged.items()):
        # Shell-safe quoting
        safe_val = val.replace("'", "'\\''")
        print(f"export {key}='{safe_val}'")

    count_cfg = len(config)
    count_sec = len(secrets)
    print(f"# Loaded {count_cfg} config + {count_sec} secret vars", file=sys.stderr)


if __name__ == "__main__":
    main()
