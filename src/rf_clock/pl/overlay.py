"""The overlay packaged with rf-clock and its version binding.

``src/rf_clock/overlay/`` holds ``rf_clock.bit``, ``.hwh``, ``rf_clock_hw.xsa`` and
``manifest.json``, written by ``tools/package_overlay.py`` from a finished
Vivado build.  The package version is A.B.C.D: A.B is the register map
``VERSION``, C counts bitstreams built for it, D counts Python-only releases.
The build script puts it into P2 ``BUILD_VERSION`` (docs/ABI.md 4.1.6); a
bitstream and this package belong together when A.B.C are equal
(docs/ABI.md 14.1).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
from typing import Any

OVERLAY_DIR = Path(__file__).resolve().parents[1] / "overlay"
DEFAULT_BITSTREAM = OVERLAY_DIR / "rf_clock.bit"
MANIFEST_NAME = "manifest.json"


class VersionMismatch(RuntimeError):
    pass


def package_version() -> str:
    """Version of the installed rf-clock distribution.

    An editable install reports the version it was installed with; after
    changing pyproject.toml, reinstall (``uv pip install --no-deps -e .``).
    """
    return importlib.metadata.version("rf-clock")


def release(version: str) -> tuple[int, int, int]:
    """``(A, B, C)`` of an ``A.B.C.D`` version: the part a bitstream fixes."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", version.strip())
    if match is None:
        raise ValueError(f"not an A.B.C.D version: {version!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def require_same_release(built: str, installed: str, what: str) -> None:
    if release(built) != release(installed):
        raise VersionMismatch(
            f"{what} was built for rf-clock {built}, installed rf-clock is {installed}; "
            "A.B.C must match (a stale editable install needs "
            "`uv pip install --no-deps -e .`)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_overlay(bitstream: str | Path, installed: str) -> dict[str, Any] | None:
    """Check ``manifest.json`` next to ``bitstream`` before it is loaded.

    Returns the manifest, or ``None`` when the directory has none (a
    bitstream straight from a build directory).  Raises ``VersionMismatch``
    when the manifest's version is another release, or when the .bit or
    .hwh is not the file the manifest lists -- such as a Git LFS pointer
    left by a clone without ``git lfs pull``, or an .hwh from another build,
    whose addresses would not match the bitstream.
    """
    bitstream = Path(bitstream)
    manifest_path = bitstream.with_name(MANIFEST_NAME)
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require_same_release(manifest["build_version"], installed, str(bitstream.parent))
    for path in (bitstream, bitstream.with_suffix(".hwh")):
        expected = manifest["sha256"].get(path.name)
        if expected is None:
            raise VersionMismatch(f"{manifest_path} does not list {path.name}")
        if not path.exists() or _sha256(path) != expected:
            raise VersionMismatch(
                f"{path} is not the file {manifest_path} lists "
                "(a Git LFS pointer? run `git lfs pull`)")
    return manifest
