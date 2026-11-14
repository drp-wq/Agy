"""Normalize exchange epoch timestamps without substituting receipt time."""
import math


def source_timestamp_ms(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        epoch = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(epoch) or epoch <= 0:
        return None
    # Contemporary epoch seconds or milliseconds only; reject ambiguous units.
    if 1e9 <= epoch < 1e10:
        epoch *= 1000
    elif not 1e12 <= epoch < 1e13:
        return None
    return int(epoch)
