"""Finish the connections Freerouting leaves open, with a plain grid router on F.Cu and B.Cu (KiCad's Python).

For each net whose copper is still in more than one piece, it joins the pieces one at a time along the cheapest
grid path that keeps the net class's track width and clearance from the copper of every other net, changing layer
through a via where it must. Freerouting 2.5.0's native CLI can only run a single pass (see pcblib.freeroute), and
a single pass leaves a few connections open on these boards.
"""

from __future__ import annotations

import heapq
import math

import numpy as np
import pcbnew

from pcblib import OX, OY, mm, pt

LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)
G = 0.1  # grid pitch, mm (fine enough to leave 0.5 mm pitch pins along their axis)
VIA, DRILL = 0.6, 0.3
# Optional: a function of a net's name that returns rectangles (x0, y0, x1, y1, mm) the net's copper must stay out
# of, e.g. an isolated area that only its own nets may enter (set by the board's generator).
KEEP_OUT = None


def _mm(v) -> float:
    return pcbnew.ToMM(v)


class Copper:
    """Every pad, track and via of the board, by net and layer, in mm from the board's corner."""

    def __init__(self, board: pcbnew.BOARD):
        self.items = []  # (net code, layer set, kind, geometry)
        for fp in board.GetFootprints():
            for p in fp.Pads():
                bb = p.GetBoundingBox()
                layers = {L for L in LAYERS if p.IsOnLayer(L)}
                geo = (_mm(bb.GetLeft()) - OX, _mm(bb.GetTop()) - OY, _mm(bb.GetRight()) - OX, _mm(bb.GetBottom()) - OY)
                self.items.append((p.GetNetCode(), layers, "rect", geo))
                if p.GetDrillSize().x > 0:
                    self.items.append((-1, set(LAYERS), "hole", (_mm(p.GetPosition().x) - OX, _mm(p.GetPosition().y) - OY,
                                                                 _mm(max(p.GetDrillSize().x, p.GetDrillSize().y)) / 2)))
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                c = (_mm(t.GetPosition().x) - OX, _mm(t.GetPosition().y) - OY)
                self.items.append((t.GetNetCode(), set(LAYERS), "seg", (*c, *c, _mm(t.GetWidth(pcbnew.F_Cu)) / 2)))
                self.items.append((-1, set(LAYERS), "hole", (*c, _mm(t.GetDrillValue()) / 2)))
            elif t.GetLayer() in LAYERS:
                self.items.append((t.GetNetCode(), {t.GetLayer()}, "seg",
                                   (_mm(t.GetStart().x) - OX, _mm(t.GetStart().y) - OY, _mm(t.GetEnd().x) - OX,
                                    _mm(t.GetEnd().y) - OY, _mm(t.GetWidth()) / 2)))


def _dist_rect(X, Y, x0, y0, x1, y1):
    dx = np.maximum(np.maximum(x0 - X, 0), X - x1)
    dy = np.maximum(np.maximum(y0 - Y, 0), Y - y1)
    return np.hypot(dx, dy)


def _dist_seg(X, Y, x0, y0, x1, y1):
    vx, vy = x1 - x0, y1 - y0
    L = vx * vx + vy * vy
    t = np.zeros_like(X) if L == 0 else np.clip(((X - x0) * vx + (Y - y0) * vy) / L, 0, 1)
    return np.hypot(X - (x0 + t * vx), Y - (y0 + t * vy))


class Grid:
    def __init__(self, w: float, h: float):
        self.nx, self.ny = int(w / G) + 1, int(h / G) + 1
        self.xs = np.arange(self.nx) * G
        self.ys = np.arange(self.ny) * G

    def window(self, x0, y0, x1, y1):
        i0, i1 = max(int(x0 / G) - 1, 0), min(int(x1 / G) + 2, self.nx)
        j0, j1 = max(int(y0 / G) - 1, 0), min(int(y1 / G) + 2, self.ny)
        X, Y = np.meshgrid(self.xs[i0:i1], self.ys[j0:j1])
        return (slice(j0, j1), slice(i0, i1)), X, Y


