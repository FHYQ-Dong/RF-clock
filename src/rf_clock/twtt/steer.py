"""Steering B's FS725 so that theta goes to its target (plan sections 7 and 7.1, M5).

theta is B's clock reading minus A's (``rf_clock.twtt.timing``).  B's FS725 sets
B's whole timebase, so raising its frequency offset SF by s parts in 1e12 makes
theta grow by s ps every second.  Each second's theta updates a Kalman filter with
two states, theta itself (x, ps) and its rate, the relative frequency of the two
clocks (y, ps/s = parts in 1e12):

    x[k+1] = x[k] + y[k] dt + w,   y[k+1] = y[k] + u[k] + v

u is the change of SF the steering made, a known input.  The process noise w, v
models the two clocks' white and random-walk frequency noise (``--clock-adev``,
the pair's Allan deviation at 1 s), and theta's own scatter (``--theta-sd``) the
measurement noise; a theta further than ``GATE`` standard deviations from the
prediction is rejected, and after ``MAX_REJECTS`` in a row the filter starts again.
Every ``interval`` seconds, once the filter has seen ``settle`` seconds, the
frequency is set so that the remaining error decays with time constant ``tau``:

    SF_wanted = SF - (x - target)/tau - y,   at most max_step from SF

SF takes effect some time after the second whose theta asked for it: the correction
is sent once that second's theta is in, and the FS725's frequency follows its command
about 1.1-1.6 s later (B's FS725, results/m5-v2-20261103); with theta averaged over
each second that is ``response_delay_s`` = 1.9 s after that second.  The filter counts
a change from then on (until then it is pending, and the control law already counts it
in y), and while a change is new it widens its outlier gate by the change times
``response_sd_s``, the uncertainty of that delay.  With ``response_lag_s`` the frequency
then follows the change with that first-order time constant, as the PRS10's frequency
lock loop moves the crystal (about 2 s at its normal gain, GA 7): the filter adds the
phase this gives (``lag_phase``) and counts the change whole after ``LAG_SETTLED`` time
constants.  The default delay 0 is the model of
the 1.x node: the change counted from the second that asked for it.  On the bench
(2026-11-03) that model, with tau = 5 s, made the filter reject ten thetas in a row at
the first large step and start again, while SF stayed and theta ran 6 ns past its target.

SF is an integer.  Rounding SF_wanted alone would leave theta free to drift until
(x - target)/tau reached half a unit, +-tau/2 ps; instead each correction carries
the rounding remainder to the next (first-order error diffusion), so that SF
alternates between neighbouring values whose mean is SF_wanted.

Only B is steered; A free-runs.  The target is the calibrated zero-baseline value of
theta for this wiring (plan section 8), given explicitly: nothing is guessed.  SF is held within
its starting value +- ``max_offset``: a large error at the start (a short tau) runs the
correction at that limit for a while; if the control law still asks for more after
``max_saturated_s`` seconds in a row, the reference cannot be followed and steering
stops, SF staying where it is.  That bounds SF; it does not show that the loop closes: where theta does not
follow SF (both boards on one FS725, as in M4), the filter takes the missing response
for clock noise and SF wanders by a few units.  Check that a step of SF moves theta's
rate by as much before steering.

On B, beside ``rf_clock.twtt.node B``, following its log::

    run="sudo /usr/local/share/pynq-venv/bin/python"
    $run -m rf_clock.twtt.steer --target-ns 10.0055 --log twtt_B.jsonl --port /dev/ttyUSB0
    $run -m rf_clock.twtt.steer --target-ns 10.0055 --log twtt_B.jsonl --dry-run   # compute, write nothing
    python -m rf_clock.twtt.steer --target-ns 10.0055 --log twtt_B.jsonl --replay  # a recorded log, no FS725

The steering log (``steer_B.jsonl`` unless ``--steer-log``) has one line per second
(theta, the filter's x and y, SF) and one per correction.  Before each correction
the unit must still have its 1 pps PLL off and the SF last set (``check_unit``); if
not (a restart, or another program), or if the FS725 stops answering, steering
stops.  Stopping (Ctrl-C, SIGTERM) leaves SF at its last value: B holds that
frequency.  The FS725 is shared through the EPG1, so after an experiment set SF back
to the value the steering started from (logged, and printed at the end) with
``python -m rf_clock.fs725 sf``.

``PredictiveSteering`` is the other control law (``m4c_link --steer-mode predictive``,
results/fs725-fll-20261105).  It knows the FS725's answer to SF as its frequency lock loop
gives it (the unit's loop at its gain GA, from the board config, ``fit_loop``: an
integrator around the 1 s filter on the crystal's tuning), so it takes its own corrections out of each theta and keeps the free
clocks' theta.  Their frequency about its 60 s mean is predicted a few seconds ahead by
an autoregressive model (``AR4``: the pair's wander has a narrow band at 5-9 s periods,
which is predictable over a second or two), and each second the next few changes of SF
are chosen so that the predicted theta, with the loop's answer to them, stays on the
target (least squares over ``horizon`` seconds, ``lam`` times the squared changes added);
the first is sent.  Replayed on the free records of 2026-11-05 it leaves 29-31 ps, and
25-27 ps with theta from the last 0.2 s of each second, against 35-37 ps for ``Steering``
at tau 5 s.  Its model must match the unit's GA: the GA 7 model on a GA 8 unit
oscillates (140 ps in the replay).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

import numpy as np

from rf_clock.fs725 import FC_HIGH, FC_LOW, FS725Error

GATE = 5.0  # reject a theta further than this many standard deviations from the prediction
MAX_REJECTS = 10  # rejections in a row after which the filter starts again
SF_LIMIT = 2000  # the FS725's SF range, parts in 1e12
DAY_S = 86400.0
LAG_SETTLED = 8  # a lagging change is counted whole this many time constants after it begins


def lag_phase(s: float, lag: float) -> float:
    """theta's answer (ps) to a frequency step of 1 ps/s that begins at s = 0 and follows
    with a first-order time constant ``lag``: s - lag (1 - exp(-s / lag)) once it begins."""
    if s <= 0:
        return 0.0
    return s if lag <= 0 else s - lag * (1 - float(np.exp(-s / lag)))


@dataclass
class ClockFilter:
    """Kalman filter for theta (x, ps) and its rate (y, ps/s), from one theta per second.

    ``q_wfm`` (ps^2/s) and ``q_rwfm`` (ps^2/s^3) are the white and random-walk
    frequency noise of the clock pair, ``r`` (ps^2) theta's measurement variance."""

    q_wfm: float
    q_rwfm: float
    r: float
    y_sd0: float = 1000.0  # initial uncertainty of y, ps/s (1e-9)
    x: float = 0.0
    y: float = 0.0
    p: list[list[float]] = field(default_factory=lambda: [[0.0, 0.0], [0.0, 0.0]])
    started: bool = False

    def start(self, z: float) -> None:
        self.x, self.y = z, 0.0
        self.p = [[self.r, 0.0], [0.0, self.y_sd0 ** 2]]
        self.started = True

    def apply(self, u: float) -> None:
        """The frequency changed by ``u`` (a known SF step)."""
        self.y += u

    def predict(self, dt: float) -> None:
        (p00, p01), (p10, p11) = self.p
        q, qr = self.q_wfm, self.q_rwfm
        self.x += self.y * dt
        self.p = [[p00 + dt * (p01 + p10) + dt * dt * p11 + q * dt + qr * dt ** 3 / 3,
                   p01 + dt * p11 + qr * dt * dt / 2],
                  [p10 + dt * p11 + qr * dt * dt / 2, p11 + qr * dt]]

    def update(self, z: float, extra_var: float = 0.0) -> bool:
        """Take one measurement of theta; False (and no change) if it is an outlier.
        ``extra_var`` (ps^2) widens the gate only, not the measurement's weight."""
        (p00, p01), (p10, p11) = self.p
        s = p00 + self.r
        nu = z - self.x
        if nu * nu > GATE * GATE * (s + extra_var):
            return False
        k0, k1 = p00 / s, p10 / s
        self.x += k0 * nu
        self.y += k1 * nu
        self.p = [[(1 - k0) * p00, (1 - k0) * p01], [p10 - k1 * p00, p11 - k1 * p01]]
        return True


