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
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

import numpy as np

from rf_clock.fs725 import FS725Error

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
