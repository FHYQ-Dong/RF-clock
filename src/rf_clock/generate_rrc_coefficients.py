# Function to generate RRC coefficients

import numpy

def generate_rrc_coefficients(
    beta: float,
    span_chips: int,
    samples_per_symbol: int,
    coeff_bits: int,
    frac_bits: int,
) -> list[int]:    
    if not numpy.isfinite(beta) or not 0.0 <= beta <= 1.0:
        raise ValueError("beta must be in [0, 1]")
    if span_chips % 2 != 0:
        raise ValueError("span_chips must be even")
    if samples_per_symbol <= 0:
        raise ValueError("samples_per_symbol must be positive")
    if coeff_bits < 2:
        raise ValueError("coeff_bits must be at least 2")
    if span_chips <= 0 or span_chips % 2 != 0:
        raise ValueError("span_chips must be a positive even number")

    half_samples = span_chips * samples_per_symbol // 2
    
    qscale = 1 << frac_bits

    t = (
        numpy.arange(
            -half_samples,
            half_samples + 1,
            dtype=numpy.float64,
        )
        / samples_per_symbol
    )

    if beta == 0.0:
        # When beta is 0, RRC degenerates to sinc
        h = numpy.sinc(t)

    else:
        h = numpy.empty_like(t)

        zero = numpy.isclose(t, 0.0, atol=1e-12)
        singular = numpy.isclose(
            numpy.abs(t),
            1.0 / (4.0 * beta),
            atol=1e-12,
        )
        ordinary = ~(zero | singular)

        # Ordinary points
        u = t[ordinary]
        h[ordinary] = (
            numpy.sin(numpy.pi * u * (1.0 - beta))
            + 4.0 * beta * u
            * numpy.cos(numpy.pi * u * (1.0 + beta))
        ) / (
            numpy.pi * u
            * (1.0 - (4.0 * beta * u) ** 2)
        )

        # t = 0
        h[zero] = 1.0 - beta + 4.0 * beta / numpy.pi

        # t = ±1/(4β)
        singular_value = (
            beta / numpy.sqrt(2.0)
            * (
                (1.0 + 2.0 / numpy.pi)
                * numpy.sin(numpy.pi / (4.0 * beta))
                + (1.0 - 2.0 / numpy.pi)
                * numpy.cos(numpy.pi / (4.0 * beta))
            )
        )
        h[singular] = singular_value

    # Unit energy normalization
    h /= numpy.sqrt(numpy.sum(h * h))

    # Limit maximum L1 gain for each phase after zero-padding
    phase_l1 = max(
        numpy.sum(numpy.abs(h[phase::samples_per_symbol]))
        for phase in range(samples_per_symbol)
    )
    h *= 0.98 / phase_l1

    # Quantize to signed Q1.(coeff_bits-1)
    qscale = 1 << (coeff_bits - 1)
    q = numpy.sign(h) * numpy.floor(numpy.abs(h) * qscale + 0.5)
    q = q.astype(numpy.int64)

    minimum = -(1 << (coeff_bits - 1))
    maximum = (1 << (coeff_bits - 1)) - 1

    if numpy.any(q < minimum) or numpy.any(q > maximum):
        raise ValueError("quantized coefficient exceeds signed range")

    if len(q) != span_chips * samples_per_symbol + 1:
        raise ValueError("unexpected coefficient count")

    if not numpy.array_equal(q, q[::-1]):
        raise ValueError("RRC coefficients are not symmetric")

    return [int(value) for value in q]
