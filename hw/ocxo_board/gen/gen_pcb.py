"""Generate and route the integrated OCXO board's layout (plan 2), hw/ocxo_board/ocxo_board.kicad_pcb.

The 12 V input and the buck converter are along the top edge, the OCXO in the middle, the DAC section's +-8 V
converter on the left, its +-7.5 V regulators along the bottom edge, and the reference, the DAC and the output
buffer on the right, next to the OCXO's tuning pin. The 10 MHz outputs are on the left edge and the SPI input
from the RFSoC 4x2 at the bottom right. Run with KiCad's Python after gen/gen_sch.py, with freerouting-cli on
PATH::

    C:/Users/FHYQ-Dong/scoop/apps/kicad/current/bin/python.exe hw/ocxo_board/gen/gen_pcb.py

Four layers. F.Cu and B.Cu carry the tracks, In1.Cu and In2.Cu are ground planes, and every SMD ground pad gets
its own via to them. The SPI input from the RFSoC 4x2 (J8, U13's side 1 and their capacitors) sits on an island
of the 4x2's ground (/ISO_GND) at the bottom right, on all four layers, 2 mm away from the board's ground, which
the ISO7741 U13 bridges. The parts sit in the places listed in PLACE (mm from the board's top left corner,
degrees). Freerouting routes everything except ground. The finished board is checked with KiCad's DRC
(build/drc.json), and fab.py turns it into the JLCPCB files.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

import maze
from maze import connect, route as maze_route
from pcblib import (Board, Obstacles, _seg_rect_dist, free_spot, freeroute, kicad_cli, prepare_dsn,
                    project_settings, pt, read_netlist, remove_dangling_vias, ring, write_project, xy)

BOARD = Path(__file__).resolve().parent.parent
NAME = "ocxo_board"
BUILD = BOARD / "build"
W, H = 115.0, 100.0
LIBS = BOARD  # where the board's own footprint library ocxo_board.pretty is


def grid(refs: list[str], x0: float, y0: float, cols: int, dx: float, dy: float, rot: int = 0) -> dict:
    """Small parts in rows of ``cols``, starting at (x0, y0)."""
    return {r: (x0 + dx * (k % cols), y0 + dy * (k // cols), rot) for k, r in enumerate(refs)}

PLACE = {
    # 12 V input, top left
    "J1": (13.5, 12, 0), "F1": (21, 7, 0), "D2": (27, 10.5, 270), "D1": (33.5, 7, 180), "C1": (38.3, 9.5, 270),
    "TP1": (38.3, 4.0, 0),
    # buck converter, whose high-current paths are drawn by buck_routes()
    "U1": (45, 13, 180), "C2": (49.4, 11.0, 0), "C3": (48.7, 8.9, 0), "C4": (45.0, 16.6, 0),
    "R1": (41.45, 10.4, 0), "R2": (41.45, 12.05, 180), "R3": (41.45, 13.9, 0), "L1": (51.0, 15.0, 0),
    "C5": (56.5, 16.475, 270), "C6": (59.5, 16.475, 270), "FB1": (63.0, 15.0, 0), "C7": (67.0, 16.475, 270),
    "TP2": (68.5, 12.0, 0),
    # the OCXO's 5 V
    "U2": (56, 31, 0), "C11": (53.3, 25.0, 90), "C8": (58.7, 25.3, 90), "C12": (50.3, 25.0, 90),
    "C13": (48.0, 25.0, 90), "R4": (51.0, 30.0, 0), "C10": (51.0, 28.4, 0), "R5": (51.0, 31.8, 180),
    "R6": (51.0, 34.0, 0), "R7": (45.5, 30.0, 90), "D3": (45.5, 33.5, 90), "C9": (60.5, 31.5, 0), "TP7": (53, 37.5, 0),
    "TP3": (44.5, 26.0, 0),
    # OCXO and its supply capacitors
    "X1": (60, 60, 180), "C23": (70, 43.8, 0), "C24": (74, 43.8, 0),
    # tuning voltage filter, under the OCXO, ending at its tuning pin
    "R8": (86, 78, 180), "C21": (82, 78.5, 270), "C22": (79, 78.5, 270), "D4": (75.5, 78.5, 0), "TP6": (71.3, 78.2, 0),
    # 3.3 V for the DAC section, top right
    "U3": (106, 14, 0), "C14": (102.5, 21, 0), "C15": (107.5, 21, 0), "C16": (102.5, 24.5, 0), "R9": (102.5, 28, 0),
    "D5": (107.0, 28, 180), "TP4": (110.5, 32, 0),
    # SPI from the RFSoC 4x2: the Pmod header J8 on the bottom edge (pin tips over the edge, pin 1 on the left),
    # the isolator U13 above it with side 1 (pins 1 to 8) facing J8 and side 2 facing the AD5791, the cable's
    # supply capacitors C73 and C74 and R10 on the island, and C75 at U13's VCC2
    "J8": (106.0, 100 - 6.58, 270), "U13": (98.5, 82.5, 90), "C73": (92.3, 87.0, 90), "C74": (105.5, 90.3, 180),
    "R10": (100.9, 90.0, 270), "C75": (92.3, 78.5, 270),
    # 10 MHz buffer and the two SMA outputs on the left edge
    "R17": (38.5, 60.5, 0), "U5": (32.5, 63, 180), "R18": (24, 58, 0), "C25": (19.5, 58, 0), "J6": (2.84, 58, 180),
    "R19": (24, 70, 0), "C26": (19.5, 70, 0), "J7": (2.84, 70, 180), "U4": (30, 53, 0), "C17": (26, 53, 90),
    "C18": (34, 52.5, 90), "C19": (36.5, 52.5, 90), "C27": (35.5, 66.0, 0), "TP5": (26.5, 48.5, 0),
    # +-8 V for the DAC section (LT3471), left middle, away from the DAC. U6 is turned so that its switch, soft
    # start and input pins face the inductors and the soft start parts on the left (SW2 top, SW1 bottom) and its
    # feedback pins face the dividers on the right. The inverter is above it and the boost below, towards U7.
    "U6": (24, 31, 180), "L3": (10, 25, 0), "C46": (16.5, 22.5, 0), "D7": (20.5, 23, 90), "L4": (26, 23, 0),
    "C47": (32.5, 22, 90), "C48": (5, 33, 90), "L2": (10, 39, 0), "D6": (17, 41, 180), "C42": (25, 41.5, 0),
    **grid(["R31", "C41", "R30", "C40"], 15.0, 28.5, 2, 3.4, 4.2, 90),
    **grid(["R34", "R35", "C45", "C44", "R32", "C43", "R33"], 30.0, 27.0, 4, 3.0, 5.0, 90),
    # +7.5 V (LT3042) and -7.5 V (LT3093), along the bottom edge
    # U7's parts in the order of its pins, so that no track has to cross another. The SET resistor and capacitor
    # are right of the SET pin, R38 below them joins the output to PGFB, R39 is under it, PG and ILIM are left.
    "U7": (14, 86, 0), "R36": (8.8, 87.4, 90), "R37": (10.9, 89.6, 270), "R40": (18.9, 86.5, 0),
    "C49": (19.8, 88.9, 0), "R38": (19.5, 91.8, 180), "R39": (17.2, 93.6, 270), "C50": (19.8, 83.6, 180),
    "U8": (42, 86, 0), **grid(["R41", "R42", "R43", "R44", "R45", "C51", "C52"], 32, 92, 7, 3.6, 0, 90),
    # 5 V reference and its buffers, the DAC and the output buffer, right of the OCXO
    # The output buffer U12 sits between the reference buffers U10 and the DAC U11, next to the DAC's INV, VOUT
    # and RFB pins at the top of U11, so that its input and feedback tracks stay short.
    "U9": (87, 46, 0), "R46": (82.5, 43.5, 90), "C53": (87, 51.5, 0), "C54": (87, 41, 0),
    "U10": (97, 46, 0), **grid(["C55", "C56", "C57", "C58", "C59"], 104, 40, 1, 0, 3.0, 0),
    "U12": (97, 56.5, 0), "C67": (97, 51.8, 0), "C69": (102.0, 55.0, 90), "C72": (102.0, 58.1, 90),
    "C70": (92.0, 58.0, 90), "C68": (105.5, 55.0, 0), "C71": (105.5, 58.0, 0),
    "U11": (97, 66, 0), **grid(["C60", "C61", "C62", "C63"], 105, 61, 1, 0, 2.8, 0),
    **grid(["C64", "C65", "C66", "D8"], 88, 61, 1, 0, 2.8, 0),
    **grid(["R24", "R25", "R26", "R27"], 87.8, 73.5, 4, 2.4, 0, 90),
    **grid(["TP10", "TP11", "TP12", "TP13"], 111, 45, 1, 0, 6.0, 0),
    # ground loops, mounting holes
    "TP8": (60, 92, 0), "TP9": (66.5, 41.5, 0), "H1": (3.5, 3.5, 0), "H2": (W - 3.5, 3.5, 0), "H3": (3.5, H - 3.5, 0),
    "H4": (W - 3.5, H - 3.5, 0),
}

NETCLASSES = {
    "Default": {"track": 0.25, "clearance": 0.2},
    "Power": {"track": 0.8, "clearance": 0.2, "priority": 1},
    "Rail": {"track": 0.4, "clearance": 0.2, "priority": 2},
    "Clock": {"track": 0.3, "clearance": 0.2, "priority": 3},
}
# +12V, /+5V6 and /BUCK_SW carry their current on buck_routes()'s fixed tracks, and what is left of them is thin
PATTERNS = [(n, "Power") for n in ("+5V6_F", "+5V_OCXO", "Net-(F1-Pad1)", "Net-(D1-A)")]
PATTERNS += [(n, "Rail") for n in ("+3V3_DAC", "+3V3_CLK", "+7V5", "/-7V5", "+8V", "/-8V", "/SW1", "/SW2", "/NX",
                                   "/ISO_3V3")]
PATTERNS += [(n, "Clock") for n in ("Net-(X1-OUT)", "Net-(R17-Pad2)", "Net-(R18-Pad1)", "Net-(R19-Pad1)",
                                    "Net-(C25-Pad1)", "Net-(C26-Pad1)", "Net-(J6-In)", "Net-(J7-In)")]


# The island of the 4x2's ground under J8 and U13's side 1, and the 2 mm gap around it that neither ground's copper
# enters. The gap crosses under U13's body, between its two rows of pins.
ISLAND = (90.6, 83.5, 108.6, H)
GAP = 2.0
NOTCH = (ISLAND[0] - GAP, ISLAND[1] - GAP, ISLAND[2] + GAP, H)


def isolated(net: str) -> bool:
    """The nets on the 4x2's side of U13."""
    return net.startswith(("/PMOD_", "/ISO_"))


