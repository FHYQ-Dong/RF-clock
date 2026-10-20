"""Unpack a Teledyne LeCroy LabNotebook (.lnb) file.

Bench tool, not an experiment: the HDO9404-MS saves its records (traces, setup,
screenshot) as LabNotebook files, e.g. the runNNN.lnb of m2_restarts.py, and
this makes them readable without the scope software::

    uv run experiments/lnb.py results/MyLabNotebook--00002.lnb
    uv run experiments/lnb.py "results/*.lnb"

writes every member (waveforms Cn.trc/Fn.trc, Setup.lss, ScreenDump.png,
XPortRecord.xml) and a copy of the .lnb into results/MyLabNotebook--00002/.
Wildcards are expanded here, since PowerShell does not expand them for
native commands.  The file format is decoded by
``tanglab_instruments.hdo9404.parse_notebook`` (the ``instruments`` group);
``parse_waveform`` there reads the .trc members.
"""

from __future__ import annotations

import argparse
import glob
import shutil
from pathlib import Path

from tanglab_instruments.hdo9404 import parse_notebook


def unpack(lnb: Path) -> Path:
    out = lnb.with_suffix("")
    out.mkdir(exist_ok=True)
    for name, member in parse_notebook(lnb.read_bytes()).items():
        (out / Path(name).name).write_bytes(member)  # never write outside out/
        print(f"{name:20s} {len(member):10d} B")
    shutil.copy2(lnb, out / lnb.name)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("lnb", nargs="+", help="LabNotebook files or wildcard patterns")
    args = parser.parse_args()
    for pattern in args.lnb:
        paths = sorted(glob.glob(pattern))
        if not paths:
            parser.error(f"no file matches {pattern}")
        for path in paths:
            print(f"-> {unpack(Path(path))}")


if __name__ == "__main__":
    main()
