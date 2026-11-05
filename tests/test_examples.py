"""The development-machine examples run to the end (the board examples need the PL)."""

from __future__ import annotations

import runpy

import pytest


@pytest.mark.parametrize("module", ["rf_clock.examples.pl.01_fake_tx", "rf_clock.examples.pl.02_fake_rx",
                                    "rf_clock.examples.twtt.01_fake_two_way", "rf_clock.examples.twtt.02_fake_steer"])
def test_example_runs(module, capsys):
    runpy.run_module(module, run_name="__main__")
    assert capsys.readouterr().out.strip()
