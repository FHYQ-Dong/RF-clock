"""The 2.0 PL helpers (docs/ABI.md 4.8-4.12) against register and memory fakes."""

from __future__ import annotations

import numpy as np
import pytest

from rf_clock.pl import abi
from rf_clock.pl.burst import BurstMemory, BurstMemoryError, pack_chips, unpack_chips
from rf_clock.pl.client import PLClient
from rf_clock.pl.measurements import MeasurementQueue, OwnMeasurement, PeerPacket, decode_entry
from rf_clock.pl.mmio import MemoryMMIO
from rf_clock.pl.pps_out import PPSOut
from rf_clock.pl.slots import Slots, rx_offset_for
from rf_clock.pl.tracking import Correlator
from rf_clock.plmodel import fir
from rf_clock.plmodel import packet as p9

REG = abi.ABI.REG
QE = abi.ABI.QE


def client(values: dict[int, int] | None = None) -> tuple[PLClient, MemoryMMIO]:
    regs = MemoryMMIO(initial={REG.CHIP_MEM_CHIPS: 65536, REG.PACKET_SPREAD: 64, REG.SLOT_TICKS: 200000,
                               REG.QUEUE_ENTRIES: 8, **(values or {})})
    return PLClient(regs, MemoryMMIO(initial={0: 1})), regs


def test_chips_pack_32_to_a_word_chip_0_in_bit_0():
    chips = np.array([1, -1, -1, 1] + [-1] * 28 + [1, 1])
    words = pack_chips(chips)
    assert list(words) == [0b1001, 0b11]
    assert list(unpack_chips(words, chips.size)) == list(chips)
    with pytest.raises(ValueError):
        pack_chips([1, 0, -1])


def test_burst_memory_load_writes_codes_taps_and_lengths():
    ctrl, regs = client()
    memory = MemoryMMIO()
    rng = np.random.default_rng(1)
    prn = rng.choice((-1, 1), 4096)
    taps = fir.coefficients(16000)
    BurstMemory(memory, ctrl).load(prn, taps)
    assert [memory.values[abi.ABI.BMEM.PRN_OFFSET + 4 * w] for w in range(128)] == list(pack_chips(prn))
    code = pack_chips(p9.code_chips(p9.PACKET_BITS * p9.SPREAD))
    assert [memory.values[abi.ABI.BMEM.PACKET_OFFSET + 4 * w] for w in range(code.size)] == list(code)
    assert BurstMemory(memory, ctrl).read_taps() == list(taps)
    assert (regs.values[REG.BURST_PRN_CHIPS], regs.values[REG.BURST_GUARD_CHIPS]) == (4096, 10)
    assert list(BurstMemory(memory, ctrl).read_prn()) == list(prn)


def test_burst_memory_refuses_while_the_engine_is_on_and_checks_taps():
    ctrl, regs = client({REG.SLOT_CONTROL: 1 << abi.ABI.SLOTCTL.ENABLE})
    taps = fir.coefficients(16000)
    with pytest.raises(BurstMemoryError):
        BurstMemory(MemoryMMIO(), ctrl).load([1, -1], taps)
    ctrl, regs = client()
    with pytest.raises(ValueError):
        BurstMemory(MemoryMMIO(), ctrl).load([1, -1], taps * 3)  # a sample would leave int16
    with pytest.raises(ValueError):
        BurstMemory(MemoryMMIO(), ctrl).load([1, -1], taps[:80])


def test_slots_configure_and_its_checks():
    ctrl, regs = client({REG.BURST_BEATS: 22548, REG.RX_SPAN_BEATS: 23924})
    slots = Slots(ctrl)
    slots.configure(role_b=True, rx_offset=rx_offset_for(1000, 300))
    assert regs.values[REG.SLOT_RX_OFFSET] == 1175
    assert regs.values[REG.SLOT_CONTROL] == 0b111
    slots.disable()
    assert regs.values[REG.SLOT_CONTROL] == 0b110
    with pytest.raises(ValueError):
        slots.configure(role_b=False, rx_offset=0, tx_offset=200000 - 22548 + 1)
    with pytest.raises(ValueError):
        slots.configure(role_b=False, rx_offset=200000 + 1000 - 23924 + 1)
    regs.values[REG.SLOT_STATUS] = (37 << abi.ABI.SLOTST.SLOT_LSB) | 1
    status = slots.status()
    assert (status.running, status.slot) == (True, 37)


