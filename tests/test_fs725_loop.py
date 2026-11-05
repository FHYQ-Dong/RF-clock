from __future__ import annotations

import numpy as np
import pytest

from rf_clock.checks.fs725_loop import measure
from rf_clock.fs725 import FS725
from rf_clock.twtt.steer import fit_loop


class LoopPRS10:
    """A PRS10 whose FC answers each change of SF through a frequency lock loop (k, lag,
    dead time), in simulated time: each query takes 10 ms, ``sleep`` advances the clock."""

    def __init__(self, k: float, lag_s: float, dead_s: float, fc_per_sf: float = 1.2) -> None:
        self.now = 0.0
        self.sf = 3
        self.changes: list[tuple[float, int]] = []
        self.fc_per_sf = fc_per_sf
        self.grid = np.arange(0, 120, 0.001)
        self.u = np.zeros_like(self.grid)
        fc = out = 0.0
        for i in range(1, self.grid.size):
            fc += k * ((1.0 if self.grid[i] > dead_s else 0.0) - out) * 0.001
            out += (fc - out) / lag_s * 0.001
            self.u[i] = fc
        self.replies: list[bytes] = []

    def fc(self) -> int:
        value = 1_675_618 + sum(c * self.fc_per_sf * np.interp(self.now - at, self.grid, self.u, left=0.0)
                                for at, c in self.changes)
        return int(round(value))

    def write(self, data: bytes) -> None:
        command = data.decode().strip("\r")
        self.now += 0.01
        if command == "SF?":
            self.replies.append(str(self.sf).encode())
        elif command == "FC?":
            v = self.fc()
            self.replies.append(f"{v // 1000},{v % 1000}".encode())
        elif command.startswith("SF"):
            self.changes.append((self.now, int(command[2:]) - self.sf))
            self.sf = int(command[2:])

    def read_line(self, timeout: float) -> bytes | None:
        return self.replies.pop(0) if self.replies else None

    def flush_input(self) -> None:
        self.replies.clear()

    def close(self) -> None:
        pass


def test_the_steps_give_back_the_loop():
    prs = LoopPRS10(1.5, 1.65, 0.15)
    unit = FS725(prs)

    def sleep(dt: float) -> None:
        prs.now += dt

    reads, commands = measure(unit, sf0=3, step=100, steps=4, hold_s=40, fc_every=0.1,
                              clock=lambda: prs.now, sleep=sleep, say=lambda m: None)
    assert [c for _, c in commands] == [100, -100, 100, -100] and prs.sf == 3
    assert len(reads) == pytest.approx((20 + 4 * 40) / 0.1, rel=0.05)
    t = np.array([r[0] for r in reads])
    fc = np.array([r[1] for r in reads], dtype=float)
    found = fit_loop(t, fc, [at for at, _ in commands])
    assert (found["k_per_s"], found["lag_s"], found["dead_s"]) == pytest.approx((1.5, 1.65, 0.15), abs=0.051)
    assert found["fc_changes"] == pytest.approx([c * 1.2 for _, c in commands], rel=0.05)
    assert len(found["alone"]) == 4
