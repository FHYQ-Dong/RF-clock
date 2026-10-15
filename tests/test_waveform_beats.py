"""Focused host-side tests for the ARM waveform and beat codec."""

import unittest

import numpy as np

from rf_clock.pl.beats import BeatLayout, DEFAULT_LAYOUT
from rf_clock.pl.waveform import (
    WaveformGenerator,
    generate_test_chips,
    rrc_config,
    simulate_full_convolution,
)


class BeatLayoutTests(unittest.TestCase):
    def test_default_layout_round_trip_and_tail(self):
        samples = [
            (-1, -2), (3, 4), (32767, -32768), (0, 0),
            (1, 2), (3, 4), (5, 6), (7, 8), (9, 10),
        ]
        beats = DEFAULT_LAYOUT.pack_samples(samples)
        self.assertEqual(len(beats), 2)
        self.assertEqual(
            DEFAULT_LAYOUT.unpack_beats(beats, sample_count=len(samples)), samples
        )
        self.assertEqual(len(DEFAULT_LAYOUT.pack_bytes(samples)), 64)

    def test_custom_lane_order(self):
        layout = BeatLayout(lane_order=tuple(reversed(range(8))))
        samples = [(lane, -lane) for lane in range(8)]
        self.assertEqual(layout.unpack_beat(layout.pack_beat(samples)), samples)

    def test_rejects_out_of_range_sample(self):
        with self.assertRaises(ValueError):
            DEFAULT_LAYOUT.pack_beat([(32768, 0)])


class WaveformTests(unittest.TestCase):
    def test_float_reference_length_and_generator_beats(self):
        config, coeffs = rrc_config(span_chips=2, samples_per_symbol=2)
        samples = simulate_full_convolution(
            generate_test_chips(4), coeffs, config["sps"], 512
        )
        self.assertEqual(samples.dtype, np.int16)
        self.assertEqual(len(samples), (4 - 1) * 2 + 5)

        generator = WaveformGenerator(span_chips=2, samples_per_symbol=2, chip_count=4)
        self.assertEqual(len(generator.generate()), len(samples))
        self.assertEqual(len(generator.generate_beats()), 2)


if __name__ == "__main__":
    unittest.main()
