#!/usr/bin/env python3
"""Quick integration test: push a mock ICS Chrono 2017-like column to local RDDMS.

Usage:
    python test_local_rddms_push.py

Requires the local RDDMS docker stack running on localhost:3000.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import httpx
import json

RDDMS_BASE = "http://localhost:3000/api/reservoir-ddms/v2"
HEADERS = {
    "Authorization": "Bearer dummy",
    "Content-Type": "application/json",
    "data-partition-id": "opendes",
}
DATASPACE = "maap/weco"
DS_ENC = "maap%2Fweco"

# ── Build a mock ICS Chrono 2017 column model ────────────────────────────────

# Simulating ~10 units (enough to exercise the fix; real ICS2017 has ~90+)
CHRONO_UNITS = [
    ("Holocene",       0.0,    0.0117, "#FEF2C0", "Qh"),
    ("Upper Pleistocene", 0.0117, 0.126, "#FFF2AE", "Q4"),
    ("Middle Pleistocene", 0.126, 0.781, "#FFF2C4", "Q3"),
    ("Calabrian",      0.781,  1.80,   "#FFF2D8", "Q2"),
    ("Gelasian",       1.80,   2.58,   "#FFF2EC", "Q1"),
    ("Piacenzian",     2.58,   3.60,   "#FFFFBF", "N23"),
    ("Zanclean",       3.60,   5.333,  "#FFFFD9", "N22"),
    ("Messinian",      5.333,  7.246,  "#FFFF73", "N21"),
    ("Tortonian",      7.246, 11.63,   "#FFFF00", "N17"),
    ("Serravallian",  11.63,  13.82,   "#E6E600", "N14"),
]

model = {
    "column": {
        "id": "ics-chrono-2017-test",
        "data": {"Name": "ICS Chronostratigraphic Chart 2017 (test)"},
    },
    "ranks": [
        {
            "rankName": "Chronostratigraphy",
            "isChrono": True,
            "unitCount": len(CHRONO_UNITS),
            "units": [
                {
                    "name": name,
                    "topMa": top,
                    "baseMa": base,
                    "color": color,
                    "code": code,
                }
                for name, top, base, color, code in CHRONO_UNITS
            ],
        }
    ],
}

# ── Convert model → RESQML objects ───────────────────────────────────────────

from app.strat import _osdu_column_to_resqml

resqml_by_type = _osdu_column_to_resqml(model)
total = sum(len(v) for v in resqml_by_type.values())
print(f"\n✓ Generated {total} RESQML objects across {len(resqml_by_type)} types:")
for typ, objs in resqml_by_type.items():
    print(f"    {typ}: {len(objs)}")

# Verify no TopBoundary/BaseBoundary in unit objects
units = resqml_by_type.get("resqml20.obj_StratigraphicUnitInterpretation", [])
for u in units:
    assert "TopBoundary" not in u, f"TopBoundary found in {u['Citation']['Title']}"
    assert "BaseBoundary" not in u, f"BaseBoundary found in {u['Citation']['Title']}"
print("✓ No TopBoundary/BaseBoundary fields (fix verified)")

# ── Push to local RDDMS ─────────────────────────────────────────────────────

print(f"\n→ Pushing to local RDDMS dataspace: {DATASPACE}")

# 1) Create dataspace (ignore if exists)
print("  1. Creating dataspace...")
r = httpx.post(f"{RDDMS_BASE}/dataspaces", headers=HEADERS, json=[{"Path": DATASPACE}])
if r.status_code in (200, 201, 400, 409):
    print(f"     OK (status={r.status_code})")
else:
    print(f"     Create dataspace: {r.status_code} {r.text[:300]}")
    # try anyway

# 2) Begin transaction
print("  2. Starting transaction...")
r = httpx.post(f"{RDDMS_BASE}/dataspaces/{DS_ENC}/transactions", headers=HEADERS)
if r.status_code not in (200, 201):
    print(f"     FAILED: {r.status_code} {r.text[:500]}")
    sys.exit(1)
try:
    tx_data = r.json()
    tx_id = tx_data if isinstance(tx_data, str) else tx_data.get("transactionId", r.text.strip('" '))
except Exception:
    tx_id = r.text.strip('" \n')
print(f"     tx={tx_id}")

# 3) PUT all objects (ordered: features first, then interpretations, then column)
put_order = [
    "resqml20.obj_RockVolumeFeature",
    "resqml20.obj_BoundaryFeature",
    "resqml20.obj_OrganizationFeature",
    "resqml20.obj_HorizonInterpretation",
    "resqml20.obj_StratigraphicUnitInterpretation",
    "resqml20.obj_StratigraphicColumnRankInterpretation",
    "resqml20.obj_StratigraphicColumn",
]
all_objects = []
for typ in put_order:
    all_objects.extend(resqml_by_type.get(typ, []))

print(f"  3. PUT {len(all_objects)} objects...")
r = httpx.put(
    f"{RDDMS_BASE}/dataspaces/{DS_ENC}/resources",
    headers=HEADERS, json=all_objects,
    params={"transactionId": tx_id},
    timeout=60,
)
if r.status_code not in (200, 201, 204):
    print(f"     PUT FAILED: {r.status_code}")
    print(f"     {r.text[:1000]}")
    # Rollback
    httpx.delete(f"{RDDMS_BASE}/dataspaces/{DS_ENC}/transactions/{tx_id}", headers=HEADERS)
    sys.exit(1)
print(f"     OK (status={r.status_code})")

# 4) Commit transaction
print("  4. Committing transaction...")
r = httpx.put(
    f"{RDDMS_BASE}/dataspaces/{DS_ENC}/transactions/{tx_id}",
    headers=HEADERS,
    timeout=60,
)
if r.status_code not in (200, 201, 204):
    print(f"     COMMIT FAILED: {r.status_code}")
    print(f"     {r.text[:1000]}")
    sys.exit(1)
print(f"     OK (status={r.status_code})")

# 5) Verify: list resources in the dataspace
print(f"\n→ Verifying: listing resources in {DATASPACE}...")
r = httpx.get(f"{RDDMS_BASE}/dataspaces/{DS_ENC}/resources", headers=HEADERS, timeout=30)
if r.status_code == 200:
    data = r.json()
    if isinstance(data, list):
        print(f"  ✓ {len(data)} resource types found")
        for item in data[:10]:
            name = item.get("name", item) if isinstance(item, dict) else item
            print(f"    - {name}")
    else:
        print(f"  Response: {json.dumps(data, indent=2)[:500]}")
else:
    print(f"  List failed: {r.status_code} {r.text[:300]}")

print("\n✓ DONE - push to local RDDMS succeeded!")
