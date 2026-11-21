"""M5: how stable are the FS725 and the RFSoC 4x2's own 160 MHz VCXO when nothing steers them?

Problem.  B might run without its FS725, on a cheaper oscillator that the link steers, and the
question is how long such an oscillator holds when the link drops.  Two oscillators are at hand.
The FS725s are compared with each other on the scope, as in the free-running runs before.  The
board's own oscillator is the 160 MHz VCXO (Abracon ABLJO-V-160.000MHZ) in front of the LMK04828,
which normally locks it to the FS725's 10 MHz.  The LMK04828 can be forced into holdover
(HOLDOVER_FORCE, register 0x14B bit 3, TI SNAS605AS 9.3.7), and then the VCXO runs on the fixed
tuning voltage of MAN_DAC (512 in our register file) while the FS725 stays connected, so no cable
has to move.

Procedure, nothing transmitted, for ``--seconds`` (one night by default).

1. The scope (experiments/m5_hdo_phase.py, C1 B's and C2 A's FS725 10 MHz) logs the two FS725s
   against each other.
2. Board A opens the PL locked to FS725 A and runs ``rf_clock.checks.ref_phase``, so P11 logs A's
   FS725 10 MHz against A's PL 100 MHz clock with the die temperature, once a second.
3. Board B opens the PL locked to FS725 B, then forces the LMK04828 into holdover.  From then on
   B's PL 100 MHz clock comes from the free VCXO, and once a second the board logs the tick that
   latched FS725 B's 1PPS (PPS_TICK, 10 ns steps).  P1 refuses those periods for the PPS lock
   (more than one tick from 100 000 000), but it still latches every edge.  P11's reading and the
   die temperature go with each line.  At the end the board writes 0x14B back without the force.

Run on RF-clock-PC as the normal user (the scope through sudo)::

    .venv/bin/python experiments/m5_free_oscillators.py [--seconds 28800]

Results in results/m5-free-oscillators-<time>/: hdo.jsonl (the scope), board_A.out and
board_B.jsonl (one JSON line a second).

Analysis (``--analyze DIR``), into free_oscillators.json, deviation.png, temperature.png and tau_ref.png.

- FS725 pair: the scope's lag (A's 10 MHz delay less B's) unwrapped and averaged per second, B's
  frequency against A's, the overlapping Allan deviation, and the holdover error: the lag T seconds
  later less its prediction from the lag now and the mean frequency over the W seconds before.
  It is the pair's error. Two equal independent units each have 1/sqrt(2) of it (inferred).
- B's VCXO: the tick count between FS725 B's 1PPS edges, as the phase of the VCXO against FS725 B
  (10 ns steps), the same statistics, and its frequency against the die temperature.
- A: P11's tau_ref (A's FS725 10 MHz against A's PL 100 MHz clock) against the die temperature.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
A, B = "192.168.2.99", "192.168.2.100"
PYNQ_PYTHON = "/usr/local/share/pynq-venv/bin/python"
REMOTE = "/tmp/rf-clock-experiments"
ENV = "env BOARD=RFSoC4x2 XILINX_XRT=/usr"
HOLDOVER_FORCE = 0x08  # LMK04828 register 0x14B bit 3


def board_vcxo(seconds: int) -> None:
    """On board B, PL.open, force the LMK04828 into holdover, then one JSON line a second."""
    from rf_clock.pl import PL
    from rf_clock.pl.abi import ABI
    from rf_clock.pl.board import LMK_FREQ_MHZ, clock_registers, die_c
    from xrfclk import xrfclk  # pyright: ignore[reportMissingImports]

    def emit(kind: str, **fields) -> None:
        print(json.dumps({"type": kind, "time": time.time(), **fields}), flush=True)

    pl = PL.open(progress=lambda s: emit("open", step=s))
    mts = pl.rfdc.mts
    emit("opened", serial=pl.board.serial, dac=mts.dac.describe(), adc=mts.adc.describe())
    image = {w >> 8: w for w in clock_registers(f"LMK04828_{LMK_FREQ_MHZ}.txt")}
    word = image[0x14B]
    count0, tick0 = pl.ctrl.latest_pps()
    period0 = pl.ctrl.read32(ABI.REG.PPS_LAST_PERIOD)
    for lmk in xrfclk.lmk_devices:
        xrfclk._write_LMK_regs([word | HOLDOVER_FORCE], lmk)
    emit("holdover", word=hex(word | HOLDOVER_FORCE), count=count0, tick=tick0, period_before=period0)
    seen = count0
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            count, tick = pl.ctrl.latest_pps()
            if count == seen:
                time.sleep(0.05)
                continue
            seen = count
            ref = pl.ref_phase.read()
            emit("pps", count=count, tick=tick, period=pl.ctrl.read32(ABI.REG.PPS_LAST_PERIOD),
                 pps_locked=pl.ctrl.status().pps_locked, die_c=die_c(),
                 p11_amplitude=None if ref is None else round(ref.amplitude, 1),
                 p11_delay_ns=None if ref is None else ref.delay_ns)
    except KeyboardInterrupt:
        pass
    finally:
        for lmk in xrfclk.lmk_devices:
            xrfclk._write_LMK_regs([word], lmk)
        emit("released", word=hex(word))


def start(ip: str, cmd: str, log: str) -> None:
    """Run ``cmd`` on the board detached from this ssh session, its output into ``log``."""
    full = f"mkdir -p {REMOTE} && cd {REMOTE} && (setsid nohup sudo -n {ENV} {cmd} > {log} 2>&1 < /dev/null &)"
    if subprocess.run(["ssh", "-o", "BatchMode=yes", f"xilinx@{ip}", full]).returncode:
        sys.exit(f"cannot start on {ip}")


TAUS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)
HORIZONS = (100, 1000, 3600)
VCXO_HZ = 160e6  # the 4x2's VCXO (Abracon ABLJO-V-160.000MHZ)


def adev(x: np.ndarray, taus=TAUS) -> dict[int, float]:
    """Overlapping Allan deviation of phase x (s, one sample a second) at each tau with enough data."""
    out = {}
    for m in taus:
        d = x[2 * m:] - 2 * x[m:-m] + x[:-2 * m]
        if len(d) >= 10:
            out[m] = float(np.sqrt(np.mean(d * d) / 2) / m)
    return out


def holdover(x: np.ndarray, horizons=HORIZONS, window: int | None = None) -> dict[int, dict]:
    """The error (s) of predicting x T seconds ahead from x now and the mean rate over the window before
    (the window is T itself unless given): rms and 95th percentile of its size over all start seconds."""
    out = {}
    for T in horizons:
        W = window or T
        if len(x) < W + T + 10:
            continue
        i = np.arange(W, len(x) - T)
        rate = (x[i] - x[i - W]) / W
        e = x[i + T] - x[i] - rate * T
        out[T] = {"rms": float(np.sqrt(np.mean(e * e))), "p95": float(np.percentile(np.abs(e), 95))}
    return out


def per_second(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Means over whole seconds, with empty seconds filled in linearly."""
    sec = np.floor(t - t[0]).astype(int)
    n = sec[-1] + 1
    s = np.asarray(np.bincount(sec, weights=v, minlength=n), dtype=float)
    c = np.asarray(np.bincount(sec, minlength=n), dtype=float)
    have = c > 0
    grid = np.arange(n, dtype=float)
    return t[0] + grid, np.interp(grid, grid[have], s[have] / c[have])


