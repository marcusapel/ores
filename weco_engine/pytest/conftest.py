"""Shared fixtures for WeCo test suite."""

import pytest


@pytest.fixture(autouse=True)
def _reset_engine_globals():
    """Reset C++ engine global state before each test.

    The WeCo C++ binding uses process-global option state that persists
    across ProjectExt instances. This fixture ensures each test starts
    with a clean slate.
    """
    # Pre-test cleanup: reset known sticky options
    try:
        from weco.ext import ProjectExt
        engine = ProjectExt()
        for key in ("no-crossing", "var-data2", "var-data3"):
            engine.set_option_ext(key, "")
        for key in ("var-weight2", "var-weight3", "const-gap-cost",
                    "band-width", "min-dist"):
            engine.set_option_ext(key, "0")
    except Exception:
        pass
    yield
