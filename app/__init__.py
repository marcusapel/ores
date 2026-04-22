# app package

# ── Auto-load env vars from k8s YAMLs (single source of truth) ──────────────
# When running outside the `ores` bash wrapper (e.g. VS Code debugger, direct
# `uvicorn app.main:app`), env vars from k8s/configmap.yaml + k8s/secret.yaml
# are injected into os.environ automatically.  Already-set vars are NOT
# overwritten, so explicit env / k8s pod injection always wins.
import os as _os
import pathlib as _pathlib
import sys as _sys


def _auto_load_k8s_env() -> None:
    """Populate os.environ from k8s/ YAMLs when vars are missing."""
    k8s_dir = _pathlib.Path(__file__).resolve().parent.parent / "k8s"
    configmap = k8s_dir / "configmap.yaml"
    secret = k8s_dir / "secret.yaml"

    if not configmap.exists() and not secret.exists():
        return  # nothing to load (e.g. in container with real k8s env)

    # Re-use the loader from env_from_k8s.py
    _sys.path.insert(0, str(k8s_dir))
    try:
        from env_from_k8s import load_k8s_yaml
    except ImportError:
        return
    finally:
        _sys.path.pop(0)

    merged = {**load_k8s_yaml(configmap), **load_k8s_yaml(secret)}
    injected = 0
    for key, val in merged.items():
        if key not in _os.environ:
            _os.environ[key] = val
            injected += 1

    if injected:
        import logging
        logging.getLogger("rddms-admin").info(
            "Auto-loaded %d/%d vars from k8s/ YAMLs (%d already set)",
            injected, len(merged), len(merged) - injected,
        )


_auto_load_k8s_env()