class SteeringError(RuntimeError):
    """The correction would leave the allowed range of SF, or the FS725 no longer holds
    the setting the steering made."""


def check_unit(unit: Any, sf: int) -> None:
    """Before a correction: the unit's 1 pps PLL is still off and SF is still the value
    the steering last set.  A restart brings back the pot's frequency and the PLL (which,
    with a PPS on the rear input, would lock the unit to it past this loop); another
    program may have changed SF.  Either way the steering's picture of the unit is
    wrong, so it stops rather than correct from it."""
    if unit.phase_lock():
        raise SteeringError("the FS725's 1 pps PLL is on again (did the unit restart?): steering stops")
    current = unit.frequency()
    if current != sf:
        raise SteeringError(f"the FS725's SF is {current}, not the {sf} last set (did the unit restart?): "
                            "steering stops")


@dataclass
class Steering:
    """The filter and the control law: ``step`` takes each second's theta and returns a
    new SF when a correction is due."""

    target_ps: float
    sf0: int  # SF at the start
    filter: ClockFilter
    tau_s: float = 50.0
    interval_s: int = 10
    settle_s: int = 60
    max_step: int = 100
    max_offset: int = 500
    apply: bool = True  # False: compute corrections without changing the plant (dry run)
    response_delay_s: float = 0.0  # a change of SF counts this long after the second that asked for it
    response_lag_s: float = 0.0  # then the frequency follows it with this first-order time constant
    response_sd_s: float = 0.5  # uncertainty of that delay: widens the gate while a change is new
    max_saturated_s: int = 120  # the control law asking beyond max_offset this long stops the steering
    sf: int = 0
    saturated_since: int | None = None
    pending: list[tuple[float, float]] = field(default_factory=list)  # (count it counts from, change)
    recent: list[tuple[int, float]] = field(default_factory=list)  # (count it was made, change)
    remainder: float = 0.0  # of the last rounding, carried to the next correction
    rejects: int = 0
    last_count: int | None = None
    first_count: int | None = None
    last_correction: int | None = None

    def __post_init__(self) -> None:
        self.sf = self.sf0
        if self.tau_s < 2 * self.interval_s:
            raise ValueError("tau must be at least twice the correction interval")

    def step(self, count: int, theta_ps: float | None) -> dict:
        """One second (``count``: A's PPS count).  Returns the record of that second; its
        ``"sf"`` is the SF to set now (None: keep)."""
        f = self.filter
        record: dict[str, Any] = {"count": count, "theta_ps": theta_ps, "sf": None}
        if not f.started:
            if theta_ps is not None:
                f.start(theta_ps)
                self.first_count = self.last_count = count
                record["event"] = "filter started"
            return record
        assert self.last_count is not None and self.first_count is not None
        f.predict(count - self.last_count)
        lag = self.response_lag_s
        for when, u in [c for c in self.pending if c[0] < count]:  # changes acting within this step
            f.x += u * (lag_phase(count - when, lag) - lag_phase(self.last_count - when, lag))
            if count - when >= LAG_SETTLED * lag:  # the rest of it the filter's y carries
                f.y += u
                self.pending.remove((when, u))
        self.last_count = count
        horizon = self.response_delay_s + self.response_lag_s + 3 * self.response_sd_s + 1
        self.recent = [c for c in self.recent if count - c[0] <= horizon]
        extra = sum((u * self.response_sd_s) ** 2 for _, u in self.recent)
        if theta_ps is not None:
            if f.update(theta_ps, extra):
                self.rejects = 0
            else:
                self.rejects += 1
                record["rejected"] = True
                if self.rejects >= MAX_REJECTS:
                    f.started = False
                    self.rejects = 0
                    record["event"] = f"{MAX_REJECTS} thetas rejected in a row: filter restarts"
                    return record
        record.update(x_ps=f.x, y_ppt=f.y, p_x=f.p[0][0], p_y=f.p[1][1])
        due = (count - self.first_count >= self.settle_s
               and (self.last_correction is None or count - self.last_correction >= self.interval_s))
        if due:
            self.last_correction = count
            y = f.y + sum(u for _, u in self.pending)  # the frequency once the pending changes count
            wanted = self.sf - (f.x - self.target_ps) / self.tau_s - y + self.remainder
            lo = max(self.sf0 - self.max_offset, -SF_LIMIT)
            hi = min(self.sf0 + self.max_offset, SF_LIMIT)
            sf = max(lo, min(hi, max(self.sf - self.max_step, min(self.sf + self.max_step, round(wanted)))))
            if self.apply:
                self.remainder = max(-0.5, min(0.5, wanted - sf))  # a clipped step carries no more
            if lo <= round(wanted) <= hi:
                self.saturated_since = None
            else:
                self.saturated_since = count if self.saturated_since is None else self.saturated_since
                if count - self.saturated_since >= self.max_saturated_s:
                    raise SteeringError(f"SF held at {sf}, the end of {self.sf0} +- {self.max_offset}, for "
                                        f"{count - self.saturated_since} s: the reference cannot be followed")
            if sf != self.sf:
                record["sf"] = sf
                if self.apply:
                    self.pending.append((count + self.response_delay_s, sf - self.sf))
                    self.recent.append((count, sf - self.sf))
                    self.sf = sf
        return record


