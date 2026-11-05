"""Two-way time transfer between two boards (milestones M4 and M5 of the plan).

rf-clock 2, the measurements carried in the bursts (docs/ABI.md 11.12):

``pairs``    the two-way equations of one window, from one node's queue, and theta per second
             or per step
``control``  what a node does with theta: its FS725 (the board config check, SF steps,
             steering) and the output PPS's SYNCED
``steer``    the control laws that steer B's FS725: the Kalman control law and the
             predictive control law, and the fits of the FS725's answer to SF
``logs``     reading the log of ``rf_clock.checks.m4c_link``, which runs one node

rf-clock 1.x, deprecated: ``timing``, ``node`` and ``link`` exchange one record a second
over Ethernet, paired by system time.  It stays for M4 and as the reference rf-clock 2 is
checked against (M4b); it gets fixes, not features.
"""
