"""
End-to-end validation of resqml_viz PG and REST paths.

Tests:
  1. PG path: fetch + validate structure + render PNG (Grid2d)
  2. PG path: fetch_geometry_3d for all supported types
  3. REST path: fetch + validate structure + render PNG (Grid2d)
  4. REST path: fetch_geometry_3d for available types
  5. Pipeline consistency: _surface_to_3d() from both sources
  6. Public API: PG-first fallback integration

Run:
    python test/test_pg_vs_rest.py          # standalone
    python -m pytest test/test_pg_vs_rest.py -v -s   # via pytest
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── Bootstrap ──────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "demo"))

os.environ.setdefault(
    "GRAPHQL_PG_CONN_STRING",
    "host=localhost port=5433 dbname=rddms user=foo password=bar",
)

from _auth import get_token, load_instance  # noqa: E402

_inst = load_instance("swedev")
_TOKEN = get_token("swedev")

from app import osdu  # noqa: E402
osdu.OSDU_BASE_URL = (_inst.get("host") or "").replace("https://", "")
osdu.DATA_PARTITION_ID = _inst.get("partition") or "dev"

from app import resqml_viz  # noqa: E402
from app.pg_backend import get_pool as _get_pool, pg_list_resources as _pg_list_resources, pg_list_arrays as _pg_list_arrays, pg_read_array as _pg_read_array  # noqa: E402

DS = "maap/drogon"
OUT_DIR = REPO / "test" / "_pg_vs_rest_output"


# ── Validation helpers ────────────────────────────────────────────────────

def validate_surface(label: str, s: dict[str, Any]) -> list[str]:
    """Check a fetch_grid2d_surface result has the right shape and contents."""
    issues: list[str] = []
    for k in ("grid", "zvalues", "dims", "geometry"):
        if k not in s:
            issues.append(f"missing key '{k}'")
    dims = s.get("dims", [0, 0])
    if dims[0] <= 0 or dims[1] <= 0:
        issues.append(f"bad dims: {dims}")
    zv = s.get("zvalues", [])
    expected = dims[0] * dims[1]
    if len(zv) != expected:
        issues.append(f"zvalues length {len(zv)} != expected {expected}")
    geo = s.get("geometry", {})
    for gk in ("origin", "u_vec", "v_vec", "u_space", "v_space", "n_slow", "n_fast"):
        if gk not in geo:
            issues.append(f"geometry missing '{gk}'")
    if s.get("crs") is None:
        issues.append("crs is None (may be OK)")
    return issues


def validate_3d(label: str, g: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Check a fetch_geometry_3d result is valid for Three.js.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []
    kind = g.get("kind")
    if kind not in ("surface", "points", "trajectory", "markers"):
        errors.append(f"unknown kind: {kind}")
    pos = g.get("positions", [])
    if not pos:
        # For markers, empty positions is expected (they use MD values)
        if kind == "markers":
            warnings.append("positions empty (expected for markers)")
        else:
            errors.append("positions is empty")
    elif len(pos) % 3 != 0:
        errors.append(f"positions length {len(pos)} not multiple of 3")
    n_verts = len(pos) // 3

    if kind == "surface":
        idx = g.get("indices", [])
        if not idx:
            errors.append("surface has no indices")
        elif len(idx) % 3 != 0:
            errors.append(f"indices length {len(idx)} not multiple of 3")
        if idx and n_verts > 0:
            max_idx = max(idx)
            if max_idx >= n_verts:
                errors.append(f"index {max_idx} >= n_verts {n_verts}")

    for key in ("zmin", "zmax"):
        if key not in g:
            errors.append(f"missing {key}")

    if g.get("zmin", 0) > g.get("zmax", 0) and g.get("zmin") != g.get("zmax"):
        errors.append(f"zmin ({g['zmin']}) > zmax ({g['zmax']})")

    return errors, warnings


# ── PG tests ──────────────────────────────────────────────────────────────

async def test_pg_grid2d_surface():
    """PG: fetch Grid2d surface + validate + render PNG."""
    pool = await _get_pool()
    if not pool:
        return "SKIP", "no PG pool"

    resources = await _pg_list_resources(pool, DS,
        "resqml20.obj_Grid2dRepresentation", limit=3)
    if not resources:
        return "SKIP", "no Grid2d in PG"

    lines: list[str] = []
    for r in resources[:2]:
        uid, title = r["uuid"], r["title"]
        t0 = time.monotonic()
        surface = await resqml_viz._pg_grid2d_surface(pool, DS, uid)
        dt = time.monotonic() - t0
        if not surface:
            lines.append(f"  {title}: PG returned None")
            continue

        issues = validate_surface(title, surface)
        dims = surface.get("dims", [0, 0])
        nz = len(surface.get("zvalues", []))
        crs_ok = "yes" if surface.get("crs") else "no"
        lines.append(f"  {title}: {dims[0]}x{dims[1]}, {nz} z-values, "
                      f"crs={crs_ok}, {dt:.2f}s")
        if issues:
            for iss in issues:
                if "crs is None" in iss:
                    continue
                lines.append(f"    ! {iss}")

        # Render PNG
        try:
            png = resqml_viz.render_grid2d_png(
                surface["zvalues"], surface["dims"],
                surface["geometry"], surface["crs"],
                title=f"PG: {title}",
            )
            lines.append(f"    PNG: {len(png)} bytes")
            OUT_DIR.mkdir(exist_ok=True)
            slug = title.replace(" ", "_")[:30]
            (OUT_DIR / f"pg_{slug}.png").write_bytes(png)
        except Exception as e:
            lines.append(f"    PNG ERROR: {e}")

    has_errors = any("ERROR" in l or ("!" in l and "crs is None" not in l) for l in lines)
    return ("FAIL" if has_errors else "PASS"), "\n".join(lines)


async def test_pg_geometry3d():
    """PG: fetch_geometry_3d for all supported types."""
    pool = await _get_pool()
    if not pool:
        return "SKIP", "no PG pool"

    types = [
        ("resqml20.obj_Grid2dRepresentation", "Grid2d"),
        ("resqml20.obj_PointSetRepresentation", "PointSet"),
        ("resqml20.obj_WellboreTrajectoryRepresentation", "Trajectory"),
        ("resqml20.obj_WellboreMarkerFrameRepresentation", "Markers"),
    ]

    lines: list[str] = []
    has_errors = False
    for typ, short in types:
        resources = await _pg_list_resources(pool, DS, typ, limit=20)
        if not resources:
            lines.append(f"  {short}: no objects in PG")
            continue
        # Pick the first object that has binary data (some have empty bins)
        chosen = None
        for r in resources:
            arrays = await _pg_list_arrays(pool, DS, r["uuid"])
            if arrays:
                vals = await _pg_read_array(pool, DS, r["uuid"], arrays[0]["path"])
                if vals:
                    chosen = r
                    break
        if not chosen:
            # Fall back to first resource even if bins empty
            chosen = resources[0]
            lines.append(f"  {short}: note: no objects with binary data, using {chosen['title']}")

        uid, title = chosen["uuid"], chosen["title"]
        t0 = time.monotonic()
        try:
            result = await resqml_viz._pg_geometry3d(pool, DS, typ, uid)
        except Exception as e:
            lines.append(f"  {short}/{title}: PG ERROR: {e}")
            has_errors = True
            continue
        dt = time.monotonic() - t0
        if not result:
            lines.append(f"  {short}/{title}: PG returned None")
            has_errors = True
            continue

        errs, warns = validate_3d(f"{short}/{title}", result)
        n_verts = len(result.get("positions", [])) // 3
        n_idx = len(result.get("indices", [])) // 3
        kind = result.get("kind", "?")
        lines.append(f"  {short}/{title}: kind={kind}, "
                      f"verts={n_verts}, tris={n_idx}, "
                      f"z=[{result.get('zmin',0):.1f}, {result.get('zmax',0):.1f}], "
                      f"{dt:.2f}s")
        if result.get("md"):
            lines.append(f"    md: {len(result['md'])} values")
        if result.get("labels"):
            lines.append(f"    labels: {result['labels'][:3]}")
        for w in warns:
            lines.append(f"    ~ {w}")
        for e in errs:
            lines.append(f"    ! {e}")
            has_errors = True

    return ("FAIL" if has_errors else "PASS"), "\n".join(lines)


# ── REST tests ────────────────────────────────────────────────────────────

async def _rest_discover_uuids(typ: str, limit: int = 2) -> list[tuple[str, str]]:
    """Get sample UUIDs from remote RDDMS."""
    import httpx
    host = f"https://{osdu.OSDU_BASE_URL}"
    hdr = osdu.headers(_TOKEN)
    url = f"{host}/api/reservoir-ddms/v2/dataspaces/maap%2Fdrogon/resources/{typ}"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, headers=hdr)
        if r.status_code != 200:
            return []
        items = r.json()
        result = []
        for obj in items[:limit]:
            uri = obj.get("uri", "")
            if "(" in uri:
                uid = uri.rsplit("(", 1)[-1].rstrip(")")
                name = obj.get("name", uid[:8])
                result.append((uid, name))
        return result


async def test_rest_grid2d_surface():
    """REST: fetch Grid2d surface + validate + render PNG."""
    samples = await _rest_discover_uuids(
        "resqml20.obj_Grid2dRepresentation", limit=2)
    if not samples:
        return "SKIP", "no Grid2d on remote RDDMS"

    lines: list[str] = []
    for uid, title in samples[:1]:
        t0 = time.monotonic()
        try:
            surface = await resqml_viz._rest_grid2d_surface(_TOKEN, DS, uid)
        except Exception as e:
            lines.append(f"  {title}: REST ERROR: {e}")
            continue
        dt = time.monotonic() - t0

        issues = validate_surface(title, surface)
        dims = surface.get("dims", [0, 0])
        nz = len(surface.get("zvalues", []))
        crs_ok = "yes" if surface.get("crs") else "no"
        lines.append(f"  {title}: {dims[0]}x{dims[1]}, {nz} z-values, "
                      f"crs={crs_ok}, {dt:.2f}s")
        for iss in issues:
            if "crs is None" in iss:
                continue
            lines.append(f"    ! {iss}")

        # Render PNG
        try:
            png = resqml_viz.render_grid2d_png(
                surface["zvalues"], surface["dims"],
                surface["geometry"], surface["crs"],
                title=f"REST: {title}",
            )
            lines.append(f"    PNG: {len(png)} bytes")
            OUT_DIR.mkdir(exist_ok=True)
            slug = title.replace(" ", "_")[:30]
            (OUT_DIR / f"rest_{slug}.png").write_bytes(png)
        except Exception as e:
            lines.append(f"    PNG ERROR: {e}")

    has_errors = any("ERROR" in l or ("!" in l and "crs is None" not in l) for l in lines)
    return ("FAIL" if has_errors else "PASS"), "\n".join(lines)


async def test_rest_geometry3d():
    """REST: fetch_geometry_3d for available types."""
    types = [
        ("resqml20.obj_Grid2dRepresentation", "Grid2d"),
        ("resqml20.obj_PointSetRepresentation", "PointSet"),
        ("resqml20.obj_WellboreTrajectoryRepresentation", "Trajectory"),
        ("resqml20.obj_WellboreMarkerFrameRepresentation", "Markers"),
    ]

    lines: list[str] = []
    has_errors = False
    for typ, short in types:
        samples = await _rest_discover_uuids(typ, limit=1)
        if not samples:
            lines.append(f"  {short}: no objects on remote RDDMS")
            continue
        uid, title = samples[0]
        t0 = time.monotonic()
        try:
            result = await resqml_viz._rest_geometry3d(_TOKEN, DS, typ, uid)
        except Exception as e:
            lines.append(f"  {short}/{title}: REST ERROR: {e}")
            has_errors = True
            continue
        dt = time.monotonic() - t0

        errs, warns = validate_3d(f"{short}/{title}", result)
        n_verts = len(result.get("positions", [])) // 3
        n_idx = len(result.get("indices", [])) // 3
        kind = result.get("kind", "?")
        lines.append(f"  {short}/{title}: kind={kind}, "
                      f"verts={n_verts}, tris={n_idx}, "
                      f"z=[{result.get('zmin',0):.1f}, {result.get('zmax',0):.1f}], "
                      f"{dt:.2f}s")
        if result.get("md"):
            lines.append(f"    md: {len(result['md'])} values")
        if result.get("labels"):
            lines.append(f"    labels: {result['labels'][:3]}")
        for w in warns:
            lines.append(f"    ~ {w}")
        for e in errs:
            lines.append(f"    ! {e}")
            has_errors = True

    return ("FAIL" if has_errors else "PASS"), "\n".join(lines)


# ── Pipeline consistency ─────────────────────────────────────────────────

async def test_surface_to_3d_consistency():
    """Validate _surface_to_3d produces valid 3D mesh from both sources."""
    lines: list[str] = []
    has_errors = False

    # PG source
    pool = await _get_pool()
    if pool:
        resources = await _pg_list_resources(pool, DS,
            "resqml20.obj_Grid2dRepresentation", limit=1)
        if resources:
            uid = resources[0]["uuid"]
            title = resources[0]["title"]
            surface = await resqml_viz._pg_grid2d_surface(pool, DS, uid)
            if surface:
                mesh = resqml_viz._surface_to_3d(surface)
                errs, warns = validate_3d(f"PG->3D/{title}", mesh)
                n_verts = len(mesh.get("positions", [])) // 3
                n_tris = len(mesh.get("indices", [])) // 3
                lines.append(f"  PG->3D/{title}: verts={n_verts}, tris={n_tris}, "
                              f"z=[{mesh.get('zmin',0):.1f}, {mesh.get('zmax',0):.1f}]")
                for w in warns:
                    lines.append(f"    ~ {w}")
                for e in errs:
                    lines.append(f"    ! {e}")
                    has_errors = True

    # REST source
    samples = await _rest_discover_uuids(
        "resqml20.obj_Grid2dRepresentation", limit=1)
    if samples:
        uid, title = samples[0]
        try:
            surface = await resqml_viz._rest_grid2d_surface(_TOKEN, DS, uid)
            mesh = resqml_viz._surface_to_3d(surface)
            errs, warns = validate_3d(f"REST->3D/{title}", mesh)
            n_verts = len(mesh.get("positions", [])) // 3
            n_tris = len(mesh.get("indices", [])) // 3
            lines.append(f"  REST->3D/{title}: verts={n_verts}, tris={n_tris}, "
                          f"z=[{mesh.get('zmin',0):.1f}, {mesh.get('zmax',0):.1f}]")
            for w in warns:
                lines.append(f"    ~ {w}")
            for e in errs:
                lines.append(f"    ! {e}")
                has_errors = True
        except Exception as e:
            lines.append(f"  REST->3D: ERROR: {e}")
            has_errors = True

    if not lines:
        return "SKIP", "no data available"
    return ("FAIL" if has_errors else "PASS"), "\n".join(lines)


# ── Public API (PG-first fallback) ───────────────────────────────────────

async def test_public_api():
    """Test the public fetch_grid2d_surface + fetch_geometry_3d API (PG-first)."""
    lines: list[str] = []
    has_errors = False

    pool = await _get_pool()
    if not pool:
        return "SKIP", "no PG pool"

    resources = await _pg_list_resources(pool, DS,
        "resqml20.obj_Grid2dRepresentation", limit=1)
    if resources:
        uid = resources[0]["uuid"]
        title = resources[0]["title"]
        t0 = time.monotonic()
        surface = await resqml_viz.fetch_grid2d_surface(_TOKEN, DS, uid)
        dt = time.monotonic() - t0
        dims = surface.get("dims", [0, 0])
        lines.append(f"  fetch_grid2d_surface/{title}: {dims[0]}x{dims[1]}, {dt:.2f}s")

    types = [
        ("resqml20.obj_Grid2dRepresentation", "Grid2d"),
        ("resqml20.obj_PointSetRepresentation", "PointSet"),
        ("resqml20.obj_WellboreTrajectoryRepresentation", "Trajectory"),
        ("resqml20.obj_WellboreMarkerFrameRepresentation", "Markers"),
    ]
    for typ, short in types:
        # Pick an object with binary data if possible
        rows = await _pg_list_resources(pool, DS, typ, limit=20)
        if not rows:
            continue
        chosen = rows[0]
        for r in rows:
            arrays = await _pg_list_arrays(pool, DS, r["uuid"])
            if arrays:
                vals = await _pg_read_array(pool, DS, r["uuid"], arrays[0]["path"])
                if vals:
                    chosen = r
                    break
        uid = chosen["uuid"]
        title = chosen["title"]
        t0 = time.monotonic()
        try:
            result = await resqml_viz.fetch_geometry_3d(_TOKEN, DS, typ, uid)
            dt = time.monotonic() - t0
            errs, warns = validate_3d(f"{short}/{title}", result)
            n_v = len(result.get("positions", [])) // 3
            lines.append(f"  fetch_geometry_3d/{short}/{title}: "
                          f"kind={result.get('kind')}, verts={n_v}, {dt:.2f}s")
            for w in warns:
                lines.append(f"    ~ {w}")
            for e in errs:
                lines.append(f"    ! {e}")
                has_errors = True
        except Exception as e:
            lines.append(f"  fetch_geometry_3d/{short}/{title}: ERROR: {e}")
            has_errors = True

    if not lines:
        return "SKIP", "no PG data"
    return ("FAIL" if has_errors else "PASS"), "\n".join(lines)


# ── Runner ────────────────────────────────────────────────────────────────

async def run_all():
    print(f"\n{'='*70}")
    print(f"resqml_viz end-to-end validation  --  dataspace: {DS}")
    print(f"{'='*70}\n")

    tests = [
        ("PG: Grid2d surface + PNG", test_pg_grid2d_surface),
        ("PG: geometry3d all types", test_pg_geometry3d),
        ("REST: Grid2d surface + PNG", test_rest_grid2d_surface),
        ("REST: geometry3d all types", test_rest_geometry3d),
        ("Pipeline: _surface_to_3d consistency", test_surface_to_3d_consistency),
        ("Public API: PG-first fallback", test_public_api),
    ]

    n_pass = 0
    n_fail = 0
    n_skip = 0

    for name, fn in tests:
        try:
            status, detail = await fn()
        except Exception as e:
            status, detail = "FAIL", f"  UNHANDLED: {e}"

        icon = {"PASS": "v", "FAIL": "X", "SKIP": "-"}[status]
        print(f"  [{icon}]  {name}")
        if detail:
            for line in detail.split("\n"):
                print(f"      {line}")

        if status == "PASS":
            n_pass += 1
        elif status == "FAIL":
            n_fail += 1
        else:
            n_skip += 1

    print(f"\n{'~'*70}")
    print(f"Results:  {n_pass} passed,  {n_fail} failed,  {n_skip} skipped")
    print(f"{'~'*70}\n")

    return n_fail


# ── pytest ────────────────────────────────────────────────────────────────

def test_pg_vs_rest():
    """Pytest wrapper."""
    n_fail = asyncio.get_event_loop().run_until_complete(run_all())
    assert n_fail == 0, f"{n_fail} test(s) failed"


if __name__ == "__main__":
    n = asyncio.run(run_all())
    sys.exit(n)