def keep_out(net: str) -> list[tuple[float, float, float, float]]:
    """Where a net's copper may not go: the island and its gap for the board's nets, everything outside the
    island for the 4x2's."""
    if isolated(net):
        x0, y0, x1, y1 = ISLAND
        return [(0, 0, W, y0), (0, y0, x0, H), (x1, y0, W, H)]
    return [NOTCH]


maze.KEEP_OUT = keep_out

# A copper-free circle around each M3 mounting hole on F.Cu and B.Cu, so that a metal screw head, washer or standoff
# (up to 7 mm across) touches bare board and cannot join the board's ground to a metal frame. The inner planes stop
# 0.25 mm from the hole wall, which the screw's shank does not reach.
HOLES = [PLACE[h][:2] for h in ("H1", "H2", "H3", "H4")]
HOLE_CLEAR_R = 3.6


def hole_keepouts(board: pcbnew.BOARD) -> None:
    """Rule areas that keep zone fills, tracks and vias out of the circle around each mounting hole."""
    layers = pcbnew.LSET()
    layers.AddLayer(pcbnew.F_Cu)
    layers.AddLayer(pcbnew.B_Cu)
    for k, (x, y) in enumerate(HOLES):
        z = pcbnew.ZONE(board)
        z.SetIsRuleArea(True)
        z.SetLayerSet(layers)
        z.SetDoNotAllowZoneFills(True)
        z.SetDoNotAllowTracks(True)
        z.SetDoNotAllowVias(True)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        ol = z.Outline()
        ol.NewOutline()
        for i in range(48):
            a = 2 * math.pi * i / 48
            q = pt(x + HOLE_CLEAR_R * math.cos(a), y + HOLE_CLEAR_R * math.sin(a))
            ol.Append(q.x, q.y)
        z.SetZoneName(f"no copper around H{k + 1}")
        board.Add(z)


