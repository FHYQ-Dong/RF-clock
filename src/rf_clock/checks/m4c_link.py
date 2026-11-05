"""M4c on the board: one node of the rf-clock 2 link (docs/ABI.md 4.8-4.12, 11.14).

Wiring: this board's DAC_A -> band-pass -> attenuator -> the peer's ADC_B, the
peer's DAC_A back the same way; both boards on one 10 MHz and one PPS.  Run the
same command on both boards, ``--role A`` on one and ``--role B`` on the other
(experiments/m4c_v2_link.py does it from RF-clock-PC).

Steps: ``PL.open``; load the PRN (the code of the 1.x bursts,
``generate_test_chips(4096, 0xACE1)``), the packet code and the taps
(``fir.coefficients(--amplitude)``) into the burst memory; configure the correlator
(the earliest-peak threshold, the lock minimum, the quality offset of this
reference), PACKET_CONFIG_ID (the RRC configuration's id) and the slot engine
(``--role``, ``--tx-offset``, the window opened 1.25 us before ``--arrival-ticks``
or at ``--rx-offset``, with packets), enabled at ``--start-at`` (Unix time, default
now); then until ``--stop-at`` (default ``--seconds`` later) read the measurement
queue into ``--out``, one JSON line per entry, with the status every second.  Both
nodes get the same two times, half a second into a second, so that their engines
start and stop at the same PPS edge (the boards keep NTP time) and neither listens
while the other is silent.  Whenever a search waits for the ARM, its strongest
peak is accepted if it lies within ``--lag-tolerance`` samples of the lag the
expected arrival gives and its peak-to-sidelobe ratio reaches ``--min-psl-db``;
otherwise the PL keeps searching fresh windows.  At the end the engine is disabled.

Protocol tests, all while the link runs (times in seconds after the start):

``--copy-every S``      while locked, copy the next window's whole span to DDR
                        (SLOT_COPY) every S seconds and save its samples, for
                        experiments/m4c_check.py to recompute with rf_clock.plmodel;
``--search-every S``    while locked, ask for a search every S seconds (P8 SEARCH);
``--silent-at T --silent-for D``  disable this node's engine at T and enable it D
                        seconds later: the peer loses its lock and should take it
                        again by itself;
``--refusal-checks``    2 s after the first lock: a TX and an RX command through P2
                        must be refused (REJ_BUSY) and a burst memory write refused
                        and counted, the memory unchanged;
``--pause-reads-at T --pause-for D``  stop reading the queue for D seconds: the ring
                        overflows, the PL counts the entries it drops and the entry
                        sequence shows the gap;
``--acquire``           before enabling the slot engine (``--acquire-after`` seconds
                        after the start, when the peer transmits), find the peer's PPS
                        in a long window (``rf_clock.pl.acquire``, plan section 5.2) and,
                        with ``--fs725 PORT``, move this node's PPS onto it with the
                        FS725's PP, wait for the PL's PPS lock, and acquire again to check.

Every second the node also writes one ``second`` line: theta, the one-way delay and
their scatter over that second's pairs, from its own queue alone (``SecondTheta``);
``--no-entries`` leaves out the queue entries themselves, for runs of hours.  With
two clocks (each board on its own FS725), on B, with ``--fs725 PORT``:

``--sf-step N``         at ``--sf-step-at`` seconds set SF to its starting value + N
                        and back ``--sf-step-for`` seconds later: theta's rate should
                        change by N ps/s (check this before steering).  At the end each
                        step is fitted (``rf_clock.twtt.steer.fit_sf_response``): the
                        change of rate per SF unit and the delay after the command at
                        which it acts, the FS725's own response (``sf_response`` lines);
``--steer-target-ns T`` steer the FS725 (``rf_clock.twtt.steer``: the Kalman filter and
                        the control law, one theta per second) so that theta goes to T,
                        from the first lock on; at the end SF goes back to its starting
                        value unless ``--keep-sf``.  ``--steer-mode predictive`` uses
                        ``PredictiveSteering`` instead, with the model of the frequency
                        lock loop at the unit's GA (refused for a GA without one), and
                        ``--steer-late-slots N`` steers on theta over the last N slots
                        of each second (100: 0.2 s, results/predictive-steering-20261105).

``--fc-every S``         on B with ``--fs725``: read the FS725's FC (the frequency lock
                        loop's tuning of its crystal, PRS10 manual p. 24-25) every S
                        seconds and write it as ``fc`` lines with the time on this
                        node's PPS count; the loop changes it every 0.2 s.

``--pps-out OFFSET`` turns on this node's output PPS (``rf_clock.pl.pps_out``, DAC_B) at
OFFSET ticks after each PPS_TICK with ``--pps-out-amplitude`` (default -24000: DAC_B is
inverted, so the leading edge rises) and ``--pps-out-idle`` between pulses (default 0),
and puts its status into the status lines; it is left on at the end.  SYNCED stays clear.

PASS (the link alone): locked when the engine is disabled, no lock lost after the
first, no packet with a bad CRC, no queue entry dropped, no slot skipped after the
first lock, and PEER packets in at least 90 % of the peer's bursts since the
first lock.  The protocol tests change what is expected; m4c_check.py judges them.

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.m4c_link --role A
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from dataclasses import asdict

import numpy as np

from .board_common import RunLog, add_common_args, add_rfdc_args, mark, open_pl
from rf_clock.pl import acquire as acq
from rf_clock.pl.abi import ABI, ResultCode
from rf_clock.pl.board import die_c
from rf_clock.pl.client import CommandRejected
from rf_clock.pl.measurements import OwnMeasurement, PeerPacket
from rf_clock.pl.slots import rx_offset_for
from rf_clock.pl.tracking import quality_offset
from rf_clock.pl.waveform import (DEFAULT_BETA, DEFAULT_SAMPLES_PER_SYMBOL, DEFAULT_SPAN_CHIPS, generate_test_chips,
                                  rrc_config_id)
from rf_clock.plmodel import fir

PRN_CHIPS = 4096
PRN_SEED = 0xACE1


def expected_lag(arrival_ticks: int, rx_offset: int, tx_offset: int) -> int:
    """The window lag of a burst that arrives ``arrival_ticks`` after it starts."""
    return 8 * (tx_offset + arrival_ticks - rx_offset)


def iq_of(buffer, beats: int) -> tuple[np.ndarray, np.ndarray]:
    """I and Q (int16) of DMA beats in RX_LAYOUT: lane n of I at bits 16 n, of Q at 128 + 16 n."""
    words = np.frombuffer(bytes(buffer[: beats * 32]), dtype="<i2").reshape(beats, 16)
    return words[:, :8].ravel().copy(), words[:, 8:].ravel().copy()


class SecondTheta:
    """theta and the one-way delay of each pair (plan section 4.2) from this node's queue
    alone, averaged over the pairs whose window lies in one second of this node's
    PPS_COUNT: for each peer packet with a measurement, this node's T_rx of that burst
    (its OWN entry of the same window) and, from the packet, the peer's T_tx of it and
    the peer's T_rx of this node's burst one slot earlier (experiments/m4c_v2_link.py
    computes the same offline)."""

    def __init__(self, role: str, slots_per_second: int, slot_ticks: int, tx_offset: int, late_slots: int = 0) -> None:
        self.role_a = role == "A"
        self.slots, self.slot_ticks, self.tx_offset = slots_per_second, slot_ticks, tx_offset
        self.late_slots = late_slots  # also theta over the last this many slots of each second
        self.own: dict[tuple[int, int], int] = {}
        self.count: int | None = None
        self.theta: list[float] = []
        self.late: list[float] = []
        self.delay: list[float] = []

    def add(self, entry: OwnMeasurement | PeerPacket) -> dict | None:
        """Take one queue entry; when it is the first pair of a new second, return the
        record of the second before."""
        if isinstance(entry, OwnMeasurement):
            if entry.locked:
                self.own[(entry.pps_count, entry.slot)] = entry.t_rx_fs
            return None
        t_rx = self.own.pop((entry.pps_count, entry.slot), None)
        if t_rx is None or not entry.measurement:
            return None
        done = self.flush() if self.count is not None and entry.pps_count != self.count else None
        self.count = entry.pps_count
        own_tx = (((entry.slot - 1) % self.slots) * self.slot_ticks + self.tx_offset) * 10_000_000  # fs
        m_in = (t_rx - entry.t_tx_fs) / 1e6  # ns, peer -> this node
        m_out = (entry.t_rx_fs - own_tx) / 1e6  # ns, this node -> peer
        m_ab, m_ba = (m_out, m_in) if self.role_a else (m_in, m_out)
        self.theta.append((m_ab - m_ba) / 2)
        if self.late_slots and entry.slot >= self.slots - self.late_slots:
            self.late.append((m_ab - m_ba) / 2)
        self.delay.append((m_ab + m_ba) / 2)
        return done

    def flush(self) -> dict | None:
        """The record of the second gathered so far (None if it has no pair)."""
        if not self.theta:
            return None
        n = len(self.theta)
        steps = [b - a for a, b in zip(self.theta, self.theta[1:])]
        record = dict(count=self.count, pairs=n, theta_ns=statistics.fmean(self.theta),
                      theta_sd_ps=statistics.stdev(self.theta) * 1000 if n > 1 else None,
                      # from pair to pair, the clocks' drift within the second left out
                      theta_pair_sd_ps=statistics.stdev(steps) / 2 ** 0.5 * 1000 if n > 2 else None,
                      delay_ns=statistics.fmean(self.delay),
                      delay_sd_ps=statistics.stdev(self.delay) * 1000 if n > 1 else None)
        if self.late_slots:
            record["theta_late_ns"] = statistics.fmean(self.late) if self.late else None
        self.theta, self.late, self.delay = [], [], []
        assert self.count is not None
        self.own = {k: v for k, v in self.own.items() if k[0] >= self.count}
        return record


class FrequencyControl:
    """This node's FS725 for ``--sf-step`` and ``--steer-target-ns`` (role B): the unit's
    1 pps PLL off, so that SF takes effect, and SF back at its starting value at the end."""

    def __init__(self, args, emit, late_fraction: float = 0.0) -> None:
        from rf_clock.fs725 import FS725
        from rf_clock.twtt import steer

        self.emit = emit
        self.unit = FS725.open(args.fs725, log=lambda e: emit("fs725", **e))
        status = self.unit.status()
        if not status.rb_locked:
            raise SystemExit("the FS725 is not locked to rubidium")
        if self.unit.phase_lock():
            self.unit.set_phase_lock(False)  # SF is ignored while it phase-locks to a 1 pps input
        self.sf0 = self.sf = self.unit.frequency()
        self.keep = args.keep_sf
        self.steer_module = steer
        self.steering = None
        self.late = late_fraction > 0  # steer on theta over the end of each second
        gain = self.unit.gain()
        if args.steer_target_ns is not None and args.steer_mode == "predictive":
            try:
                self.steering = steer.predictive_for(
                    args.steer_target_ns * 1000, self.sf0, gain, late_fraction=late_fraction,
                    ar=tuple(float(a) for a in args.steer_ar.split(",")), horizon=args.steer_horizon,
                    moves=args.steer_moves, lam=args.steer_lambda, settle_s=args.steer_settle,
                    max_step=args.steer_max_step, max_offset=args.steer_max_offset)
            except steer.SteeringError as exc:
                self.unit.close()
                raise SystemExit(str(exc)) from exc
        elif args.steer_target_ns is not None:
            self.steering = steer.Steering(
                target_ps=args.steer_target_ns * 1000, sf0=self.sf0,
                filter=steer.filter_for(args.steer_clock_adev, args.steer_theta_sd), tau_s=args.steer_tau,
                interval_s=args.steer_interval, settle_s=args.steer_settle, max_step=args.steer_max_step,
                max_offset=args.steer_max_offset, response_delay_s=args.steer_response_delay,
                response_lag_s=args.steer_response_lag)
        emit("frequency_control", identity=self.unit.identify(), sf0=self.sf0, status=list(status.bytes),
             conditions=status.conditions(), magnetic_dac=self.unit.magnetic_dac(), gain=gain)
        mark(f"FS725 SF {self.sf0} at the start; 1 pps PLL off")

    def set(self, sf: int, why: str, at_count: float | None = None) -> None:
        """Set SF (``at_count``: when, on this node's PPS count, for the step's fit)."""
        self.steer_module.check_unit(self.unit, self.sf)
        before = self.sf
        self.unit.set_frequency(sf)
        self.sf = sf
        self.emit("sf", sf=sf, why=why, magnetic_dac=self.unit.magnetic_dac(), at_count=at_count, change=sf - before)

    def second(self, record: dict) -> None:
        """One second's theta into the steering; a correction when it is due."""
        if self.steering is None:
            return
        try:
            theta = record.get("theta_late_ns") if self.late else record["theta_ns"]
            out = self.steering.step(record["count"], None if theta is None else theta * 1000)
            self.emit("steer", **out)
            if out["sf"] is not None:
                self.set(out["sf"], "steer")
        except Exception as exc:  # SteeringError, FS725Error: stop steering, keep the link
            self.emit("steer_stopped", reason=str(exc), sf=self.sf)
            mark(f"steering stopped: {exc}")
            self.steering = None

    def close(self) -> None:
        if not self.keep and self.sf != self.sf0:
            try:
                self.set(self.sf0, "restore")
            except Exception as exc:
                self.emit("restore_failed", reason=str(exc), sf=self.sf, sf0=self.sf0)
        mark(f"FS725 SF {self.sf} at the end (started at {self.sf0})")
        self.unit.close()


