"""Draw where the ground copper lies on each of a board's four copper layers, for the design notes, and list the
separate pieces the ground copper of each layer falls into.

Each panel is one copper layer seen from the top of the board (the bottom layer too, as if looking through the
board, not mirrored). Ground copper is drawn in blue, the copper of every other net on that layer (tracks, pads,
vias) in orange, ground vias as dark dots, and bare board is left white, so the slots that tracks cut into the
ground of the outer layers can be compared with the inner planes. A second ground that is kept apart from the board's,
the RFSoC 4x2's ground on the island at the board's SPI input (/ISO_GND), is drawn in light blue.

    <KiCad python> hw/ocxo_board/gen/ground_layers.py dump <board>/<name>.kicad_pcb <board>/build/ground_layers.json
    python hw/ocxo_board/gen/ground_layers.py plot <board>/build/ground_layers.json <board>/fab/ground_layers.png [--zh] [REF ...]

dump prints, for every layer, the area of each separate piece of ground copper larger than 1 mm² and its extent.
plot labels the parts named by REF (for example X1 U2) on every panel, and --zh draws the Chinese version.
"""

from __future__ import annotations

import json
import math
import sys

LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
TEXT = {
    "en": {"F.Cu": "Top layer (F.Cu)", "In1.Cu": "Inner layer 1 (In1.Cu)", "In2.Cu": "Inner layer 2 (In2.Cu)",
           "B.Cu": "Bottom layer (B.Cu), seen through the board from the top",
           "x": "mm from the board's left edge", "y": "mm from the board's top edge",
           "ground": "Ground copper", "other": "Copper of other nets (tracks, pads, vias)", "via": "Ground via",
           "none": "No copper", "iso": "The RFSoC 4x2's ground, isolated"},
    "zh": {"F.Cu": "顶层（F.Cu）", "In1.Cu": "内层 1（In1.Cu）", "In2.Cu": "内层 2（In2.Cu）",
           "B.Cu": "底层（B.Cu），从顶面透过板子看",
           "x": "离板子左边缘的距离（mm）", "y": "离板子上边缘的距离（mm）",
           "ground": "接地的铜", "other": "其他网络的铜（走线、焊盘、过孔）", "via": "接地过孔", "none": "没有铜",
           "iso": "隔离开的 RFSoC 4x2 的地"},
}
GROUND, OTHER, VIA, ISO = "#2f6db5", "#e08a1e", "#0d2a4d", "#9cc3e8"
ISO_NET = "/ISO_GND"


def dump(pcb: str, out: str) -> None:
    import pcbnew

    b = pcbnew.LoadBoard(pcb)
    bb = b.GetBoardEdgesBoundingBox()
    x0, y0 = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop())
    w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())

    def p(v):
        return [round(pcbnew.ToMM(v.x) - x0, 4), round(pcbnew.ToMM(v.y) - y0, 4)]

    data = {"size": [w, h], "ground": {n: [] for n in LAYERS}, "pieces": {n: [] for n in LAYERS},
            "other": {n: [] for n in LAYERS}, "gnd_pads": {n: [] for n in LAYERS}, "gnd_vias": [], "refs": {},
            "iso": {n: [] for n in LAYERS}, "iso_pads": {n: [] for n in LAYERS}}
    for z in b.Zones():
        if z.GetNetname() not in ("GND", ISO_NET):
            continue
        for layer in z.GetLayerSet().Seq():
            name = b.GetLayerName(layer)
            polys = z.GetFilledPolysList(layer)  # fractured, so each outline already carries its holes
            for i in range(polys.OutlineCount()):
                line = polys.Outline(i)
                pts = [p(line.CPoint(k)) for k in range(line.PointCount())]
                if z.GetNetname() == ISO_NET:
                    data["iso"][name].append(pts)
                    continue
                data["ground"][name].append(pts)
                xs, ys = [q[0] for q in pts], [q[1] for q in pts]
                data["pieces"][name].append({"area": abs(line.Area()) / 1e12, "x": [min(xs), max(xs)],
                                             "y": [min(ys), max(ys)]})
    gnd = b.FindNet("GND").GetNetCode()
    iso = b.FindNet(ISO_NET).GetNetCode() if b.FindNet(ISO_NET) else -1

    def pad_shape(pad, layer):
        try:
            shape = pad.GetShape(layer)
        except TypeError:
            shape = pad.GetShape()
        s = pad.GetBoundingBox()
        kind = "circle" if shape in (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_OVAL) else "rect"
        return {"kind": kind, "c": p(pad.GetPosition()), "w": pcbnew.ToMM(s.GetWidth()), "h": pcbnew.ToMM(s.GetHeight())}

    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            d = pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu))
            if t.GetNetCode() in (gnd, iso):
                data["gnd_vias"].append(p(t.GetPosition()) + [d])
            else:
                for name in LAYERS:
                    data["other"][name].append({"kind": "circle", "c": p(t.GetPosition()), "w": d, "h": d})
        elif t.GetNetCode() == iso:
            data["iso_pads"][b.GetLayerName(t.GetLayer())].append(
                {"kind": "track", "a": p(t.GetStart()), "b": p(t.GetEnd()), "w": pcbnew.ToMM(t.GetWidth())})
        elif t.GetNetCode() != gnd:
            data["other"][b.GetLayerName(t.GetLayer())].append(
                {"kind": "track", "a": p(t.GetStart()), "b": p(t.GetEnd()), "w": pcbnew.ToMM(t.GetWidth())})
    for fp in b.GetFootprints():
        data["refs"][fp.GetReference()] = p(fp.GetPosition())
        for pad in fp.Pads():
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                continue  # the plain mounting holes have no copper
            for name in LAYERS:
                layer = b.GetLayerID(name)
                if pad.IsOnLayer(layer):
                    key = {gnd: "gnd_pads", iso: "iso_pads"}.get(pad.GetNetCode(), "other")
                    data[key][name].append(pad_shape(pad, layer))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f)
    for name in LAYERS:
        pieces = sorted((q for q in data["pieces"][name] if q["area"] > 1), key=lambda q: -q["area"])
        print(f"{name}: {len(data['pieces'][name])} pieces of ground copper, board {w:.0f} x {h:.0f} mm")
        for q in pieces:
            print(f"    {q['area']:8.0f} mm2 ({100 * q['area'] / (w * h):4.1f} % of the board), "
                  f"x {q['x'][0]:.0f}-{q['x'][1]:.0f} mm, y {q['y'][0]:.0f}-{q['y'][1]:.0f} mm")


