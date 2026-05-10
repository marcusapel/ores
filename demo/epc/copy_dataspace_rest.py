#!/usr/bin/env python3
"""
copy_dataspace_rest.py – Copy RESQML content between RDDMS dataspaces via REST.

Uses REST transactional writes (begin_transaction → put_resources →
put_arrays → commit) which bypasses ETP PutDataObjects ACL restrictions.

Works across instances (local → remote, remote → remote, etc.).

Usage:
    # Same instance
    python -m demo.epc.copy_dataspace_rest \\
        --src maap/drogon --dst maap/drogon_copy

    # Cross-instance (local RDDMS → swedev)
    python -m demo.epc.copy_dataspace_rest \\
        --src-url http://localhost:9002/api/reservoir-ddms/v2 \\
        --src maap/drogon \\
        --dst maap/saxo3

    # Dry-run: just list what would be copied
    python -m demo.epc.copy_dataspace_rest --src maap/drogon --dry-run

Environment:
    Uses demo/_auth.py for token management.
    Default instance: swedev (override with --instance).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("Missing requests — pip install requests")

# ── Paths ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DEMO_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(DEMO_DIR))

# ── URI / type helpers ─────────────────────────────────────────────────
_EML_RE = re.compile(
    r"(?P<type>[\w.]+)\((?P<uuid>[0-9a-fA-F-]{36})\)"
)


def _enc(ds: str) -> str:
    return urllib.parse.quote(ds, safe="")


# ── Lightweight REST session ───────────────────────────────────────────
class RddmsRest:
    """Minimal sync REST client for RDDMS v2."""

    def __init__(self, base_url: str, token: str, partition: str = "dev"):
        self.base = base_url.rstrip("/")
        self.token = token
        self.partition = partition
        self.sess = requests.Session()
        a = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=10)
        self.sess.mount("https://", a)
        self.sess.mount("http://", a)

    def _h(self, ct: str = "application/json") -> dict:
        h: dict[str, str] = {"Authorization": f"Bearer {self.token}"}
        if self.partition:
            h["data-partition-id"] = self.partition
        if ct:
            h["Content-Type"] = ct
        return h

    def _url(self, *parts: str) -> str:
        return "/".join([self.base] + list(parts))

    # ── Read ───────────────────────────────────────────────────────
    def list_types(self, ds: str) -> list[dict]:
        r = self.sess.get(
            self._url("dataspaces", _enc(ds), "resources"),
            headers=self._h(""), timeout=30,
        )
        r.raise_for_status()
        return r.json() or []

    def list_resources(self, ds: str, typ: str) -> list[dict]:
        r = self.sess.get(
            self._url("dataspaces", _enc(ds), "resources", typ),
            headers=self._h(""), timeout=60,
        )
        r.raise_for_status()
        return r.json() or []

    def get_object(self, ds: str, typ: str, uid: str) -> Any:
        r = self.sess.get(
            self._url("dataspaces", _enc(ds), "resources", typ, uid),
            headers=self._h(""),
            params={"$format": "json"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        return data

    def list_arrays(self, ds: str, typ: str, uid: str) -> list[dict]:
        r = self.sess.get(
            self._url("dataspaces", _enc(ds), "resources", typ, uid, "arrays"),
            headers=self._h(""), timeout=60,
        )
        r.raise_for_status()
        return r.json() or []

    def get_array(self, ds: str, typ: str, uid: str, path: str) -> dict:
        enc_path = urllib.parse.quote(path, safe="")
        r = self.sess.get(
            self._url("dataspaces", _enc(ds), "resources", typ, uid, "arrays", enc_path),
            headers=self._h(""),
            params={"format": "json"},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    # ── Write (transactional) ──────────────────────────────────────
    def begin_transaction(self, ds: str) -> str:
        r = self.sess.post(
            self._url("dataspaces", _enc(ds), "transactions"),
            headers=self._h(), timeout=30,
        )
        r.raise_for_status()
        return r.text.strip().strip('"')

    def commit(self, ds: str, tx: str) -> None:
        r = self.sess.put(
            self._url("dataspaces", _enc(ds), "transactions", tx),
            headers=self._h(), timeout=600,
        )
        r.raise_for_status()

    def cancel(self, ds: str, tx: str) -> None:
        self.sess.delete(
            self._url("dataspaces", _enc(ds), "transactions", tx),
            headers=self._h(""), timeout=30,
        )

    def put_resources(self, ds: str, objs: list[dict], tx: str) -> None:
        r = self.sess.put(
            self._url("dataspaces", _enc(ds), "resources"),
            headers=self._h(),
            data=json.dumps(objs),
            params={"transactionId": tx},
            timeout=120,
        )
        if not r.ok:
            print(f"  PUT resources → {r.status_code}: {r.text[:500]}", file=sys.stderr)
        r.raise_for_status()

    def put_arrays(self, ds: str, arrs: list[dict], tx: str) -> None:
        r = self.sess.put(
            self._url("dataspaces", _enc(ds), "resources", "arrays"),
            headers=self._h(),
            data=json.dumps(arrs),
            params={"transactionId": tx},
            timeout=300,
        )
        if not r.ok:
            print(f"  PUT arrays → {r.status_code}: {r.text[:500]}", file=sys.stderr)
        r.raise_for_status()


# ── Copy logic ─────────────────────────────────────────────────────────

def _parse_type_from_uri(uri: str) -> tuple[str, str]:
    """Extract (type, uuid) from an EML URI."""
    m = _EML_RE.search(uri)
    if m:
        return m.group("type"), m.group("uuid")
    return "", ""


def copy_dataspace(
    src: RddmsRest,
    dst: RddmsRest,
    src_ds: str,
    dst_ds: str,
    *,
    batch_size: int = 10,
    dry_run: bool = False,
) -> dict:
    """Copy all objects + arrays from src_ds to dst_ds via REST transactions.

    Returns summary dict.
    """
    print(f"Scanning source: {src_ds}")
    types = src.list_types(src_ds)
    type_names = []
    for t in types:
        name = t.get("name") or ""
        if not name:
            uri = t.get("uri", "")
            tp, _ = _parse_type_from_uri(uri)
            name = tp
        if name:
            type_names.append(name)
    print(f"  {len(type_names)} types found")

    # Enumerate all objects
    all_items: list[tuple[str, str]] = []  # (type, uuid)
    for typ in sorted(type_names):
        entries = src.list_resources(src_ds, typ)
        for e in entries:
            uri = e.get("uri", "")
            t, uid = _parse_type_from_uri(uri)
            if t and uid:
                all_items.append((t, uid))
    print(f"  {len(all_items)} objects total")

    if dry_run:
        by_type: dict[str, int] = {}
        for t, _ in all_items:
            by_type[t] = by_type.get(t, 0) + 1
        for t in sorted(by_type):
            print(f"    {by_type[t]:>5}  {t}")
        return {"objects": len(all_items), "types": len(type_names), "dry_run": True}

    # Phase 1: Read all objects
    print("Reading objects...")
    json_objects: list[dict] = []
    errors_read = 0
    for i, (typ, uid) in enumerate(all_items):
        try:
            obj = src.get_object(src_ds, typ, uid)
            if isinstance(obj, dict):
                json_objects.append(obj)
        except Exception as exc:
            errors_read += 1
            print(f"  WARN: read {typ}/{uid} failed: {exc}", file=sys.stderr)
        if (i + 1) % 50 == 0:
            print(f"  read {i+1}/{len(all_items)}...")
    print(f"  {len(json_objects)} objects read ({errors_read} errors)")

    # Phase 2: Read arrays
    print("Reading arrays...")
    array_defs: list[dict] = []
    for i, (typ, uid) in enumerate(all_items):
        try:
            arr_meta = src.list_arrays(src_ds, typ, uid)
        except Exception:
            continue
        if not arr_meta:
            continue
        for am in arr_meta:
            # Extract path_in_resource from metadata
            am_uid = am.get("uid", {})
            path = am_uid.get("pathInResource", "") if isinstance(am_uid, dict) else ""
            if not path:
                continue
            try:
                arr_data = src.get_array(src_ds, typ, uid, path)
                # Build array def for PUT
                dims = am.get("dimensions") or arr_data.get("dimensions", [])
                data_payload = arr_data.get("data", {})
                flat_data = data_payload.get("data", []) if isinstance(data_payload, dict) else data_payload

                # Determine container info
                container_type = am_uid.get("containerType", f"eml20.obj_EpcExternalPartReference")
                container_uuid = am_uid.get("containerUuid", uid)

                array_defs.append({
                    "ContainerType": container_type,
                    "ContainerUuid": container_uuid,
                    "PathInResource": path,
                    "Dimensions": dims if isinstance(dims, list) else [dims],
                    "Data": flat_data,
                    "ArrayType": am.get("arrayType", "Float64Array"),
                })
            except Exception as exc:
                print(f"  WARN: array {typ}/{uid}/{path} failed: {exc}", file=sys.stderr)
        if (i + 1) % 50 == 0 and array_defs:
            print(f"  scanned {i+1}/{len(all_items)} objects, {len(array_defs)} arrays so far...")
    print(f"  {len(array_defs)} arrays read")

    # Phase 3: Write to destination
    print(f"Writing to {dst_ds}...")
    tx = dst.begin_transaction(dst_ds)
    print(f"  transaction: {tx}")

    try:
        # PUT objects in batches
        for i in range(0, len(json_objects), batch_size):
            batch = json_objects[i:i + batch_size]
            dst.put_resources(dst_ds, batch, tx)
            print(f"  objects {i+1}..{i+len(batch)} of {len(json_objects)}")

        # PUT arrays in batches
        ARR_BATCH = 5
        for i in range(0, len(array_defs), ARR_BATCH):
            batch = array_defs[i:i + ARR_BATCH]
            dst.put_arrays(dst_ds, batch, tx)
            print(f"  arrays {i+1}..{i+len(batch)} of {len(array_defs)}")

        # Commit
        print("  committing...")
        t0 = time.time()
        dst.commit(dst_ds, tx)
        print(f"  committed ({time.time() - t0:.1f}s)")

    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        print("  rolling back...", file=sys.stderr)
        try:
            dst.cancel(dst_ds, tx)
        except Exception:
            pass
        raise

    return {
        "objects": len(json_objects),
        "arrays": len(array_defs),
        "read_errors": errors_read,
    }


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Copy RDDMS dataspace content via REST transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--src", required=True, help="Source dataspace path (e.g. maap/drogon)")
    p.add_argument("--dst", help="Destination dataspace path (default: <src>_copy)")
    p.add_argument("--instance", default="swedev", help="Auth instance name (default: swedev)")
    p.add_argument("--src-url", help="Source RDDMS base URL (overrides --instance for reads)")
    p.add_argument("--dst-url", help="Destination RDDMS base URL (overrides --instance for writes)")
    p.add_argument("--src-partition", default="", help="Source partition (for cross-instance)")
    p.add_argument("--dst-partition", default="", help="Destination partition")
    p.add_argument("--src-token", default="", help="Source bearer token (for local/no-auth servers)")
    p.add_argument("--batch", type=int, default=10, help="Object batch size (default: 10)")
    p.add_argument("--dry-run", action="store_true", help="List objects without copying")
    args = p.parse_args()

    dst_ds = args.dst or f"{args.src}_copy"

    # Resolve auth
    from _auth import get_token, load_instance
    inst = load_instance(args.instance)
    token = get_token(args.instance)
    default_url = f"{inst['host']}/api/reservoir-ddms/v2"
    default_partition = inst["partition"]

    src_url = args.src_url or default_url
    dst_url = args.dst_url or default_url
    src_partition = args.src_partition or ("" if args.src_url else default_partition)
    dst_partition = args.dst_partition or default_partition
    src_token = args.src_token or token

    src_client = RddmsRest(src_url, src_token, src_partition)
    dst_client = RddmsRest(dst_url, token, dst_partition)

    print(f"Source:      {src_url}  ds={args.src}")
    print(f"Destination: {dst_url}  ds={dst_ds}")
    print()

    t0 = time.time()
    result = copy_dataspace(
        src_client, dst_client, args.src, dst_ds,
        batch_size=args.batch, dry_run=args.dry_run,
    )
    elapsed = time.time() - t0

    print()
    print(f"Done in {elapsed:.1f}s")
    for k, v in result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