# AR(4) of the pair's second-to-second frequency about its 60 s mean, fitted on the free
# record results/m4c-v2-free-20261103_205954 (both FS725s at GA 7).
AR4 = (0.852, -0.825, 0.301, -0.171)
# AR(20) of the pair's frequency from step to step at steps of 0.2 s, theta the mean over the
# last 0.1 s of each step, about its 60 s mean; fitted on the per-pair theta of the free record
# results/m4c-v2-acquire-20261105_105242 (A at GA 7, B at GA 7; results/two-loops-20261105).
AR20_STEP_02 = (0.9459, 0.1592, -0.1282, -0.0948, -0.0379, 0.0127, -0.0142, -0.0179, -0.0192, 0.0026,
                0.0160, -0.0307, -0.0069, -0.0244, 0.0333, 0.0052, -0.0306, 0.0323, -0.0129, -0.0199)
# The law's settings by step (s): the AR model, the horizon and the changes planned (steps), the
# cost of a change, when a change leaves after its step ends (s), theta's window (s at the end).
# At 0.2 s lam 0.05 oscillates when the plant answers harder than the model; 0.2 does not.
STEP_SETTINGS = {1.0: dict(ar=AR4, horizon=4, moves=2, lam=0.05, command_s=0.15),
                 0.2: dict(ar=AR20_STEP_02, horizon=20, moves=2, lam=0.2, command_s=0.03)}
STEP_DT = 0.02  # s, the grid of the loop's step response

# Steering through FC (``predictive_fc``), B's unit at GA 1: the AR model of theta's frequency on
# 0.1 s steps (40 of them, fitted on B's record at GA 1, results/m4c-v2-acquire-20261106_030506);
# the law's settings, lam per (ps/s)^2 as for SF (scaled by the plant's gain squared for FC's
# steps), changes of up to 1000 fine steps; and its model slower than the plant measured, the lag
# times FC_MODEL_LAG and the dead time plus FC_MODEL_DEAD_S: in replay (results/precision-20261105
# section 8) that holds theta to 4.6-5.6 ps rms with the plant's lag 0.8-1.1 s, dead time up to
# 0.1 s and gain +-10 %, and oscillates once its dead time is 0.2 s
AR40_STEP_01 = (0.3493, 0.4318, 0.2519, 0.0724, -0.001, -0.0456, -0.05, -0.047, -0.0346, -0.0154,
                -0.0204, -0.0002, 0.0018, 0.004, 0.0113, -0.0014, 0.0057, -0.0149, -0.0007, 0.0021,
                -0.0126, -0.0041, -0.0036, -0.004, 0.0231, 0.0197, -0.0042, 0.0039, -0.019, -0.0016,
                0.0122, -0.0109, 0.0011, -0.0066, -0.0004, 0.0035, 0.0105, 0.0131, 0.0206, -0.0201)
