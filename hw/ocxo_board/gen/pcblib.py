"""Helpers for building a KiCad 10 board from a schematic netlist with KiCad's own Python (pcbnew), routing it with
Freerouting and finishing it (ground vias, copper pours, checks, fabrication files).

Run the scripts that use it with KiCad's Python, C:/Users/FHYQ-Dong/scoop/apps/kicad/current/bin/python.exe.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pcbnew

KICAD = Path("C:/Users/FHYQ-Dong/scoop/apps/kicad/current")
KICAD_CLI = KICAD / "bin/kicad-cli.exe"
STOCK_FP = KICAD / "share/kicad/footprints"
OX, OY = 100.0, 100.0  # the board's top left corner on KiCad's sheet, mm

CU = {"F.Cu": pcbnew.F_Cu, "In1.Cu": pcbnew.In1_Cu, "In2.Cu": pcbnew.In2_Cu, "B.Cu": pcbnew.B_Cu}


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def pt(x: float, y: float) -> pcbnew.VECTOR2I:
    """A point in board coordinates (mm from the board's top left corner)."""
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def xy(p: pcbnew.VECTOR2I) -> tuple[float, float]:
    return pcbnew.ToMM(p.x) - OX, pcbnew.ToMM(p.y) - OY


# ------------------------------------------------------------------------------------------- netlist
def read_netlist(path: Path) -> tuple[dict, dict]:
    """Components {ref: {value, footprint, uuid, fields}} and nets {name: [(ref, pin), ...]} of a KiCad XML netlist."""
    root = ET.parse(path).getroot()
    comps = {}
    for c in root.find("components"):
        fields = {f.get("name"): (f.text or "") for f in c.iter("field")}
        fields["Description"] = c.findtext("description") or fields.get("Description", "")
        comps[c.get("ref")] = {"value": c.findtext("value"), "footprint": c.findtext("footprint"),
                               "uuid": c.findtext("tstamps"), "fields": fields}
    nets = {}
    for n in root.find("nets"):
        nets[n.get("name")] = [(x.get("ref"), x.get("pin")) for x in n.iter("node")]
    return comps, nets


def load_footprint(lib_id: str, project_dir: Path) -> pcbnew.FOOTPRINT:
    lib, name = lib_id.split(":")
    path = project_dir / f"{lib}.pretty" if (project_dir / f"{lib}.pretty").exists() else STOCK_FP / f"{lib}.pretty"
    fp = pcbnew.FootprintLoad(str(path), name)
    if fp is None:
        raise KeyError(lib_id)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


# ------------------------------------------------------------------------------------------- building
class Board:
    def __init__(self, width: float, height: float, layers: int = 4):
        self.b = pcbnew.BOARD()
        self.b.SetCopperLayerCount(layers)
        self.w, self.h = width, height
        self.nets: dict[str, pcbnew.NETINFO_ITEM] = {}
        self.fps: dict[str, pcbnew.FOOTPRINT] = {}

    def net(self, name: str) -> pcbnew.NETINFO_ITEM:
        if name not in self.nets:
            n = pcbnew.NETINFO_ITEM(self.b, name)
            self.b.Add(n)
            self.nets[name] = n
        return self.nets[name]

    def outline(self, radius: float = 1.0) -> None:
        """A rectangular outline with rounded corners on Edge.Cuts."""
        w, h, r = self.w, self.h, radius
        segs = [((r, 0), (w - r, 0)), ((w, r), (w, h - r)), ((w - r, h), (r, h)), ((0, h - r), (0, r))]
        for a, b in segs:
            s = pcbnew.PCB_SHAPE(self.b)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(pt(*a))
            s.SetEnd(pt(*b))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.b.Add(s)
        for cx, cy, a0 in ((r, r, 180), (w - r, r, 270), (w - r, h - r, 0), (r, h - r, 90)):
            s = pcbnew.PCB_SHAPE(self.b)
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetCenter(pt(cx, cy))
            s.SetStart(pt(cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0))))
            s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.b.Add(s)

    def place(self, comps: dict, nets: dict, placement: dict, project_dir: Path) -> None:
        """Every component of the netlist at placement[ref] = (x, y, degrees), its pads on their nets."""
        pad_net = {(r, p): n for n, nodes in nets.items() for r, p in nodes}
        missing = sorted(set(comps) - set(placement))
        if missing:
            raise KeyError(f"no placement for {missing}")
        for ref, c in comps.items():
            fp = load_footprint(c["footprint"], project_dir)
            fp.SetReference(ref)
            fp.SetValue(c["value"])
            fp.SetPath(pcbnew.KIID_PATH("/" + c["uuid"]))
            x, y, rot = placement[ref]
            fp.SetPosition(pt(x, y))
            fp.SetOrientationDegrees(rot)
            for name, value in c["fields"].items():
                if name in ("LCSC", "MPN", "Datasheet", "Description"):
                    fp.SetField(name, value)
                    field = fp.GetField(name)
                    if field is not None:
                        field.SetVisible(False)
            ref_text = fp.Reference()
            ref_text.SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
            ref_text.SetTextThickness(mm(0.12))
            self.b.Add(fp)
            for pad in fp.Pads():
                name = pad_net.get((ref, pad.GetNumber()))
                if name:
                    pad.SetNet(self.net(name))
            self.fps[ref] = fp

    def pad(self, ref: str, num: str) -> pcbnew.PAD:
        return next(p for p in self.fps[ref].Pads() if p.GetNumber() == num)

    def pad_xy(self, ref: str, num: str) -> tuple[float, float]:
        return xy(self.pad(ref, num).GetPosition())

    def track(self, pts: list[tuple[float, float]], width: float, net: str, layer: str = "F.Cu",
              locked: bool = True) -> None:
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(pt(*a))
            t.SetEnd(pt(*b))
            t.SetWidth(mm(width))
            t.SetLayer(CU[layer])
            t.SetNet(self.net(net))
            t.SetLocked(locked)
            self.b.Add(t)

    def via(self, x: float, y: float, net: str, size: float = 0.6, drill: float = 0.3) -> None:
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(pt(x, y))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetWidth(mm(size))
        v.SetDrill(mm(drill))
        v.SetNet(self.net(net))
        self.b.Add(v)

    def zone(self, layer: str, net: str, poly: list[tuple[float, float]] | None = None, priority: int = 0,
             clearance: float = 0.3, solid: bool = False, name: str = "") -> None:
        z = pcbnew.ZONE(self.b)
        z.SetLayer(CU[layer])
        z.SetNet(self.net(net))
        poly = poly or [(0.3, 0.3), (self.w - 0.3, 0.3), (self.w - 0.3, self.h - 0.3), (0.3, self.h - 0.3)]
        ol = z.Outline()
        ol.NewOutline()
        for x, y in poly:
            p = pt(x, y)
            ol.Append(p.x, p.y)
        z.SetAssignedPriority(priority)
        z.SetLocalClearance(mm(clearance))
        z.SetMinThickness(mm(0.25))
        z.SetThermalReliefGap(mm(0.3))
        z.SetThermalReliefSpokeWidth(mm(0.4))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL if solid else pcbnew.ZONE_CONNECTION_THT_THERMAL)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        if name:
            z.SetZoneName(name)
        self.b.Add(z)

    def text(self, s: str, x: float, y: float, size: float = 1.0, layer: int = pcbnew.F_SilkS, angle: float = 0,
             justify: str = "center") -> None:
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(s)
        t.SetPosition(pt(x, y))
        t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(max(size * 0.15, 0.12)))
        t.SetTextAngleDegrees(angle)
        t.SetHorizJustify({"left": pcbnew.GR_TEXT_H_ALIGN_LEFT, "right": pcbnew.GR_TEXT_H_ALIGN_RIGHT}.get(
            justify, pcbnew.GR_TEXT_H_ALIGN_CENTER))
        if layer == pcbnew.B_SilkS:
            t.SetMirrored(True)
        self.b.Add(t)

    def add_model(self, ref: str, path: str, offset=(0, 0, 0), rotate=(0, 0, 0)) -> None:
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = path
        m.m_Offset = pcbnew.VECTOR3D(*offset)
        m.m_Rotation = pcbnew.VECTOR3D(*rotate)
        self.fps[ref].Models().push_back(m)

    def save(self, path: Path) -> None:
        pcbnew.SaveBoard(str(path), self.b)


