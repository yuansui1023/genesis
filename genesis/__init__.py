from __future__ import annotations

from pathlib import Path

_SOURCE_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "genesis"

# Support `python -m genesis.app.main` directly from a source checkout. Package
# builds still use `src/genesis` because pyproject.toml pins setuptools package
# discovery to the `src` tree.
if _SOURCE_PACKAGE.is_dir():
    __path__ = [str(_SOURCE_PACKAGE)]  # type: ignore[name-defined]

__all__ = [
    "app",
    "core",
    "export",
    "ui",
]
