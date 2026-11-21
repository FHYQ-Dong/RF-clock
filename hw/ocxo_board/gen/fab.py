"""Turn a finished board into the files JLCPCB asks for, plus pictures for review.

    python hw/ocxo_board/gen/fab.py [board directory] [project name]

The defaults are hw/ocxo_board and ocxo_board. Into <board>/fab/:
- <name>_gerbers.zip, the copper, mask, paste, silkscreen and outline layers and the drill files
- <name>_bom_jlc.csv, the parts JLCPCB places (Comment, Designator, Footprint, LCSC Part #)
- <name>_cpl_jlc.csv, their positions (Designator, Mid X, Mid Y, Layer, Rotation), with the rotation turned for the
  parts whose JLCPCB models start at another angle than KiCad's footprints (JLC_ROTATION)
- <name>_hand.csv, the parts left for hand soldering (through-hole connectors, the OCXO, test loops, and parts
  JLCPCB does not stock, the two C0G capacitors of the tuning filter and the two edge-mount SMA connectors)
- <name>_layers.pdf, every copper layer with the outline, for a reviewer
- render_top.png, render_bottom.png and render_iso.png
The LCSC numbers come from the schematic's LCSC fields (parts.py fills them in gen_sch.py).
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

KICAD_CLI = Path("C:/Users/FHYQ-Dong/scoop/apps/kicad/current/bin/kicad-cli.exe")
# soldered by hand, the through-hole parts without an LCSC number (the Pmod header J8, the OCXO X1 and the
# ground loops TP8 and TP9) and the parts JLCPCB does not stock, the C0G capacitors C21 and C22 and the edge-mount
# SMA connectors J6 and J7 (JLCPCB solders the DC jack J1, which it stocks)
HAND = ("J6", "J7", "J8", "X1", "TP8", "TP9", "C21", "C22")
NO_PART = ("TP", "H")  # bare pads and holes, nothing to buy
# JLCPCB draws some parts' models at another zero angle than KiCad's footprints, so their rotation in the placement
# file is KiCad's plus this (degrees, counterclockwise as KiCad counts), by LCSC number. Each offset was found in
# JLCPCB's placement preview on 2026-11-20, where the part's leads and its pin 1 (or a diode's cathode) must lie on
# the footprint's pads and pin 1. The offset belongs to JLCPCB's model of that part, not to the package: the QFN
# C524802 needs none, the SOT-23-5 C2862740 180 and the SOT-23-6 C468250 270, so a new part is checked the same way.
JLC_ROTATION = {
    **dict.fromkeys(("C468250", "C130904", "C426709", "C514422", "C461518", "C670482", "C689432", "C206590",
                     "C207408", "C578299", "C571196"), 270),
    **dict.fromkeys(("C2862740", "C40919", "C2986030"), 180),
}


def cli(*args: str) -> None:
    r = subprocess.run([str(KICAD_CLI), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit(f"kicad-cli {' '.join(args[:3])} failed:\n{r.stdout}\n{r.stderr}")


def jlc_rotation(lcsc: str, rot: float) -> float:
    return (rot + JLC_ROTATION.get(lcsc, 0)) % 360


def components(net_xml: Path) -> dict[str, dict]:
    out = {}
    for c in ET.parse(net_xml).getroot().find("components"):
        fields = {f.get("name"): (f.text or "") for f in c.iter("field")}
        out[c.get("ref")] = {"value": c.findtext("value"), "footprint": c.findtext("footprint").split(":")[1],
                             "lcsc": fields.get("LCSC", ""), "mpn": fields.get("MPN", "")}
    return out


def main() -> None:
    board_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    name = sys.argv[2] if len(sys.argv) > 2 else board_dir.name
    pcb, sch = board_dir / f"{name}.kicad_pcb", board_dir / f"{name}.kicad_sch"
    build, fab = board_dir / "build", board_dir / "fab"
    gerb = build / "gerbers"
    shutil.rmtree(gerb, ignore_errors=True)
    gerb.mkdir(parents=True)
    fab.mkdir(exist_ok=True)

    layers = "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"
    cli("pcb", "export", "gerbers", "-o", str(gerb), "--layers", layers, "--subtract-soldermask", "--check-zones",
        str(pcb))
    cli("pcb", "export", "drill", "-o", str(gerb), "--format", "excellon", "--excellon-separate-th",
        "--generate-map", "--map-format", "gerberx2", str(pcb))
    zpath = fab / f"{name}_gerbers.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gerb.iterdir()):
            z.write(f, f.name)

    cli("sch", "export", "netlist", "--format", "kicadxml", "-o", str(build / "net.xml"), str(sch))
    comps = components(build / "net.xml")
    cli("pcb", "export", "pos", "-o", str(build / "pos.csv"), "--format", "csv", "--units", "mm", "--side", "both",
        str(pcb))
    pos = {r["Ref"]: r for r in csv.DictReader(open(build / "pos.csv", encoding="utf-8"))}

    groups: dict[str | tuple, list[str]] = defaultdict(list)
    hand = []
    for ref, c in sorted(comps.items(), key=lambda kv: (kv[0].rstrip("0123456789"), int(kv[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ") or 0))):
        if ref.startswith(NO_PART) and ref not in HAND:
            continue
        if ref in HAND:
            hand.append([ref, c["value"], c["footprint"], c["mpn"], c["lcsc"]])
            continue
        # one BOM line per LCSC part, so that JLCPCB does not ask to confirm several lines matched to one part
        groups[c["lcsc"] or (c["value"], c["footprint"], c["mpn"])].append(ref)
    with open(fab / f"{name}_bom_jlc.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #", "Manufacturer Part Number"])
        for refs in groups.values():
            c = comps[refs[0]]
            values = list(dict.fromkeys(comps[r]["value"] for r in refs))
            w.writerow([" / ".join(values), ",".join(refs), c["footprint"], c["lcsc"], c["mpn"]])
    with open(fab / f"{name}_cpl_jlc.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for refs in groups.values():
            for ref in refs:
                p = pos[ref]
                w.writerow([ref, f"{float(p['PosX']):.3f}mm", f"{float(p['PosY']):.3f}mm",
                            "Top" if p["Side"] == "top" else "Bottom",
                            f"{jlc_rotation(comps[ref]['lcsc'], float(p['Rot'])):.0f}"])
    with open(fab / f"{name}_hand.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Value", "Footprint", "MPN", "LCSC Part #"])
        w.writerows(hand)

    shutil.rmtree(fab / f"{name}_layers.pdf", ignore_errors=True)
    cli("pcb", "export", "pdf", "-o", str(fab / f"{name}_layers.pdf"), "--layers",
        "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Silkscreen", "--common-layers", "Edge.Cuts", "--mode-multipage", str(pcb))
    for side, extra in (("top", []), ("bottom", []), ("iso", ["--rotate", "-40,0,30", "--perspective"])):
        cli("pcb", "render", "-o", str(fab / f"render_{side}.png"), "--side", "top" if side == "iso" else side,
            "--quality", "high", "--width", "1800", "--height", "1300", *extra, str(pcb))
    print(f"written to {fab}")


if __name__ == "__main__":
    main()
