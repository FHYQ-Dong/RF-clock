"""Build a one-sheet KiCad 10 schematic from placed symbols, wires, labels and power symbols.

Coordinates are in mm on the sheet (y down). Library symbols use y up, and a symbol's ``rot`` turns it
counter-clockwise. ``mirror`` flips a symbol left to right (only used with ``rot`` 0).
"""

from __future__ import annotations

import copy
from collections import Counter

from kisexp import Q, dump, find, find_all, lib_symbol, need, pins, rotate, uid

POWER_LIB = {"GND": "power:GND", "+12V": "power:+12V"}  # other rails use power:+5V or power:+3V3 by name
TWO_PIN = {"R", "C", "L", "Polyfuse", "FerriteBead_Small"}


def _r(v: float) -> float:
    return round(v, 4)


def _effects(justify: str | None = None, size: float = 1.27) -> list:
    e = ["effects", ["font", ["size", size, size]]]
    if justify:
        e.append(["justify", *justify.split()])
    return e


def make_symbol(lib_id: str, ref: str, value: str, footprint: str, datasheet: str, description: str,
                body: tuple[float, float, float, float], pin_list: list[tuple]) -> list:
    """A rectangular library symbol. ``pin_list`` holds (number, name, type, x, y, rot) with rot 0 for a pin
    on the left side pointing right into the body."""
    name = lib_id.split(":")[1]
    x1, y1, x2, y2 = body

    def prop(key, val, at, hide=False):
        p = ["property", Q(key), Q(val), ["at", *at, 0]]
        if hide:
            p.append(["hide", "yes"])
        return p + [["show_name", "no"], ["do_not_autoplace", "no"], _effects(None if hide else "left")]

    top = max(y1, y2)
    pins_ = [["pin", typ, "line", ["at", x, y, rot], ["length", 2.54], ["name", Q(nm), _effects()],
              ["number", Q(num), _effects()]] for num, nm, typ, x, y, rot in pin_list]
    return ["symbol", Q(lib_id), ["pin_names", ["offset", 1.016]], ["exclude_from_sim", "no"], ["in_bom", "yes"],
            ["on_board", "yes"], ["in_pos_files", "yes"], ["duplicate_pin_numbers_are_jumpers", "no"],
            prop("Reference", ref, (x1, top + 3.81)), prop("Value", value, (x1, top + 1.27)),
            prop("Footprint", footprint, (0, 0), True), prop("Datasheet", datasheet, (0, 0), True),
            prop("Description", description, (0, 0), True),
            ["symbol", Q(f"{name}_0_1"), ["rectangle", ["start", x1, y1], ["end", x2, y2],
                                          ["stroke", ["width", 0.254], ["type", "default"]],
                                          ["fill", ["type", "background"]]]],
            ["symbol", Q(f"{name}_1_1"), *pins_], ["embedded_fonts", "no"]]


class Part:
    def __init__(self, sch: "Schematic", ref: str, lib_id: str, x: float, y: float, rot: int, unit: int, mirror: bool):
        self.sch, self.ref, self.lib_id, self.x, self.y = sch, ref, lib_id, x, y
        self.rot, self.unit, self.mirror = rot, unit, mirror
        self.pins = pins(sch.lib(lib_id), unit)

    def pin(self, num: str) -> tuple[float, float, int]:
        """Sheet position of pin ``num``'s end, and the direction (0 right, 90 up) pointing away from the body."""
        p = self.pins[num]
        px, a = (-p["x"], (180 - p["rot"]) % 360) if self.mirror else (p["x"], p["rot"])
        dx, dy = rotate(px, p["y"], self.rot)
        return _r(self.x + dx), _r(self.y - dy), (a + 180 + self.rot) % 360

    def __getitem__(self, num: str) -> tuple["Part", str]:
        return self, num