# ------------------------------------------------------------------------------------------- project file
def project_settings(netclasses: dict[str, dict], patterns: list[tuple[str, str]]) -> dict:
    """The board part of a .kicad_pro: JLCPCB-safe design rules, net classes and their net patterns."""
    classes = []
    for name, c in netclasses.items():
        classes.append({"name": name, "bus_width": 12, "clearance": c["clearance"], "diff_pair_gap": 0.25,
                        "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3,
                        "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": c.get("priority", 0),
                        "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": c["track"],
                        "via_diameter": c.get("via", 0.6), "via_drill": c.get("drill", 0.3), "wire_width": 6})
    return {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2, "copper_text_size_h": 1.5,
                         "copper_text_size_v": 1.5, "copper_text_thickness": 0.3, "silk_line_width": 0.12,
                         "silk_text_size_h": 1.0, "silk_text_size_v": 1.0, "silk_text_thickness": 0.15},
            "rules": {"min_clearance": 0.15, "min_track_width": 0.15, "min_via_diameter": 0.5,
                      "min_through_hole_diameter": 0.2, "min_hole_to_hole": 0.5, "min_hole_clearance": 0.25,
                      "min_copper_edge_clearance": 0.3, "min_silk_clearance": 0.0, "min_text_height": 0.8,
                      "min_text_thickness": 0.12, "min_via_annular_width": 0.13, "solder_mask_to_copper_clearance": 0.0},
            "rule_severities": {"silk_overlap": "ignore", "silk_over_copper": "ignore", "silk_edge_clearance": "ignore",
                                "lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
                                "courtyards_overlap": "error", "text_height": "warning"},
        }},
        "net_settings": {"classes": classes, "meta": {"version": 4},
                         "netclass_patterns": [{"netclass": c, "pattern": p} for p, c in patterns]},
    }


