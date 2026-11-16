"""Run-directory policy."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def run_path(path: Path) -> Path:
    """Resolve a run directory; it must not be inside the tracksim package."""
    path = Path(path).resolve()
    if path == PACKAGE_ROOT or path.is_relative_to(PACKAGE_ROOT):
        raise ValueError(f"run directory must not be inside the tracksim package: {path}")
    if path == Path(path.anchor):
        raise ValueError(f"run directory must not be a filesystem root: {path}")
    return path