class Schematic:
    custom: dict[str, list] = {}

    def __init__(self, project: str, title: str, rev: str, date: str, comments: list[str]):
        self.project, self.title, self.rev, self.date, self.comments = project, title, rev, date, comments
        self.root = uid()
        self.libs: dict[str, list] = {}
        self.parts: dict[tuple[str, int], Part] = {}
        self.items: list[list] = []
        self.ends: list[tuple[float, float]] = []  # wire ends and pin ends, for junction dots
        self.pwr = 0
        self.used: set[tuple[str, int, str]] = set()
        self.paper = "A3"

    def lib(self, lib_id: str) -> list:
        if lib_id not in self.libs:
            self.libs[lib_id] = self.custom.get(lib_id) or lib_symbol(lib_id)
        return self.libs[lib_id]

    # ---------------------------------------------------------------- symbols
    def place(self, ref: str, lib_id: str, x: float, y: float, rot: int = 0, unit: int = 1, value: str | None = None,
              fp: str | None = None, fields: dict[str, str] | None = None, ref_at: tuple | None = None,
              value_at: tuple | None = None, mirror: bool = False, hide_value: bool = False) -> Part:
        """Place one unit of a symbol. ``ref_at``/``value_at`` are (dx, dy[, justify]) from the symbol's origin."""
        part = Part(self, ref, lib_id, x, y, rot, unit, mirror)
        self.parts[(ref, unit)] = part
        sym = self.lib(lib_id)
        props = {p[1]: p for p in find_all(sym, "property")}
        virtual = ref.startswith("#")
        node = ["symbol", ["lib_id", Q(lib_id)], ["at", _r(x), _r(y), rot]]
        if mirror:
            node.append(["mirror", "y"])
        in_bom = find(sym, "in_bom")
        bare = (fp or "").startswith(("TestPoint:TestPoint_Pad", "MountingHole:"))  # nothing to buy
        in_bom = "no" if virtual or bare or (in_bom is not None and in_bom[1] == "no") else "yes"
        node += [["unit", unit], ["body_style", 1], ["exclude_from_sim", "no"], ["in_bom", in_bom],
                 ["on_board", "no" if virtual else "yes"], ["in_pos_files", "no" if virtual else "yes"], ["dnp", "no"],
                 ["uuid", uid()]]
        vals = {"Reference": ref, "Value": value or str(props["Value"][2]),
                "Footprint": fp if fp is not None else str(props["Footprint"][2]),
                "Datasheet": str(props["Datasheet"][2]) if "Datasheet" in props else "",
                "Description": str(props["Description"][2]) if "Description" in props else ""}
        vals.update(fields or {})
        for name, val in vals.items():
            hide = name not in ("Reference", "Value") or (virtual and name == "Reference") or                 (hide_value and name == "Value")
            at, just = self._field_at(part, props.get(name), name, ref_at, value_at)
            prop = ["property", Q(name), Q(val), ["at", _r(at[0]), _r(at[1]), rot % 180]]
            if hide:
                prop.append(["hide", "yes"])
            node.append(prop + [["show_name", "no"], ["do_not_autoplace", "no"], _effects(just)])
        for num in part.pins:
            node.append(["pin", Q(num), ["uuid", uid()]])
        node.append(["instances", ["project", Q(self.project), ["path", Q("/" + self.root), ["reference", Q(ref)],
                                                              ["unit", unit]]]])
        self.items.append(node)
        for num in part.pins:
            self.ends.append(part.pin(num)[:2])
        return part

    def _field_at(self, part: Part, prop, name, ref_at, value_at):
        """Where a field goes. KiCad turns field text and its justification with the symbol, so the text is
        written at the symbol's angle (horizontal on the sheet) and justified only on unturned symbols."""
        override = {"Reference": ref_at, "Value": value_at}.get(name)
        if override is None and part.lib_id.split(":")[1] in TWO_PIN and name in ("Reference", "Value"):
            k = -1 if name == "Reference" else 1
            gap = 3.3 if part.lib_id.split(":")[1] == "C" else 2.54
            override = (2.54, k * 1.27, "left") if part.rot % 180 == 0 else (0, k * gap)
        plain = part.rot == 0 and not part.mirror
        if override is not None:
            return (part.x + override[0], part.y + override[1]), (override[2] if len(override) > 2 and plain else None)
        if prop is None:
            return (part.x, part.y), None
        at = need(prop, "at")
        fx = -float(at[1]) if part.mirror else float(at[1])
        dx, dy = rotate(fx, float(at[2]), part.rot)
        eff = find(prop, "effects")
        j = find(eff, "justify") if eff else None
        return (part.x + dx, part.y - dy), (" ".join(j[1:]) if j and plain else None)

    # ------------------------------------------------------------- connections
    def _end(self, a: tuple[Part, str], mark: bool = True) -> tuple[float, float, int]:
        part, num = a
        key = (part.ref, part.unit, num)
        if mark and key in self.used:
            raise ValueError(f"{part.ref} pin {num} already has a label, power symbol or no-connect")
        self.used.add(key)
        return part.pin(num)

    def net(self, a: tuple[Part, str], net: str, path: tuple = (), mark: bool = True) -> None:
        """Connect a pin to ``net``: a wire along ``path`` (relative steps), then a label or a power symbol."""
        x, y, d = self._end(a, mark)
        for dx, dy in path:
            nx, ny = _r(x + dx), _r(y + dy)
            self.wire((x, y), (nx, ny))
            d = 0 if dx > 0 else 180 if dx < 0 else 270 if dy > 0 else 90
            x, y = nx, ny
        if net in POWER_LIB or net.startswith("+"):
            self.power(x, y, net, d)
        else:
            self.label(x, y, net, d)

    def nc(self, a: tuple[Part, str]) -> None:
        x, y, _ = self._end(a)
        self.items.append(["no_connect", ["at", x, y], ["uuid", uid()]])

    def join(self, *stops) -> None:
        """One wire through pins (Part, num) and absolute points (x, y), in order."""
        pts = []
        for s in stops:
            if isinstance(s[0], Part):
                x, y, _ = self._end(s, mark=False)
                pts.append((x, y))
            else:
                pts.append(s)
        self.wire(*pts)

    def wire(self, *pts) -> None:
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            self.items.append(["wire", ["pts", ["xy", _r(x1), _r(y1)], ["xy", _r(x2), _r(y2)]],
                               ["stroke", ["width", 0], ["type", "default"]], ["uuid", uid()]])
            self.ends += [(_r(x1), _r(y1)), (_r(x2), _r(y2))]

    def label(self, x: float, y: float, net: str, d: int) -> None:
        self.items.append(["label", Q(net), ["at", _r(x), _r(y), d],
                           _effects("left bottom" if d in (0, 90) else "right bottom"), ["uuid", uid()]])

    def power(self, x: float, y: float, net: str, d: int) -> None:
        """A power symbol whose arrow or ground mark points along ``d``."""
        lib_id = POWER_LIB.get(net) or ("power:+3V3" if "3V3" in net else "power:+5V")
        self.pwr += 1
        rot = (d + 90) % 360 if net == "GND" else (d - 90) % 360
        if d in (90, 270):
            value_at = (0, -4.6 if d == 90 else 4.6)
        else:
            value_at = ((1 if d == 0 else -1) * (3.81 + 0.6 * len(net)), 0)
        self.place(f"#PWR{self.pwr:03d}", lib_id, x, y, rot, value=net, value_at=value_at)

    def flag(self, x: float, y: float, down: bool = False) -> None:
        """A PWR_FLAG on the point (x, y) of a wire or pin, pointing up (or down)."""
        self.pwr += 1
        self.place(f"#FLG{self.pwr:03d}", "power:PWR_FLAG", x, y, 180 if down else 0,
                   value_at=(0, 4.2 if down else -4.2))

    def text(self, x: float, y: float, s: str, size: float = 1.8, bold: bool = True) -> None:
        font = ["font", ["size", size, size]] + ([["thickness", round(size * 0.15, 3)], ["bold", "yes"]] if bold else [])
        self.items.append(["text", Q(s), ["exclude_from_sim", "no"], ["at", _r(x), _r(y), 0],
                           ["effects", font, ["justify", "left", "bottom"]], ["uuid", uid()]])

    # -------------------------------------------------------------------- output
    def unconnected(self) -> list[str]:
        """Pins that have neither a label, a power symbol, a no-connect nor a wire."""
        wired = Counter(self.ends)
        out = []
        for (ref, unit), part in self.parts.items():
            if ref.startswith("#"):
                continue
            for num, p in part.pins.items():
                if (ref, unit, num) not in self.used and wired[part.pin(num)[:2]] < 2:
                    out.append(f"{ref}.{num} ({p['name']})")
        return out

    def junctions(self) -> list[list]:
        return [["junction", ["at", x, y], ["diameter", 0], ["color", 0, 0, 0, 0], ["uuid", uid()]]
                for (x, y), n in Counter(self.ends).items() if n >= 3]

    def write(self, path) -> None:
        tb = ["title_block", ["title", Q(self.title)], ["date", Q(self.date)], ["rev", Q(self.rev)]]
        tb += [["comment", i + 1, Q(c)] for i, c in enumerate(self.comments)]
        libs: list = ["lib_symbols"]
        for lib_id, sym in self.libs.items():
            s = copy.deepcopy(sym)
            s[1] = Q(lib_id)
            libs.append(s)
        root = ["kicad_sch", ["version", 20260306], ["generator", Q("eeschema")], ["generator_version", Q("10.0")],
                ["uuid", self.root], ["paper", Q(self.paper)], tb, libs, *self.junctions(), *self.items,
                ["sheet_instances", ["path", Q("/"), ["page", Q("1")]]], ["embedded_fonts", "no"]]
        path.write_text(dump(root) + "\n", encoding="utf-8", newline="\n")


def add_part_fields(s: Schematic, lookup) -> list[str]:
    """Hidden MPN and LCSC fields on every real symbol, from lookup(value, footprint) -> (MPN, LCSC, note).
    Returns the references that found no entry."""
    missing = []
    for node in s.items:
        if node[0] != "symbol":
            continue
        props = {p[1]: p for p in find_all(node, "property")}
        ref = str(props["Reference"][2])
        if ref.startswith("#") or ref.startswith(("TP", "H")) and not props["Footprint"][2].endswith("Miniature"):
            continue
        mpn, lcsc, _ = lookup(str(props["Value"][2]), str(props["Footprint"][2]))
        if not mpn:
            missing.append(ref)
        at = find(props["Reference"], "at")
        pos = len(node) - 1 - sum(1 for e in node if isinstance(e, list) and e[0] in ("pin", "instances"))
        for name, val in (("MPN", mpn), ("LCSC", lcsc)):
            node.insert(pos, ["property", Q(name), Q(val), ["at", at[1], at[2], 0], ["hide", "yes"],
                              ["show_name", "no"], ["do_not_autoplace", "no"], _effects()])
            pos += 1
    return sorted(set(missing))
