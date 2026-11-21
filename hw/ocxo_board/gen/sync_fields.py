"""Copy each part's value and its MPN and LCSC fields from the schematic onto the board, without moving or
rerouting anything. This is for a change to parts.py, or to a value, that leaves every footprint as it is. Run it
with KiCad's Python after gen_sch.py, then check the board with KiCad's DRC and its schematic comparison.

    C:/Users/FHYQ-Dong/scoop/apps/kicad/current/bin/python.exe hw/ocxo_board/gen/sync_fields.py <board dir> <name>
"""

from __future__ import annotations

import sys
from pathlib import Path

import pcbnew

from pcblib import kicad_cli, read_netlist


def field(fp: pcbnew.FOOTPRINT, name: str) -> str:
    f = fp.GetField(name)
    return f.GetText() if f is not None else ""


def main() -> None:
    board_dir, name = Path(sys.argv[1]), sys.argv[2]
    net = board_dir / "build" / "net.xml"
    r = kicad_cli("sch", "export", "netlist", "--format", "kicadxml", "-o", str(net),
                  str(board_dir / f"{name}.kicad_sch"))
    if r.returncode:
        sys.exit(r.stderr)
    comps, _ = read_netlist(net)
    pcb = board_dir / f"{name}.kicad_pcb"
    board = pcbnew.LoadBoard(str(pcb))
    changed = []
    for fp in board.GetFootprints():
        c = comps.get(fp.GetReference())
        if c is None:
            continue
        before = (fp.GetValue(), field(fp, "MPN"), field(fp, "LCSC"))
        fp.SetValue(c["value"])
        for key in ("MPN", "LCSC"):
            fp.SetField(key, c["fields"].get(key, ""))
            fp.GetField(key).SetVisible(False)
        if before != (fp.GetValue(), field(fp, "MPN"), field(fp, "LCSC")):
            changed.append(fp.GetReference())
    pcbnew.SaveBoard(str(pcb), board)
    print(f"{len(changed)} parts changed:", ", ".join(sorted(changed)))


if __name__ == "__main__":
    main()
