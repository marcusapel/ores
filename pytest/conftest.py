"""Shared fixtures for WeCo test suite."""

import os
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
        # Redirect out-file to tmp/ so it doesn't pollute the project root
        tmp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        engine.set_option_ext("out-file", os.path.join(tmp_dir, "out.txt"))
    except Exception:
        pass
    yield
