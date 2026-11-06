"""What one rf-clock 2 node does with its theta besides logging it: its FS725 (the check
against the board config, SF steps and steering), the output PPS's SYNCED, and on node B
the theta-compensated output PPS.

``configure_fs725`` checks the node's FS725 against the board config at the start of a run
and sets its gain (``rf_clock.fs725.check_config``); ``FrequencyControl`` holds the unit for
the run: the 1 pps PLL off so that SF takes effect, SF handed over from the calibration pot,
each second's or step's theta into the control law, SF back at the end; ``SyncedRule``
decides when the output PPS is declared synchronized; ``EdgeCompensation`` gives, before
each PPS edge, the fractional delay of B's output edge that makes it follow A's time scale
to the measurement rather than to the steering.  ``rf_clock.checks.m4c_link`` drives them
(docs/ABI.md 11.12, 13).
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Protocol

import numpy as np

from rf_clock.fs725 import FC_LOW, FS725, FS725Error, check_config
from rf_clock.pl.board_config import BoardConfig, BoardConfigError, config_path

from . import steer


class Law(Protocol):
    """A control law of ``rf_clock.twtt.steer``: ``Steering`` or ``PredictiveSteering``."""

    def step(self, count: int, theta_ps: float | None) -> dict: ...


def configure_fs725(config: BoardConfig | None, port: str | None,
                    emit: Callable[..., None]) -> tuple[str, tuple[float, float, float] | None]:
    """Check this node's FS725 against its board config and set its gain from it; return the
    port (``port``, or the config's when None) and the loop measured at that gain, or None.
    BoardConfigError without a config or a port; FS725Error when the unit is not the one the
    config names."""
    if config is None:
        raise BoardConfigError(f"no board config at {config_path()}: the FS725 needs one that names it "
                               f"(docs/board_setup.md section 4, then rf_clock.checks.fs725_loop --write)")
    port = port or config.fs725.get("port")
    if not port:
        raise BoardConfigError(f"{config_path()} names no FS725 port: give it (--fs725 PORT), or record it "
                               f"with rf_clock.checks.fs725_loop --port PORT --write")
    unit = FS725.open(port, log=lambda e: emit("fs725", **e))
    try:
        loop = check_config(unit, config.fs725, str(config_path()))
    finally:
        unit.close()
    emit("fs725_config", serial=config.fs725["serial"], gain=config.fs725["gain"], port=port, loop=loop)
    return port, loop


class FrequencyControl:
    """This node's FS725 for the run.  With ``set_sf`` (SF steps or steering): the unit's 1 pps
    PLL off, so that SF takes effect, and back on at the end if it was; SF set once to the
    value read (``sf0``), and back there at the end unless ``keep_sf``.  ``law(sf0, gain)``
    builds the control law once SF and GA are read; ``late``: steer on theta over the end of
    each second; ``step_s`` below 1: on ``StepTheta``'s steps instead.

    ``actuator="fc"`` (with a law on steps below a second, ``steer.predictive_fc``): the law
    sets FC, the crystal's tuning, instead of SF (results/precision-20261105 section 8).  The
    unit goes to GA 1, where it stays locked and keeps its 1PPS while FC written stays (GA 0
    or LO 0 would stop the 1PPS); FC is written every step, also when the law keeps it, which
    undoes the slow loop's pull; the law starts from the FC read (``sf0`` holds 1000 coarse +
    fine steps), its fine DAC first brought back near the middle of its range by a carry to
    the coarse one when less than ``FC_ROOM`` is left, and the law's range is what is left;
    SF is not touched.  When the steering stops, and at the end, GA goes back."""

    FC_ROOM = 500  # fine steps the law needs on either side of its start

    def __init__(self, port: str, emit: Callable[..., None], *, set_sf: bool,
                 law: Callable[[int, int], Law] | None = None, step_s: float = 1.0, late: bool = False,
                 keep_sf: bool = False, actuator: str = "sf", say: Callable[[str], None] = print) -> None:
        if actuator not in ("sf", "fc"):
            raise ValueError(f"actuator is 'sf' or 'fc': {actuator!r}")
        if actuator == "fc" and (law is None or step_s >= 1):
            raise ValueError("the FC actuator is for a control law on steps below a second")
        self.emit, self.say = emit, say
        self.fc = actuator == "fc"
        self.gain_restore: int | None = None
        self.unit = FS725.open(port, log=lambda e: emit("fs725", **e))
        try:
            status = self.unit.status()
            if not status.rb_locked:
                raise FS725Error("the FS725 is not locked to rubidium")
            self.pll_restore = False
            if set_sf and self.unit.phase_lock():
                self.unit.set_phase_lock(False)  # SF is ignored while it phase-locks to a 1 pps input
                self.pll_restore = True  # back on at the end, as found
            if self.fc:
                gain = self.unit.gain()
                self.sf0 = self.sf = self._start_fc(gain)
                self.steering: Law | None = law(self.sf0, gain) if law is not None else None
                room = min(self.sf0 - 1000 * self.fc_high - FC_LOW[0], FC_LOW[1] - (self.sf0 - 1000 * self.fc_high))
                if self.steering is not None and hasattr(self.steering, "max_offset"):
                    self.steering.max_offset = min(self.steering.max_offset, room)  # type: ignore[attr-defined]
            else:
                self.sf0 = self.sf = self.unit.frequency()
                if set_sf:
                    # A unit that has had no SF command since power-up takes SF from its calibration
                    # pot, whose reading flickers by a unit (A's: 2 and 3, 2026-11-05), so check_unit
                    # would see a change nobody made.  The first SF command hands SF over from the pot
                    # until the next restart: set the value read, once, here.
                    self.unit.set_frequency(self.sf0)
                gain = self.unit.gain()
                self.steering = None if law is None else law(self.sf0, gain)
        except BaseException:
            self._restore_gain()
            self.unit.close()
            raise
        self.keep = keep_sf
        self.late = late
        self.step_s = step_s
        self.steps_taken = 0
        emit("frequency_control", identity=self.unit.identify(), sf0=self.sf0, status=list(status.bytes),
             conditions=status.conditions(), magnetic_dac=self.unit.magnetic_dac(), gain=gain, actuator=actuator)
        if self.fc:
            say(f"FS725 FC {self.fc_high},{self.sf0 - 1000 * self.fc_high} at the start, GA {gain} -> 1 for FC "
                f"steering; 1 pps PLL {'on' if self.unit.phase_lock() else 'off'}")
        else:
            say(f"FS725 SF {self.sf0} at the start; GA {gain}; 1 pps PLL {'on' if self.unit.phase_lock() else 'off'}")

    def _start_fc(self, gain: int) -> int:
        """FC read, its fine DAC brought near the middle when short of room, GA 1; FC's total."""
        high, low = self.unit.frequency_control()
        if min(low - FC_LOW[0], FC_LOW[1] - low) < self.FC_ROOM:
            shift = round((low - (FC_LOW[0] + FC_LOW[1]) / 2) / 1000)
            self.unit.write_frequency_control(high + shift, low - 1000 * shift)
            self.emit("fc_recentred", before=[high, low], after=[high + shift, low - 1000 * shift])
            high, low = high + shift, low - 1000 * shift
        self.gain_restore = gain
        self.unit.set_gain(1)
        self.fc_high = high
        return 1000 * high + low

    def _restore_gain(self) -> None:
        if self.gain_restore is None:
            return
        try:
            self.unit.set_gain(self.gain_restore)
        except Exception as exc:
            self.emit("restore_failed", reason=f"GA {self.gain_restore}: {exc}")
        self.gain_restore = None

    def set(self, sf: int, why: str, at_count: float | None = None) -> None:
        """Set SF (``at_count``: when, on this node's PPS count, for the step's fit)."""
        steer.check_unit(self.unit, self.sf)
        before = self.sf
        self.unit.set_frequency(sf)
        self.sf = sf
        self.emit("sf", sf=sf, why=why, magnetic_dac=self.unit.magnetic_dac(), at_count=at_count, change=sf - before)

    def _stopped(self, exc: Exception) -> None:
        self.emit("steer_stopped", reason=str(exc), sf=self.sf)
        self.say(f"steering stopped: {exc}")
        self.steering = None
        self._restore_gain()  # FC steering: the unit's own loop takes the crystal back

    def step(self, record: dict, at_count: float) -> None:
        """One step's theta (``StepTheta``) into a law faster than a second; SF written at
        once without a read-back (``at_count``: now on this node's PPS count, logged), the
        unit checked once a second."""
        if self.steering is None:
            return
        try:
            theta = record["theta_late_ns"]
            out = self.steering.step(record["index"], None if theta is None else theta * 1000)
            out["index"] = out.pop("count")
            self.emit("steer", count=record["count"], step=record["step"], **out)
            if self.fc:  # every step: also undoes what the slow loop pulled since the last
                new = self.sf if out["sf"] is None else out["sf"]
                self.unit.write_frequency_control(self.fc_high, new - 1000 * self.fc_high)
                if new != self.sf:
                    self.emit("fc_set", fc=new, low=new - 1000 * self.fc_high, at_count=at_count, change=new - self.sf)
                self.sf = new
            elif out["sf"] is not None:
                before = self.sf
                self.unit.write_frequency(out["sf"])
                self.sf = out["sf"]
                self.emit("sf", sf=self.sf, why="steer", at_count=at_count, change=self.sf - before)
            self.steps_taken += 1
            if self.steps_taken % round(1 / self.step_s) == 0:
                (steer.check_fc if self.fc else steer.check_unit)(self.unit, self.sf)
        except Exception as exc:  # SteeringError, FS725Error: stop steering, keep the link
            self._stopped(exc)

    def second(self, record: dict) -> None:
        """One second's theta into the steering; a correction when it is due."""
        if self.steering is None or self.step_s < 1:
            return
        try:
            theta = record.get("theta_late_ns") if self.late else record["theta_ns"]
            out = self.steering.step(record["count"], None if theta is None else theta * 1000)
            self.emit("steer", **out)
            if out["sf"] is not None:
                self.set(out["sf"], "steer")
        except Exception as exc:  # SteeringError, FS725Error: stop steering, keep the link
            self._stopped(exc)

    def close(self) -> None:
        if self.fc:  # FC stays where the law left it; the unit's own loop at its gain takes it back
            self._restore_gain()
        elif not self.keep and self.sf != self.sf0:
            try:
                self.set(self.sf0, "restore")
            except Exception as exc:
                self.emit("restore_failed", reason=str(exc), sf=self.sf, sf0=self.sf0)
        if self.pll_restore:
            try:
                self.unit.set_phase_lock(True)
            except Exception as exc:
                self.emit("restore_failed", reason=f"1 pps PLL: {exc}")
        self.say(f"FS725 {'FC' if self.fc else 'SF'} {self.sf} at the end (started at {self.sf0})")
        self.unit.close()


