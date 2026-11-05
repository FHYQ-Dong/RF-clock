from __future__ import annotations

import json

import numpy as np
import pytest

from rf_clock.twtt.steer import (AR4, AR20_STEP_02, ClockFilter, LoopModel, PredictiveSteering, Steering, SteeringError,
                                  check_unit, filter_for, fit_frequency_ar, fit_loop, fit_sf_response, follow, lag_phase,
                                  loop_response, predictive_for)

# B's FS725 (S/N 107129) by GA, as its board config holds it (results/fs725-fll-20261105)
LOOPS = {7: (0.95, 1.1, 0.15), 8: (2.2, 1.2, 0.12)}


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


def run_predictive(steering: PredictiveSteering, free: np.ndarray, *, skip: tuple[int, ...] = ()) -> tuple[np.ndarray, list]:
    """theta of every second for free clocks ``free`` (ps per second), the plant answering
    exactly as the steering's model; the changes of SF made."""
    model = steering.model
    asked, changes, theta = [], [], []
    for k, f in enumerate(free):
        own = float(np.dot(changes, model.answer(k - np.asarray(asked)))) if asked else 0.0
        theta.append(f + own)
        record = steering.step(k, None if k in skip else theta[-1])
        if record["sf"] is not None:
            asked.append(k)
            changes.append(record["sf"] - (steering.sf0 + sum(changes)))
    return np.array(theta), changes


RESONANCE = (2 * 0.9 * np.cos(2 * np.pi / 6), -0.81)  # AR(2) of a 6 s resonance, as the bench pair's wander


def wandering_pair(seconds: int, seed: int = 3) -> np.ndarray:
    """A pair 66 ps/s apart whose frequency wanders with a 6 s resonance, as on the bench."""
    rng = np.random.default_rng(seed)
    f = np.zeros(seconds)
    for k in range(2, seconds):
        f[k] = RESONANCE[0] * f[k - 1] + RESONANCE[1] * f[k - 2] + rng.normal(0, 8.0)
    return np.cumsum(-66.0 + f)


def test_the_loop_model_answers_as_the_loop():
    model = LoopModel(*LOOPS[8])
    assert float(model.answer(0)) == 0.0  # the second that asks is not touched
    assert float(model.answer(1)) < 0.2  # the next only by its last 0.85 s through the loop
    late = float(model.answer(80)) - float(model.answer(79))
    assert late == pytest.approx(1.0, abs=1e-3)  # then a ramp of 1 ps/s per unit
    assert float(model.answer(300)) - float(model.answer(299)) == pytest.approx(1.0, abs=1e-3)  # beyond the table


def test_predictive_steering_holds_a_wandering_pair():
    # 3 ns off at the start; with the resonance known, theta holds to about 1.3 times the pair's
    # second-to-second wander (Steering at tau 5 s, every 2 s: 1.6 times; no prediction: 2.0 times)
    free = wandering_pair(900) + 3000.0
    steering = PredictiveSteering(target_ps=0.0, sf0=3, model=LoopModel(*LOOPS[8]), ar=RESONANCE)
    theta, changes = run_predictive(steering, free)
    assert max(abs(c) for c in changes) <= steering.max_step
    after = theta[300:]
    assert abs(after.mean()) < 3
    assert np.sqrt(np.mean(after ** 2)) < 1.4 * np.std(np.diff(free))


def test_predictive_steering_rides_over_missing_seconds():
    free = wandering_pair(600)
    steering = PredictiveSteering(target_ps=float(free[0]), sf0=0, model=LoopModel(*LOOPS[7]), ar=RESONANCE)
    theta, _ = run_predictive(steering, free, skip=(100, 101, 250, 320))
    assert np.sqrt(np.mean((theta[330:] - free[0]) ** 2)) < 1.6 * np.std(np.diff(free))


def test_predictive_steering_ignores_a_second_given_twice():
    steering = PredictiveSteering(target_ps=0.0, sf0=0, model=LoopModel(*LOOPS[8]))
    steering.step(10, 5.0)
    steering.step(11, 6.0)
    assert steering.step(11, 99.0)["event"] == "second already taken"
    assert steering.free == [5.0, 6.0]


