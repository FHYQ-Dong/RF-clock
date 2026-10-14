"""Copy a finished Vivado build into the rf-clock package.

    python tools/package_overlay.py BUILD_DIR

BUILD_DIR is an OUT_DIR of PL/bd/create_rf_clock_bd.tcl run with STAGE impl.
The build must be complete with timing met, and built for the version now in
pyproject.toml (status.txt ``build_version``).  Writes rf_clock.bit, .hwh,
rf_clock_hw.xsa and manifest.json to src/rf_clock/overlay/; PL.open() checks
the manifest before loading (docs/ABI.md 14.1).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "src" / "rf_clock" / "overlay"
# Build file -> packaged name.  The XSA must not share the bitstream's stem:
# PYNQ 3.1 then takes the XSA branch and fails to parse the .bit.
FILES = {"rf_clock.bit": "rf_clock.bit", "rf_clock.hwh": "rf_clock.hwh",
         "rf_clock.xsa": "rf_clock_hw.xsa"}


def read_status(path: Path) -> dict[str, str]:
    status = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.isidentifier():
            status[key] = value
    return status


def pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        sys.exit("pyproject.toml has no version")
    return match.group(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()
    build = args.build_dir.resolve()

    status = read_status(build / "status.txt")
    version = pyproject_version()
    problems = []
    if status.get("stage") != "impl" or status.get("state") != "complete":
        problems.append(f"build is stage={status.get('stage')} state={status.get('state')}, need impl/complete")
    if status.get("timing") != "met":
        problems.append(f"timing={status.get('timing')} (wns={status.get('wns')}, whs={status.get('whs')})")
    if status.get("build_version") != version:
        problems.append(f"built for {status.get('build_version')}, pyproject.toml is {version}")
    problems += [f"missing {name}" for name in FILES if not (build / name).exists()]
    if problems:
        sys.exit(f"refusing to package {build}:\n  " + "\n  ".join(problems))

    DEST.mkdir(exist_ok=True)
    for name, packaged in FILES.items():
        shutil.copyfile(build / name, DEST / packaged)
    manifest = {
        "build_version": version,
        "source": str(build),
        "packaged_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "build_status": status,
        "sha256": {packaged: sha256(DEST / packaged) for packaged in FILES.values()},
    }
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"packaged rf-clock {version} overlay from {build} into {DEST}")


if __name__ == "__main__":
    main()