def pps_now(pl) -> float:
    """Now on this node's PPS count: the latest edge's count plus the fraction of its second."""
    count, tick = pl.ctrl.latest_pps()
    return count + (pl.ctrl.read64(ABI.REG.TICK_LO) - tick) / 1e8


def steer_fit(thetas: list[tuple[int, float]], at_count: float) -> dict | None:
    from rf_clock.twtt.steer import fit_sf_response

    return fit_sf_response([c for c, _ in thetas], [t for _, t in thetas], at_count)


def acquire_peer(pl, prn, taps, args, emit) -> None:
    """Locate the peer's PPS in a long window; with --fs725, move this node's PPS so that theta
    starts at the steering target (0 without one), in whole ticks, and check.  The acquisition's
    offset is theta as the link will see it (peer's PPS minus this node's, in this node's time),
    to within the assumed one-way delay; a target of tens of ns (one that puts the two PPS
    together at the outputs, plan/checklist.md M5) is then reached by the PP, not slewed."""
    goal = args.steer_target_ns or 0.0

    def once(label: str) -> acq.Acquisition:
        t0 = time.monotonic()
        found = acq.acquire(pl, prn, taps, arrival_ticks=args.arrival_ticks)
        decoded = [b for b in found.bursts if b.packet is not None]
        mark(f"acquisition ({label}): {len(found.bursts)} bursts, {len(decoded)} packets decoded, "
             f"peer PPS {found.offset_ns} ns from ours (spread {found.spread_ticks} ticks), "
             f"{time.monotonic() - t0:.1f} s")
        emit("acquired", label=label, window_tick=found.window_tick, own_pps_tick=found.own_pps_tick,
             own_pps_count=found.own_pps_count, peer_pps_count=found.peer_pps_count, offset_ns=found.offset_ns,
             spread_ticks=found.spread_ticks,
             bursts=[dict(lag=b.lag, fraction=b.fraction, magnitude=b.magnitude, decoded=b.packet is not None,
                          slot=None if b.packet is None else b.packet.slot,
                          t_tx_fs=None if b.packet is None else b.packet.t_tx_fs) for b in found.bursts])
        return found

    found = once("first")
    if found.offset_ns is None:
        raise SystemExit("acquisition: no packet of the peer decoded")
    move = acq.place_pulse_ns(found.offset_ns - goal)
    if args.fs725 is None or move is None:  # within half a tick: nothing a whole-tick move can improve
        return
    from rf_clock.fs725 import FS725

    unit = FS725.open(args.fs725)
    try:
        unit.place_pulse(move)
    finally:
        unit.close()
    mark(f"FS725 PP {move}: this node's PPS moved by {found.offset_ns - goal:+.1f} ns (whole ticks), "
         f"toward the target {goal:+.3f} ns")
    emit("place_pulse", argument=move, offset_ns=found.offset_ns, goal_ns=goal)
    deadline = time.monotonic() + 30
    time.sleep(2.5)  # the moved edge breaks the PPS lock; it returns after a few good periods
    while not pl.ctrl.status().pps_locked:
        if time.monotonic() > deadline:
            raise SystemExit("acquisition: the PL did not lock the moved PPS within 30 s")
        time.sleep(0.5)
    pl.ctrl.clear_fault()  # the PPS jump is latched as a fault
    after = once("after PP")
    if after.offset_ns is None or abs(after.offset_ns - goal) > 20:
        raise SystemExit(f"acquisition: still {after.offset_ns} ns from the peer's PPS after PP (target {goal} ns)")