def test_pps_out_configure_control_and_status():
    ctrl, regs = client({REG.PPS_OUT_CONTROL: 1 << abi.ABI.PPSOUTCTL.SYNCED})
    out = PPSOut(ctrl)
    out.configure(amplitude=-12000, offset=500, sample=3, width=6, idle=12000)
    assert [regs.values[r] for r in (REG.PPS_OUT_OFFSET, REG.PPS_OUT_SAMPLE, REG.PPS_OUT_WIDTH,
                                     REG.PPS_OUT_AMPLITUDE, REG.PPS_OUT_IDLE)] == [500, 3, 6, (-12000) & 0xFFFF, 12000]
    assert (out.amplitude, out.offset, out.sample, out.width, out.idle) == (-12000, 500, 3, 6, 12000)
    ctl = abi.ABI.PPSOUTCTL
    assert regs.values[REG.PPS_OUT_CONTROL] == (1 << ctl.ENABLE) | (1 << ctl.SYNCED)  # SYNCED kept
    out.set_synced(False)
    assert regs.values[REG.PPS_OUT_CONTROL] == 1 << ctl.ENABLE
    out.disable()
    assert regs.values[REG.PPS_OUT_CONTROL] == 0
    low = abi.ABI.PPSOUT_MIN_OFFSET
    for amplitude, offset, sample, width in ((0x8000, low, 0, 1), (1, low, 8, 1), (1, low, 0, 0),
                                             (1, low - 1, 0, 1), (1, 100_000_000, 0, 1)):
        with pytest.raises(ValueError):
            out.configure(amplitude=amplitude, offset=offset, sample=sample, width=width)
    with pytest.raises(ValueError):
        out.configure(amplitude=1, idle=-0x8001)

    st = abi.ABI.PPSOUTST
    regs.values.update({REG.PPS_OUT_STATUS: (1 << st.HAVE_REF) | (1 << st.FLYWHEEL) | (1 << st.INVALID),
                        REG.PPS_OUT_CNT: 12, REG.PPS_OUT_FLYWHEEL_CNT: 2,
                        REG.PPS_OUT_TICK_LO: 0x2345_6789, REG.PPS_OUT_TICK_HI: 0x1})
    status = out.status()
    assert (status.have_ref, status.running, status.flywheel, status.invalid) == (True, False, True, True)
    assert (status.pulses, status.flywheel_pulses, status.last_tick) == (12, 2, 0x1_2345_6789)


def test_correlator_configure_and_report():
    ctrl, regs = client()
    p8 = Correlator(ctrl)
    p8.configure(threshold=0.25, lock_min=0.75, quality_offset=-50)
    assert regs.values[REG.P8_CONFIG] == (0xC000 << 16) | 0x4000
    assert regs.values[REG.QUALITY_OFFSET] == (-50) & 0xFFFF_FFFF
    assert p8.report() is None
    regs.values.update({
        REG.SR_SEQ: 3, REG.SR_SLOT: 7, REG.SR_TICK_LO: 5, REG.SR_TICK_HI: 1,
        REG.SR_STRONG_LAG: 1234, REG.SR_STRONG_FRAC: (-(1 << 30)) & 0xFFFF_FFFF,
        REG.SR_STRONG_PHASE: 1 << 30, REG.SR_STRONG_MAG_LO: 7, REG.SR_STRONG_MAG_HI: 2,
        REG.SR_EARLY_LAG: 1200, REG.SR_SIDELOBE_LO: 9,
    })
    found = p8.report()
    assert found is not None and found.seq == 3 and found.tick == (1 << 32) + 5
    assert found.strongest.index == 1233.75 and found.strongest.magnitude == (2 << 32) + 7
    assert abs(found.strongest.phase_rad - np.pi / 2) < 1e-12
    assert found.earliest.lag == 1200 and found.sidelobe == 9
    p8.accept(earliest=True)
    assert regs.values[REG.P8_CONTROL] == 1 << abi.ABI.P8CTL.ACCEPT_EARLIEST


def own_words(seq: int, slot: int) -> list[int]:
    w = [0] * 16
    w[QE.HEADER] = (seq << 16) | (0b101 << 8) | QE.TYPE_OWN
    w[QE.PPS_COUNT], w[QE.SLOT], w[QE.TICK_LO], w[QE.TICK_HI] = 41, slot, 0x89AB, 2
    w[QE.LAG], w[QE.FRACTION], w[QE.PHASE] = 300, (-5) & 0xFFFF_FFFF, (-(1 << 31)) & 0xFFFF_FFFF
    w[QE.MAG_LO], w[QE.MAG_HI] = 3, 1
    t_rx = -123456789012
    w[QE.TRX_LO], w[QE.TRX_HI] = t_rx & 0xFFFF_FFFF, (t_rx >> 32) & 0xFFFF_FFFF
    w[QE.QUALITY], w[QE.POWER_LO], w[QE.POWER_HI] = 201, 10, 4
    return w


