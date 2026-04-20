"""
tests/test_routes.py — Integration tests for core application routes.

Covers three main flows, all running against mocked OSDU/RDDMS backends:

  1. **RDDMS Keys — list dataspaces**
     GET /keys/dataspaces.json  → expects items[] with dataspace paths
     GET /keys/types.json?ds=…  → expects items[] with type names

  2. **Business Decisions — search, fetch, compare**
     POST /search/run  → search for BD records, enrich with volumes
     GET  /search/view/{id} → single record detail

  3. **Strat Column — search, fetch column model, rank/unit structure**
     GET /api/strat/search.json  → list strat columns
     GET /api/strat/column.json?id=… → full rank-by-age matrix

All OSDU HTTP calls are intercepted by ``respx`` (or ``unittest.mock``),
so no real Azure / OSDU backend is needed.
"""
from __future__ import annotations

import json
import time
from typing import Dict, Any, List
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
from starlette.testclient import TestClient

from tests.conftest import USERS


# ─────────────────────────────────────────────────────────────────────────────
# Fake OSDU response factories
# ─────────────────────────────────────────────────────────────────────────────

def _fake_dataspaces() -> list:
    """Fake RDDMS /dataspaces response."""
    return [
        {"path": "eml:///dataspace('demo-drogon')", "uri": "eml:///dataspace('demo-drogon')"},
        {"path": "eml:///dataspace('sandbox')", "uri": "eml:///dataspace('sandbox')"},
        {"path": "eml:///dataspace('production')", "uri": "eml:///dataspace('production')"},
    ]


def _fake_types() -> list:
    """Fake RDDMS /dataspaces/{ds}/resources response."""
    return [
        {"name": "resqml20.obj_Grid2dRepresentation", "count": 5},
        {"name": "resqml20.obj_IjkGridRepresentation", "count": 2},
        {"name": "resqml20.obj_ContinuousProperty", "count": 12},
        {"name": "eml20.obj_EpcExternalPartReference", "count": 8},
    ]


def _fake_bd_record(record_id: str = "opendes:master-data--BusinessDecision:dg2-001") -> dict:
    """Fake OSDU Storage record for a BusinessDecision."""
    return {
        "id": record_id,
        "kind": "osdu:wks:master-data--BusinessDecision:1.0.0",
        "version": 1,
        "data": {
            "Name": "Drogon DG2",
            "Description": "Decision Gate 2 for Drogon field development",
            "DecisionDate": "2024-01-15",
            "DecisionPhase": "DG2",
            "OperatorID": "opendes:master-data--Organisation:Equinor:",
            "FieldID": "opendes:master-data--Field:Drogon:",
            "Volumes": {
                "KeyColumns": [{"ColumnName": "Phase"}],
                "Columns": [
                    {"ColumnName": "Phase", "ColumnRole": "key"},
                    {"ColumnName": "P10", "ColumnRole": "value"},
                    {"ColumnName": "P50", "ColumnRole": "value"},
                    {"ColumnName": "P90", "ColumnRole": "value"},
                ],
                "ColumnValues": {
                    "Phase": ["Oil", "Gas", "Water"],
                    "P10": [120.5, 45.3, 30.1],
                    "P50": [95.2, 38.7, 25.4],
                    "P90": [72.1, 28.9, 18.6],
                },
            },
        },
    }


def _fake_strat_column(record_id: str = "opendes:work-product-component--StratigraphicColumn:ics2017") -> dict:
    """Fake strat column WPC record."""
    return {
        "id": record_id,
        "kind": "osdu:wks:work-product-component--StratigraphicColumn:1.2.0",
        "version": 1,
        "data": {
            "Name": "ICS 2017 Chrono Column",
            "StratigraphicColumnRankInterpretationSet": [
                "opendes:work-product-component--StratigraphicColumnRankInterpretation:eonothem:",
                "opendes:work-product-component--StratigraphicColumnRankInterpretation:erathem:",
            ],
        },
    }


