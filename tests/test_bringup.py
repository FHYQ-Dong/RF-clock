from __future__ import annotations

import unittest

from rf_clock.pl import abi
from rf_clock.pl.bringup import Bringup, BringupError, overlay_address
from rf_clock.pl.mmio import MemoryMMIO
from rf_clock.pl.overlay import VersionMismatch


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class TimedRegisters(MemoryMMIO):
    """P2 registers whose STATUS reports pps_locked once ``lock_at`` has passed."""

    def __init__(self, clock: FakeTime, lock_at: float, *, any_fault: bool = False) -> None:
        super().__init__(
            initial={
                abi.ABI.REG.ID: abi.ABI.DESIGN_ID,
                abi.ABI.REG.VERSION: abi.ABI.VERSION,
                abi.ABI.REG.TIME_EPOCH: 1,
                abi.ABI.REG.TX_DEPTH: 8192,
                abi.ABI.REG.RX_MAX_BEATS: 2097151,
                abi.ABI.REG.MIN_LEAD_TICKS: 64,
                abi.ABI.REG.FAULT_BITS: int(abi.FaultBit.PPS) if any_fault else 0,
                abi.ABI.REG.FAULT_INFO: 0x0001_0002 if any_fault else 0,
                abi.ABI.REG.BUILD_VERSION: 0x01_02_00_00,  # built for 1.2.0.0
            }
        )
        self.clock = clock
        self.lock_at = lock_at
        self.any_fault = any_fault

    def read(self, offset: int) -> int:
        if offset == abi.ABI.REG.STATUS:
            value = (1 << abi.ABI.STAT.TIME_VALID) | (1 << abi.ABI.STAT.CLOCKS_LOCKED)
            if self.clock.now >= self.lock_at:
                value |= 1 << abi.ABI.STAT.PPS_LOCKED
            if self.any_fault:
                value |= 1 << abi.ABI.STAT.ANY_FAULT
            self.values[offset] = value
        return super().read(offset)


class BringupTests(unittest.TestCase):
    def make(self, lock_at: float, *, any_fault: bool = False, mmcm_locks: bool = True, **kwargs):
        clock = FakeTime()
        regs = TimedRegisters(clock, lock_at, any_fault=any_fault)
        # The MMCM is unlocked until pl_resetn0 is pulsed after the clock change.
        gpio = MemoryMMIO(initial={0: 0})
        calls: list[str] = []

        def pl_reset():
            calls.append("reset")
            if mmcm_locks:
                gpio.values[0] = 1

        bringup = Bringup(
            clock_setup=lambda: calls.append("clocks"),
            overlay_loader=lambda path: calls.append("overlay") or object(),
            register_factory=lambda overlay: regs,
            clock_status_factory=lambda overlay: gpio,
            rfdc_setup=lambda overlay: calls.append("rfdc"),
            pl_reset=pl_reset,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            **kwargs,
        )
        return bringup, regs, calls

    def test_waits_for_pps_lock(self):
        # P1 needs one reference edge plus four good periods: about 5 s.
        bringup, regs, calls = self.make(lock_at=5.0)
        result = bringup.run("rf_clock.bit")
        # The overlay replaces the boot-time design before the clocks change.
        self.assertEqual(calls, ["overlay", "clocks", "reset", "rfdc"])
        self.assertEqual(result.info.tx_depth, 8192)
        self.assertIn((abi.ABI.REG.PPS_CONFIG, 0), regs.write_log)

    def test_fabric_lock_timeout(self):
        bringup, _, calls = self.make(lock_at=0.0, mmcm_locks=False)
        with self.assertRaises(BringupError):
            bringup.run("rf_clock.bit")
        self.assertNotIn("rfdc", calls)

    def test_pps_config_written(self):
        bringup, regs, _ = self.make(lock_at=0.0, pps_source_trig=True, pps_invert=True)
        bringup.run("rf_clock.bit")
        self.assertIn((abi.ABI.REG.PPS_CONFIG, 0x3), regs.write_log)

    def test_pps_lock_timeout(self):
        bringup, _, _ = self.make(lock_at=60.0)
        with self.assertRaises(BringupError):
            bringup.run("rf_clock.bit")

    def test_build_version_must_match_abc(self):
        bringup, _, _ = self.make(lock_at=0.0, package_version="1.2.0.7")
        bringup.run("rf_clock.bit")  # D changes only Python
        bringup, _, _ = self.make(lock_at=0.0, package_version="1.2.1.0")
        with self.assertRaises(VersionMismatch):
            bringup.run("rf_clock.bit")

    def test_faults_recorded_before_clear(self):
        bringup, regs, _ = self.make(lock_at=0.0, any_fault=True)
        result = bringup.run("rf_clock.bit")
        self.assertEqual(result.initial_fault_bits, int(abi.FaultBit.PPS))
        self.assertEqual(result.initial_fault_info, 0x0001_0002)
        self.assertIn((abi.ABI.REG.CONTROL, 1 << abi.ABI.CTRL.CLEAR_FAULT), regs.write_log)


class OverlayAddressTests(unittest.TestCase):
    def test_ip_dict_and_mem_dict(self):
        class Overlay:
            ip_dict = {"p_top_0": {"phys_addr": 0xA000_0000, "addr_range": 0x1000}}
            # PYNQ lists AXI BRAM controllers as memories.
            mem_dict = {"tx_bram_ctrl": {"base_address": 0xA008_0000, "size": 0x40000}}

        self.assertEqual(overlay_address(Overlay, "p_top_0"), (0xA000_0000, 0x1000))
        self.assertEqual(overlay_address(Overlay, "tx_bram_ctrl"), (0xA008_0000, 0x40000))
        with self.assertRaises(BringupError):
            overlay_address(Overlay, "missing")


if __name__ == "__main__":
    unittest.main()