def obstacles(board: pcbnew.BOARD, net: str) -> Obstacles:
    obs = Obstacles(board, board.FindNet(net).GetNetCode())
    obs.rects += keep_out(net)
    return obs


def via_spot(obs: Obstacles, x: float, y: float, r0: float) -> tuple[float, float, float] | None:
    """Where a ground pad's via goes, (dx, dy, stub width): a 0.4 mm stub if it fits, else a 0.25 mm one, which
    passes between pins at 0.65 mm pitch."""
    for hw, clear in ((0.2, 0.25), (0.125, 0.2)):
        spot = free_spot(obs, x, y, ring(r0, r0 + 3.0), stub_hw=hw, clear=clear)
        if spot is not None:
            return (*spot, 2 * hw)
    return None


def u2_stubs(bd: Board) -> None:
    """Short fixed tracks that join the TPS7A8400A's three OUT pins and three IN pins (with EN) outside the
    package, each group to its capacitor, so the wide power tracks never have to reach the 0.5 mm pins."""
    def outer(num):
        x, y = bd.pad_xy("U2", num)
        cx, cy = xy(bd.fps["U2"].GetPosition())
        if abs(x - cx) > abs(y - cy):
            return (x + (0.44 if x > cx else -0.44), y)
        return (x, y + (0.44 if y > cy else -0.44))

    o1, o19, o20 = outer("1"), outer("19"), outer("20")
    yj = o19[1] - 0.67
    xl = o1[0] - 0.57
    for p in (o19, o20):
        bd.track([p, (p[0], yj)], 0.3, "+5V_OCXO")
    bd.track([o1, (xl, o1[1]), (xl, yj)], 0.3, "+5V_OCXO")
    bd.track([(xl, yj), (o19[0], yj)], 0.5, "+5V_OCXO")
    bd.track([(xl, yj), bd.pad_xy("C11", "1")], 0.8, "+5V_OCXO")
    i15, i14, i16, i17 = outer("15"), outer("14"), outer("16"), outer("17")
    xr = i15[0] + 0.57
    for p in (i16, i17):
        bd.track([p, (p[0], yj)], 0.3, "+5V6_F")
    bd.track([i15, (xr, i15[1])], 0.3, "+5V6_F")
    bd.track([i14, (xr, i14[1]), (xr, yj)], 0.3, "+5V6_F")
    bd.track([(i17[0], yj), (xr, yj)], 0.5, "+5V6_F")
    bd.track([(xr, yj), bd.pad_xy("C8", "1")], 0.8, "+5V6_F")


