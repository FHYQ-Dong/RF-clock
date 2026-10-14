"""Focused host-side tests for the ARM waveform and beat codec."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import numpy as np

from rf_clock.pl.beats import BeatLayout, DEFAULT_LAYOUT
from rf_clock.pl.waveform import (
    WaveformGenerator,
    generate_test_chips,
    load_config,
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
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "RRC_config.db"
        with closing(sqlite3.connect(self.database)) as conn:
            conn.executescript(
                """
                CREATE TABLE RRC_config (
                    RRC_CONFIG_ID INTEGER PRIMARY KEY,
                    RRC_SAMPLES_PER_SYMBOL INTEGER NOT NULL,
                    RRC_SPAN_CHIPS INTEGER NOT NULL,
                    RRC_TAP_COUNT INTEGER NOT NULL,
                    RRC_TAIL_SAMPLES INTEGER NOT NULL,
                    RRC_BETA REAL NOT NULL
                );
                CREATE TABLE RRC_coeff (
                    RRC_CONFIG_ID INTEGER NOT NULL,
                    TAP_INDEX INTEGER NOT NULL,
                    COEFFICIENT REAL NOT NULL,
                    PRIMARY KEY (RRC_CONFIG_ID, TAP_INDEX)
                );
                INSERT INTO RRC_config VALUES (42, 2, 1, 3, 1, 0.25);
                INSERT INTO RRC_coeff VALUES (42, 0, 0.25);
                INSERT INTO RRC_coeff VALUES (42, 1, 0.50);
                INSERT INTO RRC_coeff VALUES (42, 2, 0.25);
                """
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_float_reference_length_and_generator_beats(self):
        config, coeffs = load_config(self.database, 42)
        samples = simulate_full_convolution(
            generate_test_chips(4), coeffs, config["sps"], 512
        )
        self.assertEqual(samples.dtype, np.int16)
        self.assertEqual(len(samples), (4 - 1) * 2 + 3)

        generator = WaveformGenerator(
            database_path=self.database, config_id=42, chip_count=4
        )
        self.assertEqual(len(generator.generate()), len(samples))
        self.assertEqual(len(generator.generate_beats()), 2)

    def test_unknown_config_id_is_an_error(self):
        # The only stored configuration must not silently replace another ID.
        with self.assertRaises(ValueError):
            load_config(self.database, 43)


if __name__ == "__main__":
    unittest.main()
