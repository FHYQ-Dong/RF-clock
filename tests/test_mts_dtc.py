from __future__ import annotations

from types import SimpleNamespace

import pytest

from rf_clock.pl import rfdc
from rf_clock.pl.board import DTC_MARGIN, mts_dtc_warnings
from rf_clock.pl.board_config import BoardConfig, BoardConfigError
from rf_clock.pl.rfdc import MTSResult, RFDCSetupResult

SERIAL = "16908308541004718c1ca080a0800000"


def fake_mts_api():
    """A real cffi FFI with the MTS types, and a driver that picks the window one code
    after its target when given one ("reload"), else code 64."""
    cffi = pytest.importorskip("cffi")
    ffi = cffi.FFI()
    ffi.cdef("typedef uint32_t u32; typedef struct XRFdc XRFdc;")
    ffi.cdef(rfdc._MTS_CDEF)

    class Lib:
        def XRFdc_MultiConverter_Init(self, config, pll, t1):
            for settings, codes in ((config.DTC_Set_PLL, pll), (config.DTC_Set_T1, t1)):
                settings.Scan_Mode = 0 if codes == ffi.NULL else 1
                for tile in range(4):
                    settings.Target[tile] = 0 if codes == ffi.NULL else codes[tile]

        def XRFdc_MultiConverter_Sync(self, instance, kind, config):
            for settings in (config.DTC_Set_PLL, config.DTC_Set_T1):
                for tile in range(4):
                    settings.DTC_Code[tile] = settings.Target[tile] + 1 if settings.Scan_Mode else 64
                    config.Latency[tile] = 1896
            return 0

    return ffi, Lib()


def test_run_mts_picks_windows_by_the_recorded_codes(monkeypatch):
    monkeypatch.setattr(rfdc, "_MTS", fake_mts_api())
    driver = SimpleNamespace(_instance=None)
    free = rfdc.run_mts(driver, "DAC", (0, 2), target_latency=1896)
    assert free.dtc["t1_code"] == (64, 64, 64, 64) and free.dtc_target == {}
    fixed = rfdc.run_mts(driver, "DAC", (0, 2), target_latency=1896, dtc_target={"t1": [54, -1, 53, -1]})
    assert fixed.dtc["t1_code"] == (55, 0, 54, 0)  # reload mode for T1 only
    assert fixed.dtc["pll_code"] == (64, 64, 64, 64)
    assert fixed.dtc_target == {"t1": (54, -1, 53, -1)}


def mts(kind, t1, target=None, offset=(8, 0, 8, 0)):
    return MTSResult(kind=kind, status=0, tiles=(0, 2), ref_tile=0, target_latency=1896, latency=(1896,) * 4,
                     offset=tuple(offset), dtc={"t1_code": tuple(t1), "pll_code": (-1,) * 4},
                     dtc_target={"t1": tuple(target)} if target else {})


def test_pl_open_warns_about_unrecorded_or_drifting_codes():
    setup = RFDCSetupResult(dac=mts("DAC", (54, -1, 53, -1), (54, -1, 53, -1)), adc=mts("ADC", (62, -1, 65, -1)),
                            started_after_startup_call=False, mixers={})
    config = BoardConfig(serial=SERIAL, lmx_vco_band={"adc": 125, "dac": 128},
                         mts_dtc={"dac": {"t1": [54, -1, 53, -1], "offset": [8, -1, 8, -1]},
                                  "adc": {"offset": [8, -1, 8, -1]},
                                  "recorded": {"bands": {"adc": 125, "dac": 128}}})
    (only,) = mts_dtc_warnings(setup, config)
    assert only.startswith("MTS ADC capture windows not set")
    drifted = RFDCSetupResult(dac=mts("DAC", (54 + DTC_MARGIN + 1, -1, 53, -1), (54, -1, 53, -1)),
                              adc=mts("ADC", (62, -1, 65, -1), (62, -1, 65, -1)),
                              started_after_startup_call=False, mixers={})
    (only,) = mts_dtc_warnings(drifted, config)
    assert "tile 0 T1 capture code 61 is 7 codes from the recorded 54" in only
    other_bands = BoardConfig(serial=SERIAL, lmx_vco_band={"adc": 124, "dac": 128}, mts_dtc=config.mts_dtc)
    assert any("recorded with VCO bands" in w for w in mts_dtc_warnings(drifted, other_bands))


def test_pl_open_warns_about_other_or_unrecorded_offsets():
    config = BoardConfig(serial=SERIAL, mts_dtc={"dac": {"t1": [54, -1, 53, -1], "offset": [8, -1, 8, -1]},
                                                 "adc": {"t1": [62, -1, 65, -1]}})
    usual = RFDCSetupResult(dac=mts("DAC", (54, -1, 53, -1), (54, -1, 53, -1)),
                            adc=mts("ADC", (62, -1, 65, -1), (62, -1, 65, -1), offset=(10, 0, 8, 0)),
                            started_after_startup_call=False, mixers={})
    (only,) = mts_dtc_warnings(usual, config)
    assert only.startswith("MTS ADC offsets not recorded")
    shifted = RFDCSetupResult(dac=mts("DAC", (54, -1, 53, -1), (54, -1, 53, -1), offset=(12, 0, 8, 0)),
                              adc=usual.adc, started_after_startup_call=False, mixers={})
    warnings = mts_dtc_warnings(shifted, config)
    assert any(w.startswith("MTS DAC tile 0 offset 12, recorded 8") for w in warnings)
    assert not any("tile 2 offset" in w for w in warnings)


def test_record_takes_the_window_picked_most_often():
    from rf_clock.checks.mts_dtc import choose, windows

    assert windows([54, 73, 54, 55, 73, 54]) == [[54, 54, 54, 55], [73, 73]]
    assert choose([54, 73, 54, 55, 73, 54]) == 54
    assert choose([73, 54]) == 54  # a tie: the lower window
    assert choose([62, 63, 61]) == 62


@pytest.mark.parametrize("dtc", [
    {"dac": {"t1": [54, -1, 53]}},
    {"dac": {"t2": [54, -1, 53, -1]}},
    {"DAC": {"t1": [54, -1, 53, -1]}},
    {"adc": {"pll": [54, -1, 128, -1]}},
    {"dac": {"offset": [8, -1, 8]}},
])
def test_bad_capture_codes_are_refused(dtc):
    with pytest.raises(BoardConfigError):
        BoardConfig.from_json({"serial": SERIAL, "mts_dtc": dtc})


def test_capture_codes_round_trip():
    dtc = {"dac": {"t1": [62, -1, 61, -1], "offset": [8, -1, 8, -1]},
           "adc": {"t1": [62, -1, 65, -1], "pll": [72, -1, 76, -1], "offset": [10, -1, 8, -1]},
           "recorded": {"runs": 6}}
    config = BoardConfig.from_json({"serial": SERIAL, "mts_dtc": dtc})
    assert BoardConfig.from_json(config.to_json()) == config
