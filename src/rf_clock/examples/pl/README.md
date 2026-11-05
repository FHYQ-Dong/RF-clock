# ARM <-> PL examples

Annotated examples of the software interface described in `docs/ABI.md`. The development-machine examples use an in-memory fake PL and touch no hardware:

```text
python -m rf_clock.examples.pl.01_fake_tx
python -m rf_clock.examples.pl.02_fake_rx
```

The board examples need the RFSoC 4x2 with PYNQ, the FS725 clocks and PPS connected, and root:

```text
sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.03_pynq_bringup
sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.04_link_node A
```

`03_pynq_bringup` sends one burst after a PPS and captures a window from the same tick (rf-clock 1.x's TX and RX commands). `04_link_node` is one node of the rf-clock 2 link, run as `A` on one board and `B` on the other: bursts made from chips, the tracker, the measurement queue, and theta every second; `rf_clock.checks.m4c_link` is the full node. Both use the overlay packaged with rf-clock. The two-way time transfer and the steering on a development machine are in `../twtt/`; access without `rf_clock.pl` (raw PYNQ and C++) is in `../native/` and `docs/ABI.md` appendix A; the board checks are in `rf_clock.checks`.
