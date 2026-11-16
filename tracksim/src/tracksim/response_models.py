"""Pure empirical response kernels and their explicit registry."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Callable

from .errors import ResponseError
from .specs import JITTER_NORMAL_BOUND, DetectorSpec


KernelResult = tuple[tuple[tuple[int, int], ...], tuple[float, float]] | None
Kernel = Callable[[DetectorSpec, list[float]], KernelResult]


def _stable_uniform(key: str) -> float:
    n = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    value = (n + 0.5) / (1 << 64)
    return min(math.nextafter(1.0, 0.0), max(math.nextafter(0.0, 1.0), value))


def _stable_normal(key: str) -> float:
    # Box-Muller with two independently keyed uniforms; avoid mutable shared RNG state.
    u1 = max(_stable_uniform(key + ":a"), 1e-16)
    u2 = _stable_uniform(key + ":b")
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


def _sharing_width(pitch_um: float, sigma_um: float) -> float:
    low, high = 0.0, pitch_um / 4
    minimum = math.sqrt(1 / 48) * pitch_um
    maximum = pitch_um / math.sqrt(12)
    if not minimum <= sigma_um <= maximum:
        raise ResponseError(
            f"resolution {sigma_um:g} um is not achievable for pitch {pitch_um:g} um"
        )
    for _ in range(80):
        mid = (low + high) / 2
        sigma2 = 2 / (3 * pitch_um) * ((pitch_um / 2 - mid) ** 3 + mid**3)
        if math.sqrt(sigma2) < sigma_um:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def _axis_pixels(
    x_mm: float, pitch_um: float, count: int, width_um: float
) -> tuple[list[int], float] | None:
    x = x_mm * 1000
    # Matrix origin is the center of pixel zero. Active bounds extend half a pixel.
    if x < -pitch_um / 2 or x >= (count - 0.5) * pitch_um:
        return None
    index = math.floor(x / pitch_um + 0.5)
    delta = x - index * pitch_um
    indexes = [index]
    if abs(delta) > pitch_um / 2 - width_um:
        adjacent = index + (1 if delta > 0 else -1)
        if 0 <= adjacent < count:
            indexes.append(adjacent)
    indexes = sorted(set(indexes))
    return indexes, (sum(indexes) / len(indexes) * pitch_um - x)


def _poisson(rng: random.Random, mean: float, maximum: int = 100_000) -> int:
    if (
        isinstance(mean, bool)
        or not isinstance(mean, (int, float))
        or not math.isfinite(mean)
        or mean < 0
    ):
        raise ResponseError("Poisson mean must be finite and nonnegative")
    if mean == 0:
        return 0
    # Poisson infinite divisibility gives an exact sum of independent chunks.
    # Keeping every Knuth product mean <=50 prevents exp(-mean) underflow.
    chunks = math.ceil(mean / 50.0)
    chunk_mean = mean / chunks
    total = 0
    for _ in range(chunks):
        limit = math.exp(-chunk_mean)
        product, count = 1.0, 0
        while product > limit:
            count += 1
            product *= rng.random()
        total += count - 1
        if total > maximum:
            raise ResponseError(f"sampled dark-noise count exceeds {maximum}")
    return total


def _compile_resolution(detector: DetectorSpec) -> Kernel:
    response = detector.response
    if response.resolution_um is None:
        raise ResponseError(f"{detector.id} resolution response has no target")
    widths = tuple(
        _sharing_width(pitch, resolution)
        for pitch, resolution in zip(detector.pitch_um, response.resolution_um)
    )
    return _compile_axes(detector, widths)


def _compile_cluster_mean(detector: DetectorSpec) -> Kernel:
    mean = detector.response.cluster_mean
    if mean is None or not 1 <= mean <= 2.25:
        raise ResponseError(f"{detector.id} cluster_mean must be in [1, 2.25]")
    fraction = (math.sqrt(mean) - 1) / 2
    widths = (detector.pitch_um[0] * fraction, detector.pitch_um[1] * fraction)
    return _compile_axes(detector, widths)


def _compile_axes(detector: DetectorSpec, widths: tuple[float, float]) -> Kernel:
    cols, rows = detector.pixels
    px, py = detector.pitch_um
    wx, wy = widths

    def apply(_detector: DetectorSpec, local_mm: list[float]) -> KernelResult:
        xvals = _axis_pixels(local_mm[0], px, cols, wx)
        yvals = _axis_pixels(local_mm[1], py, rows, wy)
        if xvals is None or yvals is None:
            return None
        coordinates = tuple((col, row) for col in xvals[0] for row in yvals[0])
        return coordinates, (xvals[1], yvals[1])

    return apply


class ResponseRegistry:
    """Explicit registry; configuration cannot import or replace callables."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[DetectorSpec], Kernel]] = {
            "boundary_resolution": _compile_resolution,
            "boundary_cluster_mean": _compile_cluster_mean,
        }
        self._builtin_names = frozenset(self._factories)

    def register(self, name: str, kernel: Kernel) -> None:
        if not isinstance(name, str) or not name or not name.isidentifier():
            raise ResponseError("external response name must be a Python identifier")
        if name in self._factories:
            raise ResponseError(f"response model {name!r} is already registered")
        if not callable(kernel):
            raise ResponseError("external response kernel must be callable")

        def factory(_detector: DetectorSpec) -> Kernel:
            return kernel

        self._factories[name] = factory

    def compile(self, detector: DetectorSpec) -> Kernel:
        try:
            factory = self._factories[detector.response.model]
        except KeyError as exc:
            raise ResponseError(
                f"unsupported response model {detector.response.model!r} for {detector.id}"
            ) from exc
        compiled = factory(detector)
        if not callable(compiled):
            raise ResponseError(
                f"response factory for {detector.id} returned no kernel"
            )
        return compiled

    def is_builtin(self, model: str) -> bool:
        return model in self._builtin_names
