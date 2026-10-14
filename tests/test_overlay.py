import hashlib
import json

import pytest

from rf_clock.pl.overlay import VersionMismatch, check_overlay, release


def write_overlay(directory, build_version="1.2.0.0"):
    files = {"rf_clock.bit": b"bitstream", "rf_clock.hwh": b"<hwh/>"}
    for name, data in files.items():
        (directory / name).write_bytes(data)
    manifest = {"build_version": build_version,
                "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory / "rf_clock.bit"


def test_release():
    assert release("1.2.0.0") == (1, 2, 0)
    assert release("1.10.3.4.dev1") == (1, 10, 3)
    with pytest.raises(ValueError):
        release("0.2.0")


def test_manifest_matches_python_only_releases(tmp_path):
    bit = write_overlay(tmp_path)
    manifest = check_overlay(bit, "1.2.0.5")
    assert manifest is not None and manifest["build_version"] == "1.2.0.0"
    with pytest.raises(VersionMismatch):
        check_overlay(bit, "1.2.1.0")


def test_no_manifest_is_not_checked(tmp_path):
    (tmp_path / "rf_clock.bit").write_bytes(b"bitstream")
    assert check_overlay(tmp_path / "rf_clock.bit", "1.2.0.0") is None


def test_lfs_pointer_or_foreign_hwh_is_refused(tmp_path):
    bit = write_overlay(tmp_path)
    bit.write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(VersionMismatch, match="git lfs pull"):
        check_overlay(bit, "1.2.0.0")
    bit = write_overlay(tmp_path)
    (tmp_path / "rf_clock.hwh").write_bytes(b"<other build/>")
    with pytest.raises(VersionMismatch):
        check_overlay(bit, "1.2.0.0")