def buck_routes(bd: Board, vias: bool = True) -> None:
    """The buck converter's current paths as short fixed tracks, so that the router cannot loop them around the
    chip: the 12 V bus from D1 past C1 and C3 to C2 and the VIN pin, the switch node to L1, the 5.6 V bus past
    C5 and C6 to FB1 and on to C7, the boot capacitor, the feedback divider and EN, and R1's sense line on B.Cu
    from the 5.6 V bus."""
    P = bd.pad_xy
    d1k, c1, c3, c2 = P("D1", "1"), P("C1", "1"), P("C3", "1"), P("C2", "1")
    vin, sw, vbst, en, vfb = P("U1", "3"), P("U1", "2"), P("U1", "6"), P("U1", "5"), P("U1", "4")
    yb, xr3 = d1k[1], 39.7
    bd.track([d1k, (c1[0], yb), (xr3, yb), (c3[0], yb), c3, c2], 0.8, "+12V")
    bd.track([c2, (vin[0] + 0.76, c2[1]), vin], 0.5, "+12V")  # narrower next to the switch pin
    bd.track([(c1[0], yb), c1], 0.8, "+12V")
    l1a, l1b = P("L1", "1"), P("L1", "2")
    bd.track([sw, (sw[0] + 1.46, sw[1]), (l1a[0], sw[1] + 1.15), l1a], 0.8, "/BUCK_SW")
    c5, c6, fb1a, fb1b, c7 = P("C5", "1"), P("C6", "1"), P("FB1", "1"), P("FB1", "2"), P("C7", "1")
    xs = 55.0
    bd.track([l1b, (xs, l1b[1]), c5, c6, fb1a], 0.8, "/+5V6")
    bd.track([fb1b, c7], 0.8, "+5V6_F")
    c4a, c4b = P("C4", "1"), P("C4", "2")
    bd.track([vbst, (vbst[0], c4a[1] - 0.6), c4a], 0.4, "/BUCK_BST")
    bd.track([c4b, (l1a[0] - 1.25, c4b[1]), (l1a[0], c4b[1] - 1.25), l1a], 0.4, "/BUCK_SW")
    r1a, r1b, r2a, r3a, r3b = P("R1", "1"), P("R1", "2"), P("R2", "1"), P("R3", "1"), P("R3", "2")
    bd.track([r1b, r2a, vfb], 0.25, "/BUCK_FB")
    bd.track([r3b, (r3b[0], en[1]), en], 0.25, "/BUCK_EN")
    bd.track([r3a, (xr3, r3a[1]), (xr3, yb)], 0.4, "+12V")
    vs = (r1a[0], r1a[1] - 1.2)  # sense via above R1, then B.Cu to a via on the 5.6 V bus
    bd.track([r1a, vs], 0.25, "/+5V6")
    bd.track([vs, (52.0, vs[1]), (xs, vs[1] + 3.0), (xs, l1b[1])], 0.25, "/+5V6", layer="B.Cu")
    if vias:
        bd.via(*vs, "/+5V6")
        bd.via(xs, l1b[1], "/+5V6")


