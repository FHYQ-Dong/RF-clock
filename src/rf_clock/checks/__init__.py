"""Board checks for the RFSoC 4x2 (milestones M1-M3 of the plan).

Run on the board as root, from any directory::

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr .venv/bin/python -m rf_clock.checks.m1_check 60
    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr .venv/bin/python -m rf_clock.checks.m2_tx_timing 10
    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr .venv/bin/python -m rf_clock.checks.m3_loopback 20

``--help`` lists the options; docs/ABI.md section 13 describes them.  Records
are appended to ``m2_runs.jsonl`` / ``m3_runs.jsonl`` in the current directory.
"""
