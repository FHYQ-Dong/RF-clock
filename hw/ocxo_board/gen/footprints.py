"""Write the board's own footprints and 3D model into hw/ocxo_board/ocxo_board.pretty and .3dshapes.

- OCXO_Taitien_NI-10M-3600, from the outline drawing on page 4 of the data sheet (XO-0238 Rev 3). The drawing
  is a bottom view, so it is mirrored here. Pin 1 is at the top left, next to the marking dot of the top view.
- Pmod_Header_2x06_P2.54mm_Horizontal, KiCad's right-angle 2x6 male header with its pads renumbered in Pmod
  order (pins 1 to 6 the upper row, 7 to 12 the lower row), for J8, where the Pmod cable from the RFSoC 4x2
  plugs in. The upper row is the column farther from the pin tips. Placed at 270 degrees, the tips point down
  over the board's bottom edge and pin 1 is at the left, as on a Pmod module plugged into a host.
- A simple box model (VRML) of the OCXO, so the 3D view shows it.

    python hw/ocxo_board/gen/footprints.py
"""

from __future__ import annotations

import copy
from pathlib import Path

from kisexp import KICAD, Q, dump, find, find_all, parse, uid

BOARD = Path(__file__).resolve().parent.parent
LIB = BOARD / "ocxo_board.pretty"
SHAPES = BOARD / "ocxo_board.3dshapes"
STOCK = KICAD / "share/kicad/footprints"


def _layer(*names: str) -> list:
    return ["layers", *(Q(n) for n in names)]


def line(x1, y1, x2, y2, layer, width) -> list:
    return ["fp_line", ["start", x1, y1], ["end", x2, y2], ["stroke", ["width", width], ["type", "solid"]],
            ["layer", Q(layer)], ["uuid", uid()]]


def rect(x1, y1, x2, y2, layer, width) -> list[list]:
    return [line(x1, y1, x2, y1, layer, width), line(x2, y1, x2, y2, layer, width),
            line(x2, y2, x1, y2, layer, width), line(x1, y2, x1, y1, layer, width)]


def text(kind, value, x, y, layer, hide=False) -> list:
    node = ["property", Q(kind), Q(value), ["at", x, y, 0], ["layer", Q(layer)]]
    if hide:
        node.append(["hide", "yes"])
    return node + [["uuid", uid()], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]]


def ocxo() -> list:
    """Taitien NI-10M-3600 series, 36.32 x 27.18 mm, pins 25.40 x 17.80 mm apart, 0.79 mm round."""
    w, h = 36.32 / 2, 27.18 / 2
    pins = {"1": (-12.7, -8.9), "2": (-12.7, 0.0), "3": (-12.7, 8.9), "5": (12.7, -8.9), "4": (12.7, 8.9)}
    fp = ["footprint", Q("OCXO_Taitien_NI-10M-3600"), ["version", 20260206], ["generator", Q("gen_footprints")],
          ["layer", Q("F.Cu")],
          ["descr", Q("Taitien NI-10M-3600 series double-oven OCXO, 36.32x27.18 mm DIP, data sheet XO-0238 Rev 3 p. 4")],
          ["tags", Q("OCXO Taitien")],
          text("Reference", "X**", -w, -h - 1.5, "F.SilkS"), text("Value", "NI-10M-3600", 0, h + 1.5, "F.Fab"),
          ["attr", "through_hole"]]
    fp += rect(-w, -h, w, h, "F.Fab", 0.1)
    fp += rect(-w - 0.15, -h - 0.15, w + 0.15, h + 0.15, "F.SilkS", 0.12)
    fp += rect(-w - 0.5, -h - 0.5, w + 0.5, h + 0.5, "F.CrtYd", 0.05)
    fp.append(["fp_circle", ["center", -w + 2.2, -h + 2.2], ["end", -w + 2.7, -h + 2.2],
               ["stroke", ["width", 0.12], ["type", "solid"]], ["fill", "yes"], ["layer", Q("F.SilkS")], ["uuid", uid()]])
    for num, (x, y) in pins.items():
        fp.append(["pad", Q(num), "thru_hole", "rect" if num == "1" else "circle", ["at", x, y], ["size", 1.9, 1.9],
                   ["drill", 1.0], _layer("*.Cu", "*.Mask"), ["remove_unused_layers", "no"], ["uuid", uid()]])
    fp.append(["model", Q("${KIPRJMOD}/ocxo_board.3dshapes/OCXO_Taitien_NI-10M-3600.wrl"),
               ["offset", ["xyz", 0, 0, 0]], ["scale", ["xyz", 1, 1, 1]], ["rotate", ["xyz", 0, 0, 0]]])
    fp.append(["embedded_fonts", "no"])
    return fp