def vref_routes(bd: Board, vias: bool = True) -> None:
    """The AD5791's reference force and sense lines from the AD8676 above it, as fixed tracks on B.Cu between
    vias next to the pins. On F.Cu the four lines would wall in the AD5791's pins above its reference pins, which
    go to the output buffer, and the router left some of them unconnected. Under the two ground planes the lines
    cross the other tracks without coupling to them."""
    for side, (f10, s10, f11, s11) in ((-1, ("1", "2", "4", "3")), (1, ("7", "6", "16", "17"))):
        (xf10, yf10), (xs10, ys10) = bd.pad_xy("U10", f10), bd.pad_xy("U10", s10)
        (xf11, yf11), (xs11, ys11) = bd.pad_xy("U11", f11), bd.pad_xy("U11", s11)
        edge = xf11 + side * pcbnew.ToMM(bd.pad("U11", f11).GetBoundingBox().GetWidth()) / 2
        xs, xf, ym = edge + side * 0.9, edge + side * 1.9, (ys11 + yf11) / 2
        net_f, net_s = bd.pad("U11", f11).GetNetname(), bd.pad("U11", s11).GetNetname()
        bd.track([(xf10, yf10), (xs, yf10)], 0.25, net_f)
        bd.track([(xs10, ys10), (xs, ys10)], 0.25, net_s)
        bd.track([(xs11, ys11), (xs, ys11)], 0.25, net_s)
        bd.track([(xf11, yf11), (xs + side * 0.5, yf11), (xf, ym)], 0.25, net_f)
        bd.track([(xs, ys10), (xs, ys11)], 0.25, net_s, layer="B.Cu")
        bd.track([(xs, yf10), (xf, yf10), (xf, ym)], 0.25, net_f, layer="B.Cu")
        if vias:
            for x, y, net in ((xs, yf10, net_f), (xs, ys10, net_s), (xs, ys11, net_s), (xf, ym, net_f)):
                bd.via(x, y, net)


EP_STUBS = {("U6", "5"), ("U6", "11"), ("U7", "8"), ("U7", "11")}


def ep_ground_stubs(bd: Board) -> None:
    """Ground pins of the DAC supplies' chips straight into their grounded exposed pad (U6 FB2N, U7 GND), and two
    vias in each of those pads."""
    for ref, pins, ep in (("U6", ("5",), "11"), ("U7", ("8",), "11")):
        ex, ey = bd.pad_xy(ref, ep)
        bb = bd.pad(ref, ep).GetBoundingBox()
        long_y = bb.GetHeight() > bb.GetWidth()
        for d in (-0.55, 0.55):
            bd.via(ex + (0 if long_y else d), ey + (d if long_y else 0), "GND", size=0.5, drill=0.2)
        bb = bd.pad(ref, ep).GetBoundingBox()
        x0, y0 = pcbnew.ToMM(bb.GetLeft()) - 100, pcbnew.ToMM(bb.GetTop()) - 100
        x1, y1 = pcbnew.ToMM(bb.GetRight()) - 100, pcbnew.ToMM(bb.GetBottom()) - 100
        for num in pins:
            x, y = bd.pad_xy(ref, num)
            tx, ty = min(max(x, x0 + 0.2), x1 - 0.2), min(max(y, y0 + 0.2), y1 - 0.2)
            bd.track([(x, y), (tx, ty)], 0.25, "GND", locked=False)


def u2_ground_stubs(bd: Board) -> None:
    """Pins 8 (GND), 18 (GND) and 12 (BIAS, grounded) straight into the thermal pad."""
    cx, cy = xy(bd.fps["U2"].GetPosition())
    for num in ("8", "18", "12"):
        x, y = bd.pad_xy("U2", num)
        tx, ty = (cx + (0.8 if x > cx else -0.8), y) if abs(x - cx) > abs(y - cy) else (x, cy + (0.8 if y > cy else -0.8))
        bd.track([(x, y), (tx, ty)], 0.25, "GND", locked=False)


def silkscreen(bd: Board) -> None:
    t = bd.text
    t("OCXO board, plan 2, v0.1", 74, 97.3, 1.2)
    t("RF-clock node B, 2026-11", 74, 95.4, 0.9)
    t("12V 2A", 7, 20.0, 1.0)
    # J8's pin 1 and pin 7 (the cable's pin 1 goes to pin 1), and what it joins
    t("1", 91.0, 100 - 6.58, 0.8)
    t("7", 91.0, 100 - 6.58 + 2.54, 0.8)
    t("TO 4x2", 111.8, 86.3, 0.8)
    t("PMOD0", 111.8, 87.6, 0.8)
    t("ISOLATED", 111.8, 89.6, 0.8)
    t("4x2 CLK IN", 9.5, 53.0, 0.9)
    t("50R ONLY", 9.5, 75.5, 0.9)
    for ref, label in (("TP1", "12V"), ("TP2", "5V6"), ("TP3", "5V0"), ("TP4", "3V3D"), ("TP5", "3V3C"),
                       ("TP6", "VCON"), ("TP7", "PG"), ("TP8", "GND"), ("TP9", "GND"), ("TP10", "+7V5"),
                       ("TP11", "-7V5"), ("TP12", "REF"), ("TP13", "DAC")):
        x, y = xy(bd.fps[ref].GetPosition())
        bd.fps[ref].Reference().SetVisible(False)  # the label takes the reference's place above the pad
        t(label, x, y - 2.0, 0.8)
    t("5V", 43.0, 33.5, 0.8, justify="right")
    t("3V3", 107.0, 30.2, 0.8)


