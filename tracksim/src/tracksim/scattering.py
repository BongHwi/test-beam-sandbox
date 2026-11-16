"""Gaussian-core Highland normalization and distributed air steps."""

from __future__ import annotations

import math

from .beam import AIR_X0_MM, MAX_POLAR_RAD


def highland_coefficient(
    momentum_gev: float,
    beta: float,
    reference_budget: float,
    enabled: bool = True,
) -> float:
    """Return the fixed apparatus-normalized Highland variance per X0.

    The Highland logarithmic correction is evaluated once per event from the
    complete apparatus reference budget. Callers multiply this coefficient by
    each assigned air or silicon sheet budget. It is never recomputed per step.
    """
    for value, label in (
        (momentum_gev, "momentum_gev"),
        (beta, "beta"),
        (reference_budget, "reference_budget"),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{label} must be finite")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be bool")
    if momentum_gev <= 0 or not 0 < beta <= 1 or reference_budget < 0:
        raise ValueError("invalid momentum, beta, or material reference budget")
    if not enabled or reference_budget == 0:
        return 0.0
    if not 1e-6 <= reference_budget <= 0.02 * math.cos(MAX_POLAR_RAD):
        raise ValueError("reference material budget is outside the supported domain")
    momentum_mev = momentum_gev * 1000.0
    correction = 1.0 + 0.038 * math.log(reference_budget / (beta * beta))
    if not math.isfinite(correction) or correction <= 0:
        raise ValueError("Highland logarithmic correction is outside the domain")
    coefficient = (13.6 / (beta * momentum_mev) * correction) ** 2
    if not math.isfinite(coefficient) or coefficient < 0:
        raise ValueError("Highland coefficient is invalid")
    return coefficient


def air_step(
    x: float,
    y: float,
    tx: float,
    ty: float,
    length_mm: float,
    prefactor: float,
    air_enabled: bool,
    rng,
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float], float]:
    """Propagate one air segment with correlated Gaussian displacement/kicks.

    Returns ``(new_state, delta_state, traversed_air_budget)``. The incoming
    paraxial slopes define both the path factor and the flight-time increment
    that the caller records. Two independent standard normals are drawn per
    axis when the segment has nonzero scattering variance.
    """
    values = (x, y, tx, ty, length_mm, prefactor)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("air-step inputs must be finite numbers")
    if length_mm < 0 or prefactor < 0 or not isinstance(air_enabled, bool):
        raise ValueError("invalid air-step length, prefactor, or capability")
    if length_mm == 0:
        state = (float(x), float(y), float(tx), float(ty))
        return state, (0.0, 0.0, 0.0, 0.0), 0.0

    incoming = (float(x), float(y), float(tx), float(ty))
    air_budget = (
        length_mm * math.sqrt(1.0 + tx * tx + ty * ty) / AIR_X0_MM
        if air_enabled
        else 0.0
    )
    variance = prefactor * air_budget
    if not math.isfinite(air_budget) or not math.isfinite(variance):
        raise ValueError("air-step material or variance is not finite")

    dx = dy = dtx = dty = 0.0
    if variance > 0:
        root = math.sqrt(variance)
        local = math.sqrt(variance / 12.0)
        dtx = root * rng.gauss(0.0, 1.0)
        dx = tx * length_mm + length_mm * (dtx / 2.0 + local * rng.gauss(0.0, 1.0))
        dty = root * rng.gauss(0.0, 1.0)
        dy = ty * length_mm + length_mm * (dty / 2.0 + local * rng.gauss(0.0, 1.0))
    else:
        dx = tx * length_mm
        dy = ty * length_mm
    state = (x + dx, y + dy, tx + dtx, ty + dty)
    delta = tuple(state[index] - incoming[index] for index in range(4))
    if any(not math.isfinite(value) for value in state + delta):
        raise ValueError("air-step state is not finite")
    return state, delta, air_budget
