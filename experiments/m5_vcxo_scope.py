"""M5: a board's VCXO against its FS725, read directly on the scope instead of from tick counts.

Problem.  The overnight run (results/m5-free-oscillators-20261110_201505/REPORT.md) counted
the PL's 100 MHz ticks between FS725 B's 1PPS edges.  Each count is good to one tick, 10 ns,
so its Allan deviation is the counter's own 5e-9/tau below 5 s, and the VCXO's own short-term
curve, where it falls and where it turns, could not be seen.  Here the board makes its own
1PPS from the free-running VCXO, the output PPS on DAC_B (J20), and the scope times that edge
against the FS725's 10 MHz to a few ps every second.

Mechanism.  With the LMK04828 forced into holdover (register 0x14B bit 3, the switch that
connects the VCXO's tuning voltage to the holdover DAC, set to 512 by our register file), the
VCXO runs 1e-5 or so slow (8.4e-6 on 8873292600AB, 1.73e-5 on 887329260072A) and every clock
of the board with it.  The FS725's 1PPS then arrives
hundreds of ticks off 100 000 000, so P1 drops its PPS lock and never takes it back (it needs
four periods within one tick).  From then on P10 counts 100 000 000 ticks of the free clock per
second by itself (its flywheel, PL/src/p10_pps_out.v) and sends its pulse OFFSET ticks into
each such second.  The output edge is therefore the VCXO's own second, and its time against
the FS725's time is the VCXO's phase.

Measurement.  The scope (experiments/m5_hdo_phase.py --out-pps-free) triggers on the output PPS
and fits the FS725's 10 MHz (C1) in the same 1 us record, which gives the output edge's time
against the 10 MHz modulo its 100 ns period (out_vs_10mhz_ns), to a few ps.  The board gives the
same edge's time after the FS725's 1PPS edge to one tick, about 10 ns: in the flywheel the pulses
lie exactly 100 000 000 ticks apart, and the PPS edge's tick is latched every second.  The coarse
time picks the 100 ns period, the scope supplies the rest.  The two machines' clocks match a
scope record to its second up to one offset in whole seconds, which the analysis takes as the
one that makes coarse and fine times agree (a few ns rms, against 29 ns for a wrong match).

What holds the VCXO's tuning voltage (``--hold``).  With ``dac`` (the default) the holdover DAC
drives the tuning pin for the whole run, so the DAC's noise is part of what is measured.  With
``capacitor`` the DAC only sets the voltage for 10 s, then PLL1's charge pump goes to high
impedance (register 0x15B bit 5) and holdover ends, so nothing drives the pin and the loop
filter's capacitors alone hold the voltage.  That measures the VCXO itself, plus a slow drift
from the capacitors' leakage, which the analysis also takes out as a straight line of frequency.
With ``unplug`` the LMK04828 keeps the register file's set-up, as in a TWTT run, and the board
waits until the FS725's 10 MHz is taken off its CLK IN by hand (the run prints when).  That is
the clock a node would have if its FS725's 10 MHz were lost while running (the register file
enables holdover on PLL1's loss of lock, with the DAC at 512).  With ``noref`` the board
disables holdover and turns CLKin0's buffer off itself, so PLL1 loses its reference without
holdover, as on a board started with no FS725, and the charge pump drives the tuning voltage to
one end of its range.  The run starts once the frequency has settled there.

Wiring, one board and the FS725 that clocks it (the defaults are the run of 2026-11-11, board
887329260072A at 192.168.2.99 with its FS725).

- The FS725's 10 MHz to the board's CLK IN (PL.open locks to it first).
- The FS725's other 10 MHz output to scope C1 (``--c1-coupling DC50`` when the cable ends at
  the scope, DC1M when it goes on through a tee).
- The FS725's 1PPS to the board's PPS input J7 (needed for PL.open and for the tick counts).
  A tee to the scope does no harm, the channel is not read.
- The board's J20 (DAC_B) to the scope, DC 50 ohm, on ``--out-channel`` (C3).  Board
  887329260072A's pulse comes out negative (about -0.22 V), so the scope triggers on its falling
  edge (``--out-positive`` for a board whose pulse is positive).

Run on RF-clock-PC as the normal user (the scope part through sudo)::

    .venv/bin/python experiments/m5_vcxo_scope.py [--seconds 3600]
    .venv/bin/python experiments/m5_vcxo_scope.py --analyze results/m5-vcxo-scope-<time>

Each run writes results/m5-vcxo-scope-<time>/ with hdo.jsonl (the scope), board.jsonl (one JSON
line a second from the board), vcxo_scope.json (the statistics) and vcxo_scope.png.  The board
writes the LMK04828's 0x14B back and turns the output PPS off at its end (the log ends with
`released`).
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SERIALS = {"192.168.2.99": "887329260072A", "192.168.2.100": "8873292600AB", "192.168.2.101": "887329260101"}
PYNQ_PYTHON = "/usr/local/share/pynq-venv/bin/python"
REMOTE = "/tmp/rf-clock-experiments"
ENV = "env BOARD=RFSoC4x2 XILINX_XRT=/usr"
BOARD_LOG = "/dev/shm/vcxo_scope.jsonl"
HOLDOVER_FORCE = 0x08  # LMK04828 register 0x14B bit 3
PLL1_CP_TRI = 0x20  # LMK04828 register 0x15B bit 5, PLL1's charge pump output (CPout1) at high impedance
HOLDOVER_EN = 0x01  # LMK04828 register 0x150 bit 0, holdover may be entered (on PLL1's loss of lock here)
CLKIN0_OFF = 0x03  # LMK04828 register 0x147 bits 1:0, CLKin0's buffer output to nowhere (3), not PLL1 (2)
VCXO_HZ = 160e6
TICKS = 100_000_000  # PL ticks in one second at the nominal frequency
PERIOD_NS = 100.0  # the FS725's 10 MHz
EDGE_PS = 8.0  # the scope's timing of one output edge against the 10 MHz, rms (m5_hdo_phase.py, 7 ps and 3-4 ps)
TAUS = (1, 2, 5, 10, 20, 50, 100, 200, 500)
HORIZONS = (1, 10, 100, 1000)
OVERNIGHT = HERE.parent / "results" / "m5-free-oscillators-20261110_201505" / "free_oscillators.json"
OVERNIGHT_BOARD = "192.168.2.100"  # whose VCXO the overnight run measured


# ------------------------------------------------------------------------------------- on the board
def board_run(seconds: int, offset: int, hold: str) -> None:
    """On the board, PL.open, the output PPS on, the LMK04828 into holdover, then one JSON line
    per 1PPS edge of the FS725, and at the end the holdover released and the output PPS off.

    With ``hold`` "capacitor" the holdover DAC only sets the tuning voltage.  After 10 s the
    charge pump goes to high impedance and holdover ends, so that neither the DAC nor the charge
    pump drives the VCXO's tuning pin and the loop filter's capacitors alone hold its voltage
    (TI SNAS605AS 9.3.7 and register 0x15B).  With "unplug" nothing is written to the LMK04828.
    The board waits until the FS725's 10 MHz is taken off its CLK IN, which it sees from the
    PPS periods, and measures the VCXO as the board's normal clock set-up leaves it.  With "noref"
    holdover is disabled and CLKin0's buffer turned off, so PLL1 loses its reference without
    entering holdover, as on a board that never had one, and the charge pump drives the tuning
    voltage to one end.  The measurement starts once the frequency has settled there."""
    from rf_clock.pl import PL
    from rf_clock.pl.board import LMK_FREQ_MHZ, clock_registers, die_c
    from xrfclk import xrfclk  # pyright: ignore[reportMissingImports]

    def emit(kind: str, **fields) -> None:
        print(json.dumps({"type": kind, "time": time.time(), **fields}), flush=True)

    def on_term(signum, frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    pl = PL.open(progress=lambda s: emit("open", step=s))
    emit("opened", serial=pl.board.serial)
    out = pl.pps_out
    out.configure(amplitude=24000, offset=offset)
    out.set_synced(False)
    deadline = time.monotonic() + 30
    while out.status().pulses < 3 or not pl.ctrl.status().pps_locked:
        if time.monotonic() > deadline:
            emit("error", reason="no output pulses with the PPS locked within 30 s")
            out.disable()
            return
        time.sleep(0.2)
    image = {w >> 8: w for w in clock_registers(f"LMK04828_{LMK_FREQ_MHZ}.txt")}
    word, man_dac = image[0x14B], ((image[0x14B] & 0x3) << 8) | (image[0x14C] & 0xFF)
    cp, sel, ho = image[0x15B], image[0x147], image[0x150]
    try:
        if hold == "noref":
            for lmk in xrfclk.lmk_devices:
                xrfclk._write_LMK_regs([ho & ~HOLDOVER_EN, (sel & ~0x03) | CLKIN0_OFF], lmk)
            emit("reference_off", holdover=hex(ho & ~HOLDOVER_EN), clkin=hex((sel & ~0x03) | CLKIN0_OFF))
            seen, last, periods, deadline = None, None, [], time.monotonic() + 300
            while True:  # periods off 100 000 000 and steady to 5 ticks over 10 s: the tuning voltage is at its end
                if time.monotonic() > deadline:
                    emit("note", text="the frequency did not settle within 300 s, measuring anyway")
                    break
                count, tick = pl.ctrl.latest_pps()
                if count == seen:
                    time.sleep(0.05)
                    continue
                if seen is not None and last is not None and count == seen + 1:
                    periods.append(tick - last)
                seen, last = count, tick
                tail = periods[-10:]
                if len(tail) == 10 and min(abs(x - TICKS) for x in tail) > 100 and max(tail) - min(tail) <= 5:
                    break
            emit("noref", periods=periods[-10:])
        elif hold == "unplug":
            emit("unplug", note="take the FS725's 10 MHz off the board's CLK IN now")
            seen, last, off, deadline = None, None, 0, time.monotonic() + 1200
            while off < 3:  # three periods in a row more than 100 ticks off: the reference is gone
                if time.monotonic() > deadline:
                    emit("error", reason="the 10 MHz was not taken off CLK IN within 20 min")
                    out.disable()
                    return
                count, tick = pl.ctrl.latest_pps()
                if count == seen:
                    time.sleep(0.05)
                    continue
                if seen is not None and last is not None and count == seen + 1:
                    off = off + 1 if abs(tick - last - TICKS) > 100 else 0
                seen, last = count, tick
            emit("unplugged")
        else:
            for lmk in xrfclk.lmk_devices:
                xrfclk._write_LMK_regs([word | HOLDOVER_FORCE], lmk)
            emit("holdover", word=hex(word | HOLDOVER_FORCE), man_dac=man_dac, offset=offset)
        if hold == "capacitor":
            time.sleep(10)  # the loop filter settles at the DAC's voltage
            for lmk in xrfclk.lmk_devices:
                xrfclk._write_LMK_regs([cp | PLL1_CP_TRI, word], lmk)  # charge pump off, then holdover off
            emit("capacitor", cp=hex(cp | PLL1_CP_TRI), word=hex(word))
        seen = None
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            count, tick = pl.ctrl.latest_pps()
            if count == seen:
                time.sleep(0.05)
                continue
            seen = count
            st = out.status()
            emit("pps", count=count, tick=tick, pulse_tick=st.last_tick, pulses=st.pulses,
                 flywheel_pulses=st.flywheel_pulses, pps_locked=pl.ctrl.status().pps_locked, die_c=die_c())
    except KeyboardInterrupt:
        pass
    finally:
        if hold == "noref":
            for lmk in xrfclk.lmk_devices:
                xrfclk._write_LMK_regs([sel, ho], lmk)  # CLKin0 back to PLL1, holdover enabled: PLL1 locks again
        if hold not in ("unplug", "noref"):
            for lmk in xrfclk.lmk_devices:
                xrfclk._write_LMK_regs([word, cp], lmk)  # holdover off, charge pump on: PLL1 locks again
        out.disable()
        emit("released", word=hex(word))


# ---------------------------------------------------------------------------------- on RF-clock-PC
def ssh(ip: str, cmd: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    argv = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"xilinx@{ip}", cmd]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 255, "", "timeout")


BOARD_PATTERN = f"^{PYNQ_PYTHON} {REMOTE}/{Path(__file__).name} --board-run"
SCOPE_LOGGER = [sys.executable, str(HERE / "m5_hdo_phase.py"), "--out-pps-free"]


def board_lines(ip: str) -> list[dict]:
    rows = []
    for line in ssh(ip, f"cat {BOARD_LOG}").stdout.splitlines():
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def run(args) -> Path:
    from m3_common import results_dir

    ip, name = args.board, SERIALS.get(args.board, args.board)
    busy = ssh(ip, f"ps -eo args | grep -cE '^{PYNQ_PYTHON} (-m rf_clock|{REMOTE}/)'")
    if busy.returncode == 255:
        sys.exit(f"cannot reach {name} ({busy.stderr.strip()})")
    if busy.stdout.strip() not in ("", "0"):
        sys.exit(f"an rf_clock program or experiment runs on {name}; stop it first")
    out = results_dir("m5-vcxo-scope")
    if ssh(ip, f"mkdir -p {REMOTE}").returncode or \
            subprocess.run(["scp", "-q", __file__, f"xilinx@{ip}:{REMOTE}/"]).returncode:
        sys.exit(f"cannot copy the script to {name}")
    cmd = (f"{PYNQ_PYTHON} {REMOTE}/{Path(__file__).name} --board-run {args.seconds + 70} --offset {args.offset} "
           f"--hold {args.hold}")
    ssh(ip, f"cd {REMOTE} && (setsid nohup sudo -n {ENV} {cmd} > {BOARD_LOG} 2>&1 < /dev/null &)")
    (out / "run.json").write_text(json.dumps({"board": ip, "serial": name, "seconds": args.seconds,
                                              "offset": args.offset, "out_channel": args.out_channel,
                                              "c1_coupling": args.c1_coupling,
                                              "out_positive": args.out_positive, "hold": args.hold}) + "\n",
                                  encoding="utf-8", newline="\n")
    print(f"{name}: PL.open, output PPS, holdover; results in {out}", flush=True)
    scope = None
    try:
        deadline = time.time() + {"unplug": 1500, "noref": 600}.get(args.hold, 300)
        ready = {"capacitor": "capacitor", "unplug": "unplugged", "noref": "noref"}.get(args.hold, "holdover")
        told = False
        while True:
            rows = board_lines(ip)
            if any(r["type"] == ready for r in rows):
                break
            if not told and any(r["type"] == "unplug" for r in rows):
                print(f"{name}: take the FS725's 10 MHz off the board's CLK IN now", flush=True)
                told = True
            if any(r["type"] == "error" for r in rows) or time.time() > deadline:
                print(ssh(ip, f"tail -20 {BOARD_LOG}").stdout)
                sys.exit(f"{name} did not reach holdover")
            time.sleep(5)
        print(f"{name}: holdover, the output PPS on its flywheel; the scope starts in 15 s", flush=True)
        time.sleep(15)
        scope = subprocess.Popen(["sudo", "-n", *SCOPE_LOGGER, "--out-channel", args.out_channel, "--c1-coupling",
                                  args.c1_coupling, *([] if args.out_positive else ["--out-negative"]),
                                  "--seconds", str(args.seconds), "--out", str(out / "hdo.jsonl")],
                                 stdout=open(out / "hdo.out", "w"), stderr=subprocess.STDOUT)
        scope.wait(timeout=args.seconds + 180)
        deadline = time.time() + 180
        while not any(r["type"] == "released" for r in board_lines(ip)) and time.time() < deadline:
            time.sleep(5)
    except KeyboardInterrupt:
        print(f"interrupted: stopping {name} and the scope", flush=True)
        ssh(ip, f"sudo -n pkill -TERM -f '{BOARD_PATTERN}'")
        if scope is not None:
            subprocess.run(["sudo", "-n", "pkill", "-TERM", "-f", "^" + " ".join(SCOPE_LOGGER)])
            scope.wait(timeout=60)
        time.sleep(5)
    rows = board_lines(ip)
    (out / "board.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
    if args.hold == "unplug":
        print(f"{name}: put the FS725's 10 MHz back on the board's CLK IN", flush=True)
    if not any(r["type"] == "released" for r in rows):
        print(f"WARNING: {name}'s log has no 'released' line; check that 0x14B was written back", flush=True)
    return out


# -------------------------------------------------------------------------------------- analysis
def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def wrap(x: float, period: float = PERIOD_NS) -> float:
    return (x + period / 2) % period - period / 2


def phase(out: Path) -> dict:
    """The VCXO's phase against the FS725 (s), one value per output pulse.

    The board gives each output edge's time after the FS725's 1PPS edge to one tick.  In the
    flywheel the pulses lie exactly 100 000 000 ticks apart, so the pulse after the PPS edge of
    second k comes (pulse tick - PPS tick) ticks later, which over that second's period in ticks
    is a time good to about 10 ns.  The scope gives the same edge's time against the 10 MHz to
    a few ps but only modulo 100 ns.  The coarse time picks the period, the scope gives the rest.
    The scope's records are matched to the board's seconds by their clock times, with the one
    offset (in whole seconds) that makes the two agree, since the two machines' clocks differ."""
    board = load(out / "board.jsonl")
    start = max(r["time"] for r in board if r["type"] in ("holdover", "capacitor", "unplugged", "noref"))
    pps = [r for r in board if r["type"] == "pps" and r["time"] > start + 5]
    cnt = np.array([r["count"] for r in pps])
    ppst = np.array([r["tick"] for r in pps], dtype=np.int64)
    tb = np.array([r["time"] for r in pps])
    t0 = int(pps[0]["pulse_tick"])
    if np.any((np.array([r["pulse_tick"] for r in pps], dtype=np.int64) - t0) % TICKS):
        raise ValueError("the output pulses are not on one grid of 100 000 000 ticks (not all from the flywheel)")
    period = np.diff(ppst).astype(float)
    whole = np.r_[np.diff(cnt) == 1, False]
    period = np.r_[period, period[-1]]
    pulse = np.ceil((ppst - t0) / TICKS).astype(np.int64)  # number of the pulse after each PPS edge
    coarse = (t0 + pulse * TICKS - ppst) * 1e9 / period  # ns from the PPS edge to that pulse
    scope = [r for r in load(out / "hdo.jsonl")
             if r.get("type") == "acq" and r.get("out_vs_10mhz_ns") is not None and r["time"] > start + 10]
    t = np.array([r["time"] for r in scope])
    fine = np.array([r["out_vs_10mhz_ns"] for r in scope])
    lag = t - tb[np.clip(np.searchsorted(tb, t) - 1, 0, None)]  # s from the latest board line to each record
    k0 = np.searchsorted(tb, t - np.median(lag) + 0.5) - 1  # matched half a second from either neighbour
    best = None
    for shift in range(-3, 4):
        k = k0 + shift
        use = (k >= 0) & (k < len(ppst))
        r = (fine[use] - coarse[k[use]] + 50) % 100 - 50
        c = float(np.angle(np.mean(np.exp(2j * np.pi * r / 100))) * 100 / (2 * np.pi))
        res = (r - c + 50) % 100 - 50
        if best is None or np.std(res) < best[1]:
            best = (shift, float(np.std(res)), use, k, c, res)
    assert best is not None  # the loop above ran seven shifts
    shift, rms, use, k, c, res = best
    if rms > 10:
        raise ValueError(f"the scope's records do not match the board's seconds (residual {rms:.1f} ns rms)")
    k = k[use]
    u = coarse[k] + c + res  # ns, the output edge after the PPS edge (plus a constant)
    keep = whole[k]
    return {"n": pulse[k][keep], "x": -u[keep] * 1e-9, "t": t[use][keep], "resid_ns": res[keep],
            "shift_s": shift, "y_board": float(np.mean(period[k[keep]]) / TICKS - 1), "start": start,
            "pps_locked_after": int(sum(1 for r in pps if r["time"] > start + 10 and r["pps_locked"])),
            "flywheel_pulses": int(pps[-1]["flywheel_pulses"]) if pps else 0}


