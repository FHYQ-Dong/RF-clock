"""Check a release tag and print its GitHub release notes.

    python tools/release_notes.py vA.B.C.D > NOTES.md

Used by .github/workflows/release.yml; standard library only.  Refuses the
release (exit 1) unless the tag is ``v`` + the pyproject.toml version, the
packaged overlay files are real (not Git LFS pointers) and match
manifest.json, and the overlay was built for the same A.B.C
(docs/ABI.md 14.1).  The notes hold the tag message, the overlay build
record, the commits since the previous tag and the install steps.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "src" / "rf_clock" / "overlay"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def release(version: str) -> tuple[str, ...]:
    return tuple(version.split(".")[:3])


def check(tag: str) -> tuple[str, dict]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        sys.exit("pyproject.toml has no version")
    version = match.group(1)
    problems = []
    if tag != f"v{version}":
        problems.append(f"tag {tag} is not v + the pyproject.toml version {version}")
    manifest = json.loads((OVERLAY / "manifest.json").read_text(encoding="utf-8"))
    if release(manifest["build_version"]) != release(version):
        problems.append(f"overlay built for {manifest['build_version']}, package is {version} (A.B.C differ)")
    for name, expected in manifest["sha256"].items():
        data = (OVERLAY / name).read_bytes()
        if data.startswith(b"version https://git-lfs"):
            problems.append(f"{name} is a Git LFS pointer (checkout without lfs: true?)")
        elif hashlib.sha256(data).hexdigest() != expected:
            problems.append(f"{name} does not match manifest.json")
    if problems:
        sys.exit("refusing to release:\n  " + "\n  ".join(problems))
    return version, manifest


def notes(tag: str, version: str, manifest: dict) -> str:
    # Subject and body only: a signed tag also carries its signature.
    message = git("tag", "-l", "--format=%(contents:subject)%0a%0a%(contents:body)", tag)
    previous = subprocess.run(["git", "describe", "--tags", "--abbrev=0", f"{tag}^"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    span = f"{previous}..{tag}" if previous else tag
    log = git("log", "--format=- %h %s", span)
    status = manifest.get("build_status", {})
    wheel = f"rf_clock-{version}-py3-none-any.whl"
    lines = []
    if message:
        lines += [message, ""]
    lines += [
        "## Overlay",
        "",
        f"Bitstream built for rf-clock {manifest['build_version']} "
        f"({status.get('tool', '?')}, WNS {status.get('wns', '?')} ns, WHS {status.get('whs', '?')} ns, "
        f"packaged {manifest.get('packaged_utc', '?')}); it is inside the wheel.",
        "",
        "| file | sha256 |",
        "| --- | --- |",
        *[f"| `{name}` | `{digest}` |" for name, digest in manifest["sha256"].items()],
        "",
        f"## Changes since {previous}" if previous else "## Changes",
        "",
        log or "- (none)",
        "",
        "## Install on the RFSoC 4x2",
        "",
        "Copy the wheel to the board and install it into PYNQ's environment (docs/ABI.md 9.1):",
        "",
        "```bash",
        f"sudo /usr/local/share/pynq-venv/bin/python -m pip install --no-deps --no-index {wheel}",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    tag = sys.argv[1]
    version, manifest = check(tag)
    sys.stdout.write(notes(tag, version, manifest))


if __name__ == "__main__":
    main()
