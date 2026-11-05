from __future__ import annotations

import json

import numpy as np
import pytest

from rf_clock.twtt.steer import (ClockFilter, Steering, SteeringError, check_unit, filter_for, fit_sf_response, follow,
                                  lag_phase)


def run(steering: Steering, seconds: int, *, x0: float, y0: float, adev: float = 2e-11, theta_sd: float = 3.0,
        responds: bool = True, seed: int = 1) -> np.ndarray:
    """B's clock against A's, steered: the true theta of every second."""
    rng = np.random.default_rng(seed)
    x, y_free, xs = x0, y0, []
    for count in range(seconds):
        record = steering.step(count, x + rng.normal(0, theta_sd))
        y = y_free + ((steering.sf - steering.sf0) if responds else 0)
        x += y + rng.normal(0, adev * 1e12)  # white frequency noise: theta random-walks
        xs.append(x)
        assert record["count"] == count
    return np.array(xs)


def run_delayed(steering: Steering, seconds: int, *, x0: float, y0: float, delay: float, seed: int = 1) -> tuple:
    """As ``run``, with quiet clocks, but each change of SF moves the frequency only
    ``delay`` seconds after the second that asked for it (B's FS725 on the bench: 1.9 s).
    Returns the true theta of every second and the filter's events."""
    rng = np.random.default_rng(seed)
    x, sf, changes, xs, events = x0, steering.sf0, [], [], []
    for count in range(seconds):
        record = steering.step(count, x + rng.normal(0, 0.5))
        if "event" in record:
            events.append(record["event"])
        if steering.sf != sf:
            changes.append((count + delay, steering.sf - sf))
            sf = steering.sf
        # this second's increment: the free frequency and each change for the part of the second it acts
        x += y0 + sum(u * min(1.0, max(0.0, count + 1 - when)) for when, u in changes) + rng.normal(0, 1.0)
        xs.append(x)
    return np.array(xs), events


@pytest.mark.parametrize("model_delay, model_sd, restarts", [(0.0, 0.0, True), (1.9, 0.5, False)])
def test_a_delayed_response_is_modelled(model_delay, model_sd, restarts):
    # A fast loop (a correction every 2 s, tau 5 s) starting 10 ns off, with the FS725 answering 1.9 s late:
    # counted from the second that asked (the 1.x model), the first large steps look like outliers and the
    # filter starts again, as on the bench; counted from when they act, the loop settles.
    steering = Steering(target_ps=0.0, sf0=0, filter=filter_for(1.4e-11, 3.0), tau_s=5, interval_s=2, settle_s=30,
                        response_delay_s=model_delay, response_sd_s=model_sd)
    xs, events = run_delayed(steering, 600, x0=-10_000.0, y0=-70.0, delay=1.9)
    assert any("restarts" in e for e in events) == restarts
    if not restarts:
        assert np.sqrt(np.mean(xs[200:] ** 2)) < 30
        assert np.abs(xs[100:]).max() < 2000  # no run past the target


def test_lag_phase():
    assert lag_phase(-1.0, 2.0) == lag_phase(0.0, 2.0) == 0.0
    assert lag_phase(3.0, 0.0) == 3.0
    assert lag_phase(0.01, 2.0) == pytest.approx(0.01 ** 2 / 4, rel=0.01)  # s^2 / (2 lag) at first
    assert lag_phase(40.0, 2.0) == pytest.approx(38.0)  # then a ramp, lag later


@pytest.mark.parametrize("model_lag, rejects", [(2.0, False), (0.0, True)])
def test_a_lagging_response_is_counted_as_it_acts(model_lag, rejects):
    # A change of 100 ps/s that begins at 1.3 s and follows with a 2 s lag (a PRS10 at GA 7); no clock noise.
    # The lag model predicts theta exactly; the delay-only model runs 100 ps/s x 2 s ahead and is rejected.
    steering = Steering(target_ps=0.0, sf0=0, filter=ClockFilter(q_wfm=0.01, q_rwfm=1e-8, r=0.25), tau_s=1e4,
                        interval_s=1000, settle_s=10_000, response_lag_s=model_lag, response_sd_s=0.0)
    steering.step(0, 0.0)
    steering.pending.append((1.3, 100.0))
    rejected = False
    for count in range(1, 40):
        truth = 100 * lag_phase(count - 1.3, 2.0)
        record = steering.step(count, truth)
        rejected |= record.get("rejected", False)
        if not rejects:
            assert record["x_ps"] == pytest.approx(truth, abs=0.5)
    assert rejected == rejects
    if not rejects:
        assert not steering.pending  # counted whole once settled, in the filter's y
        assert steering.filter.y == pytest.approx(100.0, abs=0.5)


def test_the_filter_finds_a_constant_frequency():
    f = ClockFilter(q_wfm=1.0, q_rwfm=1e-6, r=9.0)
    rng = np.random.default_rng(0)
    f.start(100.0)
    for t in range(1, 300):
        f.predict(1)
        f.update(100.0 + 2.5 * t + rng.normal(0, 3))
    assert f.y == pytest.approx(2.5, abs=0.1)
    assert f.x == pytest.approx(100.0 + 2.5 * 299, abs=3)


