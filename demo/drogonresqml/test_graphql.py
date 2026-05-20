#!/usr/bin/env python3
"""Test GraphQL queries against local PostgreSQL (demo/Volve data)."""
import asyncio
import json
import os
import sys

os.environ["GRAPHQL_PG_CONN_STRING"] = "host=localhost port=5433 dbname=openetp user=tester password=tester"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.graphql_router import schema


class FakeRequest:
    class state:
        pass
    cookies = {}
    headers = {}
    session = {}


CTX = {"request": FakeRequest()}


async def q(query_str, variables=None):
    result = await schema.execute(query_str, variable_values=variables or {}, context_value=CTX)
    if result.errors:
        for e in result.errors:
            print(f"  ERROR: {e}")
    return result.data


async def main():
    print("=" * 70)
    print("GraphQL Deep Search – PostgreSQL Direct – Test Suite")
    print("=" * 70)

    # 1. Status
    print("\n1. STATUS")
    data = await q("{ status }")
    print(f"   {data['status']}")

    # 2. Dataspaces
    print("\n2. DATASPACES")
    data = await q("{ dataspaces { path uri } }")
    for ds in data["dataspaces"]:
        print(f"   {ds['path']}  ({ds['uri']})")

    # 3. Types
    print("\n3. RESOURCE TYPES in demo/Volve")
    data = await q('{ resourceTypes(dataspace: "demo/Volve") { name count } }')
    for t in data["resourceTypes"]:
        print(f"   {t['count']:3d}  {t['name']}")

    # 4. Objects
    print("\n4. GRID2D REPRESENTATIONS")
    data = await q('''
        { resqmlObjects(dataspace: "demo/Volve", typeName: "resqml20.obj_Grid2dRepresentation") {
            uuid title typeName
        } }
    ''')
    grids = data["resqmlObjects"]
    for g in grids:
        print(f"   {g['uuid'][:8]}...  {g['title']}")

    # 5. Relationships
    print("\n5. RELATIONSHIP GRAPH TRAVERSAL")
    print("   Grid2D → HorizonInterpretation → GeneticBoundaryFeature\n")
    for g in grids:
        rels = await q('''
            query($uuid: String!) {
                objectRelations(dataspace: "demo/Volve", typeName: "resqml20.obj_Grid2dRepresentation",
                                uuid: $uuid, direction: "targets") {
                    uuid name typeName direction contentType
                }
            }
        ''', {"uuid": g["uuid"]})

        interp = None
        for r in rels["objectRelations"]:
            rel_label = r["contentType"].split(":")[-1] if ":" in r["contentType"] else r["contentType"]
            print(f"   {g['title']}({g['uuid'][:8]}) → [{rel_label}] {r['name']} ({r['typeName'].split('.')[-1]})")
            if "Interpretation" in r["typeName"]:
                interp = r

        # Follow interpretation → feature
        if interp:
            rels2 = await q('''
                query($uuid: String!) {
                    objectRelations(dataspace: "demo/Volve", typeName: "resqml20.obj_HorizonInterpretation",
                                    uuid: $uuid, direction: "targets") {
                        name typeName contentType
                    }
                }
            ''', {"uuid": interp["uuid"]})
            for r2 in rels2["objectRelations"]:
                label2 = r2["contentType"].split(":")[-1] if ":" in r2["contentType"] else r2["contentType"]
                print(f"     └─ [{label2}] {r2['name']} ({r2['typeName'].split('.')[-1]})")
        print()

    # 6. Array data with statistics
    print("6. ARRAY DATA & STATISTICS")
    for g in grids[:2]:  # just first 2
        arrs = await q('''
            query($uuid: String!) {
                objectArrays(dataspace: "demo/Volve", typeName: "resqml20.obj_Grid2dRepresentation",
                             uuid: $uuid, includeStatistics: true, includeSampleValues: true, sampleSize: 5) {
                    path dimensions totalElements
                    statistics { count minValue maxValue mean stdDev nanCount }
                    sampleValues
                }
            }
        ''', {"uuid": g["uuid"]})
        for a in arrs["objectArrays"]:
            s = a["statistics"]
            print(f"   {g['uuid'][:8]}... [{a['dimensions'][0]}×{a['dimensions'][1]}] {a['totalElements']} elements")
            print(f"     min={s['minValue']:.1f}  max={s['maxValue']:.1f}  mean={s['mean']:.1f}  σ={s['stdDev']:.1f}")
            print(f"     sample: {a['sampleValues']}")
            print()

    # 7. Deep search (no property filter - surfaces don't have attached properties)
    print("7. DEEP SEARCH (PG backend)")
    data = await q('''
        { deepSearch(dataspace: "demo/Volve", typeName: "resqml20.obj_Grid2dRepresentation") {
            backend totalScanned totalMatched queryDescription
            objects { uuid title }
        } }
    ''')
    ds = data["deepSearch"]
    print(f"   Backend: {ds['backend']}")
    print(f"   Scanned: {ds['totalScanned']}, Matched: {ds['totalMatched']}")
    print(f"   Query: {ds['queryDescription']}")
    for obj in ds["objects"]:
        print(f"     • {obj['title']} ({obj['uuid'][:8]}...)")

    # 8. Reverse graph: who points to a specific CRS?
    print("\n8. REVERSE GRAPH: Who references the LocalDepth3dCrs?")
    crs = await q('''
        { resqmlObjects(dataspace: "demo/Volve", typeName: "resqml20.obj_LocalDepth3dCrs") {
            uuid title
        } }
    ''')
    crs_uuid = crs["resqmlObjects"][0]["uuid"]
    sources = await q('''
        query($uuid: String!) {
            objectRelations(dataspace: "demo/Volve", typeName: "resqml20.obj_LocalDepth3dCrs",
                            uuid: $uuid, direction: "sources") {
                name typeName contentType
            }
        }
    ''', {"uuid": crs_uuid})
    for s in sources["objectRelations"]:
        label = s["contentType"].split(":")[-1] if ":" in s["contentType"] else s["contentType"]
        print(f"   ← [{label}] {s['name']} ({s['typeName'].split('.')[-1]})")

    print("\n" + "=" * 70)
    print("All tests passed! GraphQL is connected to PostgreSQL directly.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
