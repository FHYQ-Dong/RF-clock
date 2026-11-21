"""Steering through FC in the PL, P13 (docs/ABI.md 4.16), since 2.5.

P13 runs the predictive steering algorithm (``rf_clock.twtt.steer.PredictiveSteering``)
itself: it forms theta from the pairs P8 and P9 hand to the queue, takes each step at its
end tick, computes FC's new fine DAC value and has P12 send ``FC<high>,<low>`` to the FS725
``write_delay`` ticks after the step's end.  ``rf_clock.plmodel.steer`` defines every step to
the bit; this module loads its tables (``rf_clock.plmodel.steer.tables``), starts and stops
the engine and reads its state.  Each step's numbers come as ``SteerStep`` queue entries.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..plmodel import steer as pm
from . import abi
from .client import PLClient

REG = abi.ABI.REG
COEF = abi.ABI.COEF_TABLE


@dataclass(frozen=True)
class SteeringStatus:
    enabled: bool
    started: bool  # a step with theta came since ENABLE
    stopped: bool  # the wanted FC lay outside its limits too long
    settling: bool
    late: bool  # the last command started after its tick
    busy: bool
    index: int  # steps since the first with theta
    low: int  # FC's fine DAC value now
    last_change: int
    theta_ps: float  # the last step's theta (its prediction without pairs)
    missing: int  # steps without theta
    late_commands: int
    last_command_tick: int
    pairs: int
    saturated: int  # steps whose wanted FC lay outside the limits


class PLSteering:
    """``pl.steering``: load P13's tables, start and stop it, read its state."""

    def __init__(self, client: PLClient) -> None:
        self.client = client

    @property
    def sizes(self) -> tuple[int, int, int]:
        """(LMAX, NMAX, PMAX): the entries of the change, mean and AR tables in this build."""
        return (self.client.read32(REG.STEER_LMAX), self.client.read32(REG.STEER_NMAX),
                self.client.read32(REG.STEER_PMAX))

    def _coef(self, table: int, first: int, values: list[int]) -> None:
        self.client.write32(REG.STEER_COEF_ADDR, (table << 13) | first)
        for v in values:
            if not -(1 << 47) <= v < (1 << 47):
                raise ValueError(f"coefficient {v} of table {table} does not fit in 48 bits")
            self.client.write32(REG.STEER_COEF_HI, (v >> 32) & 0xFFFF)
            self.client.write32(REG.STEER_COEF_LO, v & 0xFFFF_FFFF)

    def load(self, t: pm.Tables, *, step_slots: int, late_slots: int, write_delay_ticks: int, prefix: bytes,
             steps: tuple[int, int] | None = None) -> None:
        """Load the tables and settings; the engine must be stopped (``disable``).  ``prefix``:
        the command's bytes before the fine DAC value, ``FC<high>,``."""
        lmax, nmax, pmax = self.sizes
        if not (t.L <= lmax - 2 and t.n <= nmax - 2 and t.p <= pmax):
            raise ValueError(f"tables of L {t.L}, n {t.n}, p {t.p} do not fit this build ({lmax}, {nmax}, {pmax})")
        if not 1 <= len(prefix) <= 8:
            raise ValueError("the command's prefix is 1 to 8 bytes")
        if self.client.read32(REG.STEER_CONTROL) >> abi.ABI.STEERCTL.ENABLE & 1:
            raise RuntimeError("P13 is running: disable it before loading")
        w = self.client.write32
        w(REG.STEER_STEP, int(step_slots) | int(late_slots) << 16)
        w(REG.STEER_WRITE_DELAY, int(write_delay_ticks))
        w(REG.STEER_L, t.L)
        w(REG.STEER_N, t.n)
        w(REG.STEER_P, t.p)
        w(REG.STEER_SETTLE, t.settle)
        w(REG.STEER_MAX_STEP, t.max_step)
        w(REG.STEER_FC_LIMITS, t.lo | t.hi << 16)
        w(REG.STEER_FC_START, t.low0)
        w(REG.STEER_MAX_SATURATED, t.max_saturated)
        size, hold = steps or (0, 1)
        w(REG.STEER_STEPS, int(size) | int(hold) << 16)
        word = int.from_bytes(bytes(prefix).ljust(8, b"\0"), "little")
        w(REG.STEER_PREFIX_LO, word & 0xFFFF_FFFF)
        w(REG.STEER_PREFIX_HI, word >> 32)
        w(REG.STEER_PREFIX_LEN, len(prefix))
        self._coef(COEF.H, 1, t.h[1:t.L + 1])
        self._coef(COEF.K, 1, t.K[1:t.L + 1])
        self._coef(COEF.ALPHA, 0, t.alpha)
        self._coef(COEF.AR, 0, t.ar)
        self._coef(COEF.GAMMA, 1, t.gamma[1:t.n + 1])
        self._coef(COEF.MEAN2, 1, t.mean2[1:t.n + 1])
        self._coef(COEF.SCALAR, 0, [t.rho, t.k_inf, t.r0, t.target_q16])

    def enable(self, *, role_b: bool = True, steps: bool = False, log: bool = True) -> None:
        """Start afresh: from the next step with theta, FC from STEER_FC_START."""
        c = abi.ABI.STEERCTL
        bits = (int(bool(role_b)) << c.ROLE_B) | (int(bool(steps)) << c.STEPS) | (int(bool(log)) << c.LOG)
        self.client.write32(REG.STEER_CONTROL, bits)
        self.client.write32(REG.STEER_CONTROL, bits | 1 << c.ENABLE)

    def disable(self) -> None:
        bits = self.client.read32(REG.STEER_CONTROL) & ~(1 << abi.ABI.STEERCTL.ENABLE)
        self.client.write32(REG.STEER_CONTROL, bits)

    def status(self) -> SteeringStatus:
        r = self.client.read32
        s = r(REG.STEER_STATUS)
        b = abi.ABI.STEERST
        fc = r(REG.STEER_FC)
        lo = r(REG.STEER_THETA_LO)
        theta = (r(REG.STEER_THETA_HI) << 32) | lo
        theta -= (1 << 64) if theta >> 63 else 0
        clo = r(REG.STEER_CMD_TICK_LO)
        change = fc >> 16
        return SteeringStatus(
            enabled=bool(s >> b.ENABLED & 1), started=bool(s >> b.STARTED & 1), stopped=bool(s >> b.STOPPED & 1),
            settling=bool(s >> b.SETTLING & 1), late=bool(s >> b.LATE & 1), busy=bool(s >> b.BUSY & 1),
            index=r(REG.STEER_INDEX), low=fc & 0xFFF, last_change=change - 0x10000 if change & 0x8000 else change,
            theta_ps=theta / 65536 / 1000, missing=r(REG.STEER_MISSING_CNT), late_commands=r(REG.STEER_LATE_CNT),
            last_command_tick=(r(REG.STEER_CMD_TICK_HI) << 32) | clo, pairs=r(REG.STEER_PAIRS_CNT),
            saturated=r(REG.STEER_SATURATED_CNT))
