from __future__ import annotations

import pytest

from rf_clock.pl import PL, RFDC
from rf_clock.pl.beats import RX_LAYOUT
from rf_clock.pl.bringup import BringupResult
from rf_clock.pl.client import DeviceInfo, PLClient
from rf_clock.pl.mmio import MemoryMMIO
from rf_clock.pl.rxdma import RXDMA, RXData
from rf_clock.pl.txbuf import TXBuffer


def test_rx_data_decodes_samples_once():
    data = bytearray(RX_LAYOUT.pack_bytes([(i, -i) for i in range(16)]))
    result = RXData(buffer=data, beats=2, received_bytes=64)
    assert list(result.samples) == [complex(i, -i) for i in range(16)]
    assert result.samples is result.samples


def test_rx_dma_reuses_its_buffer_until_a_window_is_larger():
    allocations = []
    rx = RXDMA(NoDMA(), buffer_factory=lambda n: allocations.append(n) or bytearray(n))
    first = rx._allocate(64)
    assert rx._allocate(32) is first
    assert rx._allocate(128) is not first
    assert allocations == [64, 128]


class NoDMA:
    def prepare(self, buffer, nbytes): ...

    def wait(self, timeout=None): ...

    def abort(self): ...

    received_bytes = 0


def test_clocks_are_written_from_the_packaged_files(tmp_path, monkeypatch):
    from collections import defaultdict
    from types import SimpleNamespace

    from rf_clock.pl import board
    from rf_clock.pl.board import program_clocks

    monkeypatch.setattr(board, "LMK_SYNC_SETTLE_S", 0)
    written, lmk_sync = [], []
    spi = [tmp_path / "spidev_adc_lmx", tmp_path / "spidev_dac_lmx"]
    for path in spi:
        path.write_bytes(b"")
    xrfclk = SimpleNamespace(_Config=defaultdict(dict), lmk_devices=[], lmx_devices=[])
    xrfclk._find_devices = lambda: (xrfclk.lmk_devices.append("lmk"),
                                    xrfclk.lmx_devices.extend({"spi_device": p} for p in spi))
    xrfclk.set_ref_clks = lambda lmk_freq, lmx_freq: written.append(
        (xrfclk._Config["lmk04828"][lmk_freq], xrfclk._Config["lmx2594"][lmx_freq]))
    xrfclk._write_LMK_regs = lambda words, lmk: lmk_sync.append((words, lmk))
    program_clocks(xrfclk)
    lmk, lmx = written[0]
    assert (len(lmk), len(lmx)) == (136, 113)  # every line of the two files
    assert lmk[0] == 0x000090 and 0x013F0B in lmk  # INIT first; PLL1 zero-delay
    assert {0x013903, 0x014331, 0x0144FF} <= set(lmk)  # continuous SYSREF; SYNC_POL = 1; every SYNC_DIS set
    # Then the LMK's dividers get their SYNC: SYSREF_MUX 0 (the SYNC drives the SYNC line),
    # SYNC_POL 0, SYNC_DIS cleared, SYNC_POL 1 then 0, SYNC_DIS and SYSREF_MUX back.
    assert lmk_sync == [([0x013900, 0x014311, 0x014400, 0x014331, 0x014311, 0x0144FF, 0x013903], "lmk")]
    assert lmx[-1] == 0x00641C and 0x240028 in lmx  # VCO_PHASE_SYNC; N = 160 / IncludedDivide 4
    # Then each LMX gets its SYNC: R0 with VCO_PHASE_SYNC cleared, then set.
    assert [p.read_bytes() for p in spi] == [bytes.fromhex("00241C00641C")] * 2
    program_clocks(xrfclk)
    assert xrfclk.lmk_devices == ["lmk"]  # devices are found once
    assert len(lmk_sync) == 2


