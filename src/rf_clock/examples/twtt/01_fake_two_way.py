"""theta and the one-way delay from one node's measurement queue, on a development machine.

    python -m rf_clock.examples.twtt.01_fake_two_way

No PL needed: the queue entries of one second are made here, as node B's PL would put them
(docs/ABI.md 4.12), for clocks 21.5 ps apart and a cable of 292 ns.  On the board they come
from ``pl.queue.read()`` (example ``pl.04_link_node``).

In each of A's slots (the even ones) B's queue gets two entries for A's burst: B's own
measurement of it (``OwnMeasurement``: T_rx, on B's time) and the burst's measurement packet
(``PeerPacket``: A's T_tx of that burst, and A's T_rx of B's burst one slot earlier).  That is
one pair, both directions 2 ms apart (``rf_clock.twtt.pairs``).
"""

from __future__ import annotations

from rf_clock.pl.abi import ABI
from rf_clock.pl.measurements import OwnMeasurement, PeerPacket
from rf_clock.twtt.pairs import SecondTheta, SlotTiming

TIMING = SlotTiming(slots_per_second=500, slot_ticks=200_000, tx_offset=1000)  # pl.slots on the board
THETA_FS = 21_500  # B's clock reads 21.5 ps ahead of A's
DELAY_FS = 292_005_000  # the one-way delay, the same both ways here (no non-reciprocity)


def queue_of_b(count: int) -> list:
    """Node B's queue entries for A's bursts in one second (PPS_COUNT ``count``)."""
    entries = []
    for k in range(0, TIMING.slots_per_second, 2):  # A sends in the even slots
        a_tx = TIMING.tx_fs(k)  # A's burst leaves, on A's time
        b_rx = a_tx + DELAY_FS + THETA_FS  # it arrives, on B's time
        b_tx = TIMING.tx_fs((k - 1) % TIMING.slots_per_second)  # B's burst of slot k - 1, on B's time
        a_rx = b_tx + DELAY_FS - THETA_FS  # it arrived at A, on A's time: A's packet carries it
        entries.append(OwnMeasurement(seq=0, pps_count=count, slot=k, tick=0, locked=True, window_bad=False,
                                      searched=False, lag=0, fraction=0, phase=0, magnitude=0, t_rx_fs=b_rx,
                                      quality=0, window_power=0))
        entries.append(PeerPacket(seq=0, pps_count=count, slot=k, tick=0, version=1, time_epoch=1, quality=0,
                                  status=1 << ABI.PKST.MEASUREMENT, config_id=0, peer_pps_count=count, peer_slot=k,
                                  t_tx_fs=a_tx, t_rx_fs=a_rx, phase=0))
    return entries


def main() -> None:
    seconds = SecondTheta("B", TIMING.slots_per_second, TIMING.slot_ticks, TIMING.tx_offset)
    records = [r for count in (100, 101) for e in queue_of_b(count) if (r := seconds.add(e)) is not None]
    last = seconds.flush()  # the last second, once no more entries come
    for r in records + ([last] if last else []):
        print(f"second {r['count']}: {r['pairs']} pairs, theta {r['theta_ns'] * 1000:+.1f} ps, "
              f"one-way delay {r['delay_ns']:.4f} ns")


if __name__ == "__main__":
    main()