def blocked(cu: Copper, grid: Grid, net: int, hw: float, clear: float, w: float, h: float, edge: float = 0.3,
            net_name: str = ""):
    """Per layer, the cells where a track of half width hw would come closer than ``clear`` to another net's
    copper; and the cells where a via cannot go (also keeping 0.5 mm between holes)."""
    blk = {L: np.zeros((grid.ny, grid.nx), bool) for L in LAYERS}
    via = np.zeros((grid.ny, grid.nx), bool)
    X, Y = np.meshgrid(grid.xs, grid.ys)
    for arr, m in ((blk[LAYERS[0]], hw), (blk[LAYERS[1]], hw), (via, VIA / 2)):
        arr |= (X < edge + m) | (Y < edge + m) | (X > w - edge - m) | (Y > h - edge - m)
        for x0, y0, x1, y1 in (KEEP_OUT(net_name) if KEEP_OUT else []):
            arr |= _dist_rect(X, Y, x0, y0, x1, y1) < m
    for code, layers, kind, geo in cu.items:
        if kind == "hole":
            x, y, r = geo
            sl, Xw, Yw = grid.window(x - r - 1.0, y - r - 1.0, x + r + 1.0, y + r + 1.0)
            via[sl] |= np.hypot(Xw - x, Yw - y) < r + DRILL / 2 + 0.5
            continue
        if code == net and code > 0:
            continue
        reach = max(hw, VIA / 2) + clear
        if kind == "rect":
            x0, y0, x1, y1 = geo
            sl, Xw, Yw = grid.window(x0 - reach, y0 - reach, x1 + reach, y1 + reach)
            d = _dist_rect(Xw, Yw, x0, y0, x1, y1)
        else:
            x0, y0, x1, y1, r = geo
            sl, Xw, Yw = grid.window(min(x0, x1) - r - reach, min(y0, y1) - r - reach, max(x0, x1) + r + reach,
                                     max(y0, y1) + r + reach)
            d = _dist_seg(Xw, Yw, x0, y0, x1, y1) - r
        for L in layers:
            blk[L][sl] |= d < hw + clear
        via[sl] |= d < VIA / 2 + clear
    return blk, via


def pieces(cu: Copper, net: int) -> list[list[tuple]]:
    """The net's copper split into connected pieces; each piece is a list of (layer, x, y) points on it."""
    items = [it for it in cu.items if it[0] == net and it[2] != "hole"]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def pts(it):
        _, layers, kind, geo = it
        if kind == "rect":
            x0, y0, x1, y1 = geo
            return [(L, (x0 + x1) / 2, (y0 + y1) / 2) for L in layers]
        x0, y0, x1, y1, _ = geo
        n = max(int(math.hypot(x1 - x0, y1 - y0) / G), 1)
        return [(L, x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for L in layers for k in range(n + 1)]

    def touch(a, b):
        la, lb = a[1] & b[1], None
        if not la:
            return False
        for L, x, y in pts(a):
            if L not in b[1]:
                continue
            if b[2] == "rect":
                x0, y0, x1, y1 = b[3]
                if x0 - 1e-3 <= x <= x1 + 1e-3 and y0 - 1e-3 <= y <= y1 + 1e-3:
                    return True
            else:
                x0, y0, x1, y1, r = b[3]
                if _dist_seg(np.array(x), np.array(y), x0, y0, x1, y1) <= r + 1e-3:
                    return True
        return lb
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if find(i) != find(j) and (touch(items[i], items[j]) or touch(items[j], items[i])):
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).extend(pts(it))
    return list(groups.values())


def route(board: pcbnew.BOARD, net_name: str, width: float, clear: float, w: float, h: float,
          via_cost: float = 30.0) -> int:
    """Join the pieces of one net. Returns how many joins failed."""
    net = board.FindNet(net_name).GetNetCode()
    failed = 0
    while True:
        cu = Copper(board)
        parts = pieces(cu, net)
        if len(parts) < 2:
            return failed
        grid = Grid(w, h)
        blk, via = blocked(cu, grid, net, width / 2, clear, w, h, net_name=net_name)
        src = {(LAYERS.index(L), int(round(y / G)), int(round(x / G))): (x, y) for L, x, y in parts[0]}
        dst = {(LAYERS.index(L), int(round(y / G)), int(round(x / G))): (x, y) for p in parts[1:] for L, x, y in p}
        path = _search(blk, via, set(src), set(dst), via_cost)
        if path is None:
            failed += 1
            return failed
        _lay(board, path, net_name, width, src[path[0]], dst[path[-1]])


