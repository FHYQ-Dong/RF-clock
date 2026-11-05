"""rf_clock.twtt.pairs and rf_clock.twtt.logs: the two-way equations of one window, and a node's
log read back, against what the node forms while it runs."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from rf_clock.pl.abi import ABI
from rf_clock.pl.measurements import OwnMeasurement, PeerPacket
from rf_clock.twtt import logs
from rf_clock.twtt.pairs import FS_PER_TICK, SecondTheta, SlotTiming, pair

SLOT_TICKS = 200_000
TX_OFFSET = 1000
TIMING = SlotTiming(500, SLOT_TICKS, TX_OFFSET)


def test_one_window_gives_theta_and_delay_on_either_node():
    theta, delay = 21_500, 292_005_000  # fs: B's clock reads theta ahead of A's
    k = 10  # A's burst in slot 10, B's in slot 9
    a_tx, b_tx = TIMING.tx_fs(k), TIMING.tx_fs(k - 1)
    on_b = pair(TIMING, False, 7, k, a_tx + delay + theta, a_tx, b_tx + delay - theta)
    assert on_b.theta_ns == pytest.approx(0.0215) and on_b.delay_ns == pytest.approx(292.005)
    # node A, for B's burst in slot 11: its T_rx in A's time, and B's packet with B's T_rx of A's slot 10
    b_tx11 = TIMING.tx_fs(k + 1)
    on_a = pair(TIMING, True, 7, k + 1, b_tx11 + delay - theta, b_tx11, a_tx + delay + theta)
    assert on_a.theta_ns == pytest.approx(0.0215) and on_a.m_ab_ns == pytest.approx(292.005 + 0.0215)
    assert TIMING.tx_fs(0) == TX_OFFSET * FS_PER_TICK


def own(count, slot, t_rx_fs, locked=True):
    return OwnMeasurement(seq=0, pps_count=count, slot=slot, tick=0, locked=locked, window_bad=False, searched=False,
                          lag=0, fraction=0, phase=0, magnitude=0, t_rx_fs=t_rx_fs, quality=0, window_power=0)


def peer(count, slot, t_tx_fs, t_rx_fs, measured=True):
    return PeerPacket(seq=0, pps_count=count, slot=slot, tick=0, version=1, time_epoch=0, quality=0,
                      status=(1 << ABI.PKST.MEASUREMENT) if measured else 0, config_id=0, peer_pps_count=count,
                      peer_slot=slot, t_tx_fs=t_tx_fs, t_rx_fs=t_rx_fs, phase=0)


def test_a_log_reads_back_as_the_node_formed_it(tmp_path):
    """Node B's queue for three seconds written as m4c_link writes it; the pairs read back are
    those SecondTheta formed, the second lines and the steering error follow."""
    path = tmp_path / "m4c_B.jsonl"
    on_node = []
    node = SecondTheta("B", 500, SLOT_TICKS, TX_OFFSET, pair_sink=lambda c, s, t: on_node.append((c, s, t)))
    with open(path, "w", encoding="utf-8") as out:
        def emit(kind, **fields):
            out.write(json.dumps({"type": kind, "time": 0.0, **fields}) + "\n")

        emit("config", role="B", slots_per_second=500, slot_ticks=SLOT_TICKS, tx_offset=TX_OFFSET,
             tests={"steer_target_ns": -0.0215})
        for count, theta in ((10, -21_500), (11, -21_400), (12, -21_700)):
            for k in range(2, 500, 2):
                a_tx = TIMING.tx_fs(k)
                entries = [own(count, k, a_tx + 292_005_000 + theta, locked=k != 4),
                           peer(count, k, a_tx, TIMING.tx_fs(k - 1) + 292_005_000 - theta, measured=k != 6)]
                for e in entries:
                    emit("own" if isinstance(e, OwnMeasurement) else "peer", **asdict(e))
                    done = node.add(e)
                    if done:
                        emit("second", **done)
            emit("fc", count=count + 0.5, high=1675, low=600 + count)
        last = node.flush()
        assert last is not None
        emit("second", **last)
    read = logs.pairs(path)
    assert [(p.count, p.slot, p.theta_ns) for p in read] == on_node
    assert len(read) == 3 * 247  # slot 4 unlocked, slot 6 without a measurement
    entries = logs.read(path, ("config", "second", "fc"))
    assert {e["type"] for e in entries} == {"config", "second", "fc"}
    k, th = logs.seconds(entries)
    assert list(k) == [10, 11, 12] and th == pytest.approx([-21.5, -21.4, -21.7])
    assert list(logs.fc(entries)[1]) == [1675610, 1675611, 1675612]
    err = logs.steering_error(entries, settle_s=1)
    assert err["seconds"] == 2 and err["from_count"] == 11
    assert err["mean_ps"] == pytest.approx(-0.05) and err["rms_ps"] == pytest.approx((0.1 ** 2 / 2 + 0.2 ** 2 / 2) ** 0.5)
    assert logs.seconds(entries, min_pairs=248)[0].size == 0  # 247 pairs each