def test_an_outlier_is_rejected():
    f = ClockFilter(q_wfm=1.0, q_rwfm=1e-6, r=9.0)
    f.start(0.0)
    for _ in range(20):
        f.predict(1)
        f.update(0.0)
    f.predict(1)
    assert not f.update(1000.0)
    assert f.x == pytest.approx(0.0, abs=1)


def test_the_loop_steers_theta_to_its_target():
    # Quiet clocks (1e-12 at 1 s), so that the convergence itself shows.
    steering = Steering(target_ps=10_000.0, sf0=40, filter=filter_for(1e-12, 3.0), tau_s=100, interval_s=10)
    xs = run(steering, 3000, x0=10_800.0, y0=30.0, adev=1e-12)  # 0.8 ns off, 3e-11 fast
    assert abs(xs[-1000:].mean() - 10_000.0) < 10  # theta random-walks by 1 ps per sqrt(s): about 7 ps rms at tau 100 s
    assert xs[-1000:].std() < 15
    assert steering.sf - steering.sf0 == pytest.approx(-30, abs=2)  # cancels the frequency offset


def test_with_the_fs725_specification_theta_wanders_by_the_clocks_noise():
    # 2e-11 at 1 s of white frequency noise lets theta random-walk by 20 ps per sqrt(s);
    # a loop of time constant tau holds it to about 2e-11 * sqrt(tau / 2), 140 ps at 100 s.
    errors = []
    for seed in range(4):
        steering = Steering(target_ps=0.0, sf0=0, filter=filter_for(2e-11, 3.0), tau_s=100, interval_s=10)
        errors.append(run(steering, 4000, x0=300.0, y0=-50.0, seed=seed)[1000:])
    assert 60 < np.sqrt(np.mean(np.concatenate(errors) ** 2)) < 300


def test_sf_stays_within_its_allowed_offset():
    # A reference 6e-10 off cannot be followed with max_offset 500.
    steering = Steering(target_ps=0.0, sf0=0, filter=filter_for(1e-12, 3.0), tau_s=100, max_offset=500)
    with pytest.raises(SteeringError):
        run(steering, 2000, x0=0.0, y0=600.0, adev=1e-12)


def test_a_dry_run_leaves_sf():
    steering = Steering(target_ps=0.0, sf0=7, filter=filter_for(2e-11, 3.0), apply=False)
    run(steering, 200, x0=500.0, y0=0.0, responds=False)
    assert steering.sf == 7


def test_tau_shorter_than_two_intervals_is_refused():
    with pytest.raises(ValueError):
        Steering(target_ps=0.0, sf0=0, filter=filter_for(2e-11, 3.0), tau_s=15, interval_s=10)


def test_follow_reads_the_results_of_a_log(tmp_path):
    log = tmp_path / "twtt_B.jsonl"
    lines = [{"type": "info"}, {"type": "result", "count_a": 1, "theta_ns": 10.0},
             {"type": "local"}, {"type": "result", "count_a": 2, "theta_ns": None}]
    log.write_text("".join(json.dumps(e) + "\n" for e in lines))
    assert [e["count_a"] for e in follow(str(log), from_start=True, wait=False)] == [1, 2]
    assert list(follow(str(log), wait=False)) == []  # from the end: nothing new


class Unit:
    def __init__(self, sf: int, pll: bool) -> None:
        self.sf, self.pll = sf, pll

    def frequency(self) -> int:
        return self.sf

    def phase_lock(self) -> bool:
        return self.pll


def test_a_correction_needs_the_unit_as_the_steering_left_it():
    check_unit(Unit(sf=40, pll=False), 40)
    with pytest.raises(SteeringError, match="PLL is on again"):
        check_unit(Unit(sf=40, pll=True), 40)  # restarted: the PLL default came back
    with pytest.raises(SteeringError, match="SF is 37, not the 40"):
        check_unit(Unit(sf=37, pll=False), 40)  # restarted (the pot's value) or changed by someone else


@pytest.mark.parametrize("delay", [0.4, 1.3, 2.2])
def test_the_response_to_an_sf_step_is_fitted(delay):
    # theta sampled finely, a rate change of +100 ps/s acting `delay` s after a command at second 30.3,
    # wander and noise added, then averaged over each second as the node does
    rng = np.random.default_rng(3)
    t = np.arange(0, 60, 0.004)
    tc = 30.3 + delay
    wander = np.cumsum(rng.normal(0, 0.3, t.size))
    theta = -70 * t + 100 * np.clip(t - tc, 0, None) + wander + rng.normal(0, 1.0, t.size)
    counts = np.arange(60)
    means = [theta[(t >= k) & (t < k + 1)].mean() for k in counts]
    fit = fit_sf_response(counts, means, 30.3)
    assert fit is not None
    assert fit["delay_s"] == pytest.approx(delay, abs=0.15)
    assert fit["rate_ps_per_s"] == pytest.approx(100, rel=0.1)


def test_a_fit_needs_seconds_on_both_sides():
    assert fit_sf_response(range(40), [0.0] * 40, 35.0) is None