def fake_xrfclk(tmp_path):
    from collections import defaultdict
    from types import SimpleNamespace

    spi = {"adc": tmp_path / "spidev0.1", "dac": tmp_path / "spidev0.2"}  # by chip select
    for path in spi.values():
        path.write_bytes(b"")
    xrfclk = SimpleNamespace(_Config=defaultdict(dict), lmk_devices=["lmk"],
                             lmx_devices=[{"spi_device": str(spi["dac"])}, {"spi_device": str(spi["adc"])}],
                             set_ref_clks=lambda lmk_freq, lmx_freq: None, written=[])
    xrfclk._write_LMX_regs = lambda image, device: xrfclk.written.append((image, device["spi_device"]))
    xrfclk._write_LMK_regs = lambda words, lmk: None
    return xrfclk, spi


def test_a_forced_vco_band_rewrites_only_that_lmx(tmp_path, monkeypatch):
    from rf_clock.pl import board
    from rf_clock.pl.board import LMX_CAPCTRL_FORCE, clock_registers, program_clocks

    monkeypatch.setattr(board, "LMK_SYNC_SETTLE_S", 0)
    xrfclk, spi = fake_xrfclk(tmp_path)
    program_clocks(xrfclk, {"dac": 123})
    ((image, device),) = xrfclk.written
    assert device == str(spi["dac"])
    packaged = clock_registers("LMX2594_480.0.txt")
    changed = {word >> 16: word for word, old in zip(image, packaged) if word != old}
    r8 = next(word for word in packaged if word >> 16 == 8)
    assert changed == {8: r8 | LMX_CAPCTRL_FORCE, 19: changed[19]} and changed[19] & 0xFF == 123
    # Both still get their SYNC after it.
    assert [p.read_bytes() for p in spi.values()] == [bytes.fromhex("00241C00641C")] * 2


def test_the_vco_is_read_back_without_recalibrating(tmp_path, monkeypatch):
    from rf_clock.pl import board

    xrfclk, spi = fake_xrfclk(tmp_path)
    registers = {str(spi["adc"]): {110: 2 << 9 | 1 << 5, 111: 118, 112: 302},
                 str(spi["dac"]): {110: 0 << 9 | 2 << 5, 111: 191, 112: 355}}
    writes = []

    def transfer(f, data):
        if data[0] & 0x80:
            return b"\0" + registers[f.name][data[0] & 0x7F].to_bytes(2, "big")
        writes.append((f.name, int.from_bytes(data, "big")))
        return bytes(len(data))

    monkeypatch.setattr(board, "spi_transfer", transfer)
    vco = board.read_lmx_vco(xrfclk, {"dac": 119})
    assert vco["adc"] == board.LMXVCO(forced=None, capctrl=118, vco_sel=1, daciset=302, ld_vtune=2)
    assert (vco["adc"].band, vco["adc"].locked) == (118, True)
    assert (vco["dac"].band, vco["dac"].locked) == (119, False)
    assert "NOT LOCKED" in vco["dac"].describe() and "calibration chose 191" in vco["dac"].describe()
    # Per chip: R0 with MUXOUT_LD_SEL = 0, then = 1; never FCAL_EN, VCO_PHASE_SYNC kept.
    assert writes == [(str(spi[side]), word) for side in ("adc", "dac") for word in (0x006410, 0x006414)]


def test_pl_parts_are_never_none():
    gpio = MemoryMMIO(initial={0: 1})
    ctrl = PLClient(MemoryMMIO(), gpio)
    info = DeviceInfo(design_id=0, version=0, tx_depth=8, rx_max_beats=8, min_lead_ticks=64)
    pl = PL(ctrl=ctrl, tx_buffer=TXBuffer(MemoryMMIO(), ctrl), rx_dma=RXDMA(NoDMA()),
            rfdc=RFDC(driver=None), overlay=None,
            bringup=BringupResult(overlay=None, client=ctrl, info=info))
    assert pl.clock_gpio is gpio
    with pytest.raises(RuntimeError):
        pl.rfdc.mts  # opened with mts=False