FC_STEP_SETTINGS: dict[float, dict[str, Any]] = {
    0.1: dict(ar=AR40_STEP_01, horizon=30, moves=4, lam=0.08, command_s=0.03, max_step=1000, max_offset=1000)}
FC_MODEL_LAG = 1.25
FC_MODEL_DEAD_S = 0.1
FC_TOLERANCE = 3  # fine steps between the FC last written and FC? (the slow loop's pull in a step)


def loop_response(k: float, lag_s: float, dead_s: float, *, dt: float = STEP_DT, span_s: float = 60.0) -> np.ndarray:
    """The output frequency's answer to a unit step of the rubidium reference (SF) at t = 0,
    on a grid of ``dt`` over ``span_s``: the loop integrates the error with gain ``k`` into
    FC, the crystal follows FC with first-order lag ``lag_s``, and the error reaches the
    loop ``dead_s`` after the step."""
    t = np.arange(0, span_s, dt)
    y = np.zeros_like(t)
    fc = out = 0.0
    for i in range(1, t.size):
        fc += k * ((1.0 if t[i] > dead_s else 0.0) - out) * dt
        out += (fc - out) / lag_s * dt
        y[i] = out
    return y


def lag_response(lag_s: float, dead_s: float, *, dt: float = STEP_DT, span_s: float = 60.0) -> np.ndarray:
    """The crystal's frequency answer to a unit step of FC at t = 0, the loop out of the way
    (GA 1): FC's DAC reaches the varactor through a 1 s RC (PRS10 manual p. 47), a first-order
    lag ``lag_s`` after ``dead_s``; fitted on B's unit at 1.12 s after about 0 s
    (results/precision-20261105 section 8)."""
    t = np.arange(0, span_s, dt)
    return np.where(t > dead_s, 1.0 - np.exp(-(t - dead_s) / lag_s), 0.0)


def fit_loop(t: Sequence[float] | np.ndarray, fc: Sequence[float] | np.ndarray, at: Sequence[float], *,
             before_s: float = 10.0, after_s: float = 20.0) -> dict:
    """The frequency lock loop of ``loop_response`` from FC's answer to changes of SF sent at the
    times ``at`` (results/fs725-fll-20261105 section 5.1): FC (``t`` when read, ``fc`` in low DAC
    steps, 1000 high + low) from ``before_s`` before to ``after_s`` after each change, fitted by
    a + b t + D u(t), u FC's answer to a unit step in that loop, over the integrator's gain k
    0.3-16 per s (in steps of 0.05 up to 3, 0.1 up to 6, 0.25 above: GA 9 and 10 put it
    beyond 3), the crystal's lag 0.5-2 s and the dead time 0-0.4 s in steps of 0.05.  FC
    alone fixes them: the loop drives FC until the crystal, lagging behind it, meets the
    new reference.  One k, lag and dead time hold for every change (each its own a, b, D):
    FC's own wander, 10-20 steps rms over a fit, moves the fit of a single change of 100
    parts in 1e12 to the ends of the grid (fs725_loop on 2026-11-05).  Returns ``k_per_s``,
    ``lag_s``, ``dead_s``, ``rms_steps`` (FC about the fit), ``fc_changes`` (each D), and
    ``alone``: each change fitted by itself, which shows how well they agree."""
    when = np.asarray(t, dtype=float)
    values = np.asarray(fc, dtype=float)
    gains = np.concatenate([np.arange(0.3, 3.0 - 1e-9, 0.05), np.arange(3.0, 6.0 - 1e-9, 0.1),
                            np.arange(6.0, 16.0 + 1e-9, 0.25)])
    k, lag, dead = (a.ravel() for a in np.meshgrid(gains, np.arange(0.5, 2.0 + 1e-9, 0.05),
                                                    np.arange(0.0, 0.4 + 1e-9, 0.05), indexing="ij"))

    def one(time: float) -> tuple[np.ndarray, np.ndarray, int]:
        """Each candidate's residual and D for the change at ``time``, and the reads fitted."""
        keep = (when > time - before_s) & (when < time + after_s)
        x, v = when[keep] - time, values[keep]
        dt = 0.01
        pos = np.clip(x, 0, None) / dt
        i0 = np.floor(pos).astype(int)
        frac = pos - i0
        need = np.unique(np.concatenate([i0, i0 + 1]))
        column = {int(i): c for c, i in enumerate(need)}
        at_grid = np.zeros((k.size, need.size))
        u = out = np.zeros(k.size)
        for i in range(int(need[-1]) + 1):  # every candidate loop at once, as loop_response steps one
            if i:
                u = u + k * ((i * dt > dead) - out) * dt
                out = out + (u - out) / lag * dt
            if i in column:
                at_grid[:, column[i]] = u
        c0 = np.searchsorted(need, i0)
        answer = at_grid[:, c0] * (1 - frac) + at_grid[:, c0 + 1] * frac
        q, _ = np.linalg.qr(np.column_stack([np.ones_like(x), x]))  # a + b t projected out
        v_free = v - q @ (q.T @ v)
        a_free = answer - (answer @ q) @ q.T
        norm = np.einsum("ij,ij->i", a_free, a_free)
        dot = a_free @ v_free
        return np.maximum(v_free @ v_free - dot ** 2 / norm, 0.0), dot / norm, x.size

    def best(i: int) -> dict:
        return dict(k_per_s=round(float(k[i]), 2), lag_s=round(float(lag[i]), 2), dead_s=round(float(dead[i]), 2))

    fits = [one(time) for time in at]
    alone = [{**best(int(np.argmin(r))), "fc_change": float(d[np.argmin(r)]), "rms_steps": float(np.sqrt(r.min() / n))}
             for r, d, n in fits]
    total = np.sum([r for r, _, _ in fits], axis=0)
    i = int(np.argmin(total))
    return {**best(i), "rms_steps": float(np.sqrt(total[i] / sum(n for _, _, n in fits))),
            "fc_changes": [float(d[i]) for _, d, _ in fits], "alone": alone}


