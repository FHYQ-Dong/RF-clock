from __future__ import annotations

import json
import math

import pytest

from rf_clock.pl import board_config
from rf_clock.pl.board_config import BoardConfig, BoardConfigError, check_board, load_board_config

SERIAL = "16908308541004718c1ca080a0800000"


def test_config_round_trips(tmp_path):
    config = BoardConfig(serial=SERIAL, label="887329260072A", lmx_vco_band={"adc": 117, "dac": 122},
                         band_table={"adc": {"locked": [116, 117, 118]}})
    path = board_config.save_board_config(config, tmp_path / "rf-clock" / "board.json")
    assert load_board_config(path) == config


@pytest.mark.parametrize("data", [
    {"serial": SERIAL.upper()},
    {"serial": SERIAL[:-2]},
    {"serial": SERIAL, "lmx_vco_bands": {"adc": 117}},  # misspelled key
    {"serial": SERIAL, "lmx_vco_band": {"ADC": 117}},
    {"serial": SERIAL, "lmx_vco_band": {"adc": 184}},
    {"serial": SERIAL, "lmx_vco_band": {"adc": 117.0}},
    [SERIAL],
])
def test_bad_configs_are_refused(data):
    with pytest.raises(BoardConfigError):
        BoardConfig.from_json(data)


def test_only_the_default_file_may_be_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(board_config, "DEFAULT_PATH", tmp_path / "board.json")
    monkeypatch.delenv(board_config.PATH_ENV, raising=False)
    assert load_board_config() is None
    with pytest.raises(BoardConfigError):
        load_board_config(tmp_path / "other.json")
    monkeypatch.setenv(board_config.PATH_ENV, str(tmp_path / "other.json"))
    with pytest.raises(BoardConfigError):
        load_board_config()
    (tmp_path / "other.json").write_text(json.dumps({"serial": SERIAL}))
    assert load_board_config() == BoardConfig(serial=SERIAL)


def test_a_config_for_another_board_is_refused():
    check_board(BoardConfig(serial=SERIAL), SERIAL)
    with pytest.raises(BoardConfigError, match="this board is"):
        check_board(BoardConfig(serial=SERIAL), "0" * 32)


def test_time_shift_counts_whole_turns_with_the_envelope():
    from rf_clock.checks.vco_bands import K3, time_shift

    for shift in (-30.0, -9.6, 0.0, 7.7, 17.2, 40.0):  # ps; a turn of three times the phase is 57.5 ps
        phase3 = math.remainder(0.4 - K3 * shift, 2 * math.pi)
        envelope = 288056.5 + shift + 4.0  # 4 ps of envelope error
        assert time_shift(envelope, phase3, 288056.5, 0.4) == pytest.approx(shift, abs=1e-9)