def plot(src: str, out: str, lang: str, labels: list[str]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Circle, Ellipse, Patch, Polygon, Rectangle

    if lang == "zh":
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    tx = TEXT[lang]
    with open(src, encoding="utf-8") as f:
        d = json.load(f)
    w, h = d["size"]

    def shapes(items):
        out = []
        for s in items:
            if s["kind"] == "track":
                (xa, ya), (xb, yb), tw = s["a"], s["b"], s["w"]
                length = math.hypot(xb - xa, yb - ya) or 1e-9
                nx, ny = -(yb - ya) / length * tw / 2, (xb - xa) / length * tw / 2
                out.append(Polygon([(xa + nx, ya + ny), (xb + nx, yb + ny), (xb - nx, yb - ny), (xa - nx, ya - ny)]))
                out += [Circle((xa, ya), tw / 2), Circle((xb, yb), tw / 2)]
            elif s["kind"] == "circle":
                out.append(Ellipse(s["c"], s["w"], s["h"]))
            else:
                out.append(Rectangle((s["c"][0] - s["w"] / 2, s["c"][1] - s["h"] / 2), s["w"], s["h"]))
        return out

    fig, axes = plt.subplots(2, 2, figsize=(12, 12 * h / w + 1.4), sharex=True, sharey=True)
    for ax, name in zip(axes.flat, LAYERS):
        ax.add_patch(Rectangle((0, 0), w, h, facecolor="white", edgecolor="#444444", linewidth=0.8))
        ax.add_collection(PatchCollection([Polygon(pts, closed=True) for pts in d["ground"][name]],
                                          facecolor=GROUND, edgecolor="none"))
        ax.add_collection(PatchCollection(shapes(d["gnd_pads"][name]), facecolor=GROUND, edgecolor="none"))
        ax.add_collection(PatchCollection([Polygon(pts, closed=True) for pts in d.get("iso", {}).get(name, [])],
                                          facecolor=ISO, edgecolor="none"))
        ax.add_collection(PatchCollection(shapes(d.get("iso_pads", {}).get(name, [])), facecolor=ISO,
                                          edgecolor="none"))
        ax.add_collection(PatchCollection(shapes(d["other"][name]), facecolor=OTHER, edgecolor="none"))
        ax.add_collection(PatchCollection([Circle((x, y), dia / 2) for x, y, dia in d["gnd_vias"]],
                                          facecolor=VIA, edgecolor="none"))
        for ref in labels:
            x, y = d["refs"][ref]
            ax.annotate(ref, (x, y), fontsize=9, ha="center", va="center", color="black",
                        bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.85})
        ax.set_title(tx[name], fontsize=12)
        ax.set_xlim(-1, w + 1)
        ax.set_ylim(h + 1, -1)
        ax.set_aspect("equal")
    for ax in axes[1]:
        ax.set_xlabel(tx["x"])
    for ax in axes[:, 0]:
        ax.set_ylabel(tx["y"])
    handles = [Patch(color=GROUND, label=tx["ground"]), Patch(color=OTHER, label=tx["other"]),
               Patch(color=VIA, label=tx["via"]), Patch(facecolor="white", edgecolor="#444444", label=tx["none"])]
    if any(d.get("iso", {}).values()):
        handles.insert(1, Patch(color=ISO, label=tx["iso"]))
    fig.legend(handles=handles, loc="lower center", ncol=len(handles) if len(handles) < 5 else 3, frameon=False,
               fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, dpi=130)


if __name__ == "__main__":
    if sys.argv[1] == "dump":
        dump(sys.argv[2], sys.argv[3])
    else:
        rest = sys.argv[4:]
        plot(sys.argv[2], sys.argv[3], "zh" if "--zh" in rest else "en", [r for r in rest if r != "--zh"])
