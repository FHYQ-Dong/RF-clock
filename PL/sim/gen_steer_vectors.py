"""Test vectors for tb_p13_steer: pairs, the registers and coefficient tables, and every step's
queue entry and command, from rf_clock.plmodel.steer.

The testbench runs P13 with a short second (TB_SLOTS slots of TB_SLOT_TICKS ticks) and small
tables, so that a few hundred steps take a few million cycles.  Two runs: the algorithm, closed
in Python through a crystal that follows FC with a lag, until the wanted FC stays outside its
limits long enough to stop; then, after ENABLE 0 -> 1, FC steps on a schedule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from rf_clock.plmodel import steer as pm
from rf_clock.twtt import steer

TB_SLOTS = 20
TB_SLOT_TICKS = 2500
TB_STEP_SLOTS = 4
TB_LATE = 2
TB_TX_OFFSET = 300
TB_WRITE_DELAY = 1200
FS_TICK = pm.FS_PER_TICK
PREFIX = b"FC1675,"
PLANT = {"lag_s": 0.994, "dead_s": 0.0235, "ps_per_step": 0.849}


def hexval(value: int, bits: int) -> str:
    return format(int(value) & ((1 << bits) - 1), f"0{(bits + 3) // 4}x")


def law():
    lw = steer.predictive_fc(-21.5, 1_675_000 + 2000, PLANT, late_s=0.05, step_s=0.1,
                             lag_s=PLANT["lag_s"] * 1.1, dead_s=PLANT["dead_s"] + 0.02,
                             ar=steer.AR40_STEP_01[:8], mean_window=8.0, settle_s=6.0, max_saturated_s=6.0,
                             max_step=60)
    return lw


def pairs_for(rng, sec: int, j: int, theta_fs: float, kind: str) -> list[tuple]:
    """The pairs of step j of second sec (kind: normal, missing, early, unlocked, nomeas)."""
    out = []
    late0 = j * TB_STEP_SLOTS + TB_STEP_SLOTS - TB_LATE
    slots = {"normal": [late0, late0 + 1], "missing": [], "early": [j * TB_STEP_SLOTS],
             "unlocked": [late0, late0 + 1], "nomeas": [late0, late0 + 1], "one": [late0 + 1]}[kind]
    for i, slot in enumerate(slots):
        two = int(round(2 * theta_fs + rng.normal(0, 400.0)))
        m_out = 292_000_000 + int(rng.integers(-5000, 5000))
        m_in = m_out + two
        tx_prev = (((slot - 1) % TB_SLOTS) * TB_SLOT_TICKS + TB_TX_OFFSET) * FS_TICK
        peer_trx = m_out + tx_prev
        peer_ttx = (slot * TB_SLOT_TICKS + 7000) * FS_TICK + int(rng.integers(0, 10_000_000))
        t_rx = m_in + peer_ttx
        locked = not (kind == "unlocked" and i == 0)
        meas = not (kind == "nomeas" and i == 1)
        out.append((sec, slot, int(locked), t_rx, int(meas), peer_ttx, peer_trx))
    return out


def main(out: Path) -> None:
    rng = np.random.default_rng(20261116)
    t = pm.tables(law(), table_s=6.0, low0=2000, lo=2000 - 400, hi=2000 + 400)
    steps_per_sec = TB_SLOTS // TB_STEP_SLOTS
    n_steps = 520  # the first run
    n_steps2 = 60  # the second run (FC steps)
    plant = steer.LoopModel.crystal(1.0, 0.03, 0.849 * 1.05, window=(0.05, 0.1), command_s=0.03, step_s=0.1)
    # the clocks: a wandering pair, offset 2 ns at the start
    f = np.zeros(n_steps + n_steps2 + 10)
    for i in range(2, f.size):
        f[i] = 2 * 0.99 * np.cos(2 * np.pi * 0.1 / 6) * f[i - 1] - 0.99 ** 2 * f[i - 2] + rng.normal(0, 1.0)
    free = np.cumsum((-66.0 + f) * 0.1) + 300.0  # ps
    kinds = []
    for k in range(n_steps + n_steps2):
        r = rng.random()
        kinds.append("missing" if r < 0.04 else "early" if r < 0.06 else "unlocked" if r < 0.09
                     else "nomeas" if r < 0.12 else "one" if r < 0.2 else "normal")
    kinds[0] = "missing"  # the engine starts at the first step with theta
    kinds[1] = "missing"
    # run 1: steps 0 .. n_steps-1 are steps of seconds 1, 2, ...; the engine is enabled before second 1
    eng = pm.Engine(t)
    asked, changes, low = [], [], t.low0
    pairs, expect, cmds = [], [], []
    push_off = 0.0  # from step 420 on theta runs off, so that the wanted FC leaves its limits and steering stops
    for k in range(n_steps):
        sec, j = 1 + k // steps_per_sec, k % steps_per_sec
        if k >= 420:
            push_off += 60.0
        th_ps = free[k] + push_off + (float(np.dot(changes, plant.answer(k - np.asarray(asked)))) if asked else 0.0)
        ps = pairs_for(rng, sec, j, th_ps * 1000, kinds[k])
        pairs += ps
        late = [p for p in ps if p[1] % TB_STEP_SLOTS >= TB_STEP_SLOTS - TB_LATE and p[2] and p[4]]
        if late:
            s2 = sum(pm.pair_2theta_fs(True, p[1], p[3], p[5], p[6], slot_ticks=TB_SLOT_TICKS, slots=TB_SLOTS,
                                       tx_offset=TB_TX_OFFSET) for p in late)
            theta = pm.step_theta_q16(s2, len(late))
        else:
            theta = None
        before_started = eng.index == 0
        out_ = eng.step(theta)
        if before_started and theta is None:
            continue
        flags = (int(out_.get("missing", False)) | int(out_.get("settling", False)) << 1
                 | int(out_.get("saturated", False)) << 2 | int(bool(out_.get("stopped"))) << 3
                 | int(before_started) << 4)
        dF = 0 if before_started or out_.get("dF_q16") is None else out_["dF_q16"]
        S = out_.get("S_q56") or 0
        expect.append((flags, sec, j | (len(late) << 16), out_["index"], eng.theta_prev, dF, S >> 32,
                       ((out_["change"] & 0xFFFF) << 16) | eng.low, eng.rem >> 32, eng.s0))
        if not out_.get("stopped"):
            digits = f"{eng.low:04d}".encode()
            cmd = PREFIX + digits + b"\r"
            end_tick = sec * TB_SLOTS * TB_SLOT_TICKS + (j + 1) * TB_STEP_SLOTS * TB_SLOT_TICKS
            cmds.append((end_tick + TB_WRITE_DELAY, cmd))
        if out_["change"]:
            asked.append(k)
            changes.append(out_["change"])
        if out_.get("stopped"):
            break
    stop_k = k
    # run 2: ENABLE 0 -> 1 during second 2 + stop_sec, STEPS, from the next second's first step
    sec0 = 3 + stop_k // steps_per_sec
    eng2 = pm.Engine(t, steps=(150, 7))
    for i in range(n_steps2):
        k = stop_k + 1 + i
        sec, j = sec0 + i // steps_per_sec, i % steps_per_sec
        ps = pairs_for(rng, sec, j, free[k] * 1000, kinds[k] if i else "normal")
        pairs += ps
        late = [p for p in ps if p[1] % TB_STEP_SLOTS >= TB_STEP_SLOTS - TB_LATE and p[2] and p[4]]
        theta = (pm.step_theta_q16(sum(pm.pair_2theta_fs(True, p[1], p[3], p[5], p[6], slot_ticks=TB_SLOT_TICKS,
                                                       slots=TB_SLOTS, tx_offset=TB_TX_OFFSET) for p in late), len(late))
                 if late else None)
        before_started = eng2.index == 0
        out_ = eng2.step(theta)
        if before_started and theta is None:
            continue
        flags = (int(out_.get("missing", False)) | int(out_.get("settling", False)) << 1
                 | int(before_started) << 4 | 1 << 6)
        expect.append((flags, sec, j | (len(late) << 16), out_["index"], eng2.theta_prev, 0, 0,
                       ((out_["change"] & 0xFFFF) << 16) | eng2.low, eng2.rem >> 32, eng2.s0))
        digits = f"{eng2.low:04d}".encode()
        end_tick = sec * TB_SLOTS * TB_SLOT_TICKS + (j + 1) * TB_STEP_SLOTS * TB_SLOT_TICKS
        cmds.append((end_tick + TB_WRITE_DELAY, PREFIX + digits + b"\r"))
    for jj in range(j + 1, steps_per_sec):  # the rest of the last second: steps without pairs
        out_ = eng2.step(None)
        expect.append((1 | 1 << 6, sec, jj, out_["index"], eng2.theta_prev, 0, 0,
                       ((out_["change"] & 0xFFFF) << 16) | eng2.low, eng2.rem >> 32, eng2.s0))
        end_tick = sec * TB_SLOTS * TB_SLOT_TICKS + (jj + 1) * TB_STEP_SLOTS * TB_SLOT_TICKS
        cmds.append((end_tick + TB_WRITE_DELAY, PREFIX + f"{eng2.low:04d}".encode() + b"\r"))
    last_sec = sec + 1  # the testbench ends 2000 ticks into this second
    # registers (address, value) written before second 1, and the coefficients
    prefix = int.from_bytes(PREFIX.ljust(8, b"\0"), "little")
    regs = [(0x448, TB_STEP_SLOTS | TB_LATE << 16), (0x44C, TB_WRITE_DELAY), (0x450, t.L), (0x454, t.n),
            (0x458, t.p), (0x45C, t.settle), (0x460, t.max_step), (0x464, t.lo | t.hi << 16), (0x468, t.low0),
            (0x46C, t.max_saturated), (0x470, 150 | 7 << 16), (0x474, prefix & 0xFFFFFFFF),
            (0x478, prefix >> 32), (0x47C, len(PREFIX))]
    coefs = []
    for table, values, first in ((0, t.h, 1), (1, t.K, 1), (2, t.alpha, 0), (3, t.ar, 0), (4, t.gamma, 1),
                                 (5, t.mean2, 1), (6, [t.rho, t.k_inf, t.r0, t.target_q16], 0)):
        for i, v in enumerate(values[first:]):
            assert -(1 << 47) <= v < (1 << 47), (table, i, v)
            coefs.append((table, first + i, v))
    lines = [hexval(a, 12) + hexval(v, 32) for a, v in regs]
    (out / "st_regs.hex").write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    (out / "st_coefs.hex").write_text("\n".join(hexval(tb, 4) + hexval(i, 16) + hexval(v, 48) for tb, i, v in coefs)
                                      + "\n", encoding="ascii", newline="\n")
    (out / "st_pairs.hex").write_text("\n".join(hexval(s_, 32) + hexval(sl, 16) + hexval(lk, 4) + hexval(me, 4)
                                                + hexval(a, 64) + hexval(b, 64) + hexval(c, 64)
                                                for s_, sl, lk, a, me, b, c in pairs) + "\n",
                                      encoding="ascii", newline="\n")
    (out / "st_expect.hex").write_text("\n".join(
        hexval(fl, 8) + hexval(sec, 32) + hexval(sj, 32) + hexval(ix, 32) + hexval(th, 64) + hexval(df, 64)
        + hexval(s, 64) + hexval(fc, 32) + hexval(rm, 32) + hexval(s0, 32)
        for fl, sec, sj, ix, th, df, s, fc, rm, s0 in expect) + "\n", encoding="ascii", newline="\n")
    (out / "st_cmds.hex").write_text("\n".join(hexval(tk, 64) + hexval(len(c), 8) + c[::-1].hex().rjust(32, "0")
                                               for tk, c in cmds) + "\n", encoding="ascii", newline="\n")
    (out / "st_cfg.hex").write_text("\n".join(hexval(v, 32) for v in (len(regs), len(coefs), len(pairs), len(expect),
                                                                      len(cmds), last_sec, sec0)) + "\n",
                                    encoding="ascii", newline="\n")
    print(f"steer vectors: {len(pairs)} pairs, {len(expect)} steps (stopped at step {stop_k}), {len(cmds)} commands, "
          f"{len(coefs)} coefficients, last second {last_sec}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