class SyncedRule:
    """When this node's output PPS is declared synchronized (SYNCED: the validity line
    and the LED).  Node A, the master: while its link is locked and a second brings at
    least ``min_pairs`` pairs (B's packets come in).  Node B: the same, while the steering
    runs, and over the last ``window_s`` seconds the steering error (theta, the mean over
    each second, minus the target) has an rms below ``set_rms_ps`` and a mean below
    ``set_mean_ps``; cleared when the link or the steering stops, or the last
    ``clear_window_s`` seconds have an rms above ``clear_rms_ps``."""

    def __init__(self, role_b: bool, *, set_rms_ps: float = 100.0, set_mean_ps: float = 30.0,
                 clear_rms_ps: float = 200.0, min_pairs: int = 200, window_s: int = 60,
                 clear_window_s: int = 10) -> None:
        self.role_b = role_b
        self.set_rms, self.set_mean, self.clear_rms = set_rms_ps, set_mean_ps, clear_rms_ps
        self.min_pairs, self.window, self.clear_window = min_pairs, window_s, clear_window_s
        self.errors: list[float] = []  # the steering error of consecutive good seconds
        self.last_count: int | None = None
        self.synced = False

    def _change(self, synced: bool) -> bool | None:
        if synced == self.synced:
            return None
        self.synced = synced
        return synced

    def second(self, record: dict, locked: bool, error_ps: float | None) -> bool | None:
        """One second's record; ``error_ps`` is theta minus the target (None on node A or
        without steering).  The new state when it changes, else None."""
        good = locked and record["pairs"] >= self.min_pairs
        consecutive = self.last_count is not None and record["count"] == self.last_count + 1
        self.last_count = record["count"]
        if not self.role_b:
            return self._change(good)
        if not good or error_ps is None:
            self.errors = []
            return self._change(False)
        self.errors = (self.errors if consecutive else []) + [error_ps]
        self.errors = self.errors[-self.window:]
        e = np.array(self.errors)
        if self.synced:
            last = e[-self.clear_window:]
            return self._change(not float(np.sqrt(np.mean(last * last))) > self.clear_rms)
        full = e.size >= self.window
        return self._change(full and float(np.sqrt(np.mean(e * e))) < self.set_rms and abs(float(e.mean())) < self.set_mean)

    def lost(self) -> bool | None:
        """The link lost its lock between seconds."""
        self.errors = []
        return self._change(False)


