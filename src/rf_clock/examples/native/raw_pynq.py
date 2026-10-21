"""Raw PYNQ access to the RF-clock PL, without rf_clock.pl (docs/ABI.md appendix A.2).

    sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.native.raw_pynq

Needs the rf_clock overlay loaded and the fabric clock running.  Reads ID,
VERSION and TICK, times MMIO.read, then writes a test pattern over the TX
buffer word by word and as one numpy block (overwrites the loaded waveform).
"""
import time

import numpy as np
from pynq import MMIO

gpio = MMIO(0xA002_0000, 0x1000)
p2 = MMIO(0xA000_0000, 0x1000)
txb = MMIO(0xA040_0000, 4 << 20)
assert gpio.read(0) & 1, "fabric clock not running"
print(f"ID 0x{p2.read(0x00):08x} VERSION 0x{p2.read(0x04):08x}")
lo = p2.read(0x20)
hi = p2.read(0x24)
print("TICK", (hi << 32) | lo)

n = 20000
t0 = time.perf_counter()
for _ in range(n):
    p2.read(0x0C)
print(f"P2 read via MMIO.read: {(time.perf_counter() - t0) / n * 1e6:.2f} us each")

state = (p2.read(0x0C) >> 8) & 7
assert state not in (1, 2), "TX ARMED/RUNNING: buffer writes would fault TX"
words = np.arange(4106 * 8, dtype=np.uint32) * 2654435761  # 4106 beats, arbitrary pattern
t0 = time.perf_counter()
for i, w in enumerate(words):
    txb.write(4 * i, int(w))
t_loop = time.perf_counter() - t0
t0 = time.perf_counter()
txb.array[: words.size] = words
t_slice = time.perf_counter() - t0
back = np.array(txb.array[: words.size])
print(f"TX buffer {words.nbytes} B: word loop {t_loop:.3f} s, numpy slice {t_slice * 1e3:.2f} ms, "
      f"read-back {'OK' if np.array_equal(back, words) else 'MISMATCH'}")
