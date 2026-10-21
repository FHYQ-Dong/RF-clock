# ARM <-> PL examples

Annotated examples of the software interface described in `docs/ABI.md`. The development-machine examples use an in-memory fake PL and touch no hardware:

```text
python -m rf_clock.examples.pl.01_fake_tx
python -m rf_clock.examples.pl.02_fake_rx
```

The board example needs the RFSoC 4x2 with PYNQ, the FS725 clocks and PPS connected, and root:

```text
sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.03_pynq_bringup
```

It uses the overlay packaged with rf-clock. Access without `rf_clock.pl` (raw PYNQ and C++) is in `../native/` and `docs/ABI.md` appendix A; the board checks for milestones M1-M3 are in `rf_clock.checks`.
