"""M5: where the ~25 s of each initial acquisition go on the board's ARM, and what is faster.

Problem.  Every acquisition (``m4c_link --acquire``, ``rf_clock.pl.acquire``) takes 25.0-25.5 s
(105 of them, 2026-11-03 to 11-06), twice per start, and nothing in it had been timed.  It
captures an 11 ms window (8.8 M samples) to DDR, turns the DMA buffer into I and Q, finds the
peer's bursts by an FFT correlation in float over the whole window, refines each burst with the
exact integer correlation of P8 and despreads its measurement packet.  The coarse correlation
only has to find each burst to within two samples: the exact step sets the lag, the sub-sample
position and the packet, so a faster coarse correlation must give the same lags to change
nothing.

Procedure.  Node A runs its link alone (``m4c_link --role A``, no steering), so that its bursts
are on the air; a script on B, which runs no link, opens its PL and captures ``--windows``
long windows while A transmits.  Each window: every stage of ``acquire`` timed
(``capture``'s DMA and ``iq_of``, ``correlation_power``, ``find_peaks``, ``decode_burst``,
``locate``), then the candidate coarse correlations on the same samples, each timed and checked
against the current one: the same peaks, and the same bursts, offset and packets after the exact
step.  The first window's samples are kept (``window.npz``, int16).  Results in
results/m5-acquire-timing-<time>/.

    .venv/bin/python experiments/m5_acquire_timing.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from m3_common import check_idle, results_dir, ssh

A, B = "192.168.2.99", "192.168.2.100"
PYNQ = "sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
BOARD_CODE = r'''
import json, sys, time
import numpy as np
from rf_clock.pl import PL
from rf_clock.pl import acquire as acq
from rf_clock.pl.waveform import generate_test_chips
from rf_clock.plmodel import fir
from rf_clock.plmodel import packet as p9

start_at, windows, out = float(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
PRN_CHIPS, PRN_SEED = 4096, 0xACE1
prn = generate_test_chips(PRN_CHIPS, PRN_SEED)
taps = fir.coefficients(16000.0)
pl = PL.open()
pl.slots.disable()
time.sleep(max(0.0, start_at + 3.0 - time.time()))  # A's slot engine runs from start_at


def corr_scipy(rx_i, rx_q, ref, nfft=1 << 20, workers=4):
    import scipy.fft as sf
    x = rx_i.astype(np.float32) + 1j * rx_q.astype(np.float32)
    r = ref.size
    lags = x.size - r + 1
    chunk = nfft - r + 1
    h = np.conj(sf.fft(ref.astype(np.complex64), nfft, workers=workers))
    out = np.empty(lags, dtype=np.float32)
    for start in range(0, lags, chunk):
        count = min(chunk, lags - start)
        seg = x[start:start + count + r - 1]
        c = sf.ifft(sf.fft(seg, nfft, workers=workers) * h, workers=workers)[:count]
        out[start:start + count] = c.real * c.real + c.imag * c.imag
    return out


def corr_numpy_full(rx_i, rx_q, ref, nfft=1 << 20):
    x = rx_i.astype(np.float32) + 1j * rx_q.astype(np.float32)
    r = ref.size
    lags = x.size - r + 1
    chunk = nfft - r + 1
    h = np.conj(np.fft.fft(ref.astype(np.float32), nfft))
    out = np.empty(lags, dtype=np.float32)
    for start in range(0, lags, chunk):
        count = min(chunk, lags - start)
        seg = x[start:start + count + r - 1]
        c = np.fft.ifft(np.fft.fft(seg, nfft) * h)[:count]
        out[start:start + count] = (c.real * c.real + c.imag * c.imag).astype(np.float32)
    return out


CANDIDATES = {
    "numpy, nfft 2^20 used whole": lambda i, q, r: corr_numpy_full(i, q, r),
    "scipy complex64, nfft 2^20, 1 worker": lambda i, q, r: corr_scipy(i, q, r, workers=1),
    "scipy complex64, nfft 2^20, 4 workers": lambda i, q, r: corr_scipy(i, q, r, workers=4),
    "scipy complex64, nfft 2^18, 4 workers": lambda i, q, r: corr_scipy(i, q, r, nfft=1 << 18, workers=4),
    "scipy complex64, nfft 2^21, 4 workers": lambda i, q, r: corr_scipy(i, q, r, nfft=1 << 21, workers=4),
}


def summary(found):
    return dict(offset_ns=found.offset_ns, spread_ticks=found.spread_ticks, peer_pps_count=found.peer_pps_count,
                bursts=[(b.lag, b.fraction, b.magnitude, None if b.packet is None else b.packet.t_tx_fs)
                        for b in found.bursts])


ref = fir.shape(prn, taps).astype(np.int64)
cref = p9.code_reference(taps)
rows = []
for w in range(windows):
    t = {}
    t0 = time.perf_counter()
    pl.rx_dma.prepare(acq.LONG_WINDOW_BEATS)
    target = pl.ctrl.read64(acq.abi.ABI.REG.TICK_LO) + 2_000_000
    pl.ctrl.arm_rx(target, acq.LONG_WINDOW_BEATS)
    window = pl.ctrl.wait_rx(timeout=1.0 + (2_000_000 + acq.LONG_WINDOW_BEATS) / acq.TICKS_PER_SECOND)
    data = pl.rx_dma.wait(window.beats, timeout=2.0)
    t["capture: arm, 11 ms window, DMA"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    rx_i, rx_q = acq.iq_of(data.buffer, window.beats)
    t["iq_of: DMA buffer to I, Q"] = time.perf_counter() - t1
    own_count, own_tick = pl.ctrl.latest_pps()
    t1 = time.perf_counter()
    power = acq.correlation_power(rx_i, rx_q, ref)
    t["correlation_power (current)"] = time.perf_counter() - t1
    t1 = time.perf_counter()
    peaks = acq.find_peaks(power, ref.size)
    t["find_peaks"] = time.perf_counter() - t1
    t1 = time.perf_counter()
    bursts = [acq.decode_burst(rx_i, rx_q, k, ref, cref, len(prn)) for k in peaks]
    t["decode_burst, all peaks"] = time.perf_counter() - t1
    if own_tick > window.actual:
        own_tick -= acq.TICKS_PER_SECOND
        own_count -= 1
    t1 = time.perf_counter()
    found = acq.locate(window.actual, own_tick, own_count, bursts, 29.0)
    t["locate"] = time.perf_counter() - t1
    t["total"] = time.perf_counter() - t0
    if w == 0:
        np.savez(out + "/window.npz", rx_i=rx_i, rx_q=rx_q, window_tick=window.actual, own_tick=own_tick,
                 own_count=own_count)
    cands = {}
    top = np.argpartition(power, -200000)[-200000:]  # the strongest lags, where the peaks are found
    for name, fn in CANDIDATES.items():
        t1 = time.perf_counter()
        p2 = fn(rx_i, rx_q, ref)
        dt = time.perf_counter() - t1
        k2 = acq.find_peaks(p2, ref.size)
        b2 = [acq.decode_burst(rx_i, rx_q, k, ref, cref, len(prn)) for k in k2]
        f2 = acq.locate(window.actual, own_tick, own_count, b2, 29.0)
        rel = float(np.max(np.abs(p2[top] - power[top]) / power[top]))
        cands[name] = dict(seconds=round(dt, 3), same_peaks=k2 == peaks, same_result=summary(f2) == summary(found),
                           max_rel_power_diff_top=rel)
    rows.append(dict(window=w, stages=t, peaks=peaks, result=summary(found), candidates=cands))
    print(json.dumps(rows[-1]), flush=True)
json.dump(rows, open(out + "/timing.json", "w"), indent=1)
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--windows", type=int, default=3)
    args = parser.parse_args()
    for ip in (A, B):
        check_idle(ip)
    out = results_dir("m5-acquire-timing")
    start_at = int(time.time() + 45) + 0.5
    stop_at = start_at + 120 * args.windows + 60
    ssh(A, "sudo -n rm -rf /dev/shm/m4c_A.jsonl /dev/shm/m4c_A.out; cd /tmp && (setsid nohup " + PYNQ +
        f" -m rf_clock.checks.m4c_link --role A --start-at {start_at} --stop-at {stop_at} --no-entries "
        "--pps-trig --out /dev/shm/m4c_A.jsonl > /dev/shm/m4c_A.out 2>&1 < /dev/null &)")
    with tempfile.TemporaryDirectory() as d:
        code = Path(d) / "acq_timing_board.py"
        code.write_text(BOARD_CODE, encoding="utf-8", newline="\n")
        subprocess.run(["scp", "-q", str(code), f"xilinx@{B}:/tmp/acq_timing_board.py"], check=True)
    remote = "/dev/shm/acq_timing"
    run = ssh(B, f"sudo -n rm -rf {remote}; mkdir -p {remote}; cd /tmp && {PYNQ} /tmp/acq_timing_board.py "
                 f"{start_at} {args.windows} {remote} 2>&1 | grep -v Warning", timeout=150 * args.windows + 300)
    (out / "board.out").write_text(run.stdout + run.stderr, encoding="utf-8", newline="\n")
    subprocess.run(["scp", "-q", f"xilinx@{B}:{remote}/timing.json", f"xilinx@{B}:{remote}/window.npz", str(out)])
    ssh(A, "sudo -n pkill -TERM -f '^/usr/local/share/pynq-venv/bin/python -m rf_clock[.]checks[.]m4c_link '")
    rows = json.loads((out / "timing.json").read_text(encoding="utf-8"))
    for r in rows:
        print(f"window {r['window']}: {len(r['peaks'])} peaks, offset {r['result']['offset_ns']} ns")
        for k, v in r["stages"].items():
            print(f"   {k:34s} {v:7.2f} s")
        for k, v in r["candidates"].items():
            print(f"   {k:40s} {v['seconds']:7.2f} s  same peaks {v['same_peaks']}  same result {v['same_result']}  "
                  f"rel diff {v['max_rel_power_diff_top']:.1e}")
    print(f"results in {out}")


if __name__ == "__main__":
    main()
