from __future__ import annotations

import numpy as np
import pytest

from rf_clock.pl.waveform import (DEFAULT_CONFIG_ID, WaveformGenerator, rrc_config,
                                  rrc_config_id)

# Coefficients of the default filter (beta 0.25, span 10, 8 samples/chip) as
# stored in the former RRC coefficient database; they must not drift.
GOLDEN_RRC = [
    -0.0041841625937675386, -0.0060512576712495585, -0.006374663875729477,
    -0.0048564656437480236, -0.0016357278848564158, 0.0026881872028280316,
    0.0071402181827947, 0.01055532587546208, 0.011834598974560484,
    0.010221408903193823, 0.005536800766307449, -0.0016848392744489187,
    -0.010203775662837202, -0.01824423205057157, -0.023809641997382742,
    -0.025095023149511883, -0.020920812968837677, -0.011098532964949542,
    0.0033582774180054994, 0.02024324858838316, 0.036415159287357626,
    0.04828698805342467, 0.052480583579380825, 0.04654167684468995,
    0.029586497436401193, 0.0027500803394758682, -0.030670470923014795,
    -0.06543939306422882, -0.09497368556691249, -0.1122384579139888,
    -0.11082315479417992, -0.08603423380153949, -0.035824569588526756,
    0.038605166754717854, 0.13265371969970385, 0.2386685714349705,
    0.34677165159803625, 0.4460574859335574, 0.525996072799209,
    0.5778347648126534, 0.5957882380808381, 0.5778347648126534,
    0.525996072799209, 0.4460574859335574, 0.34677165159803625,
    0.2386685714349705, 0.13265371969970385, 0.038605166754717854,
    -0.035824569588526756, -0.08603423380153949, -0.11082315479417992,
    -0.1122384579139888, -0.09497368556691249, -0.06543939306422882,
    -0.030670470923014795, 0.0027500803394758682, 0.029586497436401193,
    0.04654167684468995, 0.052480583579380825, 0.04828698805342467,
    0.036415159287357626, 0.02024324858838316, 0.0033582774180054994,
    -0.011098532964949542, -0.020920812968837677, -0.025095023149511883,
    -0.023809641997382742, -0.01824423205057157, -0.010203775662837202,
    -0.0016848392744489187, 0.005536800766307449, 0.010221408903193823,
    0.011834598974560484, 0.01055532587546208, 0.0071402181827947,
    0.0026881872028280316, -0.0016357278848564158, -0.0048564656437480236,
    -0.006374663875729477, -0.0060512576712495585, -0.0041841625937675386,
]


def test_default_filter_matches_the_reference_coefficients():
    config, coeffs = rrc_config()
    assert config["id"] == DEFAULT_CONFIG_ID == 2_134_958_710
    assert (config["tap_count"], config["tail_samples"]) == (81, 73)
    # Allow last-bit differences between machines, nothing more.
    np.testing.assert_allclose(coeffs, GOLDEN_RRC, rtol=0, atol=1e-15)


def test_other_parameters_give_another_filter():
    generator = WaveformGenerator(span_chips=2, samples_per_symbol=2, chip_count=2)
    assert generator.tap_count == 5
    assert len(generator.generate()) == (2 - 1) * 2 + 5
    assert generator.config_id == rrc_config_id(0.25, 2, 2) != DEFAULT_CONFIG_ID
    with pytest.raises(ValueError):
        rrc_config(span_chips=3)  # the filter needs an even span


def test_periodic_waveform_repeats_without_a_seam():
    import numpy as np

    from rf_clock.pl.waveform import (generate_test_chips, simulate_full_convolution,
                                      simulate_periodic_convolution)

    gen = WaveformGenerator()
    sps = gen.samples_per_symbol
    chips = generate_test_chips(64)
    period = simulate_periodic_convolution(chips, gen.coeffs, sps, 512)
    assert period.size == 64 * sps
    # Three passes back to back equal the burst of three copies away from its ends.
    burst = simulate_full_convolution(np.tile(chips, 3), gen.coeffs, sps, 512)
    assert np.array_equal(burst[64 * sps: 128 * sps], period)
    assert len(WaveformGenerator(chip_count=64, periodic=True).generate_beats()) == 64