def write_project(path: Path, board_settings: dict) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("meta", {"filename": path.name, "version": 3})
    data.update(board_settings)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


# ------------------------------------------------------------------------------------------- routing
def _strip(text: str, key: str) -> str:
    """Remove every balanced S-expression that starts with ``key``."""
    out, i = [], 0
    while True:
        j = text.find(key, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        depth, k = 0, j
        while True:
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1


def prepare_dsn(src: Path, dst: Path, split_nets: list[str], route_layers: tuple[str, ...]) -> None:
    """Make KiCad's Specctra export digestible for Freerouting 2.5.0's native CLI, which crashes on component
    outlines, planes and keepouts, and keep it from routing ``split_nets`` (ground and planes, which the board
    connects by its own vias): each pin of those nets becomes a one-pin net, so it stays an obstacle. Layers not
    in ``route_layers`` become power layers, which Freerouting does not route on."""
    t = src.read_text(encoding="utf-8")
    for key in ("(outline", "(plane", "(keepout", "(via_keepout", "(wire_keepout"):
        t = _strip(t, key)
    for name in split_nets:
        m = re.search(r"\(net " + re.escape(name) + r"\s*\(pins ([^)]*)\)\s*\)", t)
        if not m:
            continue
        pins = m.group(1).split()
        repl = " ".join(f"(net {name}__{k} (pins {p}))" for k, p in enumerate(pins))
        t = t[:m.start()] + repl + t[m.end():]
        t = re.sub(r"(\(class [^\n]*?) " + re.escape(name) + r"(?=[\s)])", r"\1", t)

    def layer_type(m):
        return m.group(0) if m.group(1) in route_layers else m.group(0).replace("(type signal)", "(type power)")
    t = re.sub(r"\(layer (\S+)\s*\(type signal\)", layer_type, t)
    # power layers alone do not stop Freerouting, every net class is also limited to the routing layers
    t = t.replace("(circuit", "(circuit (use_layer " + " ".join(route_layers) + ")")
    dst.write_text(t, encoding="utf-8", newline="\n")


FREEROUTING = Path.home() / "scoop/apps/freerouting-cli/current/freerouting-cli.exe"


def freeroute(dsn: Path, ses: Path, timeout: int = 300) -> tuple[str, int | None]:
    """One routing pass of Freerouting's native CLI, with its usage reporting off. Version 2.5.0 crashes and
    then hangs when a second pass starts (a missing serialization entry in the native build), so the board is
    routed one pass at a time. Returns the log and the number of connections left unrouted."""
    env = dict(os.environ, FREEROUTING__USAGE_AND_DIAGNOSTIC_DATA__DISABLE_ANALYTICS="true")
    ses.unlink(missing_ok=True)
    p = subprocess.Popen([str(FREEROUTING), "-de", str(dsn), "-do", str(ses), "--router.autorouter.max_passes=1",
                          "--gui.enabled=false", "--usage_and_diagnostic_data.disable_analytics=true"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        log, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        log, _ = p.communicate()
    if not ses.exists():
        raise RuntimeError("Freerouting wrote no session file:\n" + log[-3000:])
    m = re.findall(r"final score: [\d.]+ \((\d+) unrouted", log)
    return log, int(m[-1]) if m else None


# ------------------------------------------------------------------------------------------- finishing
class Obstacles:
    """Copper of every net but one, and every drilled hole, for placing that net's vias and short tracks."""

    def __init__(self, board: pcbnew.BOARD, net_code: int):
        self.rects, self.segs, self.holes = [], [], []
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.GetDrillSize().x > 0:
                    self.holes.append((*xy(p.GetPosition()), pcbnew.ToMM(max(p.GetDrillSize().x, p.GetDrillSize().y)) / 2))
                if p.GetNetCode() != net_code or p.GetNetCode() == 0:
                    bb = p.GetBoundingBox()
                    self.rects.append((pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
                                       pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY))
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                self.holes.append((*xy(t.GetPosition()), pcbnew.ToMM(t.GetDrillValue()) / 2))
                if t.GetNetCode() != net_code:
                    p = xy(t.GetPosition())
                    self.segs.append((*p, *p, pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2))
            elif t.GetNetCode() != net_code:
                self.segs.append((*xy(t.GetStart()), *xy(t.GetEnd()), pcbnew.ToMM(t.GetWidth()) / 2))
        self.w, self.h = board_size(board)

    def add_via(self, x: float, y: float, r: float = 0.3, drill: float = 0.3) -> None:
        self.holes.append((x, y, drill / 2))

    def free(self, x0: float, y0: float, x1: float, y1: float, hw: float, clear: float) -> bool:
        """Whether a track from (x0, y0) to (x1, y1) of half width hw keeps ``clear`` from all other copper."""
        need = hw + clear
        for r in self.rects:
            if _seg_rect_dist(x0, y0, x1, y1, *r) < need:
                return False
        for sx0, sy0, sx1, sy1, shw in self.segs:
            if _seg_seg_dist(x0, y0, x1, y1, sx0, sy0, sx1, sy1) < need + shw:
                return False
        return True

    def via_ok(self, x: float, y: float, r: float = 0.3, drill: float = 0.3, clear: float = 0.25,
               hole_gap: float = 0.5, edge: float = 0.3) -> bool:
        if not (edge + r <= x <= self.w - edge - r and edge + r <= y <= self.h - edge - r):
            return False
        for hx, hy, hr in self.holes:
            if math.hypot(hx - x, hy - y) < hr + drill / 2 + hole_gap:
                return False
        return self.free(x, y, x, y, r, clear)


def free_spot(obs: Obstacles, x: float, y: float, tries: list[tuple[float, float]], stub_hw: float | None = 0.2,
              clear: float = 0.25) -> tuple[float, float] | None:
    """The first offset (dx, dy) from (x, y) where a via fits, and, with stub_hw, a track from (x, y) to it too."""
    for dx, dy in tries:
        cx, cy = x + dx, y + dy
        if obs.via_ok(cx, cy, clear=clear) and (stub_hw is None or obs.free(x, y, cx, cy, stub_hw, clear)):
            return dx, dy
    return None


def _seg_dist(px, py, x0, y0, x1, y1) -> float:
    vx, vy = x1 - x0, y1 - y0
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x0) * vx + (py - y0) * vy) / L))
    return math.hypot(px - (x0 + t * vx), py - (y0 + t * vy))


