"""P9: the measurement packet (plan section 5.1): fields, CRC, spreading, coherent despreading.

On air a packet is one 288-bit word, sent most significant bit first: the fields of
plan section 5.1 in their order, each big-endian, the last two bytes the CRC-16 of
the others (CRC-16/CCITT-FALSE: polynomial 0x1021, initial value 0xFFFF, no
reflection, no final XOR; a shift register fed most significant bit first).

Each bit takes L chips of the packet code: bit 0 sends the code's chips as they
are, bit 1 inverted.  The packet code is a 16-bit maximal-length LFSR sequence with
a different polynomial from the PRN's (taps 0, 1, 3, 12 against 0, 2, 3, 5), so no
part of it is a copy of the PRN for the tracker or the search to lock onto.

The receiver knows where the packet starts from the burst's correlation peak
(the PRN starts at window sample ``lag``; the packet's chips follow the PRN and the
guard) and its carrier phase from the complex correlation c at that peak.  For bit
b it correlates the samples around its chips' pulse peaks with the packet code shaped
by P3's FIR (all bits 0), z_b, and decides by the sign of Re(z_b c*) = Re(z_b) Re(c)
+ Im(z_b) Im(c): no trigonometry.  In the PL the lag and c come from the peer's
previous burst, 4 ms earlier, so the packet is despread as its samples stream in.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from . import fir

FORMAT_VERSION = 1
FIELDS = ">BIIHBqqiBB"  # version, config_id, PPS_COUNT, slot, TIME_EPOCH, T_tx, T_rx, phase, quality, status
PACKET_BYTES = struct.calcsize(FIELDS) + 2  # with the CRC: 36
PACKET_BITS = 8 * PACKET_BYTES
SPREAD = 64  # L: chips per bit (plan section 5.1; measured on the link, 64 until then)
CODE_TAPS = (0, 1, 3, 12)  # the packet code's LFSR feedback bits; the PRN's are 0, 2, 3, 5
CODE_SEED = 0xACE1
PRN_CHIPS = 4096
GUARD_CHIPS = 10  # zero chips between the PRN and the packet: the RRC span
PULSE_PEAK = 40  # a chip's pulse peaks 40 samples after its start (81 taps)


@dataclass(frozen=True)
class Measurement:
    """The fields of one packet (plan section 5.1)."""

    config_id: int
    pps_count: int
    slot: int
    time_epoch: int
    t_tx_fs: int  # start of this burst, fs after the sender's PPS
    t_rx_fs: int  # arrival of the peer's burst in the previous slot, fs after the sender's PPS
    phase: int  # carrier phase at that peak, 2^-32 turn
    quality: int  # the tracker's peak over noise power, 0.25 dB
    status: int
    version: int = FORMAT_VERSION


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE of ``data`` (check value 0x29B1 for b"123456789")."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def pack(m: Measurement) -> bytes:
    body = struct.pack(FIELDS, m.version, m.config_id, m.pps_count, m.slot, m.time_epoch, m.t_tx_fs,
                       m.t_rx_fs, m.phase, m.quality, m.status)
    return body + struct.pack(">H", crc16(body))


def unpack(data: bytes) -> Measurement | None:
    """The packet's fields, or None if its length or CRC is wrong."""
    if len(data) != PACKET_BYTES or crc16(data[:-2]) != struct.unpack(">H", data[-2:])[0]:
        return None
    version, config_id, pps_count, slot, epoch, t_tx, t_rx, phase, quality, status = struct.unpack(FIELDS, data[:-2])
    return Measurement(config_id, pps_count, slot, epoch, t_tx, t_rx, phase, quality, status, version)


def to_bits(data: bytes) -> np.ndarray:
    """The packet's bits in the order they are sent (most significant first)."""
    return np.asarray(np.unpackbits(np.frombuffer(data, dtype=np.uint8)))


def from_bits(bits: np.ndarray) -> bytes:
    return np.asarray(np.packbits(np.asarray(bits, dtype=np.uint8))).tobytes()


def code_chips(count: int, seed: int = CODE_SEED) -> np.ndarray:
    """The packet code: ``count`` chips of +1/-1 from a 16-bit right-shifting LFSR whose
    feedback is the XOR of bits CODE_TAPS; a chip is +1 when bit 0 is 1, as the PRN's."""
    chips = np.empty(count, dtype=np.int64)
    state = seed
    for n in range(count):
        chips[n] = 1 if state & 1 else -1
        feedback = 0
        for tap in CODE_TAPS:
            feedback ^= (state >> tap) & 1
        state = (feedback << 15) | (state >> 1)
    return chips


def spread(data: bytes, spreading: int = SPREAD) -> np.ndarray:
    """The packet's chips: each bit's L code chips, inverted for a 1."""
    signs = 1 - 2 * to_bits(data).astype(np.int64)
    return np.repeat(signs, spreading) * code_chips(len(signs) * spreading)


def code_reference(taps: np.ndarray, bits: int = PACKET_BITS, spreading: int = SPREAD) -> np.ndarray:
    """The packet code shaped by P3's FIR with every bit 0: the despreader's reference."""
    return fir.shape(code_chips(bits * spreading), taps).astype(np.int64)


def packet_start(lag: int) -> int:
    """The window sample where the packet's first chip starts, for a burst whose PRN
    starts at window sample ``lag``."""
    return lag + (PRN_CHIPS + GUARD_CHIPS) * fir.SPS


def despread(rx_i: np.ndarray, rx_q: np.ndarray, start: int, c_i: int, c_q: int, reference: np.ndarray,
             spreading: int = SPREAD, bits: int = PACKET_BITS) -> np.ndarray:
    """The packet's bits from a window, coherently: bit b correlates the window around
    its chips' pulse peaks (8 L samples from 8 L b + PULSE_PEAK - 4 after ``start``)
    with ``reference`` there, and is 1 when Re(z_b) c_i + Im(z_b) c_q < 0, (c_i, c_q)
    being the complex correlation at the burst's peak.  Exact in integers."""
    span = fir.SPS * spreading
    out = np.empty(bits, dtype=np.uint8)
    for b in range(bits):
        first = span * b + PULSE_PEAK - fir.SPS // 2
        ref = reference[first:first + span]
        window = slice(start + first, start + first + span)
        z_i = int(np.dot(rx_i[window].astype(np.int64), ref))
        z_q = int(np.dot(rx_q[window].astype(np.int64), ref))
        out[b] = 1 if z_i * int(c_i) + z_q * int(c_q) < 0 else 0
    return out
