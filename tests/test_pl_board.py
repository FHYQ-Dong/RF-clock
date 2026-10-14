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


def test_clocks_are_written_from_the_packaged_files():
    from collections import defaultdict
    from types import SimpleNamespace

    from rf_clock.pl.board import program_clocks

    written = []
    xrfclk = SimpleNamespace(_Config=defaultdict(dict), lmk_devices=[], lmx_devices=[])
    xrfclk._find_devices = lambda: (xrfclk.lmk_devices.append("lmk"), xrfclk.lmx_devices.append("lmx"))
    xrfclk.set_ref_clks = lambda lmk_freq, lmx_freq: written.append(
        (xrfclk._Config["lmk04828"][lmk_freq], xrfclk._Config["lmx2594"][lmx_freq]))
    program_clocks(xrfclk)
    lmk, lmx = written[0]
    assert (len(lmk), len(lmx)) == (136, 113)  # every line of the two files
    assert lmk[0] == 0x000090 and 0x013F0B in lmk  # INIT first; PLL1 zero-delay
    program_clocks(xrfclk)
    assert xrfclk.lmk_devices == ["lmk"]  # devices are found once


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
