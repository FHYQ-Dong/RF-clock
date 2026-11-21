"""Route a board several times and keep the best result.

Freerouting's single pass comes out differently from run to run, so the layout is generated ``tries`` times and
the run with the fewest DRC errors and unconnected items is kept (each unconnected item counts as ten errors).

    python hw/ocxo_board/gen/best_of.py <board directory> <name> [tries]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

KICAD_PY = Path("C:/Users/FHYQ-Dong/scoop/apps/kicad/current/bin/python.exe")


def score(drc: Path) -> tuple[int, int, int]:
    d = json.loads(drc.read_text(encoding="utf-8"))
    errors = sum(1 for v in d["violations"] if v["severity"] == "error")
    warnings = sum(1 for v in d["violations"] if v["severity"] != "error")
    return errors + 10 * len(d["unconnected_items"]) + 10 * len(d.get("schematic_parity", [])), errors, warnings


def main() -> None:
    board_dir, name = Path(sys.argv[1]), sys.argv[2]
    tries = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    pcb, drc = board_dir / f"{name}.kicad_pcb", board_dir / "build" / "drc.json"
    best = None
    for k in range(tries):
        r = subprocess.run([str(KICAD_PY), str(board_dir / "gen" / "gen_pcb.py")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        (board_dir / "build" / f"try_{k + 1}.log").write_text(r.stdout + r.stderr, encoding="utf-8", newline="\n")
        if r.returncode or not drc.exists():
            print(f"try {k + 1}: failed\n{r.stdout[-800:]}{r.stderr[-800:]}", flush=True)
            continue
        s = score(drc)
        print(f"try {k + 1}: score {s[0]} ({s[1]} errors, {s[2]} warnings)", flush=True)
        if best is None or s < best:
            best = s
            shutil.copy(pcb, board_dir / "build" / "best.kicad_pcb")
            shutil.copy(drc, board_dir / "build" / "best_drc.json")
        if s[0] == 0 and s[2] == 0:
            break
    if best is None:
        sys.exit("no run finished")
    shutil.copy(board_dir / "build" / "best.kicad_pcb", pcb)
    shutil.copy(board_dir / "build" / "best_drc.json", drc)
    print(f"kept score {best[0]} ({best[1]} errors, {best[2]} warnings)")


if __name__ == "__main__":
    main()
