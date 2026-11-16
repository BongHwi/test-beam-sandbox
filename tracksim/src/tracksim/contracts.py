"""Shared input limits for values consumed by more than one simulation stage."""

SEED_MAX = (1 << 31) - 1


def validate_seed(value: object, name: str = "seed") -> int:
    """Return a seed in the signed 32-bit range shared by every stage and backend."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= SEED_MAX
    ):
        raise ValueError(f"{name} must be an integer in [0, {SEED_MAX}]")
    return value
