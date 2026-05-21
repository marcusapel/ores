"""Integration test for the full auto-correlation pipeline (Q1).

Tests the complete flow: load wells → suggest defaults → run engine →
extract diverse results → label scenarios.

Uses only small demos (≤10 wells) to keep runtime reasonable.
"""

import pytest
from pathlib import Path

DEMO_DIR = Path(__file__).parent.parent / "demo" / "data"


def _get_demo_paths():
    """Find demo datasets with ≤10 wells (fast enough for CI)."""
    from weco.data import WellList
    paths = []
    for p in sorted(DEMO_DIR.glob("*/wells.txt")):
        try:
            wl = WellList()
            wl.read(str(p))
            if len(wl.wells) <= 10:
                paths.append(p)
        except Exception:
            pass
    return paths


class TestAutoCorrelationPipeline:
    """End-to-end tests for the /auto pipeline logic."""

    @pytest.fixture(params=_get_demo_paths(), ids=lambda p: p.parent.name)
    def demo_wl(self, request):
        """Load a demo WellList."""
        from weco.data import WellList
        wl = WellList()
        wl.read(str(request.param))
        return wl

    def test_suggest_defaults(self, demo_wl):
        """Suggest-defaults should return valid options for any demo."""
        from weco.api import _suggest_defaults_for_wells
        options, reasoning = _suggest_defaults_for_wells(demo_wl)

        assert isinstance(options, dict)
        assert isinstance(reasoning, dict)
        assert len(options) > 0, "Suggest-defaults returned empty options"

    def test_full_pipeline(self, demo_wl):
        """Full pipeline: suggest → run → extract → diversify → label."""
        from weco.api import (_suggest_defaults_for_wells, _run_engine,
                              _extract_results, _diverse_results,
                              _topology_signature, _label_scenario)

        # Suggest
        options, reasoning = _suggest_defaults_for_wells(demo_wl)
        options["nbr-cor"] = 10  # keep fast
        options["out-nbr-cor"] = 5
        options["max-cor"] = 30

        # Run
        rf, data, elapsed = _run_engine(demo_wl, options)
        assert elapsed >= 0
        if rf.get_nbr_results() == 0:
            pytest.skip("Engine returned 0 results (dataset too constrained for max-cor=30)")

        # Extract
        results = _extract_results(rf, data, 10)
        assert len(results) > 0
        assert results[0].cost >= 0
        assert len(results[0].lines) > 0

        # Diversify
        diverse_idx = _diverse_results(rf, data, n_best=10, n_diverse=3)
        assert len(diverse_idx) >= 1
        assert all(isinstance(i, int) for i in diverse_idx)

        # Topology + scenario
        for idx in diverse_idx:
            sig = _topology_signature(rf, idx, rf.nbr_well())
            assert isinstance(sig, tuple)
            assert all(isinstance(s, int) for s in sig)
            label = _label_scenario(sig)
            assert label in ("Layer-cake", "Pinch-out", "Unconformity",
                             "Condensed", "Onlap", "Complex", "Unknown")

    def test_pipeline_result_structure(self, demo_wl):
        """Result structure matches what the /auto endpoint returns."""
        from weco.api import (_suggest_defaults_for_wells, _run_engine,
                              _extract_results)

        options, _ = _suggest_defaults_for_wells(demo_wl)
        options["nbr-cor"] = 10
        options["max-cor"] = 30

        rf, data, elapsed = _run_engine(demo_wl, options)
        results = _extract_results(rf, data, 5)

        for r in results:
            assert hasattr(r, "index")
            assert hasattr(r, "cost")
            assert hasattr(r, "lines")
            assert hasattr(r, "diversity_score")
            for line in r.lines:
                assert hasattr(line, "markers")
                assert hasattr(line, "line_type")
                assert len(line.markers) == len(demo_wl.wells)
                assert line.line_type in ("boundary", "gap", "framework")
