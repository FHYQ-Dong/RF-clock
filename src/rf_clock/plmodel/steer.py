"""P13: the predictive steering algorithm through FC as the PL computes it, in integers.

The PL runs ``rf_clock.twtt.steer.PredictiveSteering`` (built by ``predictive_fc``) itself, one
step per ``STEER_STEP_SLOTS`` slots, and sends each step's FC to the FS725 at a fixed tick after
the step's end (P12).  With its settings fixed the algorithm is one linear filter followed by a
rounding and two clips, and the ARM loads the filter's coefficients (``tables``), as it loads the
RRC coefficients for P3.  This module defines the PL's arithmetic to the bit, so that the RTL
testbench compares with it step by step and the ARM can reproduce every step.

The filter.  Let theta_k be the step's theta, U_k the change of FC decided in step k (0 for none),
F_k = theta_k - own_k theta less B's own corrections, own_k = sum_d g(d) U_{k-d} the phase B's own
changes give theta (g: the algorithm's model of theta's response to one change, per step), and
dF_k = F_k - F_{k-1}.  The algorithm's correction is u = -S_k, rounded, with

    S_k = R0 (theta_k - target) + sum_{j<p} alpha_j dF_{k-j} + (gamma / n_e) W_k + sum_{d>=1} K(d) U_{k-d}

where W_k = F_k - F_{k-n_e} is the sum of the last n_e = min(n, k) values of dF, and

    dF_k = (theta_k - theta_{k-1}) - sum_{d>=1} h(d) U_{k-d},    h(d) = g(d) - g(d-1).

Only differences of F enter, so every quantity stays bounded while the clocks drift: the levels
of F and own grow without end, their differences do not.  R0 = sum_h r_h, alpha, gamma and K
follow from the first row r of the least-squares solution, the AR model and the model's response g
(``tables``).  Beyond the tables (d > L) h(d) is the constant rho and K(d) the constant K_inf, so
the changes older than L steps enter through their sum s0 alone.

A step without theta (no pair in its last ``late_slots`` slots) takes the algorithm's prediction
of theta less B's own corrections one step ahead, dF_k = sum_i ar_i dF_{k-1-i} + (1 - sum ar) / n_e W,
and changes nothing; its theta for the next difference is theta_{k-1} + dh_k + dF_k, dh_k the
own increment.  This is ``PredictiveSteering._next_free``.

Number formats (two's complement, every rounding half up: floor(x + 1/2)):

- theta, dF, W: 2^-16 fs (Q16), operands clamped to 48 bits (2 us);
- U, s0, FC: fine steps of FC; the PL keeps FC's fine DAC value ``low`` (FC = 1000 high + low);
- coefficients: alpha, gamma/n_e and R0 in FC steps per fs times 2^40, K and K_inf in FC steps per
  FC step times 2^40, h and rho in fs per FC step times 2^32, ar and mean2 (no unit) times 2^40;
- S and the remainder: 2^-56 FC steps; the products of fs data (Q16) with Q40 coefficients are
  Q56, those of FC data with Q40 coefficients are shifted left by 16.

The 16 fraction bits of the fs data matter: alpha is about 0.1 FC step per fs, so rounding dF to
2^-8 fs moved S by up to 0.003 FC step, enough to flip a rounding now and then.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

Q_THETA = 16  # theta, dF and W in 2^-16 fs
Q_COEF = 40  # alpha, gamma, R0, mean2, ar, K
Q_H = 32  # h and rho, fs per FC step
Q_S = 56  # S, wanted and the remainder
OPERAND_BITS = 48
S_LIMIT = 1 << (Q_S + 30)  # |S| below 2^30 FC steps
FS_PER_TICK = 10_000_000


def _clamp(v: int, bits: int = OPERAND_BITS) -> int:
    lim = 1 << (bits - 1)
    return max(-lim, min(lim - 1, v))


def _round_shift(v: int, shift: int) -> int:
    """floor(v / 2^shift + 1/2)."""
    return (v + (1 << (shift - 1))) >> shift if shift else v


def pair_2theta_fs(role_b: bool, slot: int, t_rx_fs: int, peer_t_tx_fs: int, peer_t_rx_fs: int, *,
                   slot_ticks: int, slots: int, tx_offset: int) -> int:
    """2 theta of one window, fs (``rf_clock.twtt.pairs.pair``): m_in = T_rx - peer T_tx,
    m_out = peer T_rx - this node's T_tx of the slot before; B: theta = (m_in - m_out) / 2."""
    m_in = t_rx_fs - peer_t_tx_fs
    m_out = peer_t_rx_fs - (((slot - 1) % slots) * slot_ticks + tx_offset) * FS_PER_TICK
    return (m_in - m_out) if role_b else (m_out - m_in)


def step_theta_q16(sum_2theta_fs: int, pairs: int) -> int:
    """The mean of a step's pairs in 2^-16 fs: floor(sum / (2 pairs) * 2^16 + 1/2)."""
    return (sum_2theta_fs * (1 << Q_THETA) + pairs) // (2 * pairs)


