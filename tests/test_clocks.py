from __future__ import annotations

from pathlib import Path

import pytest

from rf_clock.clocks.lmk04828 import decode_lmk, load_lmk, make_100mhz_config, validate_sysref_frequency
from rf_clock.clocks.lmx2594 import decode_lmx, load_lmx, make_phase_sync_config


FILES = Path(__file__).parents[1] / "src" / "rf_clock" / "clocks" / "files"


def test_mts_lmk_fixture_decodes_known_500_mhz_plan() -> None:
    config = decode_lmk(load_lmk(FILES / "LMK04828_500.0.txt"))
    assert config.pll2_r == 32
    assert config.pll2_p == 2
    assert config.pll2_n == 300
    assert config.vco_mux == 1  # MTS uses the LMK VCO1 3 GHz range.
    assert config.vco_hz == pytest.approx(3_000_000_000.0)
    assert config.sysref_hz == pytest.approx(5_000_000.0)
    assert config.clock_outputs_hz["DCLKout0"] == pytest.approx(500_000_000.0)
    assert config.clock_outputs_hz["DCLKout8"] == pytest.approx(500_000_000.0)


@pytest.mark.parametrize("name", ["LMK04828_100.0.txt", "LMK04828_100.1.txt"])
def test_generated_lmk_configs_have_project_rates(name: str) -> None:
    config = decode_lmk(load_lmk(FILES / name))
    assert config.vco_hz == pytest.approx(2_400_000_000.0)
    assert config.sysref_hz == pytest.approx(5_000_000.0)
    assert config.clock_outputs_hz["DCLKout8"] == pytest.approx(100_000_000.0)
    assert config.clock_outputs_hz["DCLKout0"] == pytest.approx(480_000_000.0)
    assert config.clock_outputs_hz["DCLKout4"] == pytest.approx(480_000_000.0)


def test_ext10m_locks_pl_clk_to_the_fs725_reference() -> None:
    # Nested zero-delay: PLL1 compares DCLKout8 (PL_CLK) with CLKin0, so PL_CLK
    # has one phase to the FS725 10 MHz/PPS after every programming.
    config = decode_lmk(load_lmk(FILES / "LMK04828_100.0.txt"), pll1_reference_hz=10e6)
    assert config.pll1_feedback == "DCLKout8"
    assert config.pll1_output_hz == pytest.approx(config.clock_outputs_hz["DCLKout8"])
    assert (config.pll1_r, config.pll1_n) == (12, 120)  # same 833.3 kHz phase detector
    # The packaged image is a fixed point of the generator: regenerating it
    # from itself changes nothing.
    image = load_lmk(FILES / "LMK04828_100.0.txt")
    generated = make_100mhz_config(image, mode="ext10m")
    assert generated.registers.render().splitlines() == image.render().splitlines()


def test_lmx_known_and_generated_files_decode() -> None:
    known = decode_lmx(load_lmx(FILES / "LMX2594_500.0.txt"), reference_hz=500e6)
    generated = decode_lmx(load_lmx(FILES / "LMX2594_480.0.txt"), reference_hz=480e6)
    assert known.output_a_hz == pytest.approx(500e6)
    assert generated.output_a_hz == pytest.approx(480e6)
    assert generated.output_b_hz == pytest.approx(480e6)
    assert generated.vco_hz == pytest.approx(7.68e9)
    assert generated.channel_divider == 16
    # Phase SYNC: SEG0 x SEG1 = 4 of the channel divider joins the feedback, so N = 160 / 4.
    assert (generated.phase_sync, generated.included_divide, generated.pll_n) == (True, 4, 40)


def test_phase_sync_image_keeps_the_frequencies() -> None:
    # The RFSoC-MTS fixture has no phase SYNC (like the PYNQ images): same VCO
    # and outputs with it, and generating again changes nothing.
    base = load_lmx(FILES / "LMX2594_500.0.txt")
    synced = make_phase_sync_config(base)
    before, after = (decode_lmx(f, reference_hz=500e6) for f in (base, synced))
    assert not before.phase_sync and after.phase_sync
    assert after.pll_n * after.included_divide == before.pll_n
    assert (after.vco_hz, after.output_a_hz, after.output_b_hz) == pytest.approx(
        (before.vco_hz, before.output_a_hz, before.output_b_hz))
    assert make_phase_sync_config(synced) == synced
    packaged = load_lmx(FILES / "LMX2594_480.0.txt")
    assert make_phase_sync_config(packaged) == packaged


def test_sysref_is_an_integer_subfrequency_of_all_project_clocks() -> None:
    sysref = 5_000_000
    for frequency in (100_000_000, 480_000_000, 4_800_000_000, 9_600_000_000):
        assert frequency % sysref == 0
    assert sysref < 10_000_000  # PG269 requires SYSREF strictly below 10 MHz.
    with pytest.raises(ValueError, match="strictly below"):
        validate_sysref_frequency(10_000_000)


def test_lmk_encoder_preserves_order_and_address_words() -> None:
    source = load_lmk(FILES / "LMK04828_100.0.txt")
    rendered = source.render().splitlines()
    assert rendered[0].startswith("R0 (INIT)\t0x000090")
    assert [line.split("\t", 1)[0] for line in rendered] == [f"R{entry.address}" if not entry.label else f"R{entry.address} ({entry.label})" for entry in source.entries]