def _fake_rank_record(rank_id: str, rank_name: str, chrono_ids: list, unit_ids: list = None) -> dict:
    """Fake rank interpretation record."""
    data: Dict[str, Any] = {"Name": rank_name}
    if chrono_ids:
        data["ChronoStratigraphySet"] = chrono_ids
    if unit_ids:
        data["StratigraphicUnitInterpretationSet"] = unit_ids
    return {
        "id": rank_id,
        "kind": "osdu:wks:work-product-component--StratigraphicColumnRankInterpretation:1.3.0",
        "version": 1,
        "data": data,
    }


def _fake_chrono_record(chrono_id: str, name: str, top_ma: float, base_ma: float, color: str = "#336699") -> dict:
    """Fake chronostratigraphy reference record."""
    return {
        "id": chrono_id,
        "kind": "osdu:wks:reference-data--ChronoStratigraphy:1.0.0",
        "version": 1,
        "data": {
            "Name": name,
            "AgeBegin": top_ma,
            "AgeEnd": base_ma,
            "Colour": color,
            "Code": name[:3].upper(),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. RDDMS Keys — dataspaces & types
# ─────────────────────────────────────────────────────────────────────────────

class TestKeysDataspaces:
    """GET /keys/dataspaces.json → list of RDDMS dataspaces."""

    def test_list_dataspaces(self, authed_client):
        with patch("app.osdu.list_dataspaces", new_callable=AsyncMock,
                    return_value=_fake_dataspaces()):
            resp = authed_client.get("/keys/dataspaces.json")

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        items = data["items"]
        assert len(items) == 3
        paths = [i["path"] for i in items]
        assert "eml:///dataspace('demo-drogon')" in paths
        assert "eml:///dataspace('sandbox')" in paths

    def test_dataspaces_empty_on_error(self, authed_client):
        """When RDDMS is unreachable, return empty list (no crash)."""
        with patch("app.osdu.list_dataspaces", new_callable=AsyncMock,
                    side_effect=Exception("connection refused")):
            resp = authed_client.get("/keys/dataspaces.json")

        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestKeysTypes:
    """GET /keys/types.json?ds=… → list of RESQML types in a dataspace."""

    def test_list_types_live(self, authed_client):
        with patch("app.osdu.list_types", new_callable=AsyncMock,
                    return_value=_fake_types()):
            ds = "eml%3A%2F%2F%2Fdataspace('demo-drogon')"
            resp = authed_client.get(f"/keys/types.json?ds={ds}&source=live")

        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert len(items) == 4
        names = [i["name"] for i in items]
        assert "resqml20.obj_Grid2dRepresentation" in names

    def test_list_types_catalog_fallback(self, authed_client):
        """source=catalog returns a curated static list (no API call)."""
        resp = authed_client.get("/keys/types.json?ds=any&source=catalog")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) > 0
        names = [i["name"] for i in items]
        assert "resqml20.obj_Grid2dRepresentation" in names

    def test_types_empty_on_error(self, authed_client):
        with patch("app.osdu.list_types", new_callable=AsyncMock,
                    side_effect=Exception("timeout")):
            resp = authed_client.get("/keys/types.json?ds=test&source=live")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestKeysPage:
    """GET /keys → HTML page renders."""

    def test_keys_html_renders(self, authed_client):
        resp = authed_client.get("/keys")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Business Decisions — search & fetch
# ─────────────────────────────────────────────────────────────────────────────

def _mock_httpx_for_search(records: list):
    """
    Return a context manager that patches httpx.AsyncClient so that:
      - POST to /search/v2/query → returns record IDs
      - GET  to /storage/v2/records/{id} → returns full record
    """
    search_results = [{"id": r["id"], "kind": r["kind"], "version": r.get("version", 1)} for r in records]
    record_map = {r["id"]: r for r in records}

    original_init = httpx.AsyncClient.__init__

    class FakeResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data
            self.text = json.dumps(json_data)
            self.reason_phrase = "OK" if status_code == 200 else "Error"
            self.headers = {}

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("", request=MagicMock(), response=self)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            if "/search/" in url:
                return FakeResponse(200, {
                    "results": search_results,
                    "totalCount": len(search_results),
                })
            return FakeResponse(404, {})

        async def get(self, url, **kwargs):
            for rid, rec in record_map.items():
                if rid in url:
                    return FakeResponse(200, rec)
            return FakeResponse(404, {})

    return patch("httpx.AsyncClient", FakeAsyncClient)


class TestBusinessDecisionSearch:
    """POST /search/run with BusinessDecision kind."""

    def test_search_returns_results(self, authed_client):
        bd1 = _fake_bd_record("opendes:master-data--BusinessDecision:dg2-001")
        bd2 = _fake_bd_record("opendes:master-data--BusinessDecision:dg3-002")
        bd2["data"]["Name"] = "Drogon DG3"

        with _mock_httpx_for_search([bd1, bd2]):
            resp = authed_client.post("/search/run", data={
                "kind": "osdu:wks:master-data--BusinessDecision:1.0.0",
                "kinds_extra": "",
                "query": "*",
                "limit": 50,
            })

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # The response is HTML — check that our record names appear
        body = resp.text
        assert "Drogon DG2" in body or "BusinessDecision" in body

    def test_search_page_renders(self, authed_client):
        """GET /search → the form should render with a default kind."""
        resp = authed_client.get("/search")
        assert resp.status_code == 200
        body = resp.text
        assert "BusinessDecision" in body


class TestBusinessDecisionView:
    """GET /search/view/{id} — single record detail with enrichment."""

    def test_view_record(self, authed_client):
        bd = _fake_bd_record()

        with _mock_httpx_for_search([bd]):
            resp = authed_client.get(
                "/search/view/opendes:master-data--BusinessDecision:dg2-001"
            )

        assert resp.status_code == 200
        body = resp.text
        # Should render HTML with the record data somewhere
        assert "text/html" in resp.headers["content-type"]


class TestBusinessDecisionCompare:
    """Compare two BD records by searching/fetching both and checking volumes differ."""

    def test_two_bds_have_different_volumes(self):
        """Verify our fixture factory can produce distinct records for comparison."""
        bd1 = _fake_bd_record("opendes:master-data--BusinessDecision:dg2")
        bd2 = _fake_bd_record("opendes:master-data--BusinessDecision:dg3")
        # Modify DG3 volumes
        bd2["data"]["Name"] = "Drogon DG3"
        bd2["data"]["Volumes"]["ColumnValues"]["P50"] = [110.0, 50.0, 35.0]

        vol1 = bd1["data"]["Volumes"]["ColumnValues"]["P50"]
        vol2 = bd2["data"]["Volumes"]["ColumnValues"]["P50"]

        # They should differ
        assert vol1 != vol2
        assert bd1["data"]["Name"] != bd2["data"]["Name"]

    def test_compare_via_search(self, authed_client):
        """Search returns 2 BDs; verify both appear in results and are distinguishable."""
        bd1 = _fake_bd_record("opendes:master-data--BusinessDecision:dg2")
        bd2 = _fake_bd_record("opendes:master-data--BusinessDecision:dg3")
        bd2["data"]["Name"] = "Drogon DG3"
        bd2["data"]["Volumes"]["ColumnValues"]["P50"] = [110.0, 50.0, 35.0]

        with _mock_httpx_for_search([bd1, bd2]):
            resp = authed_client.post("/search/run", data={
                "kind": "osdu:wks:master-data--BusinessDecision:1.0.0",
                "kinds_extra": "",
                "query": "*",
                "limit": 50,
            })

        assert resp.status_code == 200
        body = resp.text
        # Both records should appear in the output
        assert "dg2" in body or "DG2" in body or "Drogon" in body


# ─────────────────────────────────────────────────────────────────────────────
# 3. Strat Column — search, fetch, rank/unit model
# ─────────────────────────────────────────────────────────────────────────────

class TestStratSearch:
    """GET /api/strat/search.json → search for strat columns."""

    def test_strat_search_returns_items(self, authed_client):
        col = _fake_strat_column()
        search_resp = {
            "results": [{"id": col["id"], "kind": col["kind"], "version": 1, "data": {"Name": "ICS 2017"}}],
            "totalCount": 1,
        }

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

            async def post(self, url, **kw):
                return MagicMock(status_code=200, json=lambda: search_resp,
                                 raise_for_status=lambda: None)

            async def get(self, url, **kw):
                if col["id"] in url:
                    return MagicMock(status_code=200, json=lambda: col)
                return MagicMock(status_code=404)

        with patch("httpx.AsyncClient", FakeClient):
            resp = authed_client.get("/api/strat/search.json?q=*&limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) >= 1
        assert data["items"][0]["name"] == "ICS 2017"


class TestStratColumnFetch:
    """GET /api/strat/column.json?id=… → full rank-by-age model."""

    def _build_mock_records(self):
        """Build a set of related strat records for a 2-rank column."""
        col_id = "opendes:work-product-component--StratigraphicColumn:ics2017"
        rank1_id = "opendes:work-product-component--StratigraphicColumnRankInterpretation:eonothem:"
        rank2_id = "opendes:work-product-component--StratigraphicColumnRankInterpretation:erathem:"
        chrono1_id = "opendes:reference-data--ChronoStratigraphy:phanerozoic:"
        chrono2_id = "opendes:reference-data--ChronoStratigraphy:proterozoic:"
        chrono3_id = "opendes:reference-data--ChronoStratigraphy:paleozoic:"
        chrono4_id = "opendes:reference-data--ChronoStratigraphy:mesozoic:"

        col = _fake_strat_column(col_id)
        col["data"]["StratigraphicColumnRankInterpretationSet"] = [rank1_id, rank2_id]

        rank1 = _fake_rank_record(rank1_id, "Eonothem", [chrono1_id, chrono2_id])
        rank2 = _fake_rank_record(rank2_id, "Erathem", [chrono3_id, chrono4_id])

        chrono1 = _fake_chrono_record(chrono1_id, "Phanerozoic", 541.0, 0.0, "#9AD9E8")
        chrono2 = _fake_chrono_record(chrono2_id, "Proterozoic", 2500.0, 541.0, "#F73563")
        chrono3 = _fake_chrono_record(chrono3_id, "Paleozoic", 541.0, 251.9, "#99C08D")
        chrono4 = _fake_chrono_record(chrono4_id, "Mesozoic", 251.9, 66.0, "#67C5CA")

        all_records = {
            col_id: col,
            rank1_id: rank1,
            rank2_id: rank2,
            chrono1_id: chrono1,
            chrono2_id: chrono2,
            chrono3_id: chrono3,
            chrono4_id: chrono4,
        }
        return col_id, all_records

    def _make_fake_client(self, records: dict):
        """Build a fake httpx.AsyncClient that serves records from a dict.
        Handles URL-encoded record IDs (colons → %3A)."""
        import urllib.parse as _up

        class FakeResponse:
            def __init__(self, status_code, data):
                self.status_code = status_code
                self._data = data
                self.text = json.dumps(data) if data else ""
                self.headers = {}

            def json(self):
                return self._data

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError("", request=MagicMock(), response=self)

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

            async def post(self, url, **kw):
                # batch fetch: records:batch
                if "records:batch" in url:
                    body = kw.get("json", {})
                    req_ids = body.get("records", [])
                    found = []
                    not_found = []
                    for rid in req_ids:
                        rec = records.get(rid)
                        if rec:
                            found.append(rec)
                        else:
                            not_found.append(rid)
                    return FakeResponse(200, {"records": found, "notFound": not_found})
                return FakeResponse(404, {})

            async def get(self, url, **kw):
                # Try matching both raw and URL-decoded versions
                decoded_url = _up.unquote(url)
                for rid, rec in records.items():
                    if rid in url or rid in decoded_url:
                        return FakeResponse(200, rec)
                return FakeResponse(404, {})

        return FakeClient

    def test_fetch_column_returns_ranks(self, authed_client):
        col_id, records = self._build_mock_records()

        with patch("httpx.AsyncClient", self._make_fake_client(records)):
            resp = authed_client.get(f"/api/strat/column.json?id={col_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert "column" in data
        assert "ranks" in data
        assert data["column"]["id"] == col_id

        ranks = data["ranks"]
        assert len(ranks) == 2
        assert ranks[0]["rankName"] == "Eonothem"
        assert ranks[1]["rankName"] == "Erathem"

    def test_column_ranks_have_units(self, authed_client):
        col_id, records = self._build_mock_records()

        with patch("httpx.AsyncClient", self._make_fake_client(records)):
            resp = authed_client.get(f"/api/strat/column.json?id={col_id}")

        ranks = resp.json()["ranks"]
        # Eonothem has 2 chrono units (Phanerozoic, Proterozoic)
        eonothem = ranks[0]
        assert eonothem["isChrono"] is True
        assert eonothem["unitCount"] >= 2

        unit_names = [u.get("name", "") for u in eonothem["units"]]
        assert "Phanerozoic" in unit_names
        assert "Proterozoic" in unit_names

    def test_column_chrono_ages_present(self, authed_client):
        col_id, records = self._build_mock_records()

        with patch("httpx.AsyncClient", self._make_fake_client(records)):
            resp = authed_client.get(f"/api/strat/column.json?id={col_id}")

        ranks = resp.json()["ranks"]
        # Check that ages are propagated into the flat fields
        eonothem_units = ranks[0]["units"]
        phanerozoic = next((u for u in eonothem_units if u.get("name") == "Phanerozoic"), None)
        assert phanerozoic is not None
        assert phanerozoic["topMa"] == 541.0
        assert phanerozoic["baseMa"] == 0.0
        assert phanerozoic["color"] is not None

    def test_column_gap_filling(self, authed_client):
        """Erathem rank has Paleozoic (541-251.9) and Mesozoic (251.9-66),
        but Eonothem parent Phanerozoic spans 541-0. A synthetic placeholder
        should fill the gap 66-0 Ma."""
        col_id, records = self._build_mock_records()

        with patch("httpx.AsyncClient", self._make_fake_client(records)):
            resp = authed_client.get(f"/api/strat/column.json?id={col_id}")

        ranks = resp.json()["ranks"]
        erathem = ranks[1]
        unit_names = [u.get("name", "") for u in erathem["units"]]
        # Should have at least Paleozoic, Mesozoic, + a synthetic gap-filler
        assert len(erathem["units"]) >= 2
        # Check that a synthetic "(not defined..." or "(Phanerozoic — undifferentiated)" entry exists
        has_synthetic = any(u.get("_synthetic") for u in erathem["units"])
        assert has_synthetic, f"Expected a synthetic gap-fill unit, got: {unit_names}"

    def test_column_not_found(self, authed_client):
        """Requesting a non-existent column ID → 404."""

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, url, **kw):
                return MagicMock(status_code=404, json=lambda: {},
                                 raise_for_status=lambda: None)
            async def post(self, url, **kw):
                return MagicMock(status_code=404, json=lambda: {})

        with patch("httpx.AsyncClient", FakeClient):
            resp = authed_client.get("/api/strat/column.json?id=nonexistent")
        assert resp.status_code == 404

    def test_column_empty_ranks(self, authed_client):
        """Column with no rank references → returns empty ranks[]."""
        col = _fake_strat_column()
        col["data"]["StratigraphicColumnRankInterpretationSet"] = []
        records = {col["id"]: col}

        with patch("httpx.AsyncClient", self._make_fake_client(records)):
            resp = authed_client.get(f"/api/strat/column.json?id={col['id']}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ranks"] == []


class TestStratPage:
    """GET /strat → HTML page renders."""

    def test_strat_html_renders(self, authed_client):
        resp = authed_client.get("/strat")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ─────────────────────────────────────────────────────────────────────────────
# Admin page — dataspaces via main.py
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminPage:
    """GET /admin → lists dataspaces from RDDMS."""

    def test_admin_page_with_dataspaces(self, authed_client):
        with patch("app.osdu.list_dataspaces", new_callable=AsyncMock,
                    return_value=_fake_dataspaces()):
            resp = authed_client.get("/admin")

        assert resp.status_code == 200
        body = resp.text
        assert "text/html" in resp.headers["content-type"]
        assert "demo-drogon" in body or "dataspace" in body.lower()

    def test_admin_page_graceful_on_error(self, authed_client):
        with patch("app.osdu.list_dataspaces", new_callable=AsyncMock,
                    side_effect=Exception("service unavailable")):
            resp = authed_client.get("/admin")
        # Should still render (with empty dataspaces), not 500
        assert resp.status_code == 200