@dataclass
class Tables:
    """What the ARM loads into P13 (``STEER_*`` registers and the coefficient memory)."""

    L: int  # table length, steps
    n: int  # the mean's window, steps
    p: int  # AR order
    h: list[int]  # h(1..L), Q32 fs per FC step; index 0 unused
    K: list[int]  # K(1..L), Q40; index 0 unused
    alpha: list[int]  # alpha_0..p-1, Q40 FC per fs
    gamma: list[int]  # gamma / n_e for n_e = 1..n, Q40 FC per fs; index 0 unused
    ar: list[int]  # ar_0..p-1, Q40
    mean2: list[int]  # (1 - sum ar) / n_e for n_e = 1..n, Q40; index 0 unused
    rho: int  # h beyond the table, Q32
    k_inf: int  # K beyond the table, Q40
    r0: int  # Q40 FC per fs
    target_q16: int  # theta0 in 2^-16 fs
    settle: int  # steps before the first change
    max_step: int
    lo: int  # FC's fine DAC value, its lowest and highest allowed
    hi: int
    low0: int  # FC's fine DAC value at the start
    max_saturated: int  # steering stops when the wanted FC has lain outside lo..hi for this many steps


def ar_maps(ar, P: float, H: int) -> np.ndarray:
    """Q (H x p): the predicted theta's AR part h steps on from the last p noises, chronological."""
    p = len(ar)
    out = np.zeros((H, p))
    for i in range(p):
        noise = list(np.eye(p)[i])
        pred = []
        for _ in range(H):
            nxt = float(np.dot(ar, noise[::-1][:p]))
            noise.append(nxt)
            pred.append(nxt)
        out[:, i] = np.cumsum(pred) * P
    return out


def tables(law, *, table_s: float = 40.0, low0: int, lo: int, hi: int) -> Tables:
    """The integer tables of a ``PredictiveSteering`` (``predictive_fc``): its model, AR, horizon,
    moves, cost, mean window, settling and limits.  ``low0``, ``lo``, ``hi``: FC's fine DAC value
    at the start and its allowed range (``FrequencyControl``)."""
    m, P, H, M = law.model, law.step_s, law.horizon, law.moves
    p = len(law.ar)
    L = int(round(table_s / P))
    if L > m.last:
        raise ValueError(f"a table of {L} steps is longer than the model's {m.last}")
    a = np.vstack([law._g, np.sqrt(law.lam) * np.eye(M)])
    r = np.linalg.pinv(a)[0, :H]
    c = r @ ar_maps(law.ar, P, H)
    R0 = float(r.sum())
    R1 = P * float(np.dot(np.arange(1, H + 1), r))
    beta = np.zeros(p + 1)
    beta[0] += R0
    for i in range(p):
        beta[p - 1 - i] += c[i] / P
        beta[p - i] -= c[i] / P
    alpha = [-float(beta[i + 1:].sum()) for i in range(p)]  # per ps of dF
    gamma = (R1 - float(c.sum())) / P
    d = np.arange(L + 1)
    g = m.answer(d).astype(float)
    G2 = np.array([float(np.dot(r, m.answer(dd + np.arange(1, H + 1)))) for dd in d])
    K = G2 - R0 * g
    k_inf = R0 * 0.0 + float(np.dot(r, np.arange(1, H + 1))) * P * m._final  # sum_h r_h h rho
    rho = P * m._final  # ps per step per FC step
    n = law._steps(law.mean_window)
    q40, q32 = float(1 << Q_COEF), float(1 << Q_H)
    sum_ar = float(np.sum(law.ar))
    return Tables(
        L=L, n=n, p=p,
        h=[0] + [int(round((g[j] - g[j - 1]) * 1000 * q32)) for j in range(1, L + 1)],
        K=[0] + [int(round(K[j] * q40)) for j in range(1, L + 1)],
        alpha=[int(round(x / 1000 * q40)) for x in alpha],
        gamma=[0] + [int(round(gamma / ne / 1000 * q40)) for ne in range(1, n + 1)],
        ar=[int(round(x * q40)) for x in law.ar],
        mean2=[0] + [int(round((1 - sum_ar) / ne * q40)) for ne in range(1, n + 1)],
        rho=int(round(rho * 1000 * q32)), k_inf=int(round(k_inf * q40)), r0=int(round(R0 / 1000 * q40)),
        target_q16=int(round(law.target_ps * 1000 * (1 << Q_THETA))),
        settle=max(law._steps(law.settle_s), p + 2), max_step=int(law.max_step), lo=lo, hi=hi, low0=low0,
        max_saturated=law._steps(law.max_saturated_s))