class EdgeCompensation:
    """The theta-compensated output PPS on node B (plan/pps_output.md section 7).

    Each node's output PPS leaves at its own tick, so out_B - out_A = C - (theta - theta0) at
    an edge (results/pps-out-20261104 section 8): moved later by theta - theta0, B's edge
    follows A's time scale to the measurement.  Before the edge that starts second ``edge``
    (on B's PPS count), ``frac(edge)`` takes the mean theta of the pairs of the last
    ``window_s`` before the newest, carries it to the edge by theta's rate over the last
    ``rate_s``, and turns the delay into P10's fraction: ``base_frac`` (the plain edge's place
    within its sample) plus the delay over ``ps_per_frac``, the slope calibrated on the scope,
    clipped to 0 .. 65535.  Pairs come from ``SecondTheta``'s pair sink (``add``).  On the bench
    (results/precision-20261105 section 6) the pairs up to 50 ms before an edge put it 1.4 ps
    rms from theta at the edge."""

    def __init__(self, target_ps: float, *, ps_per_frac: float, base_frac: int = 0x8000, window_s: float = 0.02,
                 rate_s: float = 1.0, slots_per_second: int = 500) -> None:
        self.target_ps, self.ps_per_frac, self.base = target_ps, ps_per_frac, base_frac
        self.window, self.rate_s, self.slots = window_s, rate_s, slots_per_second
        self.t: deque[float] = deque()
        self.theta: deque[float] = deque()

    def add(self, count: int, slot: int, theta_ns: float) -> None:
        self.t.append(count + slot / self.slots)
        self.theta.append(theta_ns * 1e3)
        while self.t[-1] - self.t[0] > self.rate_s:
            self.t.popleft()
            self.theta.popleft()

    def delay_ps(self, edge: float) -> float | None:
        """theta - theta0 at ``edge`` (B's count), or None without a second of recent pairs."""
        if len(self.t) < 50 or not 0 <= edge - self.t[-1] < 0.5:
            return None
        t = np.array(self.t)
        th = np.array(self.theta)
        rate = float(np.polyfit(t - t[-1], th, 1)[0])  # ps per second
        recent = t >= t[-1] - self.window
        return float(th[recent].mean() + rate * (edge - t[recent].mean())) - self.target_ps

    def frac(self, edge: float) -> tuple[int, float] | None:
        """P10's fraction for the edge, and the delay it stands for (ps), or None."""
        d = self.delay_ps(edge)
        if d is None:
            return None
        return int(min(max(round(self.base + d / self.ps_per_frac), 0), 0xFFFF)), d