PMOD_FROM_STOCK = {"11": "1", "9": "2", "7": "3", "5": "4", "3": "5", "1": "6",
                   "12": "7", "10": "8", "8": "9", "6": "10", "4": "11", "2": "12"}


def pmod_header() -> list:
    """KiCad's PinHeader_2x06_P2.54mm_Horizontal with Pmod numbering, for the 2x6 Pmod cable to the RFSoC 4x2.
    The odd stock pads (x = 0) are the column farther from the pin tips, the upper row, so PMOD_FROM_STOCK gives
    them Pmod 1 to 6 and the column nearer the tips Pmod 7 to 12."""
    stock = parse((STOCK / "Connector_PinHeader_2.54mm.pretty/PinHeader_2x06_P2.54mm_Horizontal.kicad_mod")
                  .read_text(encoding="utf-8"))
    fp = copy.deepcopy(stock)
    fp[1] = Q("Pmod_Header_2x06_P2.54mm_Horizontal")
    descr = find(fp, "descr")
    if descr is not None:
        descr[1] = Q("Pmod peripheral header, right-angle 2x6 male, 2.54 mm, Pmod numbering (1-6 upper row, 7-12 "
                     "lower row). KiCad PinHeader_2x06_P2.54mm_Horizontal with renumbered pads")
    for pad in find_all(fp, "pad"):
        new = PMOD_FROM_STOCK[pad[1]]
        pad[1] = Q(new)
        pad[3] = "rect" if new == "1" else "circle"
    return fp


def vrml_box(name: str, boxes: list[tuple]) -> str:
    """A VRML 2 file of coloured boxes. Each box is (x, y, z, sx, sy, sz, (r, g, b)) in mm, centre and size,
    x right, y away from the viewer (KiCad's y up), z up. KiCad reads VRML in units of 0.1 inch."""
    k = 1 / 2.54
    parts = []
    for x, y, z, sx, sy, sz, (r, g, b) in boxes:
        parts.append(f"""Transform {{ translation {x * k:.4f} {y * k:.4f} {z * k:.4f} children [ Shape {{
  appearance Appearance {{ material Material {{ diffuseColor {r} {g} {b} specularColor 0.2 0.2 0.2 shininess 0.3 }} }}
  geometry Box {{ size {sx * k:.4f} {sy * k:.4f} {sz * k:.4f} }} }} ] }}""")
    return f"#VRML V2.0 utf8\n# {name}, made by gen/footprints.py\n" + "\n".join(parts) + "\n"


def models() -> dict[str, str]:
    metal = (0.75, 0.75, 0.78)
    # OCXO body on four 0.3 mm glass standoffs, pins 6.6 mm long below the board's top surface
    ocxo = [(0, 0, 0.3 + 18.5 / 2, 36.32, 27.18, 18.5, metal)]
    ocxo += [(x, -y, -3.3, 0.79, 0.79, 6.6, metal) for x, y in
             ((-12.7, -8.9), (-12.7, 0), (-12.7, 8.9), (12.7, -8.9), (12.7, 8.9))]
    return {"OCXO_Taitien_NI-10M-3600": vrml_box("Taitien NI-10M-3600 OCXO", ocxo)}


def main() -> None:
    LIB.mkdir(exist_ok=True)
    SHAPES.mkdir(exist_ok=True)
    for fp in (ocxo(), pmod_header()):
        (LIB / f"{fp[1]}.kicad_mod").write_text(dump(fp) + "\n", encoding="utf-8", newline="\n")
        print(LIB / f"{fp[1]}.kicad_mod")
    for name, body in models().items():
        (SHAPES / f"{name}.wrl").write_text(body, encoding="utf-8", newline="\n")
    (BOARD / "fp-lib-table").write_text(
        '(fp_lib_table\n\t(version 7)\n\t(lib (name "ocxo_board") (type "KiCad") (uri "${KIPRJMOD}/ocxo_board.pretty") '
        '(options "") (descr "Footprints of the OCXO board"))\n)\n', encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