class LoopModel:
    """theta's answer to one change of SF, as seen in the windows theta is averaged over, on
    steps of ``step_s`` (a second, or less for a faster law).

    ``window``: the part of each step theta is the mean over, in s from the step's start,
    (0, step_s) for the whole step; ``command_s``: when the change leaves, after the end of the
    step that asked for it.  ``answer(j)`` is the mean, over that window of step k, of the phase
    a unit change asked for in step k - j gives (ps per SF unit)."""

    def __init__(self, k: float, lag_s: float, dead_s: float, *, window: tuple[float, float] = (0.0, 1.0),
                 command_s: float = 0.15, table_s: int = 120, step_s: float = 1.0,
                 response: np.ndarray | None = None) -> None:
        y = loop_response(k, lag_s, dead_s) if response is None else np.asarray(response, dtype=float)
        self._phase = np.concatenate([[0.0], np.cumsum(y[:-1]) * STEP_DT])
        self._t = np.arange(self._phase.size) * STEP_DT
        self._final = float(y[-1])
        self.step_s = step_s
        # the window's mean from 5 ms midpoints, the phase interpolated: rounding to the 20 ms
        # grid put times exactly half-way between grid points (0.03 s, 0.15 s after a step's
        # end), where round-half-to-even made the table jagged from one step to the next, and
        # the law on 0.2 s steps followed the jags (63 ps instead of 16 in the replay)
        grid = np.arange(window[0] + 0.0025, window[1], 0.005)
        n = int(round(table_s / step_s))
        self._table = np.array([float(np.mean(self.phase(j * step_s - step_s - command_s + grid)))
                                for j in range(n + 1)])

    @classmethod
    def crystal(cls, lag_s: float, dead_s: float, gain: float, **kwargs: Any) -> "LoopModel":
        """theta's answer to one change of FC, in its fine steps, with the unit at GA 1: the
        crystal follows FC through a first-order lag (``lag_response``), ``gain`` ps/s per step."""
        return cls(0.0, lag_s, dead_s, response=gain * lag_response(lag_s, dead_s), **kwargs)

    def phase(self, s: np.ndarray | float) -> np.ndarray:
        """The phase (ps) of a unit change, s seconds after it leaves (interpolated)."""
        s = np.asarray(s, dtype=float)
        span = self._t[-1]
        inside = np.interp(s, self._t, self._phase)
        return np.where(s <= 0, 0.0, np.where(s <= span, inside, self._phase[-1] + (s - span) * self._final))

    @property
    def last(self) -> int:
        """The last step of the table; beyond it the answer grows linearly."""
        return self._table.size - 1

    def answer(self, j: np.ndarray | int) -> np.ndarray:
        j = np.asarray(j)
        last = self.last
        return np.where(j <= last, self._table[np.clip(j, 0, last)],
                        self._table[last] + (j - last) * self.step_s * self._final)

    def tail(self, count: int, total: float, moment: float) -> float:
        """The summed answer in step ``count`` of changes all older than the table: ``total``
        the sum of the changes, ``moment`` the sum of each change times its step."""
        rate = self.step_s * self._final
        return total * (float(self._table[self.last]) - self.last * rate) + rate * (count * total - moment)


def fit_frequency_ar(theta_ps: Sequence[float] | np.ndarray, order: int = 4, mean_window: int = 60) -> tuple[float, ...]:
    """AR(``order``) coefficients of a free record's frequency from step to step (the
    differences of consecutive per-step thetas) about its running ``mean_window`` mean (steps)."""
    f = np.diff(np.asarray(theta_ps, dtype=float))
    n = f - np.array([f[max(0, i - mean_window + 1):i + 1].mean() for i in range(f.size)])
    x = np.column_stack([n[order - j - 1:n.size - j - 1] for j in range(order)])
    a, *_ = np.linalg.lstsq(x, n[order:], rcond=None)
    return tuple(float(v) for v in a)


