from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from rf_clock.pl import rfdc
from rf_clock.pl.rfdc import MTSResult, RFDCError, decode_mts_status, sync_nco_phase

TILE, SYSREF = 2, 3  # xrfdc.EVNT_SRC_TILE, EVNT_SRC_SYSREF


class FakeBlock:
    """An xrfdc block: MixerSettings assignment and ResetNCOPhase, logged in order."""

    def __init__(self, name: str, log: list) -> None:
        self.name, self.log = name, log
        self._mixer = {"Freq": -1000.0, "EventSource": TILE}

    @property
    def MixerSettings(self) -> dict:
        return dict(self._mixer)

    @MixerSettings.setter
    def MixerSettings(self, value: dict) -> None:
        self._mixer = dict(value)
        self.log.append(f"{self.name} source {value['EventSource']}")

    def ResetNCOPhase(self) -> None:
        self.log.append(f"{self.name} reset")


def fake_rfdc(log: list, status: int = 0):
    tiles = {kind: [SimpleNamespace(blocks=[FakeBlock(f"{kind}{t}", log)]) for t in range(4)]
             for kind in ("dac", "adc")}
    lib = SimpleNamespace(
        XRFdc_MultiConverter_Init=lambda config, pll, t1: None,
        XRFdc_MTS_Sysref_Config=lambda inst, dac, adc, enable: log.append(
            f"sysref {enable} tiles {dac.Tiles:#x}/{adc.Tiles:#x}") or status)
    ffi = SimpleNamespace(new=lambda ctype: SimpleNamespace(Tiles=0), NULL=None)
    driver = SimpleNamespace(_instance="instance", dac_tiles=tiles["dac"], adc_tiles=tiles["adc"])
    return driver, ffi, lib


class MTSStatusTests(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(decode_mts_status(0), [])
        self.assertEqual(decode_mts_status(32 | 64), ["TARGET_LOW", "IP_NOT_READY"])
        self.assertEqual(decode_mts_status(256), ["UNKNOWN_0x100"])

    def test_result_describes_only_synced_tiles(self):
        result = MTSResult(kind="DAC", status=0, tiles=(0, 2), ref_tile=0, target_latency=-1,
                           latency=(96, 0, 104, 0), offset=(1, 0, 0, 0))
        self.assertTrue(result.ok)
        self.assertEqual(
            result.describe(),
            "DAC MTS OK (ref tile 0; tile 0: latency 96 offset 1, tile 2: latency 104 offset 0)",
        )

    def test_fixed_target_must_be_reached_by_every_tile(self):
        def result(latency_2):
            return MTSResult(kind="ADC", status=0, tiles=(0, 2), ref_tile=0, target_latency=1296,
                             latency=(1296, 0, latency_2, 0), offset=(10, 0, 8, 0))

        self.assertTrue(result(1296).ok)
        self.assertFalse(result(1293).ok)
        self.assertIn("OFF_TARGET", result(1293).describe())


class NCOSyncTests(unittest.TestCase):
    """sync_nco_phase follows PG269 Use Case 1 and leaves the event sources as it found them."""

    def run_sync(self, status: int = 0) -> tuple[list, object]:
        log: list = []
        driver, ffi, lib = fake_rfdc(log, status)
        xrfdc = SimpleNamespace(EVNT_SRC_SYSREF=SYSREF)
        with mock.patch.object(rfdc, "_mts_api", return_value=(ffi, lib)), \
                mock.patch.dict(sys.modules, {"xrfdc": xrfdc}):
            sync_nco_phase(driver, (0, 2), sleep=lambda s: log.append("wait"))
        return log, driver

    def test_reset_armed_with_sysref_off_then_one_edge(self):
        log, driver = self.run_sync()
        blocks = ["dac0", "dac2", "adc0", "adc2"]
        self.assertEqual(log, [
            "sysref 0 tiles 0x5/0x5",
            *[line for b in blocks for line in (f"{b} source {SYSREF}", f"{b} reset")],
            "sysref 1 tiles 0x5/0x5", "wait", "sysref 0 tiles 0x5/0x5",
            *[f"{b} source {TILE}" for b in blocks],
        ])
        self.assertEqual(driver.dac_tiles[1].blocks[0].MixerSettings["EventSource"], TILE)  # untouched

    def test_sysref_config_failure_raises(self):
        with self.assertRaises(RFDCError):
            self.run_sync(status=512)


if __name__ == "__main__":
    unittest.main()
