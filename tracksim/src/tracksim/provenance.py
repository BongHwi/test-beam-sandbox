"""Small provenance helpers shared by transport and the run lifecycle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import random
import sys

from .runtime import PACKAGE_ROOT


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SOURCE_ROOTS: dict[str, Path] = {}


def register_source_root(label: str, root: Path) -> None:
    """Include every file under ``root`` in ``source_hashes()`` as ``label/<rel>``."""
    if not isinstance(label, str) or not label.isidentifier():
        raise ValueError(f"source root label must be an identifier: {label!r}")
    if label == "tracksim" or label in _SOURCE_ROOTS:
        raise ValueError(f"source root {label!r} is already registered")
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"source root is not a directory: {root}")
    _SOURCE_ROOTS[label] = root


def _tree_hashes(label: str, root: Path) -> dict[str, str]:
    return {
        f"{label}/{path.relative_to(root).as_posix()}": file_hash(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def source_hashes() -> dict[str, str]:
    """Hash the tracksim package and every registered source root."""
    hashes = _tree_hashes("tracksim", PACKAGE_ROOT)
    for label in sorted(_SOURCE_ROOTS):
        hashes.update(_tree_hashes(label, _SOURCE_ROOTS[label]))
    return hashes


def python_identity() -> dict:
    return {
        "sys_version": sys.version,
        "sys_platform": sys.platform,
        "implementation": sys.implementation.name,
        "machine": platform.machine(),
        "random_version": random.Random.VERSION,
        "libc": list(platform.libc_ver()),
    }


def write_json(path: Path, value) -> None:
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    pending.replace(path)