def main() -> None:
    from rf_clock.twtt import steer as steer_defaults

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--role", choices=("A", "B"), required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--start-at", type=float, default=None, help="Unix time to enable the slot engine")
    parser.add_argument("--stop-at", type=float, default=None, help="Unix time to disable it")
    parser.add_argument("--out", default="/dev/shm/m4c_link.jsonl")
    parser.add_argument("--copy-dir", default="/dev/shm/m4c_copies")
    parser.add_argument("--log", default="m4c_link_runs.jsonl")
    parser.add_argument("--amplitude", type=float, default=16000.0, help="RRC taps: round(h x amplitude), as M3/M4")
    parser.add_argument("--tx-offset", type=int, default=1000, help="ticks from the slot start to the burst")
    parser.add_argument("--arrival-ticks", type=int, default=29, help="expected one-way delay in ticks")
    parser.add_argument("--rx-offset", type=int, default=None, help="ticks to the window (default: from the arrival)")
    parser.add_argument("--lag-tolerance", type=int, default=400, help="samples around the expected lag")
    parser.add_argument("--min-psl-db", type=float, default=10.0)
    parser.add_argument("--threshold", type=float, default=0.5, help="earliest peak: magnitude relative to the strongest")
    parser.add_argument("--lock-min", type=float, default=0.5, help="tracked magnitude relative to the last lock's")
    parser.add_argument("--poll", type=float, default=0.02, help="seconds between queue reads")
    parser.add_argument("--copy-every", type=float, default=0.0)
    parser.add_argument("--search-every", type=float, default=0.0)
    parser.add_argument("--silent-at", type=float, default=None)
    parser.add_argument("--silent-for", type=float, default=3.0)
    parser.add_argument("--refusal-checks", action="store_true")
    parser.add_argument("--pause-reads-at", type=float, default=None)
    parser.add_argument("--pause-for", type=float, default=3.0)
    parser.add_argument("--acquire", action="store_true")
    parser.add_argument("--acquire-after", type=float, default=2.5)
    parser.add_argument("--fs725", default=None, help="this node's FS725 serial port, for PP and SF")
    parser.add_argument("--no-entries", action="store_true", help="write the per-second lines, not the queue entries")
    parser.add_argument("--sf-step", type=int, default=0, help="test: SF + this (1e-12) for a while")
    parser.add_argument("--sf-step-at", type=float, default=60.0)
    parser.add_argument("--sf-step-for", type=float, default=60.0)
    parser.add_argument("--steer-target-ns", type=float, default=None, help="steer the FS725 so that theta goes here")
    parser.add_argument("--steer-tau", type=float, default=50.0)
    parser.add_argument("--steer-interval", type=int, default=10)
    parser.add_argument("--steer-settle", type=int, default=30)
    parser.add_argument("--steer-max-step", type=int, default=100)
    parser.add_argument("--steer-max-offset", type=int, default=500)
    parser.add_argument("--steer-clock-adev", type=float, default=2e-11)
    parser.add_argument("--steer-theta-sd", type=float, default=3.0)
    parser.add_argument("--steer-response-delay", type=float, default=1.9,
                        help="s after the second that asked for it that a change of SF acts (B's FS725, 2026-11-03)")
    parser.add_argument("--steer-mode", choices=("kalman", "predictive"), default="kalman",
                        help="kalman: rf_clock.twtt.steer.Steering (tau, interval); predictive: PredictiveSteering, "
                             "a correction every second from the FS725's loop model at its GA and an AR prediction")
    parser.add_argument("--steer-late-slots", type=int, default=0, metavar="N",
                        help="steer on theta over the last N slots of each second (second lines get theta_late_ns)")
    parser.add_argument("--steer-ar", default=",".join(str(a) for a in steer_defaults.AR4),
                        help="predictive: AR coefficients of the frequency about its 60 s mean")
    parser.add_argument("--steer-horizon", type=int, default=4, help="predictive: s held on the target")
    parser.add_argument("--steer-moves", type=int, default=2, help="predictive: changes planned each second")
    parser.add_argument("--steer-lambda", type=float, default=0.05, help="predictive: cost of a change, ps^2 per unit^2")
    parser.add_argument("--steer-response-lag", type=float, default=0.0,
                        help="then the frequency follows with this first-order time constant, s (0: at once)")
    parser.add_argument("--keep-sf", action="store_true", help="leave SF where the steering put it")
    parser.add_argument("--fc-every", type=float, default=0.0, metavar="S",
                        help="on B with --fs725: log the FS725's FC every S seconds (0.1 catches every update)")
    parser.add_argument("--pps-out", type=int, default=None, metavar="OFFSET",
                        help="the output PPS on DAC_B, OFFSET ticks after each PPS_TICK")
    parser.add_argument("--pps-out-amplitude", type=int, default=-24000, help="its DAC code")
    parser.add_argument("--pps-out-idle", type=int, default=0, help="its DAC code between pulses (2.2)")
    add_common_args(parser)
    add_rfdc_args(parser)
    args = parser.parse_args()

    log = RunLog(args.log, role=args.role, seconds=args.seconds, arrival_ticks=args.arrival_ticks)
    pl = open_pl(args, log)
    if pl is None:
        raise SystemExit(1)

    prn = generate_test_chips(PRN_CHIPS, PRN_SEED)
    taps = fir.coefficients(args.amplitude)
    config_id = rrc_config_id(DEFAULT_BETA, DEFAULT_SPAN_CHIPS, DEFAULT_SAMPLES_PER_SYMBOL)
    pl.slots.disable()
    pl.burst.load(prn, taps)
    window_samples = 8 * pl.slots.window_beats
    offset = quality_offset(window_samples, fir.shape(prn, taps))
    pl.correlator.configure(threshold=args.threshold, lock_min=args.lock_min, quality_offset=offset)
    pl.ctrl.write32(ABI.REG.PACKET_CONFIG_ID, config_id)
    if args.pps_out is not None:
        pl.pps_out.configure(amplitude=args.pps_out_amplitude, offset=args.pps_out, idle=args.pps_out_idle)
    rx_offset = rx_offset_for(args.tx_offset, args.arrival_ticks) if args.rx_offset is None else args.rx_offset
    want_lag = expected_lag(args.arrival_ticks, rx_offset, args.tx_offset)
    span = pl.slots.span_beats
    mark(f"node {args.role}: {PRN_CHIPS}-chip PRN, {pl.correlator.lags} lags, quality offset {offset}, "
         f"tx_offset {args.tx_offset}, rx_offset {rx_offset}, expected lag {want_lag}")

    out = open(args.out, "w")

    def emit(kind: str, **fields) -> None:
        out.write(json.dumps({"type": kind, "time": time.time(), **fields}) + "\n")

    emit("config", role=args.role, prn_chips=PRN_CHIPS, prn_seed=PRN_SEED, amplitude=args.amplitude,
         tx_offset=args.tx_offset, rx_offset=rx_offset, arrival_ticks=args.arrival_ticks, expected_lag=want_lag,
         window_beats=pl.slots.window_beats, span_beats=span, slot_ticks=pl.slots.slot_ticks,
         slots_per_second=pl.slots.slots_per_second, quality_offset=offset, config_id=config_id,
         serial=pl.board.serial, label=pl.board.config.label if pl.board.config else None,
         tests={k: getattr(args, k) for k in ("copy_every", "search_every", "silent_at", "silent_for",
                                              "refusal_checks", "pause_reads_at", "pause_for", "sf_step",
                                              "sf_step_at", "sf_step_for", "steer_target_ns", "steer_tau",
                                              "steer_interval", "steer_settle", "steer_max_step",
                                              "steer_max_offset", "steer_clock_adev", "steer_theta_sd",
                                              "steer_response_delay", "steer_response_lag", "steer_mode",
                                              "steer_late_slots", "steer_ar", "steer_horizon", "steer_moves",
                                              "steer_lambda", "pps_out", "pps_out_amplitude",
                                              "pps_out_idle")})
    start_at = time.time() if args.start_at is None else args.start_at
    stop_at = start_at + args.seconds if args.stop_at is None else args.stop_at
    if time.time() < start_at:
        mark(f"waiting {start_at - time.time():.1f} s for the start")
        time.sleep(start_at - time.time())
    if args.acquire:
        time.sleep(max(0.0, start_at + args.acquire_after - time.time()))
        acquire_peer(pl, prn, taps, args, emit)
    control = None
    if args.sf_step or args.steer_target_ns is not None or args.fc_every:
        if args.role != "B" or args.fs725 is None:
            raise SystemExit("--sf-step, --steer-target-ns and --fc-every are for node B, with --fs725")
        control = FrequencyControl(args, emit, args.steer_late_slots / pl.slots.slots_per_second)
    seconds = SecondTheta(args.role, pl.slots.slots_per_second, pl.slots.slot_ticks, args.tx_offset,
                          args.steer_late_slots)
    pl.queue.clear()
    pl.slots.configure(role_b=args.role == "B", rx_offset=rx_offset, tx_offset=args.tx_offset, packets=True)
    mark("slot engine enabled: it starts at the next PPS edge")

    if args.copy_every:
        os.makedirs(args.copy_dir, exist_ok=True)
    first_lock: float | None = None
    lost_at_lock = 0
    skipped_at_lock = 0
    peer_packets = 0
    peer_windows = 0
    counted: set[tuple[int, int]] = set()  # windows counted whose packet has not come yet
    seen_report = 0
    copies = 0
    copy_seq: int | None = None  # RX_SEQ before the copy in flight
    copy_asked = 0.0
    next_copy = next_search = 0.0
    silent_state = 0  # 0 before, 1 silent, 2 enabled again
    refusals_done = False
    paused_until = None
    step_state = 0  # 0 before the SF step, 1 during, 2 after
    steps: list[tuple[float, int]] = []  # (when on this node's PPS count, change of SF)
    thetas: list[tuple[int, float]] = []  # (second, theta ps) for the step's fit
    next_status = next_fc = time.monotonic()
    begin = time.monotonic()
    end = begin + (stop_at - time.time())
    while time.monotonic() < end:
        now = time.monotonic() - begin
        reading = paused_until is None or now >= paused_until
        if args.pause_reads_at is not None and paused_until is None and now >= args.pause_reads_at:
            paused_until = now + args.pause_for
            reading = False
            emit("pause_reads", seconds=args.pause_for, head=pl.ctrl.read32(ABI.REG.QUEUE_HEAD),
                 dropped=pl.queue.dropped())
        if reading:
            for entry in pl.queue.read():
                kind = "own" if isinstance(entry, OwnMeasurement) else "peer"
                if not args.no_entries:
                    emit(kind, **asdict(entry))
                done = seconds.add(entry)
                if done is not None:
                    emit("second", **done)
                    thetas.append((done["count"], done["theta_ns"] * 1000))
                    if control is not None and first_lock is not None:
                        control.second(done)
                if first_lock is not None:
                    key = (entry.pps_count, entry.slot)
                    if kind == "own":
                        peer_windows += 1
                        counted.add(key)
                    elif key in counted:  # not the packet of a window read before the first lock
                        peer_packets += 1
                        counted.discard(key)
        status = pl.correlator.status()
        if status.locked and first_lock is None:
            first_lock = now
            lost_at_lock = status.lost
            skipped_at_lock = pl.slots.status().skipped
            next_copy = next_search = now + 1.0
            mark(f"locked at lag {status.lock_lag}")
            emit("locked", lag=status.lock_lag, magnitude=status.lock_magnitude)
        report = pl.correlator.report()
        if report is not None and report.seq != seen_report:
            seen_report = report.seq
            emit("report", seq=report.seq, pps_count=report.pps_count, slot=report.slot, flags=int(report.flags),
                 tick=report.tick, strongest=asdict(report.strongest), earliest=asdict(report.earliest),
                 sidelobe=report.sidelobe, psl_db=report.peak_to_sidelobe_db, locked=status.locked,
                 lock_lag=status.lock_lag)
            if status.waiting and not status.locked:
                near = abs(report.strongest.lag - want_lag) <= args.lag_tolerance
                strong = report.peak_to_sidelobe_db >= args.min_psl_db
                if near and strong:
                    pl.correlator.accept()
                    mark(f"accepted the strongest peak at lag {report.strongest.index:.3f} "
                         f"(expected {want_lag}), PSL {report.peak_to_sidelobe_db:.1f} dB")
                    emit("accept", seq=report.seq, lag=report.strongest.lag)
                else:
                    emit("reject", seq=report.seq, lag=report.strongest.lag, near=near, strong=strong,
                         psl_db=report.peak_to_sidelobe_db)

        # ---- protocol tests ----
        if args.search_every and status.locked and first_lock is not None and now >= next_search:
            next_search = now + args.search_every
            pl.correlator.search()
            emit("search_request", lock_lag=status.lock_lag)
        if args.copy_every and status.locked and copy_seq is None and first_lock is not None and now >= next_copy:
            next_copy = now + args.copy_every
            pl.rx_dma.prepare(span)
            copy_seq = pl.ctrl.read32(ABI.REG.RX_SEQ)
            copy_asked = now
            pl.slots.copy_next_window()
        if copy_seq is not None and pl.ctrl.read32(ABI.REG.RX_SEQ) != copy_seq:
            raw = pl.ctrl.read32(ABI.REG.RX_RESULT)
            beats = pl.ctrl.read32(ABI.REG.RX_BEATS)
            tick = pl.ctrl.read64(ABI.REG.RX_ACTUAL_LO)
            record = dict(tick=tick, beats=beats, result=raw & 0xFF, flags=(raw >> ABI.RX_RESULT_FLAGS_LSB) & 0xF)
            if (raw & 0xFF) == ResultCode.DONE and beats == span:
                data = pl.rx_dma.wait(beats)
                rx_i, rx_q = iq_of(data.buffer, beats)
                path = os.path.join(args.copy_dir, f"copy_{copies:03d}.npz")
                np.savez(path, i=rx_i, q=rx_q)
                copies += 1
                emit("copy", file=path, **record)
            else:
                pl.rx_dma.abort()
                emit("copy_failed", **record)
            copy_seq = None
        elif copy_seq is not None and now - copy_asked > 1.0:
            pl.rx_dma.abort()
            emit("copy_failed", reason="no window copied within 1 s")
            copy_seq = None
        if args.silent_at is not None:
            if silent_state == 0 and now >= args.silent_at:
                pl.slots.disable()
                silent_state = 1
                mark(f"silent for {args.silent_for} s")
                emit("silent", seconds=args.silent_for)
            elif silent_state == 1 and now >= args.silent_at + args.silent_for:
                pl.slots.enable()
                silent_state = 2
                emit("enabled_again")
        if args.sf_step and control is not None:
            if step_state == 0 and now >= args.sf_step_at:
                at = pps_now(pl)
                control.set(control.sf0 + args.sf_step, "step", at)
                steps.append((at, args.sf_step))
                step_state = 1
                mark(f"SF step: {control.sf}")
            elif step_state == 1 and now >= args.sf_step_at + args.sf_step_for:
                at = pps_now(pl)
                control.set(control.sf0, "step back", at)
                steps.append((at, -args.sf_step))
                step_state = 2
                mark(f"SF back to {control.sf}")
        if args.refusal_checks and not refusals_done and first_lock is not None and now >= first_lock + 2.0:
            refusals_done = True
            outcome = {}
            for name, arm in (("tx", pl.ctrl.arm_tx), ("rx", pl.ctrl.arm_rx)):
                try:
                    arm(pl.ctrl.read64(ABI.REG.TICK_LO) + 1_000_000, 4)
                    outcome[name] = "accepted"
                except CommandRejected as exc:
                    outcome[name] = ResultCode(exc.result).name
            before = int(pl.burst.memory.read(ABI.BMEM.TAPS_OFFSET))
            refused = pl.ctrl.read32(ABI.REG.BURST_MEM_REJ_CNT)
            pl.burst.memory.write(ABI.BMEM.TAPS_OFFSET, before ^ 0x5555)
            outcome["burst_write_refused"] = pl.ctrl.read32(ABI.REG.BURST_MEM_REJ_CNT) - refused
            outcome["burst_memory_unchanged"] = int(pl.burst.memory.read(ABI.BMEM.TAPS_OFFSET)) == before
            mark(f"refusals: {outcome}")
            emit("refusals", **outcome)
        if args.fc_every and control is not None and time.monotonic() >= next_fc:
            next_fc += args.fc_every
            at = pps_now(pl)
            high, low = control.unit.frequency_control()
            emit("fc", count=at, high=high, low=low)
        if time.monotonic() >= next_status:
            next_status += 1.0
            slots = pl.slots.status()
            emit("status", running=slots.running, skipped=slots.skipped, correlator=asdict(status),
                 packets_rx=pl.ctrl.read32(ABI.REG.PACKET_RX_CNT), packets_crc=pl.ctrl.read32(ABI.REG.PACKET_CRC_CNT),
                 bursts=pl.ctrl.read32(ABI.REG.BURST_CNT), dropped=pl.queue.dropped(),
                 pps_jitter=pl.ctrl.read32(ABI.REG.PPS_JITTER_CNT), die_c=die_c(),
                 pps_out=asdict(pl.pps_out.status()) if args.pps_out is not None else None)
        time.sleep(args.poll)

    locked_at_stop = pl.correlator.status().locked
    final = pl.correlator.status()
    pl.slots.disable()
    time.sleep(1.5)  # the engine stops at the next PPS edge; the last entries follow
    for entry in pl.queue.read():
        if not args.no_entries:
            emit("own" if isinstance(entry, OwnMeasurement) else "peer", **asdict(entry))
        done = seconds.add(entry)
        if done is not None:
            emit("second", **done)
    done = seconds.flush()
    if done is not None:
        emit("second", **done)
    if control is not None:
        control.close()
    responses = []
    for at, change in steps:  # the FS725's answer to each SF step
        fit = steer_fit(thetas, at)
        if fit is not None:
            fit.update(at_count=at, sf_change=change, ps_per_s_per_sf=fit["rate_ps_per_s"] / change)
            responses.append(fit)
            emit("sf_response", **fit)
            mark(f"SF {change:+d}: theta's rate {fit['rate_ps_per_s']:+.1f} ps/s "
                 f"({fit['ps_per_s_per_sf']:.3f} per SF unit), acting {fit['delay_s']:.2f} s after the command")
    slots = pl.slots.status()
    crc = pl.ctrl.read32(ABI.REG.PACKET_CRC_CNT)
    dropped = pl.queue.dropped()
    result = dict(
        locked=locked_at_stop, first_lock_s=None if first_lock is None else round(first_lock, 3),
        lost_after_lock=final.lost - lost_at_lock, relocks=final.relocks, crc_errors=crc, dropped=dropped,
        skipped_after_lock=slots.skipped - skipped_at_lock, peer_packets=peer_packets, peer_windows=peer_windows,
        bursts=pl.ctrl.read32(ABI.REG.BURST_CNT), searches=final.searches, refused=final.refused, copies=copies,
    )
    emit("result", **result)
    out.close()
    passed = (first_lock is not None and locked_at_stop and result["lost_after_lock"] == 0 and crc == 0
              and dropped == 0 and result["skipped_after_lock"] == 0
              and peer_packets >= 0.9 * max(peer_windows, 1))
    log.record.update(result)
    if responses:
        log.record["sf_responses"] = responses
    print(f"M4c node {args.role}: {'PASS' if passed else 'FAIL'} {result}")
    log.write(passed)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
