from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from rf_clock.pl.abi import ABI


ROOT = Path(__file__).resolve().parents[1]


def test_generated_abi_matches_rtl_sources():
    result = subprocess.run(
        [sys.executable, "tools/generate_abi.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ABI.REG.ID == 0x00
    assert ABI.CTRL.TX_DOORBELL == 0
    assert ABI.STAT.RX_STATE_LSB == 12


def test_package_version_starts_with_register_map_version():
    # pyproject.toml version A.B.C.D: A.B is VERSION (docs/ABI.md 14.1).
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None
    version = match.group(1)
    a, b = (int(field) for field in version.split(".")[:2])
    assert (a, b) == (ABI.VERSION >> 16, ABI.VERSION & 0xFFFF), version