def build(comps: dict, nets: dict) -> Board:
    bd = Board(W, H)
    bd.outline()
    bd.place(comps, nets, PLACE, LIBS)
    for fp in bd.fps.values():
        fp.SetLocked(True)
    for pad in bd.fps["U2"].Pads():  # the thermal pad and its vias join the pours solidly
        pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    u2_stubs(bd)
    buck_routes(bd)
    vref_routes(bd)
    silkscreen(bd)
    return bd


def rip_up_for_ground(board: pcbnew.BOARD, bd: Board, ref_pad: str) -> bool:
    """A ground pad still boxed in by other nets' tracks: take those nets' tracks up, give the pad its via, and
    route the nets again with the grid router (which now goes around the via)."""
    ref, num = ref_pad.split(".")
    pad = bd.pad(ref, num)
    code = pad.GetNetCode()
    x, y = xy(pad.GetPosition())
    near = set()
    for t in board.GetTracks():
        if t.GetNetCode() in (0, code) or t.IsLocked():
            continue
        a, b = xy(t.GetStart()), xy(t.GetEnd())
        if min(math.hypot(a[0] - x, a[1] - y), math.hypot(b[0] - x, b[1] - y), _point_seg(x, y, a, b)) < 1.6:
            near.add(t.GetNetname())
    for t in list(board.GetTracks()):
        if t.GetNetname() in near and not t.IsLocked():
            board.Delete(t)
    obs = obstacles(board, "GND")
    bb = pad.GetBoundingBox()
    r0 = max(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())) / 2 + 0.4
    spot = via_spot(obs, x, y, r0)
    ok = spot is not None
    if ok:
        bd.track([(x, y), (x + spot[0], y + spot[1])], spot[2], "GND", locked=False)
        bd.via(x + spot[0], y + spot[1], "GND")
    ns = board.GetDesignSettings().m_NetSettings
    for name in sorted(near):
        nc = ns.GetEffectiveNetClass(name)
        width = pcbnew.ToMM(nc.GetTrackWidth())
        if maze_route(board, name, 0.25 if width < 0.5 else width, pcbnew.ToMM(nc.GetClearance()) + 0.02, W, H):
            print(f"grid router could not route {name} again after making room at {ref_pad}")
            ok = False
    print(f"made room for a ground via at {ref_pad} by routing {', '.join(sorted(near))} again" if ok else "")
    return ok


def _point_seg(px, py, a, b) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - a[0]) * vx + (py - a[1]) * vy) / L))
    return math.hypot(px - a[0] - t * vx, py - a[1] - t * vy)


def pad_vias(board: pcbnew.BOARD, bd: Board, net: str) -> list[str]:
    """A via next to every SMD pad of a ground net, on that ground's side of the gap. Returns the pads that found
    no room for one."""
    code = board.FindNet(net).GetNetCode()
    obs = obstacles(board, net)
    skipped = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() != code or pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            if fp.GetReference() == "U2" or (fp.GetReference(), pad.GetNumber()) in EP_STUBS:
                continue  # these go to a thermal pad, which has its own vias
            x, y = xy(pad.GetPosition())
            bb = pad.GetBoundingBox()
            r0 = max(pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())) / 2 + 0.4
            spot = via_spot(obs, x, y, r0)
            if spot is not None:
                vx, vy = x + spot[0], y + spot[1]
                bd.track([(x, y), (vx, vy)], spot[2], net, locked=False)
                bd.via(vx, vy, net)
                obs.add_via(vx, vy)
                continue
            # boxed in, so a via a little farther away, reached by a grid path
            spot = free_spot(obs, x, y, ring(r0 + 0.5, r0 + 6.0), stub_hw=None, clear=0.3)
            if spot is not None:
                vx, vy = x + spot[0], y + spot[1]
                bd.via(vx, vy, net)
                if connect(board, net, 0.3, 0.22, (pcbnew.F_Cu, x, y), (pcbnew.F_Cu, vx, vy), W, H):
                    obs = obstacles(board, net)
                    continue
                for t in list(board.GetTracks()):  # the via again (the track list is not in the order of adding)
                    if t.Type() == pcbnew.PCB_VIA_T and math.dist(xy(t.GetPosition()), (vx, vy)) < 1e-3:
                        board.Delete(t)
            skipped.append(f"{fp.GetReference()}.{pad.GetNumber()}")
    return skipped