@dataclass
class PredictiveSteering:
    """The predictive control law (module docstring): ``step`` takes each step's theta (a
    second's, or the model's shorter step's) and returns a new SF when it changes."""

    target_ps: float
    sf0: int
    model: LoopModel
    ar: Sequence[float] = AR4
    horizon: int = 4  # steps over which the predicted theta is held on the target
    moves: int = 2  # changes of SF planned each step (the first is sent)
    lam: float = 0.05  # ps^2 per SF unit^2: the cost of a change
    mean_window: float = 60  # s, the frequency's mean that the AR model predicts about
    settle_s: float = 30
    max_step: int = 100  # per change
    max_offset: int = 500
    max_saturated_s: float = 120
    limits: tuple[int, int] = (-SF_LIMIT, SF_LIMIT)  # the setting's own range: SF's, or FC's (predictive_fc)
    apply: bool = True
    sf: int = 0
    first_count: int | None = None
    free: list[float] = field(default_factory=list)  # the free clocks' theta, one per step from first_count
    asked: deque = field(default_factory=deque)  # step that asked for each change within the model's table
    changes: deque = field(default_factory=deque)
    old_total: float = 0.0  # the changes older than the table, summed (their answer is linear: LoopModel.tail)
    old_moment: float = 0.0  # ... each times its step
    remainder: float = 0.0
    saturated_since: int | None = None

    def __post_init__(self) -> None:
        self.sf = self.sf0
        self.step_s = self.model.step_s
        h, m = self.horizon, self.moves
        self._g = np.array([[float(self.model.answer(i - j)) for j in range(m)] for i in range(1, h + 1)])

    def _steps(self, seconds: float) -> int:
        return max(1, int(round(seconds / self.step_s)))

    def own(self, count: int) -> float:
        """The phase the corrections so far give theta in step ``count``, the last step taken
        or a later one (older changes are folded for those)."""
        total = self.model.tail(count, self.old_total, self.old_moment) if self.old_total or self.old_moment else 0.0
        if self.asked:
            total += float(np.dot(self.changes, self.model.answer(count - np.asarray(self.asked))))
        return total

    def _own_ahead(self, count: int) -> np.ndarray:
        """``own`` for the ``horizon`` steps after ``count``, in one product."""
        ahead = count + np.arange(1, self.horizon + 1)
        rate = self.model.step_s * self.model._final
        out = (self.old_total * (float(self.model.answer(self.model.last)) - self.model.last * rate)
               + rate * (ahead * self.old_total - self.old_moment))
        if self.asked:
            out = out + self.model.answer(ahead[:, None] - np.asarray(self.asked)[None, :]) @ np.asarray(self.changes)
        return out

    def _fold(self, count: int) -> None:
        """Move changes that no future step will see within the table into the linear sums,
        so that ``own`` costs the same at any age (at 0.2 s steps it summed every change since
        the start, and after 13 minutes the node fell behind its queue)."""
        while self.asked and count - self.asked[0] > self.model.last + self.horizon + 1:
            asked, change = self.asked.popleft(), self.changes.popleft()
            self.old_total += change
            self.old_moment += change * asked

    def _predict(self, steps: int) -> list[float]:
        """The free frequency (ps/s) of the next ``steps`` steps."""
        n = self._steps(self.mean_window)
        f = np.diff(self.free[-(n + len(self.ar) + 1):]) / self.step_s
        mean = float(f[-n:].mean())
        p = len(self.ar)
        noise = list(f[-p:] - mean)
        out = []
        for _ in range(steps):
            nxt = float(np.dot(self.ar, noise[::-1][:p]))
            noise.append(nxt)
            out.append(mean + nxt)
        return out

    def step(self, count: int, theta_ps: float | None) -> dict:
        record: dict[str, Any] = {"count": count, "theta_ps": theta_ps, "sf": None}
        if self.first_count is None:
            if theta_ps is None:
                return record
            self.first_count = count
            record["event"] = "started"
        index = count - self.first_count
        if index < len(self.free):  # a step already taken (or an earlier one): nothing to do
            record["event"] = "second already taken"
            return record
        while len(self.free) < index:  # steps without theta: the free clocks as predicted
            self.free.append(self._next_free())
        if theta_ps is None:
            if self.free:
                self.free.append(self._next_free())
            return record
        self._fold(count)
        self.free.append(theta_ps - self.own(count))
        record["free_ps"] = self.free[-1]
        if index < max(self._steps(self.settle_s), len(self.ar) + 2):
            return record
        free_pred = self.free[-1] + np.cumsum(self._predict(self.horizon)) * self.step_s
        error = free_pred + self._own_ahead(count) - self.target_ps
        a = np.vstack([self._g, np.sqrt(self.lam) * np.eye(self.moves)])
        b = np.concatenate([-error, np.zeros(self.moves)])
        plan, *_ = np.linalg.lstsq(a, b, rcond=None)
        wanted = self.sf + float(plan[0]) + self.remainder
        record["error_predicted_ps"] = float(error[0])
        lo = max(self.sf0 - self.max_offset, self.limits[0])
        hi = min(self.sf0 + self.max_offset, self.limits[1])
        sf = max(lo, min(hi, max(self.sf - self.max_step, min(self.sf + self.max_step, round(wanted)))))
        if lo <= round(wanted) <= hi:
            self.saturated_since = None
        else:
            self.saturated_since = count if self.saturated_since is None else self.saturated_since
            if count - self.saturated_since >= self._steps(self.max_saturated_s):
                raise SteeringError(f"SF held at {sf}, the end of {self.sf0} +- {self.max_offset}, for "
                                    f"{(count - self.saturated_since) * self.step_s:.0f} s: the reference cannot be followed")
        if not self.apply:
            return record
        self.remainder = max(-0.5, min(0.5, wanted - sf)) if sf == round(wanted) else 0.0
        if sf != self.sf:
            record["sf"] = sf
            self.asked.append(count)
            self.changes.append(sf - self.sf)
            self.sf = sf
        return record

    def _next_free(self) -> float:
        """The free clocks' theta of a step without a measurement, as predicted."""
        if len(self.free) > len(self.ar) + 1:
            return self.free[-1] + self._predict(1)[0] * self.step_s
        return self.free[-1]


