from __future__ import annotations

import unittest

from rf_clock.pl import abi
from rf_clock.pl.client import (ClockUnavailable, CommandRejected, IdentityError, PLClient,
                                RXWindow, RXWindowInvalid, TargetMissed, WaitTimeout)
from rf_clock.pl.mmio import MemoryMMIO
from rf_clock.pl.rxdma import RXDMA, PynqS2MMDMA
from rf_clock.pl.txbuf import TXBuffer


class FakeBuffer:
    def __init__(self, nbytes: int):
        self.nbytes = nbytes
        self.invalidated = False

    def invalidate(self):
        self.invalidated = True


class FakeDMA:
    def __init__(self, on_wait=None):
        self.buffer = None
        self.nbytes = 0
        self._received = 0
        self.on_wait = on_wait
        self.aborted = False

    def abort(self):
        self.aborted = True

    def prepare(self, buffer, nbytes):
        self.buffer = buffer
        self.nbytes = nbytes

    def wait(self, timeout=None):
        self._received = self.nbytes
        if self.on_wait is not None:
            self.on_wait()

    @property
    def received_bytes(self):
        return self._received


def status_word(*, tx=abi.TxState.IDLE, rx=abi.RxState.IDLE, dma=True):
    value = (int(tx) << abi.ABI.STAT.TX_STATE_LSB) | (int(rx) << abi.ABI.STAT.RX_STATE_LSB)
    value |= 1 << abi.ABI.STAT.TIME_VALID
    value |= 1 << abi.ABI.STAT.CLOCKS_LOCKED
    value |= 1 << abi.ABI.STAT.PPS_LOCKED
    if dma:
        value |= 1 << abi.ABI.STAT.DMA_READY
    return value


class PLInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.clock = MemoryMMIO(initial={0: 1})
        self.regs = MemoryMMIO(
            initial={
                abi.ABI.REG.ID: abi.ABI.DESIGN_ID,
                abi.ABI.REG.VERSION: abi.ABI.VERSION,
                abi.ABI.REG.STATUS: status_word(),
                abi.ABI.REG.TIME_EPOCH: 7,
                abi.ABI.REG.TX_DEPTH: 8,
                abi.ABI.REG.RX_MAX_BEATS: 100,
                abi.ABI.REG.MIN_LEAD_TICKS: 64,
            }
        )
        self.client = PLClient(self.regs, self.clock)
        self.client.validate()

    def test_abi_namespace_and_atomic_read_order(self):
        self.assertEqual(abi.ABI.REG.TICK_LO, 0x20)
        self.assertEqual(abi.ABI.CTRL.TX_DOORBELL, 0)
        self.assertEqual(abi.ABI.STAT.RX_STATE_LSB, 12)
        self.regs.values[abi.ABI.REG.TICK_LO] = 0x11223344
        self.regs.values[abi.ABI.REG.TICK_HI] = 0x55667788
        self.assertEqual(self.client.read64(abi.ABI.REG.TICK_LO), 0x5566778811223344)
        self.assertEqual(self.regs.read_log[-2:], [abi.ABI.REG.TICK_LO, abi.ABI.REG.TICK_HI])

    def test_tx_shadow_then_doorbell_and_little_endian_buffer(self):
        def on_write(offset, value):
            if offset == abi.ABI.REG.CONTROL and value == 1 << abi.ABI.CTRL.TX_DOORBELL:
                self.regs.values[abi.ABI.REG.TX_RESULT] = int(abi.ResultCode.ARMED)
                self.regs.values[abi.ABI.REG.STATUS] = status_word(tx=abi.TxState.ARMED)

        self.regs.on_write = on_write
        self.client.arm_tx(1000, 2)
        self.assertEqual(
            [offset for offset, _ in self.regs.write_log[-4:]],
            [abi.ABI.REG.TX_TARGET_LO, abi.ABI.REG.TX_TARGET_HI,
             abi.ABI.REG.TX_LENGTH, abi.ABI.REG.CONTROL],
        )
        # Return to an editable state for the buffer test.
        self.regs.values[abi.ABI.REG.STATUS] = status_word(tx=abi.TxState.IDLE)
        memory = MemoryMMIO()
        TXBuffer(memory, self.client).write_beat(0, bytes(range(32)))
        self.assertEqual(memory.values[0], 0x03020100)
        self.assertEqual(memory.values[28], 0x1F1E1D1C)

    def _rx_window(self, *, result, actual=100):
        """Arm a 2-beat RX window at tick 100 that finishes with ``result``."""
        def on_write(offset, value):
            if offset == abi.ABI.REG.CONTROL and value == 1 << abi.ABI.CTRL.RX_DOORBELL:
                self.regs.values[abi.ABI.REG.RX_RESULT] = int(abi.ResultCode.ARMED)
                self.regs.values[abi.ABI.REG.STATUS] = status_word(rx=abi.RxState.ARMED)

        self.regs.on_write = on_write
        self.regs.values[abi.ABI.REG.STATUS] = status_word(rx=abi.RxState.IDLE)
        self.client.arm_rx(100, 2)
        # P4 reports DONE once the DMA has taken the last beat.
        self.regs.values.update({
            abi.ABI.REG.RX_RESULT: int(result),
            abi.ABI.REG.RX_BEATS: 2,
            abi.ABI.REG.RX_ACTUAL_LO: actual,
            abi.ABI.REG.RX_ACTUAL_HI: 0,
            abi.ABI.REG.STATUS: status_word(rx=abi.RxState.DONE),
        })

    def test_rx_window_checks_on_ctrl_and_dma(self):
        rx_dma = RXDMA(FakeDMA())
        buffer = FakeBuffer(64)
        rx_dma.prepare(2, buffer=buffer)  # before the doorbell (docs/ABI.md 7.3)
        self._rx_window(result=abi.ResultCode.DONE)
        window = self.client.wait_rx()
        self.assertEqual(window, RXWindow(target=100, actual=100, beats=2))
        data = rx_dma.wait(window.beats)
        self.assertEqual(data.received_bytes, 64)
        self.assertTrue(buffer.invalidated)

    def test_rx_window_invalid_or_late(self):
        self._rx_window(result=int(abi.ResultCode.RX_INVALID) | (1 << (8 + 1)))
        with self.assertRaises(RXWindowInvalid):
            self.client.wait_rx()
        self._rx_window(result=abi.ResultCode.DONE, actual=101)
        with self.assertRaises(TargetMissed):
            self.client.wait_rx()

    def test_wait_tx_returns_actual(self):
        self._on_tx_doorbell(abi.ResultCode.ARMED)
        self.client.arm_tx(1000, 2)
        self.regs.values.update({
            abi.ABI.REG.TX_ACTUAL_LO: 1000,
            abi.ABI.REG.TX_ACTUAL_HI: 0,
            abi.ABI.REG.STATUS: status_word(tx=abi.TxState.DONE),
        })
        self.assertEqual(self.client.wait_tx(), 1000)
        self.regs.values[abi.ABI.REG.TX_ACTUAL_LO] = 1001
        with self.assertRaises(TargetMissed) as caught:
            self.client.wait_tx()
        self.assertEqual(caught.exception.actual, 1001)

    def _on_tx_doorbell(self, result, *, seq_step=0):
        def on_write(offset, value):
            if offset == abi.ABI.REG.CONTROL and value == 1 << abi.ABI.CTRL.TX_DOORBELL:
                self.regs.values[abi.ABI.REG.TX_RESULT] = int(result)
                self.regs.values[abi.ABI.REG.TX_SEQ] = (
                    self.regs.values.get(abi.ABI.REG.TX_SEQ, 0) + seq_step
                )

        self.regs.on_write = on_write

    def test_near_target_already_running_is_accepted(self):
        # The PL can pass ARMED before the ARM reads RESULT.
        self._on_tx_doorbell(abi.ResultCode.RUNNING)
        self.client.arm_tx(1000, 2)

    def test_done_counts_only_if_sequence_advanced(self):
        self.regs.values[abi.ABI.REG.TX_SEQ] = 5
        self._on_tx_doorbell(abi.ResultCode.DONE, seq_step=1)
        self.client.arm_tx(1000, 2)

        # A DONE left by the previous command, with no new completion.
        self.regs.values[abi.ABI.REG.STATUS] = status_word(tx=abi.TxState.DONE)
        self._on_tx_doorbell(abi.ResultCode.DONE, seq_step=0)
        with self.assertRaises(CommandRejected):
            self.client.arm_tx(1000, 2)

    def test_rejected_rx_leaves_dma_for_the_next_prepare(self):
        def on_write(offset, value):
            if offset == abi.ABI.REG.CONTROL and value == 1 << abi.ABI.CTRL.RX_DOORBELL:
                self.regs.values[abi.ABI.REG.RX_RESULT] = int(abi.ResultCode.REJ_STALE)

        self.regs.on_write = on_write
        with self.assertRaises(CommandRejected):
            self.client.arm_rx(100, 2)

        class Channel:
            idle = True

            def __init__(self):
                self.transfers = 0

            def transfer(self, array, start=0, nbytes=0):
                self.transfers += 1
                self.idle = False  # waits for a TLAST that never comes

            def start(self):
                self.idle = True

        class DMA:
            def __init__(self):
                self.recvchannel = Channel()
                self.resets = 0

            def write(self, offset, value):
                self.resets += 1

            def read(self, offset):
                return 0  # reset bit already cleared

        dma = DMA()
        adapter = PynqS2MMDMA(dma)
        adapter.prepare(FakeBuffer(64), 64)  # window whose doorbell was rejected
        adapter.prepare(FakeBuffer(64), 64)  # next window: reset the stuck transfer first
        self.assertEqual((dma.resets, dma.recvchannel.transfers), (1, 2))

    def test_tx_buffer_load_checks_state_once(self):
        memory = MemoryMMIO()
        before = self.regs.read_log.count(abi.ABI.REG.STATUS)
        TXBuffer(memory, self.client).write_beats([1, 2, (5 << 224) | 3])
        self.assertEqual(self.regs.read_log.count(abi.ABI.REG.STATUS) - before, 1)
        self.assertEqual(memory.block_writes, 1)  # one block copy, not 24 word writes
        self.assertEqual(memory.values[2 * 32], 3)
        self.assertEqual(memory.values[2 * 32 + 28], 5)

    def test_pps_inputs_and_config(self):
        self.regs.values[abi.ABI.REG.PPS_INPUTS] = 0x00_07_2A_02
        inputs = self.client.pps_inputs()
        self.assertEqual(
            (inputs.comp_level, inputs.trig_level, inputs.comp_edges, inputs.trig_edges),
            (False, True, 0x2A, 0x07),
        )
        self.client.set_pps_config(source_trig=True, invert=True)
        self.assertEqual(self.regs.write_log[-1], (abi.ABI.REG.PPS_CONFIG, 0x3))
        self.client.set_pps_config()
        self.assertEqual(self.regs.write_log[-1], (abi.ABI.REG.PPS_CONFIG, 0x0))

    def test_latest_pps_reads_lo_first(self):
        self.regs.values[abi.ABI.REG.PPS_TICK_LO] = 0x10
        self.regs.values[abi.ABI.REG.PPS_TICK_HI] = 0x2
        self.regs.values[abi.ABI.REG.PPS_COUNT] = 7
        self.assertEqual(self.client.latest_pps(), (7, 0x2_0000_0010))
        self.assertEqual(
            self.regs.read_log[-3:],
            [abi.ABI.REG.PPS_TICK_LO, abi.ABI.REG.PPS_TICK_HI, abi.ABI.REG.PPS_COUNT],
        )

    def test_wait_next_pps(self):
        now = [0.0]
        self.regs.values[abi.ABI.REG.PPS_COUNT] = 3

        def sleep(seconds):
            now[0] += seconds
            if now[0] >= 0.5:  # the next edge arrives half a second later
                self.regs.values[abi.ABI.REG.PPS_COUNT] = 4
                self.regs.values[abi.ABI.REG.PPS_TICK_LO] = 100

        client = PLClient(self.regs, self.clock, monotonic=lambda: now[0], sleep=sleep)
        self.assertEqual(client.wait_next_pps(), (4, 100))
        self.regs.values[abi.ABI.REG.PPS_COUNT] = 4
        with self.assertRaises(WaitTimeout):
            PLClient(self.regs, self.clock, monotonic=lambda: now[0],
                     sleep=lambda s: now.__setitem__(0, now[0] + s)).wait_next_pps(timeout=1.0)

    def test_pps_registers_need_abi_1_1(self):
        self.regs.values[abi.ABI.REG.VERSION] = 0x0001_0000
        self.client.validate()
        with self.assertRaises(IdentityError):
            self.client.pps_inputs()
        with self.assertRaises(IdentityError):
            self.client.set_pps_config(source_trig=True)

    def test_build_version_needs_abi_1_2(self):
        self.regs.values[abi.ABI.REG.BUILD_VERSION] = 0x01_02_03_04
        self.assertEqual(self.client.build_version(), "1.2.3.4")
        self.regs.values[abi.ABI.REG.VERSION] = 0x0001_0001
        self.client.validate()
        with self.assertRaises(IdentityError):
            self.client.build_version()

    def test_pynq_s2mm_adapter_programs_exact_window(self):
        class Channel:
            def __init__(self):
                self.calls = []

            def transfer(self, array, start=0, nbytes=0):
                self.calls.append(nbytes)

            def wait(self):
                pass

        class Buffer:
            nbytes = 4096

        class DMA:
            recvchannel = Channel()

        dma = DMA()
        adapter = PynqS2MMDMA(dma, length_reader=lambda: 64)
        adapter.prepare(Buffer(), 64)
        adapter.wait()
        self.assertEqual(dma.recvchannel.calls, [64])
        self.assertEqual(adapter.received_bytes, 64)

    def test_fabric_clock_guard(self):
        self.clock.values[0] = 0
        with self.assertRaises(ClockUnavailable):
            self.client.status()


if __name__ == "__main__":
    unittest.main()