def ground(board: pcbnew.BOARD) -> list[str]:
    """A via next to every SMD ground pad, ground planes on In1 and In2, ground pours on F.Cu and B.Cu, and
    stitching vias between them, for the board's ground and for the 4x2's on the island. Returns the pads that
    found no room for a via."""
    bd = Board.__new__(Board)
    bd.b, bd.fps = board, {f.GetReference(): f for f in board.GetFootprints()}
    bd.nets = {n: board.FindNet(n) for n in ("GND", "/ISO_GND")}
    bd.w, bd.h = W, H
    u2_ground_stubs(bd)
    ep_ground_stubs(bd)
    skipped = pad_vias(board, bd, "GND")
    skipped = [s for s in skipped if not rip_up_for_ground(board, bd, s)]
    skipped += pad_vias(board, bd, "/ISO_GND")
    # the board's ground everywhere but the island and its gap, the 4x2's on the island
    x0, y0, x1, _ = NOTCH
    e = 0.3
    notched = [(e, e), (W - e, e), (W - e, H - e), (x1, H - e), (x1, y0), (x0, y0), (x0, H - e), (e, H - e)]
    island = [(ISLAND[0], ISLAND[1]), (ISLAND[2], ISLAND[1]), (ISLAND[2], H - e), (ISLAND[0], H - e)]
    for layer in ("In1.Cu", "In2.Cu", "F.Cu", "B.Cu"):
        bd.zone(layer, "GND", notched, priority=0, clearance=0.3, name=f"GND {layer}")
        bd.zone(layer, "/ISO_GND", island, priority=0, clearance=0.3, name=f"ISO_GND {layer}")
    hole_keepouts(board)
    for net in ("GND", "/ISO_GND"):  # stitching vias on a 5 mm grid wherever there is room
        obs = obstacles(board, net)  # the tracks routed again above are new obstacles for them
        y = 2.5
        while y < H - 2:
            x = 2.5
            while x < W - 2:
                if obs.via_ok(x, y, clear=0.4) and all(math.hypot(x - hx, y - hy) > HOLE_CLEAR_R + 0.3
                                                        for hx, hy in HOLES):
                    bd.via(x, y, net)
                    obs.add_via(x, y)
                x += 5.0
            y += 5.0
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    return skipped


def evict(board: pcbnew.BOARD) -> list[str]:
    """Take up the routed copper that is on the wrong side of the gap: the board's nets in the island or its gap,
    the 4x2's outside the island. The grid router then joins those nets again within their side. Returns the
    nets it touched."""
    touched = set()
    for t in list(board.GetTracks()):
        name = t.GetNetname()
        if t.IsLocked() or not name:
            continue
        a, b = xy(t.GetStart()), xy(t.GetEnd())
        hw = pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu) if t.Type() == pcbnew.PCB_VIA_T else t.GetWidth()) / 2
        if any(_seg_rect_dist(*a, *b, *r) < hw + 0.2 for r in keep_out(name)):
            board.Delete(t)
            touched.add(name)
    return sorted(touched)


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    pcb = BOARD / f"{NAME}.kicad_pcb"
    if "--route" in sys.argv:
        route(pcb)
        return
    r = kicad_cli("sch", "export", "netlist", "--format", "kicadxml", "-o", str(BUILD / "net.xml"),
                  str(BOARD / f"{NAME}.kicad_sch"))
    if r.returncode:
        sys.exit(r.stderr)
    comps, nets = read_netlist(BUILD / "net.xml")
    settings = project_settings(NETCLASSES, PATTERNS)
    # Freerouting necks a 0.25 mm track down to 0.125 mm next to some fine-pitch pins. JLCPCB's 4-layer process
    # makes tracks down to 0.09 mm.
    settings["board"]["design_settings"]["rules"]["min_track_width"] = 0.12
    write_project(BOARD / f"{NAME}.kicad_pro", settings)
    bd = build(comps, nets)
    bd.save(pcb)
    if "--place" in sys.argv:  # placement only, to check courtyards and look at it before routing
        r = kicad_cli("pcb", "drc", "--format", "json", "--severity-all", "-o", str(BUILD / "drc_place.json"), str(pcb))
        print(r.stdout.strip()[-300:])
        return
    # a new process, so that LoadBoard reads the project's net classes afresh (this one has cached the project
    # without them when it saved the new board)
    subprocess.run([sys.executable, __file__, "--route"], check=True)


