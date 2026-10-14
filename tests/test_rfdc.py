from __future__ import annotations

import unittest

from rf_clock.pl.rfdc import MTSResult, decode_mts_status


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


if __name__ == "__main__":
    unittest.main()
