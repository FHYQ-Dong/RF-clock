"""A small reader and writer for KiCad's S-expression files, and the symbol library lookups the generators need."""

from __future__ import annotations

import copy
import math
import re
import uuid as _uuid
from pathlib import Path

KICAD = Path("C:/Users/FHYQ-Dong/scoop/apps/kicad/current")
SYMBOLS = KICAD / "share/kicad/symbols"

_TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')


class Q(str):
    """A string that is written in double quotes."""


def parse(text: str) -> list:
    """Parse S-expression text into nested lists of atoms (str) and quoted strings (Q)."""
    stack: list[list] = [[]]
    for m in _TOKEN.finditer(text):
        t = m.group(0)
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        elif t[0] == '"':
            stack[-1].append(Q(re.sub(r"\\(.)", lambda e: "\n" if e.group(1) == "n" else e.group(1), t[1:-1])))
        else:
            stack[-1].append(t)
    return stack[0][0]


def _atom(x) -> str:
    if isinstance(x, Q):
        return '"' + x.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(x, float):
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return str(x)


def dump(x, ind: int = 0) -> str:
    """Write nested lists back as KiCad-style S-expression text, one sub-list per line."""
    if not isinstance(x, list):
        return _atom(x)
    if not any(isinstance(e, list) for e in x):
        return "(" + " ".join(_atom(e) for e in x) + ")"
    pad = "\t" * (ind + 1)
    head = [e for e in x if not isinstance(e, list)]
    out = "(" + " ".join(_atom(e) for e in head)
    for e in x:
        if isinstance(e, list):
            out += "\n" + pad + dump(e, ind + 1)
    return out + "\n" + "\t" * ind + ")"


def find(node: list, key: str) -> list | None:
    """The first child list whose head is ``key``."""
    for e in node:
        if isinstance(e, list) and e and e[0] == key:
            return e
    return None


def need(node: list, key: str) -> list:
    """The first child list whose head is ``key``, which must exist."""
    found = find(node, key)
    if found is None:
        raise KeyError(f"no ({key} ...) in ({node[0]} {node[1] if len(node) > 1 else ''} ...)")
    return found


def find_all(node: list, key: str) -> list[list]:
    return [e for e in node if isinstance(e, list) and e and e[0] == key]


_SEED = {"name": "", "n": 0}


def seed(name: str) -> None:
    """Make the following uid() calls repeat from run to run (UUIDs derived from name and a counter), so that a
    regenerated schematic keeps the UUIDs the board's footprints refer to."""
    _SEED["name"], _SEED["n"] = name, 0


def uid() -> Q:
    if not _SEED["name"]:
        return Q(str(_uuid.uuid4()))
    _SEED["n"] += 1
    return Q(str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"rf-clock/{_SEED['name']}/{_SEED['n']}")))


_LIBS: dict[str, list] = {}


def _lib(name: str) -> list:
    if name not in _LIBS:
        _LIBS[name] = parse((SYMBOLS / f"{name}.kicad_sym").read_text(encoding="utf-8"))
    return _LIBS[name]


def _rename(node: list, old: str, new: str) -> None:
    """Rename the unit sub-symbols ``old_u_b`` of a symbol to ``new_u_b``."""
    for e in find_all(node, "symbol"):
        if e[1].startswith(old + "_"):
            e[1] = Q(new + e[1][len(old):])


def lib_symbol(lib_id: str) -> list:
    """The library symbol ``lib:name`` as embedded in a schematic, with ``extends`` flattened."""
    lib, name = lib_id.split(":")
    syms = {e[1]: e for e in find_all(_lib(lib), "symbol")}
    if name not in syms:
        raise KeyError(lib_id)
    sym = copy.deepcopy(syms[name])
    ext = find(sym, "extends")
    if ext is not None:
        parent = lib_symbol(f"{lib}:{ext[1]}")
        _rename(parent, ext[1], name)
        props = {p[1]: p for p in find_all(sym, "property")}
        merged = [parent[0], Q(name)]
        for e in parent[2:]:
            if isinstance(e, list) and e[0] == "property" and e[1] in props:
                merged.append(props.pop(e[1]))
            else:
                merged.append(e)
        merged.extend(props.values())
        sym = merged
    sym[1] = Q(f"{lib}:{name}")
    return sym


def pins(sym: list, unit: int = 1, body_style: int = 1) -> dict[str, dict]:
    """Pins of one unit: number -> {name, type, x, y, rot} in library coordinates (y up)."""
    base = sym[1].split(":")[-1]
    out: dict[str, dict] = {}
    for sub in find_all(sym, "symbol"):
        m = re.fullmatch(re.escape(base) + r"_(\d+)_(\d+)", sub[1])
        if not m or int(m.group(1)) not in (0, unit) or int(m.group(2)) not in (0, body_style):
            continue
        for p in find_all(sub, "pin"):
            at = need(p, "at")
            out[need(p, "number")[1]] = {"name": need(p, "name")[1], "type": p[1], "x": float(at[1]),
                                         "y": float(at[2]), "rot": int(float(at[3])) if len(at) > 3 else 0}
    return out


def rotate(x: float, y: float, rot: int) -> tuple[float, float]:
    """Rotate a library vector counter-clockwise by ``rot`` degrees (library y up)."""
    a = math.radians(rot)
    return round(x * math.cos(a) - y * math.sin(a), 4), round(x * math.sin(a) + y * math.cos(a), 4)