def predictive_for(target_ps: float, sf0: int, loop: tuple[float, float, float], *, late_s: float = 0.0,
                   step_s: float = 1.0, **kwargs: Any) -> PredictiveSteering:
    """A ``PredictiveSteering`` on steps of ``step_s`` (1 s, or 0.2 s: ``STEP_SETTINGS``) with
    the unit's frequency lock loop at its gain, ``loop`` = (k per s, lag s, dead time s; the
    board config's, ``fit_loop``), and theta the mean over the last ``late_s`` of each step
    (0: the whole step); ``kwargs`` override the step's settings."""
    if step_s not in STEP_SETTINGS:
        raise SteeringError(f"no settings for steps of {step_s} s (known: {sorted(STEP_SETTINGS)})")
    settings = dict(STEP_SETTINGS[step_s])
    command_s = settings.pop("command_s")
    settings.update(kwargs)
    window = (step_s - late_s, step_s) if late_s else (0.0, step_s)
    model = LoopModel(*loop, window=window, command_s=command_s, step_s=step_s)
    return PredictiveSteering(target_ps=target_ps, sf0=sf0, model=model, **settings)


def predictive_fc(target_ps: float, fc0: int, plant: dict, *, late_s: float = 0.0, step_s: float = 0.1,
                  lag_s: float | None = None, dead_s: float | None = None, **kwargs: Any) -> PredictiveSteering:
    """A ``PredictiveSteering`` that sets FC (1000 coarse + fine steps, from ``fc0``) instead of
    SF, B's unit at GA 1 (``FrequencyControl`` with ``actuator="fc"``).  ``plant``: the board
    config's ``fs725["fc"]``, the crystal's answer to FC (``lag_s``, ``dead_s``, ``ps_per_step``);
    the law's model is slower (``FC_MODEL_LAG``, ``FC_MODEL_DEAD_S``) unless ``lag_s`` and
    ``dead_s`` are given; ``kwargs`` override ``FC_STEP_SETTINGS``, ``lam`` still per (ps/s)^2."""
    if step_s not in FC_STEP_SETTINGS:
        raise SteeringError(f"no FC settings for steps of {step_s} s (known: {sorted(FC_STEP_SETTINGS)})")
    settings = dict(FC_STEP_SETTINGS[step_s])
    command_s = settings.pop("command_s")
    settings.update(kwargs)
    gain = float(plant["ps_per_step"])
    settings["lam"] = settings["lam"] * gain ** 2
    settings["limits"] = (1000 * FC_HIGH[0] + FC_LOW[0], 1000 * FC_HIGH[1] + FC_LOW[1])
    window = (step_s - late_s, step_s) if late_s else (0.0, step_s)
    model = LoopModel.crystal(lag_s if lag_s is not None else plant["lag_s"] * FC_MODEL_LAG,
                              dead_s if dead_s is not None else plant["dead_s"] + FC_MODEL_DEAD_S, gain,
                              window=window, command_s=command_s, step_s=step_s)
    return PredictiveSteering(target_ps=target_ps, sf0=fc0, model=model, **settings)


def check_fc(unit: Any, fc: int) -> None:
    """Now and then while steering through FC: the 1 pps PLL still off, GA still 1 and the unit
    locked (else its 1PPS stops), and FC within ``FC_TOLERANCE`` of the value last written."""
    if unit.phase_lock():
        raise SteeringError("the FS725's 1 pps PLL is on again (did the unit restart?): steering stops")
    gain = unit.gain()
    if gain != 1:
        raise SteeringError(f"the FS725's GA is {gain}, not the 1 FC steering set (did the unit restart?): steering stops")
    high, low = unit.frequency_control()
    if abs(1000 * high + low - fc) > FC_TOLERANCE:
        raise SteeringError(f"the FS725's FC is {high},{low} ({1000 * high + low}), not the {fc} last written: "
                            "steering stops")
    if not unit.status().rb_locked:
        raise SteeringError("the FS725 lost its rubidium lock: steering stops")


def fit_sf_response(counts: Sequence[float], theta_ps: Sequence[float], at_count: float, *,
                    window_s: float = 20.0) -> dict | None:
    """How theta answered one change of SF: its change of rate (ps/s) and how long after the
    command the change began to act (``response_delay_s`` measures this from the second that
    asked for a correction; the command leaves about 0.1 s after that second ends).

    ``counts`` and ``theta_ps`` are the per-second thetas (each the mean over its second,
    second k spanning k .. k + 1 on the node's PPS count), ``at_count`` the command's time on
    the same axis.  Over +-``window_s`` the model is a quadratic, the clocks' own wander, plus
    rate * (the mean over each second of max(0, t - (at_count + delay))); the delay is searched
    from -0.5 to 4 s in steps of 0.02 s.  None with fewer than 10 seconds on either side."""
    k = np.asarray(counts, dtype=float)
    y = np.asarray(theta_ps, dtype=float)
    keep = np.abs(k + 0.5 - at_count) <= window_s
    k, y = k[keep], y[keep]
    if (k + 1 <= at_count).sum() < 10 or (k >= at_count).sum() < 10:
        return None
    x = k + 0.5 - at_count
    best = None
    for delay in np.arange(-0.5, 4.0 + 1e-9, 0.02):
        tc = at_count + delay
        ramp = np.where(tc <= k, k + 0.5 - tc, np.where(tc >= k + 1, 0.0, (k + 1 - tc) ** 2 / 2))
        m = np.column_stack([np.ones_like(x), x, x * x, ramp])
        coef, *_ = np.linalg.lstsq(m, y, rcond=None)
        resid = float(np.sum((y - m @ coef) ** 2))
        if best is None or resid < best[0]:
            best = (resid, float(delay), float(coef[3]))
    assert best is not None
    return {"delay_s": best[1], "rate_ps_per_s": best[2], "seconds": int(k.size),
            "residual_ps": float(np.sqrt(best[0] / max(k.size - 4, 1)))}


