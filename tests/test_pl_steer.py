"""P12 and P13 (rf-clock 2.5): the integer model of the steering in the PL against the float
algorithm, and the drivers of the serial line and the steering engine against fake registers."""

from __future__ import annotations

import numpy as np
import pytest

from rf_clock.fs725 import FS725
from rf_clock.pl import abi
from rf_clock.pl.measurements import SteerStep, decode_entry
from rf_clock.pl.serial import PLPort, SerialLine
from rf_clock.pl.steering import PLSteering
from rf_clock.plmodel import steer as pm
from rf_clock.twtt import pairs, steer

REG = abi.ABI.REG
PLANT = {"lag_s": 0.994, "dead_s": 0.0235, "ps_per_step": 0.849}


def law(**kw):
    return steer.predictive_fc(-21.5, 1_677_000, PLANT, late_s=0.05, step_s=0.1, **kw)


def wandering(steps: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    f = np.zeros(steps)
    for i in range(2, steps):
        f[i] = 2 * 0.99 * np.cos(2 * np.pi * 0.1 / 6) * f[i - 1] - 0.99 ** 2 * f[i - 2] + rng.normal(0, 1.0)
    return np.cumsum((-66.0 + f) * 0.1) + 300.0


def test_a_pair_is_twice_the_pairs_module_theta():
    timing = pairs.SlotTiming(500, 200_000, 1000)
    rng = np.random.default_rng(1)
    for _ in range(50):
        slot = int(rng.integers(0, 500))
        t_rx, ttx, ptrx = (int(x) for x in rng.integers(0, 10 ** 15, 3))
        p = pairs.pair(timing, False, 7, slot, t_rx, ttx, ptrx)
        two = pm.pair_2theta_fs(True, slot, t_rx, ttx, ptrx, slot_ticks=200_000, slots=500, tx_offset=1000)
        assert two == round(2 * p.theta_ns * 1e6)


def test_a_steps_theta_rounds_half_up():
    assert pm.step_theta_q16(3, 2) == (3 * 65536 + 2) // 4  # 0.75 fs
    assert pm.step_theta_q16(-1, 1) == -32768  # -0.5 fs exactly
    assert pm.step_theta_q16(1, 3 * 65536) == 0  # 1/6 of 2^-16 fs rounds to 0


def test_the_tables_fit_the_pl():
    t = pm.tables(law(), low0=2000, lo=1100, hi=2900)
    assert (t.L, t.n, t.p, t.settle) == (400, 600, 40, 300)
    for values in (t.h, t.K, t.alpha, t.ar, t.gamma, t.mean2, [t.rho, t.k_inf, t.r0, t.target_q16]):
        assert max(abs(v) for v in values) < 1 << 47
    assert t.K[t.L] == pytest.approx(t.k_inf, rel=1e-9)  # K is flat by the table's end
    assert t.h[t.L] == pytest.approx(t.rho, rel=1e-9)
    # 0.02 s steps (2.5.0.1): 40 s of changes and the 60 s mean in 2000 and 3000 entries, within 4094
    fast = steer.predictive_fc(-21.5, 1_677_000, PLANT, late_s=0.01, step_s=0.02, command_s=0.0002)
    t = pm.tables(fast, low0=2000, lo=1500, hi=2500)
    assert (t.L, t.n, t.p) == (2000, 3000, 40) and max(t.L, t.n) <= 4096 - 2
    for values in (t.h, t.K, t.alpha, t.ar, t.gamma, t.mean2, [t.rho, t.k_inf, t.r0, t.target_q16]):
        assert max(abs(v) for v in values) < 1 << 47


def test_the_pl_steers_as_the_float_algorithm():
    # each in its own closed loop through a crystal 5 % harder and 30 ms later than the model
    free = wandering(2500, 3)
    plant = steer.LoopModel.crystal(1.0, 0.03, 0.849 * 1.05, window=(0.05, 0.1), command_s=0.03, step_s=0.1)
    out = {}
    for name in ("float", "pl"):
        flt, eng = law(), pm.Engine(pm.tables(law(), low0=2000, lo=1000, hi=3000))
        asked, changes, th_hist, low = [], [], [], 2000
        for k, fk in enumerate(free):
            th = fk + (float(np.dot(changes, plant.answer(k - np.asarray(asked)))) if asked else 0.0)
            th_hist.append(th)
            q = int(round(th * 1000 * 65536))
            if name == "float":
                rec = flt.step(k, q / 65_536_000)
                new = low if rec["sf"] is None else rec["sf"] - 1_675_000
            else:
                new = eng.step(q)["low"]
            if new != low:
                asked.append(k)
                changes.append(new - low)
                low = new
        out[name] = float(np.sqrt(np.mean((np.array(th_hist[1200:]) + 21.5) ** 2)))
    assert out["pl"] == pytest.approx(out["float"], rel=0.01)
    assert out["pl"] < 5


def test_a_step_without_theta_changes_nothing_and_fc_steps_follow_their_schedule():
    t = pm.tables(law(), low0=2000, lo=1000, hi=3000)
    e = pm.Engine(t)
    assert e.step(None)["low"] == 2000 and e.index == 0  # not started without theta
    e.step(0)
    out = e.step(None)
    assert out["missing"] and out["change"] == 0
    s = pm.Engine(t, steps=(150, 3))
    lows = [s.step(0)["low"] for _ in range(t.settle + 13)]
    assert lows[t.settle - 1] == 2000
    assert lows[t.settle:t.settle + 12] == [2150] * 3 + [2000] * 3 + [1850] * 3 + [2000] * 3


class FakeRegs:
    """The P12 and P13 registers as far as the drivers use them; P12 answers each line
    through ``reply``."""

    def __init__(self, reply=lambda line: b"") -> None:
        self.reply = reply
        self.line = bytearray()
        self.rx = bytearray()
        self.lines: list[bytes] = []
        self.writes: list[tuple[int, int]] = []
        self.regs = {REG.SERIAL_CONFIG: 10417, REG.STEER_CONTROL: 0b1010, REG.STEER_LMAX: 4096,
                     REG.STEER_NMAX: 4096, REG.STEER_PMAX: 64}

    def read32(self, offset: int) -> int:
        if offset == REG.SERIAL_RX:
            if not self.rx:
                return 0
            b = self.rx.pop(0)
            return b | 1 << 8 | len(self.rx) << 16
        if offset == REG.SERIAL_STATUS:
            return len(self.line) << 8
        return self.regs.get(offset, 0)

    def write32(self, offset: int, value: int) -> None:
        self.writes.append((offset, value))
        if offset == REG.SERIAL_LINE:
            assert len(self.line) < 32
            self.line.append(value & 0xFF)
        elif offset == REG.SERIAL_CONTROL:
            if value & 1:
                self.lines.append(bytes(self.line))
                self.rx += self.reply(bytes(self.line))
                self.line.clear()
            if value & 4:
                self.rx.clear()
        else:
            self.regs[offset] = value


def test_the_fs725_works_through_the_pls_line():
    answers = {b"GA?\r": b"1\r", b"FC?\r": b"1675,2048\r", b"ID?\r": b"PRS10_3.56_SN_107129\r"}
    regs = FakeRegs(lambda line: answers.get(line, b""))
    unit = FS725(PLPort(SerialLine(regs)))  # type: ignore[arg-type]
    assert regs.regs[REG.SERIAL_CONFIG] >> 16 & 1  # the port enabled the line
    assert unit.gain() == 1 and unit.frequency_control() == (1675, 2048)
    assert unit.serial() == "107129"
    long = bytes(range(65, 65 + 40))
    SerialLine(regs).send(long)  # type: ignore[arg-type]
    assert regs.lines[-2:] == [long[:32], long[32:]]


def test_the_steering_loads_its_tables_and_starts_afresh():
    regs = FakeRegs()
    t = pm.tables(law(), low0=2000, lo=1100, hi=2900)
    pls = PLSteering(regs)  # type: ignore[arg-type]
    pls.load(t, step_slots=50, late_slots=25, write_delay_ticks=20000, prefix=b"FC1675,")
    w = dict(regs.writes)
    assert w[REG.STEER_STEP] == 50 | 25 << 16 and w[REG.STEER_L] == 400 and w[REG.STEER_FC_START] == 2000
    assert w[REG.STEER_FC_LIMITS] == 1100 | 2900 << 16 and w[REG.STEER_PREFIX_LEN] == 7
    coefs = [v for a, v in regs.writes if a == REG.STEER_COEF_LO]
    assert len(coefs) == 2 * t.L + 2 * t.p + 2 * t.n + 4
    pls.enable()
    assert [v for a, v in regs.writes if a == REG.STEER_CONTROL][-2:] == [0b1010, 0b1011]
    regs.regs[REG.STEER_CONTROL] = 0b1011
    with pytest.raises(RuntimeError):
        pls.load(t, step_slots=50, late_slots=25, write_delay_ticks=20000, prefix=b"FC1675,")


def test_a_steer_entry_decodes():
    w = [0] * 16
    w[0] = 3 | (1 << abi.ABI.QESF.SETTLING | 1 << abi.ABI.QESF.FIRST) << 8 | 77 << 16
    w[1], w[2] = 12, 3 | 13 << 16
    w[3], w[4] = 0x1234, 1
    w[5] = 0
    theta = -21_500 * 65536
    w[6], w[7] = theta & 0xFFFF_FFFF, (theta >> 32) & 0xFFFF_FFFF
    w[12] = 2048 | (0xFFFF & -5) << 16
    e = decode_entry(w)
    assert isinstance(e, SteerStep)
    assert e.settling and e.first and not e.missing and e.step == 3 and e.pairs == 13
    assert e.command_tick == (1 << 32) + 0x1234 and e.low == 2048 and e.change == -5
    assert e.theta_ps == pytest.approx(-21.5)