def _cross(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _seg_seg_dist(ax, ay, bx, by, cx, cy, dx, dy) -> float:
    d1, d2 = _cross(cx, cy, dx, dy, ax, ay), _cross(cx, cy, dx, dy, bx, by)
    d3, d4 = _cross(ax, ay, bx, by, cx, cy), _cross(ax, ay, bx, by, dx, dy)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return 0.0
    return min(_seg_dist(ax, ay, cx, cy, dx, dy), _seg_dist(bx, by, cx, cy, dx, dy),
               _seg_dist(cx, cy, ax, ay, bx, by), _seg_dist(dx, dy, ax, ay, bx, by))


def _seg_rect_dist(x0, y0, x1, y1, rx0, ry0, rx1, ry1) -> float:
    def inside(px, py):
        return rx0 <= px <= rx1 and ry0 <= py <= ry1
    if inside(x0, y0) or inside(x1, y1):
        return 0.0
    edges = ((rx0, ry0, rx1, ry0), (rx1, ry0, rx1, ry1), (rx1, ry1, rx0, ry1), (rx0, ry1, rx0, ry0))
    return min(_seg_seg_dist(x0, y0, x1, y1, *e) for e in edges)


def remove_dangling_vias(board: pcbnew.BOARD, keep_nets: tuple[str, ...] = ("GND",)) -> int:
    """Delete vias that a track reaches on fewer than two layers (leftovers of the router's fan-out)."""
    ends = {}
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T:
            for p in (t.GetStart(), t.GetEnd()):
                ends.setdefault((t.GetNetCode(), t.GetLayer()), []).append((p.x, p.y))
    pad_layers = {}
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
                pad_layers.setdefault(p.GetNetCode(), []).append((p, p.GetLayer()))
    gone = 0
    for v in [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]:
        if v.GetNetname() in keep_nets:
            continue
        r = v.GetWidth(pcbnew.F_Cu) / 2
        pos = v.GetPosition()
        layers = set()
        for (code, layer), pts in ends.items():
            if code == v.GetNetCode() and any(math.hypot(px - pos.x, py - pos.y) <= r for px, py in pts):
                layers.add(layer)
        for p, layer in pad_layers.get(v.GetNetCode(), []):
            if p.HitTest(pos):
                layers.add(layer)
        if len(layers) < 2:
            board.Delete(v)
            gone += 1
    return gone


def board_size(board: pcbnew.BOARD) -> tuple[float, float]:
    bb = board.GetBoardEdgesBoundingBox()
    return pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY


def ring(r_min: float, r_max: float, step: float = 0.25) -> list[tuple[float, float]]:
    """Candidate offsets in growing rings, 16 directions each."""
    out = []
    r = r_min
    while r <= r_max + 1e-9:
        for k in range(16):
            a = math.radians(22.5 * k)
            out.append((round(r * math.cos(a), 3), round(r * math.sin(a), 3)))
        r += step
    return out


def kicad_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(KICAD_CLI), *args], capture_output=True, text=True)