def filter_for(clock_adev: float, theta_sd_ps: float, *, rwfm_adev_day: float = 1e-13) -> ClockFilter:
    """A filter for a clock pair with Allan deviation ``clock_adev`` at 1 s (white
    frequency noise), ``rwfm_adev_day`` at a day (random-walk frequency noise), and
    theta scattering by ``theta_sd_ps`` per second."""
    q_wfm = (clock_adev * 1e12) ** 2  # sigma_y^2(tau) = q / tau
    q_rwfm = 3 * (rwfm_adev_day * 1e12) ** 2 / DAY_S  # sigma_y^2(tau) = q tau / 3
    return ClockFilter(q_wfm=q_wfm, q_rwfm=q_rwfm, r=theta_sd_ps ** 2)


def follow(path: str, *, from_start: bool = False, wait: bool = True, poll_s: float = 0.2) -> Iterator[dict]:
    """The ``result`` entries of a twtt log as they are appended (from its end unless
    ``from_start``); with ``wait`` False, stop at its end."""
    with open(path) as f:
        if not from_start:
            f.seek(0, 2)
        rest = ""
        while True:
            line = f.readline()
            if not line:
                if not wait:
                    return
                time.sleep(poll_s)
                continue
            rest += line
            if not rest.endswith("\n"):  # the writer is in the middle of a line
                continue
            entry, rest = json.loads(rest), ""
            if entry.get("type") == "result":
                yield entry


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--target-ns", type=float, required=True,
                        help="theta to steer to: the zero-baseline calibration of this wiring")
    parser.add_argument("--log", default="twtt_B.jsonl", help="the twtt node's log to follow")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="the FS725's serial device")
    parser.add_argument("--tau", type=float, default=50.0,
                        help="time constant of the phase correction, s (experiments/m5_steer_sim.py)")
    parser.add_argument("--interval", type=int, default=10, help="seconds between corrections")
    parser.add_argument("--settle", type=int, default=60, help="seconds of filtering before the first correction")
    parser.add_argument("--max-step", type=int, default=100, help="largest SF change per correction (1e-12)")
    parser.add_argument("--max-offset", type=int, default=500, help="largest SF change from the start (1e-12)")
    parser.add_argument("--clock-adev", type=float, default=2e-11,
                        help="Allan deviation at 1 s of the two references together (the FS725's specification)")
    parser.add_argument("--theta-sd", type=float, default=3.0, help="scatter of theta per second, ps")
    parser.add_argument("--dry-run", action="store_true", help="compute the corrections, write nothing to the FS725")
    parser.add_argument("--replay", action="store_true", help="a recorded log from its start, no FS725 (a dry run)")
    parser.add_argument("--steer-log", default="steer_B.jsonl", help="this command's JSON-lines log")
    args = parser.parse_args()
    dry = args.dry_run or args.replay
    out = open(args.steer_log, "a")

    def log(entry: dict) -> None:
        out.write(json.dumps({"time": time.time(), **entry}) + "\n")
        out.flush()

    def stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    unit = None
    sf0 = 0
    if not args.replay:
        from rf_clock.fs725 import FS725

        unit = FS725.open(args.port, log=lambda e: log({"type": "fs725", **e}))
        status = unit.status()
        sf0 = unit.frequency()
        log({"type": "start", "identity": unit.identify(), "status": list(status.bytes),
             "conditions": status.conditions(), "sf": sf0, "dry_run": dry, "settings": vars(args)})
        print(f"FS725 {unit.identify()}, SF {sf0}; status: {'; '.join(status.conditions()) or 'none set'}")
        if not status.rb_locked:
            print("the FS725 is not locked to rubidium")
            return 1
        if not dry and unit.phase_lock():
            unit.set_phase_lock(False)  # SF is ignored while it phase-locks to a 1 pps input
            print("1 pps PLL disabled until the next restart, so that SF takes effect")
    steering = Steering(target_ps=args.target_ns * 1000, sf0=sf0,
                        filter=filter_for(args.clock_adev, args.theta_sd), tau_s=args.tau,
                        interval_s=args.interval, settle_s=args.settle, max_step=args.max_step,
                        max_offset=args.max_offset, apply=not dry)
    previous = sf0  # the SF last written (or that would have been, in a dry run)
    try:
        for result in follow(args.log, from_start=args.replay, wait=not args.replay):
            theta = result["theta_ns"]
            record = steering.step(result["count_a"], None if theta is None else theta * 1000)
            if record["sf"] is not None:
                if unit is not None and not dry:
                    check_unit(unit, previous)
                    unit.set_frequency(record["sf"])
                previous = record["sf"]
                log({"type": "correction", "count": record["count"], "sf": record["sf"], "dry_run": dry})
                print(f"{record['count']}: theta {record['x_ps'] / 1000:.4f} ns, y {record['y_ppt']:+.2f}e-12, "
                      f"SF {'would be ' if dry else ''}{record['sf']}")
            log({"type": "second", **record})
    except KeyboardInterrupt:
        print("stopped")
    except (SteeringError, FS725Error) as exc:
        log({"type": "event", "event": str(exc)})
        print(exc)
        return 1
    finally:
        log({"type": "stop", "sf": steering.sf, "sf_at_start": sf0})
        out.close()
        if unit is not None:
            unit.close()
        if not args.replay:
            print(f"SF stays at {steering.sf}; it was {sf0} at the start "
                  f"(python -m rf_clock.fs725 sf {sf0} --port {args.port} restores it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
