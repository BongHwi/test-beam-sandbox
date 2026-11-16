"""Local/global affine map for a planar pixel matrix (origin at the centre of pixel zero)."""

from __future__ import annotations

import math


def _multiply(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)
    ]


def rotation_matrix(degrees):
    x, y, z = (math.radians(a) for a in degrees)
    cx, sx, cy, sy, cz, sz = (
        math.cos(x),
        math.sin(x),
        math.cos(y),
        math.sin(y),
        math.cos(z),
        math.sin(z),
    )
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _multiply(_multiply(rz, ry), rx)


def local_to_global(detector, local):
    centered = [
        local[0] - (detector.pixels[0] - 1) * detector.pitch_um[0] / 2000,
        local[1] - (detector.pixels[1] - 1) * detector.pitch_um[1] / 2000,
        local[2],
    ]
    rotation = rotation_matrix(detector.orientation_deg)
    return [
        detector.position_mm[i] + sum(rotation[i][j] * centered[j] for j in range(3))
        for i in range(3)
    ]


def global_to_local(detector, point):
    """Invert the common sensor-center affine map, retaining pixel-zero origin."""
    centered = [point[i] - detector.position_mm[i] for i in range(3)]
    rotation = rotation_matrix(detector.orientation_deg)
    local = [sum(rotation[j][i] * centered[j] for j in range(3)) for i in range(3)]
    return [
        local[0] + (detector.pixels[0] - 1) * detector.pitch_um[0] / 2000,
        local[1] + (detector.pixels[1] - 1) * detector.pitch_um[1] / 2000,
        local[2],
    ]