def test_the_loop_fit_finds_the_loop_from_fc_alone():
    # FC read about every 0.1 s around a change of SF at t = 3.0: drift, the loop's answer, noise
    rng = np.random.default_rng(2)
    t = np.sort(rng.uniform(-10, 25, 350)) + 3.0
    k, lag, dead = 1.5, 1.65, 0.15
    grid = np.arange(0, 30, 0.01)
    u = np.zeros_like(grid)
    fc = out = 0.0
    for i in range(1, grid.size):  # FC in the loop of loop_response
        fc += k * ((1.0 if grid[i] > dead else 0.0) - out) * 0.01
        out += (fc - out) / lag * 0.01
        u[i] = fc
    assert out == pytest.approx(loop_response(k, lag, dead, span_s=30)[-1], abs=1e-3)
    v = 1_675_600 + 0.3 * t + 130 * np.interp(np.clip(t - 3.0, 0, None), grid, u) + rng.normal(0, 3, t.size)
    found = fit_loop(t, v, [3.0])
    assert (found["k_per_s"], found["lag_s"], found["dead_s"]) == pytest.approx((k, lag, dead), abs=0.051)
    assert found["fc_changes"][0] == pytest.approx(130, rel=0.05) and found["rms_steps"] < 4
    assert found["alone"][0]["k_per_s"] == found["k_per_s"]


def test_the_ar_fit_finds_the_resonance():
    a = fit_frequency_ar(wandering_pair(3000, seed=5), order=2, mean_window=60)
    assert a == pytest.approx(RESONANCE, abs=0.05)
    assert len(AR4) == 4


def test_the_loop_model_on_short_steps():
    model = LoopModel(*LOOPS[8], window=(0.1, 0.2), command_s=0.03, step_s=0.2)
    assert float(model.answer(0)) == 0.0
    late = float(model.answer(400)) - float(model.answer(399))
    assert late == pytest.approx(0.2, abs=1e-3)  # 1 ps/s per unit, 0.2 s a step
    assert float(model.answer(1000)) - float(model.answer(999)) == pytest.approx(0.2, abs=1e-3)  # beyond the table


def test_predictive_steering_on_steps_of_a_fifth_of_a_second():
    # the pair of wandering_pair on a 0.2 s grid: the same 6 s resonance, AR(2) per step
    rng = np.random.default_rng(4)
    r, w = 0.98, 2 * np.pi * 0.2 / 6
    ar = (2 * r * np.cos(w), -r * r)
    f = np.zeros(4000)
    for i in range(2, f.size):
        f[i] = ar[0] * f[i - 1] + ar[1] * f[i - 2] + rng.normal(0, 1.5)
    free = np.cumsum((-66.0 + f) * 0.2) + 2000.0
    steering = PredictiveSteering(target_ps=0.0, sf0=0, ar=ar, horizon=20, lam=0.2,
                                  model=LoopModel(*LOOPS[8], window=(0.1, 0.2), command_s=0.03, step_s=0.2))
    theta, changes = run_predictive(steering, free)
    assert max(abs(c) for c in changes) <= steering.max_step
    after = theta[1500:]
    assert abs(after.mean()) < 3
    assert np.sqrt(np.mean(after ** 2)) < 0.5 * np.std(np.diff(free[::5]))  # half the pair's second-to-second wander


def test_predictive_for_picks_the_steps_settings():
    fast = predictive_for(0.0, 0, LOOPS[8], late_s=0.1, step_s=0.2)
    assert fast.step_s == 0.2 and tuple(fast.ar) == AR20_STEP_02 and fast.horizon == 20 and fast.lam == 0.2
    assert predictive_for(0.0, 0, LOOPS[8], step_s=0.2, lam=0.5).lam == 0.5
    with pytest.raises(SteeringError, match="0.5 s"):
        predictive_for(0.0, 0, LOOPS[8], step_s=0.5)


def test_old_changes_fold_into_the_linear_tail():
    # a long run at 0.2 s steps with a short table: the folded own() equals the sum over every
    # change made, whose steps this loop keeps itself
    rng = np.random.default_rng(2)
    free = np.cumsum((-66.0 + rng.normal(0, 8.0, 3000)) * 0.2)
    model = LoopModel(*LOOPS[8], window=(0.1, 0.2), command_s=0.03, step_s=0.2, table_s=20)
    steering = PredictiveSteering(target_ps=0.0, sf0=0, ar=(0.5,), horizon=20, lam=0.2, model=model)
    asked, changes, sf = [], [], 0
    for k, f in enumerate(free):
        own = float(np.dot(changes, model.answer(k - np.asarray(asked)))) if asked else 0.0
        record = steering.step(k, f + own)
        if record["sf"] is not None:
            asked.append(k)
            changes.append(record["sf"] - sf)
            sf = record["sf"]
    assert steering.old_total != 0 and len(steering.asked) < len(changes)  # some folded
    for k in (2999, 3010, 3100):  # own() holds for the last step taken and later ones
        brute = float(np.dot(changes, model.answer(k - np.asarray(asked))))
        assert steering.own(k) == pytest.approx(brute, abs=1e-6)
