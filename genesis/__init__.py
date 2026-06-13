from __future__ import annotations

from pathlib import Path

_SOURCE_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "genesis"

# Let `python -m genesis.app.main` work directly from the repository root while
# keeping the actual package implementation under `src/genesis`.
if _SOURCE_PACKAGE.is_dir():
    __path__ = [str(_SOURCE_PACKAGE)]  # type: ignore[name-defined]

__all__ = [
    "app",
    "core",
    "export",
    "ui",
]