def _search(blk, via, src, dst, via_cost):
    layers = [blk[LAYERS[0]], blk[LAYERS[1]]]
    ny, nx = layers[0].shape
    dist = {}
    prev = {}
    heap = []
    dj0, dj1 = min(c[1] for c in dst), max(c[1] for c in dst)
    di0, di1 = min(c[2] for c in dst), max(c[2] for c in dst)

    def h(j, i):  # distance to the targets' bounding box, never more than the real remaining cost
        return math.hypot(max(dj0 - j, 0, j - dj1), max(di0 - i, 0, i - di1))
    for s in src:
        dist[s] = 0.0
        heapq.heappush(heap, (h(s[1], s[2]), s))
    steps = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.4142), (1, -1, 1.4142),
             (-1, 1, 1.4142), (-1, -1, 1.4142)]
    cells = list(src) + list(dst)
    jlo, jhi = min(c[1] for c in cells) - 150, max(c[1] for c in cells) + 150  # 15 mm around the ends
    ilo, ihi = min(c[2] for c in cells) - 150, max(c[2] for c in cells) + 150
    pops = 0
    while heap:
        pops += 1
        if pops > 4_000_000:  # no path worth finding
            return None
        f, (L, j, i) = heapq.heappop(heap)
        d = dist.get((L, j, i), 1e18)
        if f - h(j, i) > d + 1e-9:
            continue
        if (L, j, i) in dst:
            path = [(L, j, i)]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        nbrs = []
        for dj, di, c in steps:
            jj, ii = j + dj, i + di
            if max(0, jlo) <= jj < min(ny, jhi) and max(0, ilo) <= ii < min(nx, ihi) and                     (not layers[L][jj, ii] or (L, jj, ii) in dst):
                nbrs.append(((L, jj, ii), c))
        if not via[j, i]:
            nbrs.append(((1 - L, j, i), via_cost))
        for n, c in nbrs:
            nd = d + c
            if nd < dist.get(n, 1e18):
                dist[n] = nd
                prev[n] = (L, j, i)
                heapq.heappush(heap, (nd + h(n[1], n[2]), n))
    return None


def _lay(board: pcbnew.BOARD, path, net_name: str, width: float, start, end) -> None:
    """Tracks along the grid path, starting and ending exactly on the copper it joins."""
    net = board.FindNet(net_name)
    pts = [(L, i * G, j * G) for L, j, i in path]
    pts = [(pts[0][0], *start)] + pts + [(pts[-1][0], *end)]
    run = [pts[0]]

    def flush(run):
        keep = [run[0]]
        for k in range(1, len(run) - 1):
            ax, ay = run[k][1] - keep[-1][1], run[k][2] - keep[-1][2]
            bx, by = run[k + 1][1] - run[k][1], run[k + 1][2] - run[k][2]
            if abs(ax * by - ay * bx) > 1e-9:
                keep.append(run[k])
        keep.append(run[-1])
        for a, b in zip(keep, keep[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pt(a[1], a[2]))
            t.SetEnd(pt(b[1], b[2]))
            t.SetWidth(mm(width))
            t.SetLayer(LAYERS[a[0]])
            t.SetNet(net)
            board.Add(t)
    for p in pts[1:]:
        if p[0] != run[-1][0]:
            if len(run) > 1:
                flush(run)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pt(p[1], p[2]))
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetWidth(mm(VIA))
            v.SetDrill(mm(DRILL))
            v.SetNet(net)
            board.Add(v)
            run = [p]
        else:
            run.append(p)
    if len(run) > 1:
        flush(run)


def connect(board: pcbnew.BOARD, net_name: str, width: float, clear: float, start: tuple, goal: tuple,
            w: float, h: float) -> bool:
    """A grid path for one net from start (layer, x, y) to goal (layer, x, y), e.g. from a pad to a via."""
    net = board.FindNet(net_name).GetNetCode()
    cu = Copper(board)
    grid = Grid(w, h)
    blk, via = blocked(cu, grid, net, width / 2, clear, w, h, net_name=net_name)
    s = (LAYERS.index(start[0]), int(round(start[2] / G)), int(round(start[1] / G)))
    g = (LAYERS.index(goal[0]), int(round(goal[2] / G)), int(round(goal[1] / G)))
    path = _search(blk, np.ones_like(via), {s}, {g}, 1e9)
    if path is None:
        return False
    _lay(board, path, net_name, width, start[1:], goal[1:])
    return True