def route(pcb: Path) -> None:
    board = pcbnew.LoadBoard(str(pcb))
    tracks = [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T]
    widths = {t.m_Uuid.AsString(): t.GetWidth() for t in tracks}
    board.SynchronizeNetsAndNetClasses(True)  # apply the project's net classes (this also resets track widths)
    for t in tracks:
        t.SetWidth(widths[t.m_Uuid.AsString()])
    logs, best = [], None
    for k in range(1):  # a second pass from the first one's tracks has always come out worse
        pcbnew.ExportSpecctraDSN(board, str(BUILD / "route.dsn"))
        prepare_dsn(BUILD / "route.dsn", BUILD / "route_fr.dsn", ["GND", "/ISO_GND"], ("F.Cu", "B.Cu"))
        log, now = freeroute(BUILD / "route_fr.dsn", BUILD / "route.ses")
        logs.append(log)
        print(f"round {k + 1}: {now} connections unrouted", flush=True)
        if now is None or (best is not None and now >= best):
            break
        if not pcbnew.ImportSpecctraSES(board, str(BUILD / "route.ses")):
            sys.exit("session import failed")
        shutil.copy(BUILD / "route.ses", BUILD / "route_best.ses")
        best = now
        if now == 0:
            break
    print("taken up on the wrong side of the island's gap:", ", ".join(evict(board)) or "nothing", flush=True)
    print("dangling vias removed:", remove_dangling_vias(board, keep_nets=("GND", "/ISO_GND")))
    fixed = Board.__new__(Board)  # the fixed tracks again, since the session import sometimes drops a piece of them
    fixed.b, fixed.w, fixed.h = board, W, H
    fixed.nets = {n.GetNetname(): n for n in board.GetNetInfo().NetsByNetcode().values()}
    fixed.fps = {f.GetReference(): f for f in board.GetFootprints()}
    u2_stubs(fixed)
    buck_routes(fixed, vias=False)
    vref_routes(fixed, vias=False)
    ns = board.GetDesignSettings().m_NetSettings
    for net in list(board.GetNetInfo().NetsByNetcode().values()):
        name = net.GetNetname()
        if not name or name in ("GND", "/ISO_GND") or name.startswith("unconnected-"):
            continue
        nc = ns.GetEffectiveNetClass(name)
        width, clear = pcbnew.ToMM(nc.GetTrackWidth()), pcbnew.ToMM(nc.GetClearance())
        # The grid router ends its tracks on pad centres, where 0.4 mm is too wide at 0.5 mm pitch and 0.8 mm
        # next to a SOT-23's other pins. What it joins of a power net is a side branch (D4's clamp, a test point),
        # since Freerouting has routed the main paths, and is printed for review.
        width = 0.25 if width < 0.5 else 0.4
        before = len(board.GetTracks())
        if maze_route(board, name, width, clear + 0.02, W, H):
            print(f"grid router could not finish {name}", flush=True)
        if width == 0.4 and len(board.GetTracks()) > before:
            print(f"grid router joined part of power net {name} at 0.4 mm", flush=True)
    (BUILD / "freerouting.log").write_text("\n".join(logs), encoding="utf-8", newline="\n")
    skipped = ground(board)
    if skipped:
        print("no room for a ground via at", ", ".join(skipped))
    pcbnew.SaveBoard(str(pcb), board)
    r = kicad_cli("pcb", "drc", "--schematic-parity", "--format", "json", "--severity-all", "-o",
                  str(BUILD / "drc.json"), str(pcb))
    for _ in range(6):  # take away track pieces that end in nothing, and tiny pieces in clearance errors
        report = json.loads((BUILD / "drc.json").read_text(encoding="utf-8"))["violations"]
        dangling = [v["items"][0]["pos"] for v in report if v["type"] == "track_dangling"]
        tiny = [i["pos"] for v in report if v["type"] == "clearance" for i in v["items"]]
        gone = 0
        for t in list(board.GetTracks()):
            if t.Type() == pcbnew.PCB_VIA_T or t.IsLocked():
                continue
            ends = [(pcbnew.ToMM(q.x), pcbnew.ToMM(q.y)) for q in (t.GetStart(), t.GetEnd(), t.GetCenter())]
            short = pcbnew.ToMM(t.GetLength()) <= 0.1
            if any(abs(x - p["x"]) < 0.01 and abs(y - p["y"]) < 0.01 for p in dangling + (tiny if short else [])
                   for x, y in ends):
                board.Delete(t)
                gone += 1
        if not gone:
            break
        print(f"removed {gone} track pieces that ended in nothing", flush=True)
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        pcbnew.SaveBoard(str(pcb), board)
        r = kicad_cli("pcb", "drc", "--schematic-parity", "--format", "json", "--severity-all", "-o",
                      str(BUILD / "drc.json"), str(pcb))
    print(r.stdout.strip()[-400:])


if __name__ == "__main__":
    main()