def lines(path: Path, prefix: str = "") -> list[dict]:
    rows = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith(prefix) and ln[len(prefix):].lstrip().startswith("{"):
            try:
                rows.append(json.loads(ln[len(prefix):]))
            except json.JSONDecodeError:
                pass
    return rows


def analyze(out: Path) -> dict:
    """The statistics into free_oscillators.json and three figures, one panel per quantity, each with one
    y axis: deviation.png (B's VCXO against FS725 B, its frequency over the night, Allan deviation and time
    error after the link stops), temperature.png (B's die temperature, and the VCXO's frequency against it)
    and tau_ref.png (A's tau_ref and die temperature, recorded the same night for another question)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    res: dict = {}

    # FS725 pair on the scope: lag = A's delay less B's, so its rise is B's frequency above A's.
    acq = [r for r in lines(out / "hdo.jsonl") if r.get("type") == "acq"]
    t = np.array([r["time"] for r in acq])
    lag = np.unwrap(np.array([r["lag_ps"] for r in acq]) * (2 * np.pi / 1e5)) * (1e5 / (2 * np.pi)) * 1e-12
    ts, xs = per_second(t, lag)
    rate = np.polyfit(ts - ts[0], xs, 1)[0]
    res["fs725_pair"] = {"seconds": len(xs), "b_above_a": float(f"{rate:.3g}"),
                         "adev": {k: float(f"{v:.3g}") for k, v in adev(xs).items()},
                         "holdover_ns": {k: {q: round(v[q] * 1e9, 3) for q in v} for k, v in holdover(xs).items()}}

    # B's VCXO against FS725 B: ticks of the PL's 100 MHz clock between 1PPS edges.
    pps = [r for r in lines(out / "board_B.jsonl") if r.get("type") == "pps"]
    cnt = np.array([r["count"] for r in pps])
    tick = np.array([r["tick"] for r in pps], dtype=float)
    die_b = np.array([r["die_c"] for r in pps])
    keep = np.r_[True, np.diff(cnt) > 0]
    cnt, tick, die_b = cnt[keep], tick[keep], die_b[keep]
    grid = np.arange(cnt[0], cnt[-1] + 1)
    ticks = np.interp(grid, cnt, tick)
    nominal = (ticks[-1] - ticks[0]) / (len(ticks) - 1)
    xv = (ticks - ticks[0] - nominal * np.arange(len(ticks))) * 1e-8  # the VCXO's phase, s, against its mean rate
    yv = np.diff(ticks) / 1e8 - 1
    y100 = yv[: len(yv) // 100 * 100].reshape(-1, 100).mean(1)  # 100 s means, below the 10 ns tick's 1e-10
    d100 = np.interp(grid[1:], cnt, die_b)[: len(y100) * 100].reshape(-1, 100).mean(1)
    h100 = (np.arange(len(y100)) * 100 + 50) / 3600
    f100 = (y100 - y100.mean()) * VCXO_HZ  # Hz, the VCXO's frequency less its mean over the night
    res["vcxo"] = {"seconds": len(xv), "offset": float(f"{nominal / 1e8 - 1:.4g}"),
                   "offset_hz": round((nominal / 1e8 - 1) * VCXO_HZ, 1),
                   "frequency_100s_hz": {"min": round(float(f100.min()), 1), "max": round(float(f100.max()), 1),
                                         "rms": round(float(np.std(f100)), 2)},
                   "die_c_100s": [round(float(d100.min()), 2), round(float(d100.max()), 2)],
                   "adev": {k: float(f"{v:.3g}") for k, v in adev(xv).items()},
                   "holdover_ns": {k: {q: round(v[q] * 1e9, 1) for q in v} for k, v in holdover(xv).items()}}
    slope = None
    if len(y100) > 3 and np.ptp(d100) > 0.2:
        slope = np.polyfit(d100, f100, 1)
        res["vcxo"]["hz_per_degc"] = round(float(slope[0]), 2)
        res["vcxo"]["hz_rms_after_temperature"] = round(float(np.std(f100 - np.polyval(slope, d100))), 2)
        res["vcxo"]["correlation"] = round(float(np.corrcoef(d100, f100)[0, 1]), 2)

    # A: tau_ref and the die temperature, 60 s means.
    sec = lines(out / "board_A.out", "second ")
    a_tau = None  # (hours, tau_ref change ps, die temperature) of A's 60 s means, when A was recorded
    if sec:
        ta = np.array([r["time"] for r in sec])
        da = np.unwrap(np.array([r["delay_ns"] for r in sec]) * (2 * np.pi / 100)) * (100 / (2 * np.pi)) * 1e3
        die_a = np.array([r["die_c"] for r in sec])
        n = len(da) // 60 * 60
        m_d, m_t = da[:n].reshape(-1, 60).mean(1), die_a[:n].reshape(-1, 60).mean(1)
        m_d = m_d - m_d[0]
        ha = (ta[:n:60] - ta[0]) / 3600
        a_tau = (ha, m_d, m_t)
        fit = np.polyfit(m_t, m_d, 1) if np.ptp(m_t) > 0.2 else (0.0, float(m_d.mean()))
        res["a_tau_ref"] = {"seconds": len(da), "span_ps": round(float(np.ptp(m_d)), 2),
                            "die_c": [round(float(m_t.min()), 2), round(float(m_t.max()), 2)],
                            "ps_per_degc": round(float(fit[0]), 2),
                            "rms_after_temperature_ps": round(float(np.std(m_d - np.polyval(fit, m_t))), 2)}

    # Figure 1, the VCXO's deviation from FS725 B. Figure 2, its sensitivity to temperature.
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    ax[0].plot(h100, f100, "C3", lw=1)
    ax[0].set(xlabel="hours", ylabel="VCXO frequency less its mean (Hz)",
              title=f"VCXO frequency, FS725 B as reference, 100 s means\n"
                    f"(mean 160 MHz {res['vcxo']['offset_hz']:+.0f} Hz)")
    a = res["vcxo"]["adev"]
    ax[1].loglog(list(a), list(a.values()), "o-", color="C3", label="VCXO against FS725 B")
    tau = np.array(list(a), dtype=float)
    # one reading a second, each good to one 10 ns tick: white phase noise of 10 ns/sqrt(12) rms
    ax[1].loglog(tau, np.sqrt(3) * 1e-8 / np.sqrt(12) / tau, "--", color="0.5",
                 label="resolution of the count (10 ns once a second)")
    ax[1].set(xlabel="averaging time τ (s)", ylabel="Allan deviation (fraction of 160 MHz)",
              title="Allan deviation")
    ax[1].legend(fontsize=8)
    h = res["vcxo"]["holdover_ns"]
    ax[2].loglog(list(h), [v["rms"] / 1e3 for v in h.values()], "o-", color="C3", label="rms")
    ax[2].loglog(list(h), [v["p95"] / 1e3 for v in h.values()], "s--", color="C3", alpha=0.6,
                 label="95 % of cases below")
    ax[2].set(xlabel="time since the link stopped, T (s)", ylabel="time error (µs)",
              title="Time error T seconds after the link stops")
    ax[2].legend(fontsize=8)
    for a in ax:
        a.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(out / "deviation.png", dpi=110)

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    ax[0].plot(h100, d100, "C7", lw=1)
    ax[0].set(xlabel="hours", ylabel="°C", title="B's die temperature, 100 s means")
    ax[1].plot(d100, f100, ".", color="C3", ms=4, label="100 s means")
    if slope is not None:
        dd = np.linspace(d100.min(), d100.max(), 2)
        ax[1].plot(dd, np.polyval(slope, dd), "k-", lw=1, label=f"straight-line fit, {slope[0]:.1f} Hz per °C")
        ax[1].legend(fontsize=8)
    ax[1].set(xlabel="B's die temperature (°C)", ylabel="VCXO frequency less its mean (Hz)",
              title="VCXO frequency against B's die temperature")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "temperature.png", dpi=110)

    if a_tau is not None:
        ha, m_d, m_t = a_tau
        fig = plt.figure(figsize=(12, 6))
        gs = fig.add_gridspec(2, 2, width_ratios=(1.6, 1))
        a0 = fig.add_subplot(gs[0, 0])
        a1 = fig.add_subplot(gs[1, 0], sharex=a0)
        a2 = fig.add_subplot(gs[:, 1])
        a0.plot(ha, m_d, "C2", lw=1)
        a0.set(ylabel="change from the start (ps)", title="A's τ_ref, 60 s means")
        a1.plot(ha, m_t, "C7", lw=1)
        a1.set(xlabel="hours", ylabel="°C", title="A's die temperature, 60 s means")
        a2.scatter(m_t, m_d, c=ha, s=6, cmap="viridis")
        a2.set(xlabel="A's die temperature (°C)", ylabel="τ_ref change from the start (ps)",
               title="A's τ_ref against A's die temperature\n(colour shows the hour, dark is early)")
        for a in (a0, a1, a2):
            a.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out / "tau_ref.png", dpi=110)

    (out / "free_oscillators.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seconds", type=int, default=28800)
    parser.add_argument("--board-vcxo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--analyze", type=Path, help="analyse a finished run's directory")
    args = parser.parse_args()
    if args.board_vcxo:
        board_vcxo(args.seconds)
        return
    if args.analyze:
        print(json.dumps(analyze(args.analyze), indent=1))
        return
    from m3_common import results_dir

    out = results_dir("m5-free-oscillators")
    scope = subprocess.Popen(["sudo", "-n", sys.executable, str(HERE / "m5_hdo_phase.py"), "--seconds",
                              str(args.seconds + 300), "--out", str(out / "hdo.jsonl")],
                             stdout=open(out / "hdo.out", "w"), stderr=subprocess.STDOUT)
    if subprocess.run(["ssh", "-o", "BatchMode=yes", f"xilinx@{B}", f"mkdir -p {REMOTE}"]).returncode or \
            subprocess.run(["scp", "-q", __file__, f"xilinx@{B}:{REMOTE}/"]).returncode:
        sys.exit("cannot copy the script to B")
    start(A, f"{PYNQ_PYTHON} -m rf_clock.checks.ref_phase {args.seconds} --log {REMOTE}/ref_phase_runs.jsonl",
          "/dev/shm/free_A.out")
    start(B, f"{PYNQ_PYTHON} {REMOTE}/{Path(__file__).name} --board-vcxo --seconds {args.seconds}",
          "/dev/shm/free_B.jsonl")
    print(f"started; results in {out}", flush=True)
    time.sleep(args.seconds + 240)
    scope.wait()
    for ip, src, dst in ((A, "/dev/shm/free_A.out", "board_A.out"), (B, "/dev/shm/free_B.jsonl", "board_B.jsonl")):
        subprocess.run(["scp", "-q", f"xilinx@{ip}:{src}", str(out / dst)])
    print(f"results in {out}")


if __name__ == "__main__":
    main()
