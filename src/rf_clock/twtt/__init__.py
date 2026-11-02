"""Two-way time transfer between two boards (milestone M4 of the plan).

``timing``  slots, timestamps, pairing and the two-way equations (pure functions)
``node``    one board's side: the PL loop, and the command that starts it
``link``    the process beside it that exchanges records with the other board

Deprecated: rf-clock 2 (branch ``v2-rf-data-transfer``) moves this exchange into the
PL: its slot engine runs a 2 ms ping-pong, the PL correlates every window, and the
measurements ride in the bursts' measurement packets, paired by ``PPS_COUNT`` and
slot without NTP.  This once-a-second exchange, driven by the ARM and carried over
Ethernet, stays on the 1.x line for M4 and as the reference v2 is checked against
(M4b); it gets fixes, not features.
"""
