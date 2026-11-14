"""Load integration modules without Home Assistant.

The package ``__init__`` imports Home Assistant, which these unit tests do not
need. ``const``, ``profiles`` and ``gateway`` are loaded as submodules of a stub
package so their relative imports work.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "danfoss_ally_local"
PKG = "dal"


def load(name: str) -> types.ModuleType:
    """Return module ``name`` from the integration, loading it on first use."""
    full = f"{PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(PKG_DIR)]
        sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(full, PKG_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module
