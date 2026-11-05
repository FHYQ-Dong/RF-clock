"""What one rf-clock 2 node does with its theta besides logging it: its FS725 (the check
against the board config, SF steps and steering) and the output PPS's SYNCED.

``configure_fs725`` checks the node's FS725 against the board config at the start of a run
and sets its gain (``rf_clock.fs725.check_config``); ``FrequencyControl`` holds the unit for
the run: the 1 pps PLL off so that SF takes effect, SF handed over from the calibration pot,
each second's or step's theta into the control law, SF back at the end; ``SyncedRule``
decides when the output PPS is declared synchronized.  ``rf_clock.checks.m4c_link`` drives
them (docs/ABI.md 11.12, 13).
"""

from __future__ import annotations

from typing import Callable, Protocol

import numpy as np

from rf_clock.fs725 import FS725, FS725Error, check_config
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
    each second; ``step_s`` below 1: on ``StepTheta``'s steps instead."""

    def __init__(self, port: str, emit: Callable[..., None], *, set_sf: bool,
                 law: Callable[[int, int], Law] | None = None, step_s: float = 1.0, late: bool = False,
                 keep_sf: bool = False, say: Callable[[str], None] = print) -> None:
        self.emit, self.say = emit, say
        self.unit = FS725.open(port, log=lambda e: emit("fs725", **e))
        try:
            status = self.unit.status()
            if not status.rb_locked:
                raise FS725Error("the FS725 is not locked to rubidium")
            self.pll_restore = False
            if set_sf and self.unit.phase_lock():
                self.unit.set_phase_lock(False)  # SF is ignored while it phase-locks to a 1 pps input
                self.pll_restore = True  # back on at the end, as found
            self.sf0 = self.sf = self.unit.frequency()
            if set_sf:
                # A unit that has had no SF command since power-up takes SF from its calibration
                # pot, whose reading flickers by a unit (A's: 2 and 3, 2026-11-05), so check_unit
                # would see a change nobody made.  The first SF command hands SF over from the pot
                # until the next restart: set the value read, once, here.
                self.unit.set_frequency(self.sf0)
            gain = self.unit.gain()
            self.steering: Law | None = None if law is None else law(self.sf0, gain)
        except BaseException:
            self.unit.close()
            raise
        self.keep = keep_sf
        self.late = late
        self.step_s = step_s
        self.steps_taken = 0
        emit("frequency_control", identity=self.unit.identify(), sf0=self.sf0, status=list(status.bytes),
             conditions=status.conditions(), magnetic_dac=self.unit.magnetic_dac(), gain=gain)
        say(f"FS725 SF {self.sf0} at the start; GA {gain}; 1 pps PLL {'on' if self.unit.phase_lock() else 'off'}")

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
            if out["sf"] is not None:
                before = self.sf
                self.unit.write_frequency(out["sf"])
                self.sf = out["sf"]
                self.emit("sf", sf=self.sf, why="steer", at_count=at_count, change=self.sf - before)
            self.steps_taken += 1
            if self.steps_taken % round(1 / self.step_s) == 0:
                steer.check_unit(self.unit, self.sf)
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
        if not self.keep and self.sf != self.sf0:
            try:
                self.set(self.sf0, "restore")
            except Exception as exc:
                self.emit("restore_failed", reason=str(exc), sf=self.sf, sf0=self.sf0)
        if self.pll_restore:
            try:
                self.unit.set_phase_lock(True)
            except Exception as exc:
                self.emit("restore_failed", reason=f"1 pps PLL: {exc}")
        self.say(f"FS725 SF {self.sf} at the end (started at {self.sf0})")
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
