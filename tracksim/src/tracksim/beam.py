"""Electron kinematics and scaled finite-dimensional Gaussian source contracts."""

from __future__ import annotations
import math

ELECTRON_MASS_GEV = 0.00051099895
SILICON_X0_MM = 93.70
AIR_X0_MM = 303900.0
MAX_POLAR_RAD = 0.05


def electron_kinematics(kinetic_energy_gev: float) -> tuple[float, float]:
    if not math.isfinite(kinetic_energy_gev) or kinetic_energy_gev <= 0:
        raise ValueError("electron kinetic energy must be finite and positive")
    momentum = math.sqrt(
        kinetic_energy_gev * (kinetic_energy_gev + 2 * ELECTRON_MASS_GEV)
    )
    return momentum, momentum / (kinetic_energy_gev + ELECTRON_MASS_GEV)


def covariance_factor(covariance) -> tuple[tuple[float, ...], ...]:
    """Return a PSD Gaussian factor, using dimensionless correlation tolerances.

    Zero-variance rows must be exactly zero. No diagonal jitter is introduced.
    A negative correlation pivot below -1e-12 is outside the numerical domain.
    """
    if not isinstance(covariance, (list, tuple)) or len(covariance) != 5:
        raise ValueError("beam.phase_space_covariance must be a 5x5 matrix")
    matrix = []
    for row in covariance:
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            raise ValueError("beam.phase_space_covariance must be a 5x5 matrix")
        if any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(x)
            for x in row
        ):
            raise ValueError("beam covariance must contain finite numbers")
        matrix.append([float(x) for x in row])
    if any(matrix[i][i] < 0 for i in range(5)):
        raise ValueError("beam covariance variances must be nonnegative")
    scales = [math.sqrt(matrix[i][i]) for i in range(5)]
    correlation = [[0.0] * 5 for _ in range(5)]
    for i in range(5):
        for j in range(5):
            if scales[i] == 0 or scales[j] == 0:
                if matrix[i][j] != 0 or matrix[j][i] != 0:
                    raise ValueError("zero-variance covariance rows must be zero")
                continue
            scale = scales[i] * scales[j]
            if abs(matrix[i][j] - matrix[j][i]) > 1e-12 * scale:
                raise ValueError("beam covariance must be symmetric in scaled units")
            correlation[i][j] = (matrix[i][j] / scale + matrix[j][i] / scale) / 2
    factor = [[0.0] * 5 for _ in range(5)]
    for i in range(5):
        for j in range(i + 1):
            residual = correlation[i][j] - sum(
                factor[i][k] * factor[j][k] for k in range(j)
            )
            if i == j:
                if residual < -1e-12:
                    raise ValueError("beam covariance must be positive semidefinite")
                factor[i][j] = math.sqrt(max(0.0, residual))
            elif factor[j][j] != 0:
                factor[i][j] = residual / factor[j][j]
            elif abs(residual) > 1e-12:
                raise ValueError("beam covariance must be positive semidefinite")
    return tuple(tuple(scales[i] * factor[i][j] for j in range(5)) for i in range(5))


def material_reference(detectors, source_z_mm: float, air: bool, extra_layers) -> dict:
    """Versioned apparatus-wide thin-sheet reference; it is not a ray path."""
    silicon_mm = sum((d.active_um + d.passive_um) / 1000 for d in detectors)
    silicon = silicon_mm / SILICON_X0_MM
    distance = max(d.position_mm[2] for d in detectors) - source_z_mm
    air_budget = distance / AIR_X0_MM if air else 0.0
    extras = sum(layer.material_budget for layer in extra_layers)
    total = silicon + air_budget + extras
    return {
        "rule": "apparatus-normal-incidence-v1",
        "silicon_budget": silicon,
        "air_budget": air_budget,
        "extra_budget": extras,
        "reference_budget": total,
        "possible_budget_upper_bound": total / math.cos(MAX_POLAR_RAD),
        "thin_sheet_air_overlap_upper_bound": silicon_mm / AIR_X0_MM if air else 0.0,
        "longitudinal_distance_mm": distance,
    }