@dataclass
class Engine:
    """P13's state from its start; ``step`` takes one step's theta (Q16 fs, None without pairs)."""

    t: Tables
    index: int = 0
    low: int = 0
    rem: int = 0  # Q56 FC steps
    theta_prev: int | None = None  # Q16, the last step's theta or its prediction
    dF: list[int] = field(default_factory=list)  # dF_1, dF_2, ... (Q16); only the last max(n, p) are needed
    U: list[int] = field(default_factory=list)  # U_0, U_1, ...
    s0: int = 0  # the changes older than L steps, summed
    saturated_since: int | None = None
    stopped: str | None = None
    steps: tuple[int, int] | None = None  # FC steps (size, hold) instead of the algorithm (STEER_CONTROL.STEPS)

    def __post_init__(self) -> None:
        self.low = self.t.low0

    def _W(self, ne: int) -> int:
        return sum(self.dF[-ne:]) if ne else 0

    def _down(self, k: int) -> int:
        """The own increment of step k, Q16 fs."""
        acc = sum(self.t.h[d] * self.U[k - d] for d in range(1, min(self.t.L, k) + 1))
        acc += self.t.rho * self.s0
        return _round_shift(acc, Q_H - Q_THETA)

    def step(self, theta_q16: int | None) -> dict:
        t = self.t
        out: dict = {"index": self.index, "theta_q16": theta_q16, "change": 0, "missing": theta_q16 is None,
                     "settling": False, "saturated": False, "S_q56": None}
        if self.stopped:
            out.update(low=self.low, stopped=self.stopped)
            return out
        k = self.index
        if k == 0:
            if theta_q16 is None:  # not started: the first step with theta is step 0
                out.update(low=self.low, started=False)
                return out
            self.theta_prev = _clamp(theta_q16)
            self.U.append(0)
            self.index = 1
            out.update(low=self.low, settling=True, started=True, dF_q16=None)
            return out
        if k > t.L:  # U_{k-L-1} leaves the table
            self.s0 += self.U[k - t.L - 1]
        down = self._down(k)
        assert self.theta_prev is not None
        if self.steps is not None:  # rf_clock.twtt.steer.FCSteps: theta is not used
            size, hold = self.steps
            change = 0
            if k < t.settle:
                out["settling"] = True
            elif (k - t.settle) % hold == 0:
                change = t.low0 + (size, 0, -size, 0)[((k - t.settle) // hold) % 4] - self.low
            self.U.append(change)
            self.low += change
            self.index += 1
            out.update(low=self.low, change=change, down_q16=down)
            return out
        if theta_q16 is None:
            D = len(self.dF)  # differences known before this step
            if D > t.p:  # PredictiveSteering: len(free) > p + 1
                ne = min(t.n, D)
                acc = sum(t.ar[i] * self.dF[-1 - i] for i in range(t.p)) + t.mean2[ne] * _clamp(self._W(ne))
                dF = _clamp(_round_shift(acc, Q_COEF))
            else:
                dF = 0
            self.dF.append(dF)
            self.theta_prev = _clamp(self.theta_prev + down + dF)
            self.U.append(0)
            self.index += 1
            out.update(low=self.low, dF_q16=dF, down_q16=down)
            return out
        theta = _clamp(theta_q16)
        dF = _clamp(theta - self.theta_prev - down)
        self.dF.append(dF)
        self.theta_prev = theta
        out.update(dF_q16=dF, down_q16=down)
        if k < t.settle:
            self.U.append(0)
            self.index += 1
            out.update(low=self.low, settling=True)
            return out
        ne = min(t.n, len(self.dF))
        acc56 = t.r0 * _clamp(theta - t.target_q16)
        acc56 += sum(t.alpha[j] * self.dF[-1 - j] for j in range(t.p))
        acc56 += t.gamma[ne] * _clamp(self._W(ne))
        acc40 = sum(t.K[d] * self.U[k - d] for d in range(1, min(t.L, k) + 1)) + t.k_inf * self.s0
        S = max(-S_LIMIT, min(S_LIMIT - 1, acc56 + (acc40 << (Q_S - Q_COEF))))
        wanted = (self.low << Q_S) + self.rem - S
        rounded = (wanted + (1 << (Q_S - 1))) >> Q_S
        new = max(self.low - t.max_step, min(self.low + t.max_step, rounded))
        new = max(t.lo, min(t.hi, new))
        if t.lo <= rounded <= t.hi:
            self.saturated_since = None
        else:
            out["saturated"] = True
            self.saturated_since = k if self.saturated_since is None else self.saturated_since
            if k - self.saturated_since >= t.max_saturated:  # PredictiveSteering raises: FC stays
                self.stopped = "saturated"
                self.U.append(0)
                self.index += 1
                out.update(low=self.low, S_q56=S, stopped=self.stopped)
                return out
        self.rem = wanted - (new << Q_S) if new == rounded else 0
        change = new - self.low
        self.U.append(change)
        self.low = new
        self.index += 1
        out.update(low=new, change=change, S_q56=S, rem_q56=self.rem)
        return out
