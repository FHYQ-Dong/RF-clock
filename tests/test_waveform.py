from __future__ import annotations

import sqlite3

import pytest

from rf_clock.pl.waveform import DEFAULT_CONFIG_ID, WaveformGenerator, load_config


def make_rrc_db(path):
    with sqlite3.connect(path) as conn:
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
            INSERT INTO RRC_config VALUES (123, 2, 2, 5, 3, 0.25);
            """
        )
        # A short symmetric filter is enough to exercise the current schema.
        conn.executemany(
            "INSERT INTO RRC_coeff VALUES (123, ?, ?)",
            list(enumerate([-0.1, 0.2, 0.8, 0.2, -0.1])),
        )


def test_current_rrc_schema(tmp_path):
    database = tmp_path / "RRC_config.db"
    make_rrc_db(database)
    config, coeffs = load_config(database, 123)
    assert config["id"] == 123
    assert coeffs == [-0.1, 0.2, 0.8, 0.2, -0.1]
    generator = WaveformGenerator(database, config_id=123, chip_count=2)
    assert len(generator.generate()) == 2 * 2 + 3


def test_requested_config_id_is_never_substituted(tmp_path):
    database = tmp_path / "RRC_config.db"
    make_rrc_db(database)
    with pytest.raises(ValueError, match="not found"):
        load_config(database, DEFAULT_CONFIG_ID)