def peer_words(seq: int) -> list[int]:
    w = [0] * 16
    w[QE.HEADER] = (seq << 16) | QE.TYPE_PEER
    w[QE.PPS_COUNT], w[QE.SLOT] = 41, 3
    w[QE.P_HEAD] = (1 << 24) | (0x5A << 16) | (180 << 8) | 0b110011
    w[QE.P_CONFIG_ID], w[QE.P_PPS_COUNT], w[QE.P_SLOT] = 0xC0DE, 40, 2
    t_tx, t_rx = 4_000_010_000_000, -7
    w[QE.P_TTX_LO], w[QE.P_TTX_HI] = t_tx & 0xFFFF_FFFF, t_tx >> 32
    w[QE.P_TRX_LO], w[QE.P_TRX_HI] = t_rx & 0xFFFF_FFFF, (t_rx >> 32) & 0xFFFF_FFFF
    w[QE.P_PHASE] = 12345
    return w


def test_decode_entries():
    own = decode_entry(own_words(9, 3))
    assert isinstance(own, OwnMeasurement)
    assert (own.seq, own.locked, own.window_bad, own.searched) == (9, True, False, True)
    assert (own.tick, own.fraction, own.phase, own.magnitude) == ((2 << 32) + 0x89AB, -5, -(1 << 31), (1 << 32) + 3)
    assert (own.t_rx_fs, own.quality_db, own.window_power) == (-123456789012, 50.25, (4 << 32) + 10)
    peer = decode_entry(peer_words(10))
    assert isinstance(peer, PeerPacket)
    assert (peer.version, peer.time_epoch, peer.quality, peer.config_id) == (1, 0x5A, 180, 0xC0DE)
    assert (peer.t_tx_fs, peer.t_rx_fs, peer.phase) == (4_000_010_000_000, -7, 12345)
    assert (peer.peer_locked, peer.measurement, peer.window_valid, peer.overflow) == (True, True, False, False)
    assert (peer.pps_locked, peer.clocks_locked) == (True, True)


def test_queue_reads_from_tail_to_head_across_the_end():
    ctrl, regs = client({REG.QUEUE_TAIL: 6, REG.QUEUE_HEAD: 1})
    memory = MemoryMMIO()
    for index, words in ((6, own_words(1, 1)), (7, peer_words(2)), (0, own_words(3, 3))):
        for k, word in enumerate(words):
            memory.values[64 * index + 4 * k] = word
    queue = MeasurementQueue(memory, ctrl)
    assert queue.pending() == 3
    first = queue.read(max_entries=2)
    assert [e.seq for e in first] == [1, 2] and regs.values[REG.QUEUE_TAIL] == 0
    assert [e.seq for e in queue.read()] == [3] and regs.values[REG.QUEUE_TAIL] == 1
    assert queue.read() == []


class RecordingArray:
    """A numpy-like array that records each store as (first word, word count)."""

    def __init__(self, size: int) -> None:
        self.data = np.zeros(size, dtype=np.uint32)
        self.stores: list[tuple[int, int]] = []

    def __setitem__(self, key, value) -> None:
        if isinstance(key, slice):
            self.stores.append((key.start, key.stop - key.start))
        else:
            self.stores.append((key, 1))
        self.data[key] = value


@pytest.mark.parametrize("offset, count", [(0, 81), (0x4000, 81), (0, 128), (4, 3), (8, 1), (12, 9), (0, 0)])
def test_block_writes_store_only_aligned_16_byte_blocks(offset, count):
    from rf_clock.pl.mmio import PynqMMIO

    class Mmio:
        array = RecordingArray(0x5000)

    mmio = Mmio()
    words = np.arange(1, count + 1, dtype=np.uint32)
    PynqMMIO(0, mmio=mmio).write_block(offset, words)
    start = offset // 4
    assert list(mmio.array.data[start:start + count]) == list(words)
    for first, n in mmio.array.stores:
        assert n == 1 or (first % 4 == 0 and n % 4 == 0)  # 16-byte aligned, a multiple of 16 bytes
    assert sum(n for _, n in mmio.array.stores) == count