def analyze(out: Path) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from m5_free_oscillators import adev, holdover

    meta = json.loads((out / "run.json").read_text(encoding="utf-8")) if (out / "run.json").exists() else {}
    name = meta.get("serial", "the board")
    p = phase(out)
    n, x = p["n"], p["x"]
    grid = np.arange(n[0], n[-1] + 1)
    xg = np.interp(grid, n, x)  # gaps (pulses the scope missed) filled on the line between their neighbours
    rate = float((xg[-1] - xg[0]) / (grid[-1] - grid[0]))  # mean frequency over the run, as the ticks give it
    res = {"board": name, "pulses": len(grid), "records": len(n), "missed": int(len(grid) - len(n)),
           "offset": float(f"{rate:.5g}"), "offset_hz": round(rate * VCXO_HZ, 2),
           "offset_from_ticks": float(f"{p['y_board']:.5g}"),
           "scope_minus_board_seconds": p["shift_s"],
           "unwrap_residual_ns": {"rms": round(float(np.std(p["resid_ns"])), 2),
                                  "max": round(float(np.max(np.abs(p["resid_ns"]))), 2),
                                  "over_25": int(np.sum(np.abs(p["resid_ns"]) > 25))},
           "pps_locked_seconds_after_start": p["pps_locked_after"], "flywheel_pulses": p["flywheel_pulses"],
           "hold": meta.get("hold", "dac"),
           "adev": {k: float(f"{v:.3g}") for k, v in adev(xg, TAUS).items()},
           # the same less a straight line of frequency (a parabola of phase), the slow drift
           "adev_less_drift": {k: float(f"{v:.3g}") for k, v in adev(
               xg - np.polyval(np.polyfit(grid, xg, 2), grid), TAUS).items()},
           "holdover_ns": {k: {q: round(v[q] * 1e9, 3) for q in v} for k, v in holdover(xg, HORIZONS).items()}}

    f10 = np.diff(xg[::10]) / 10  # 10 s means; one pulse is one second of the VCXO, 1 s to within 1e-5
    h10 = (np.arange(len(f10)) * 10 + 5) / 60
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    ax[0].plot(h10, (f10 - rate) * VCXO_HZ, "C3", lw=1)
    ax[0].set(xlabel="minutes", ylabel="VCXO frequency less its mean (Hz)",
              title=f"{name}'s VCXO frequency, its FS725 as reference, 10 s means\n"
                    f"(mean 160 MHz {rate * VCXO_HZ:+.0f} Hz)")
    a = res["adev"]
    ax[1].loglog(list(a), list(a.values()), "o-", color="C3", label=f"{name}'s VCXO, this run, on the scope")
    a = res["adev_less_drift"]
    ax[1].loglog(list(a), list(a.values()), "^:", color="C3", alpha=0.7,
                 label="the same, less a straight-line drift of frequency")
    if OVERNIGHT.exists() and meta.get("board") == OVERNIGHT_BOARD:
        night = json.loads(OVERNIGHT.read_text(encoding="utf-8"))["vcxo"]["adev"]
        night = {int(k): v for k, v in night.items() if int(k) >= 10}
        ax[1].loglog(list(night), list(night.values()), "s--", color="C3", alpha=0.5,
                     label="the same VCXO overnight, tick counts (from 10 s)")
    tau = np.array(TAUS, dtype=float)
    ax[1].loglog(tau, np.sqrt(3) * EDGE_PS * 1e-12 / tau, "--", color="0.5",
                 label=f"resolution of the scope (about {EDGE_PS:.0f} ps a record)")
    ax[1].set(xlabel="averaging time τ (s)", ylabel="Allan deviation (fraction of 160 MHz)", title="Allan deviation")
    ax[1].legend(fontsize=8)
    h = res["holdover_ns"]
    ax[2].loglog(list(h), [v["rms"] for v in h.values()], "o-", color="C3", label="rms")
    ax[2].loglog(list(h), [v["p95"] for v in h.values()], "s--", color="C3", alpha=0.6, label="95 % of cases below")
    ax[2].set(xlabel="time since the link stopped, T (s)", ylabel="time error (ns)",
              title="Time error T seconds after the link stops")
    ax[2].legend(fontsize=8)
    for a_ in ax:
        a_.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(out / "vcxo_scope.png", dpi=110)
    (out / "vcxo_scope.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
    return res


# The FS725's Allan deviation limits (hw/ref/FS725m.pdf, page 5 "Specifications").
FS725_ADEV = {1: 2e-11, 10: 1e-11, 100: 2e-12}
# The ABLJO's typical phase noise at a 156.25 MHz carrier, the analyser's markers on the plot of
# hw/ref/ABLJO_2022-09-02.pdf, page 2 "Typical phase Noise Performance @ 156.25MHz Carrier" (Hz, dBc/Hz).
ABLJO_PHASE_NOISE = ((1, -34.03), (10, -73.88), (100, -107.26), (1e3, -134.01), (1e4, -148.67), (1e5, -154.47),
                     (1e6, -158.78), (5e6, -160.45), (2e7, -160.61))
def abljo_adev(taus, nu0: float = 156.25e6) -> dict[float, float]:
    """The Allan deviation the ABLJO's typical phase noise implies.  L(f) is interpolated in log-log
    between the markers and carried below 1 Hz at the 1-10 Hz slope (-40 dB a decade), which is an
    assumption.  S_y(f) = (f/nu0)^2 * 2 * 10^(L/10), and sigma^2(tau) = 2 * integral of
    S_y(f) sin^4(pi f tau) / (pi f tau)^2 df."""
    lf = np.log10([f for f, _ in ABLJO_PHASE_NOISE])
    ll = np.array([level for _, level in ABLJO_PHASE_NOISE])
    f = np.logspace(-4, np.log10(2e7), 200_000)
    x = np.log10(f)
    level = np.where(x < lf[0], ll[0] + (ll[1] - ll[0]) / (lf[1] - lf[0]) * (x - lf[0]), np.interp(x, lf, ll))
    sy = (f / nu0) ** 2 * 2 * 10 ** (level / 10)
    out = {}
    for tau in taus:
        g = sy * np.sin(np.pi * f * tau) ** 4 / (np.pi * f * tau) ** 2
        y, x = g * f, np.log(f)  # the integral over ln f, by trapezoids
        out[tau] = float(np.sqrt(2 * np.sum((y[1:] + y[:-1]) / 2 * np.diff(x))))
    return out


def running_mean(v: np.ndarray, w: int) -> np.ndarray:
    """Mean over w points centred on each, over fewer at the ends (no zero padding)."""
    c = np.cumsum(np.r_[0.0, v])
    i = np.arange(len(v))
    lo, hi = np.clip(i - w // 2, 0, len(v)), np.clip(i + w // 2 + 1, 0, len(v))
    return (c[hi] - c[lo]) / (hi - lo)


def temperature_report(out: Path, run: Path, order: int = 3) -> dict:
    """The report's two figures for one run.  vcxo_dac.png shows the VCXO against the FS725 as
    measured and less its drift with the board's die temperature, and data_sheets.png the Allan
    deviations the two data sheets give.  The die temperature (the RFSoC's own sensor, a stand-in
    for the VCXO's, read once a second) jumps by about 0.4 C from one reading to the next, so only
    its slow course is used, a polynomial of degree ``order`` in time fitted to the readings.  Any
    smoothing that keeps those jumps puts them into the correction as frequency noise.  The
    frequency in 10 s means is fitted to that course, and the fit times the course, less its mean,
    is taken off the frequency, and its sum over the seconds off the phase."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from m5_free_oscillators import adev, holdover

    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    name = meta["serial"]
    p = phase(run)
    n, x, t = p["n"], p["x"], p["t"]
    grid = np.arange(n[0], n[-1] + 1)
    xg = np.interp(grid, n, x)
    tg = np.interp(grid, n, t)
    pps = [r for r in load(run / "board.jsonl") if r["type"] == "pps"]
    tb = np.array([r["time"] for r in pps])
    die_raw = np.interp(tg, tb, np.array([r["die_c"] for r in pps]))
    die = np.polyval(np.polyfit(tg - tg[0], die_raw, order), tg - tg[0])  # the slow course only
    rate = float((xg[-1] - xg[0]) / (grid[-1] - grid[0]))
    f10 = np.diff(xg[::10]) / 10
    d10 = die[5::10][: len(f10)]
    slope = float(np.polyfit(d10, f10, 1)[0])
    xc = xg - np.r_[0.0, np.cumsum(slope * (die[:-1] - die.mean()))]  # phase less the temperature's share
    fc10 = np.diff(xc[::10]) / 10
    rate_c = float((xc[-1] - xc[0]) / (grid[-1] - grid[0]))
    taus = (1, 2, 5, 10, 20, 50, 100, 200)
    res = {"board": name, "run": run.name, "offset": float(f"{rate:.5g}"), "offset_hz": round(rate * VCXO_HZ, 1),
           "die_c": [round(float(die.min()), 2), round(float(die.max()), 2)], "die_fit_order": order,
           "per_degc": float(f"{slope:.3g}"), "hz_per_degc": round(slope * VCXO_HZ, 1),
           "correlation": round(float(np.corrcoef(d10, f10)[0, 1]), 3),
           "frequency_10s_rms_hz": round(float(np.std(f10)) * VCXO_HZ, 1),
           "frequency_10s_rms_hz_less_temperature": round(float(np.std(fc10)) * VCXO_HZ, 1),
           "adev": {k: float(f"{v:.3g}") for k, v in adev(xg, taus).items()},
           "adev_less_temperature": {k: float(f"{v:.3g}") for k, v in adev(xc, taus).items()},
           "time_error_ns": {k: round(v["rms"] * 1e9, 1) for k, v in holdover(xg, (1, 10, 100)).items()},
           "time_error_ns_less_temperature": {k: round(v["rms"] * 1e9, 1)
                                              for k, v in holdover(xc, (1, 10, 100)).items()}}
    m10 = (np.arange(len(f10)) * 10 + 5) / 60
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    ax[0, 0].plot(m10, (f10 - rate) * VCXO_HZ, "C1", lw=1, label="as measured")
    ax[0, 0].plot(m10, (fc10 - rate_c) * VCXO_HZ, "C0", lw=1, label="less the drift with the die temperature")
    ax[0, 0].set(xlabel="minutes", ylabel="VCXO frequency less its mean (Hz)",
                 title=f"{name}'s VCXO frequency, its FS725 as reference, 10 s means\n"
                       f"(mean 160 MHz {rate * VCXO_HZ:+.0f} Hz, tuning voltage from the holdover DAC)")
    ax[0, 0].legend(fontsize=8)
    ax[0, 1].plot((tg - tg[0]) / 60, running_mean(die_raw, 10), "C7", lw=1, label="readings, 10 s means")
    ax[0, 1].plot((tg - tg[0]) / 60, die, "k-", lw=1.5, label=f"slow course used (polynomial of degree {order})")
    ax[0, 1].set(xlabel="minutes", ylabel="°C", title="RFSoC die temperature")
    ax[0, 1].legend(fontsize=8)
    ax[1, 0].plot(d10, (f10 - rate) * VCXO_HZ, ".", color="C1", ms=5, label="10 s means")
    dd = np.linspace(d10.min(), d10.max(), 2)
    ax[1, 0].plot(dd, slope * (dd - d10.mean()) * VCXO_HZ + (np.mean(f10) - rate) * VCXO_HZ, "k-", lw=1,
                  label=f"straight-line fit, {slope * VCXO_HZ:.0f} Hz per °C")
    ax[1, 0].set(xlabel="RFSoC die temperature, slow course (°C)", ylabel="VCXO frequency less its mean (Hz)",
                 title="VCXO frequency against the die temperature")
    ax[1, 0].legend(fontsize=8)
    for key, color, label in (("adev", "C1", "as measured"),
                              ("adev_less_temperature", "C0", "less the drift with the die temperature")):
        ax[1, 1].loglog(list(res[key]), list(res[key].values()), "o-", color=color, label=label)
    tt = np.array(taus, dtype=float)
    ax[1, 1].loglog(tt, np.sqrt(3) * EDGE_PS * 1e-12 / tt, ":", color="0.5", label="resolution of the scope")
    ax[1, 1].set(xlabel="averaging time τ (s)", ylabel="Allan deviation (fraction of 160 MHz)",
                 title="Allan deviation of the VCXO against the FS725")
    ax[1, 1].legend(fontsize=8)
    for a_ in ax.flat:
        a_.grid(alpha=0.3, which="both")
    fig.tight_layout()
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "vcxo_dac.png", dpi=110)

    taus_ds = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100]
    ds = abljo_adev(taus_ds)
    fig, a1 = plt.subplots(figsize=(7, 5))
    a1.loglog(taus_ds, list(ds.values()), "--", color="C3",
              label="ABLJO-V, from its typical phase noise (below 1 Hz extrapolated)")
    a1.loglog(list(FS725_ADEV), list(FS725_ADEV.values()), "v-", color="C0", ms=8, label="FS725, data sheet limit")
    a1.set(xlabel="averaging time τ (s)", ylabel="Allan deviation (fraction of the nominal frequency)",
           title="Allan deviation from the data sheets")
    a1.grid(alpha=0.3, which="both")
    a1.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "data_sheets.png", dpi=110)
    res["abljo_adev_from_data_sheet"] = {str(k): float(f"{v:.3g}") for k, v in ds.items()}
    res["fs725_data_sheet_adev"] = FS725_ADEV
    (out / "vcxo_dac.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seconds", type=int, default=3600, help="seconds on the scope (default an hour)")
    parser.add_argument("--board", default="192.168.2.99", help="the board's address (default 887329260072A)")
    parser.add_argument("--out-channel", choices=("C3", "C4"), default="C3", help="the scope channel with J20")
    parser.add_argument("--c1-coupling", choices=("DC1M", "DC50"), default="DC50",
                        help="DC50 when the FS725's 10 MHz cable ends at the scope (default), DC1M through a tee")
    parser.add_argument("--out-positive", action="store_true", help="trigger on a positive output pulse")
    parser.add_argument("--hold", choices=("dac", "capacitor", "unplug", "noref"), default="dac",
                        help="what holds the VCXO's tuning voltage: the holdover DAC, the loop filter's "
                             "capacitors alone with the charge pump at high impedance, or the board's normal "
                             "set-up with the 10 MHz taken off CLK IN by hand, or that set-up with holdover off and "
                             "CLKin0 turned off, as on a board started without a reference")
    parser.add_argument("--offset", type=int, default=100, help="ticks from the VCXO's second to its output pulse")
    parser.add_argument("--analyze", type=Path, help="analyse a finished run's directory")
    parser.add_argument("--temperature", type=Path, nargs=2, metavar=("OUT", "RUN"),
                        help="write the report's figures (vcxo_dac.png, data_sheets.png) and vcxo_dac.json into OUT "
                             "from the run RUN, its drift with the die temperature taken off")
    parser.add_argument("--board-run", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.board_run is not None:
        board_run(args.board_run, args.offset, args.hold)
        return
    if args.temperature:
        print(json.dumps(temperature_report(*args.temperature), indent=1))
        return
    out = args.analyze or run(args)
    try:
        print(json.dumps(analyze(out), indent=1))
    except ModuleNotFoundError as e:  # RF-clock-PC's venv has no matplotlib
        print(f"{e}: analyse {out} where matplotlib is installed, with --analyze")


if __name__ == "__main__":
    main()
