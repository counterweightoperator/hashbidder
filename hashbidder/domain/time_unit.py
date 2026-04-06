"""Time period units."""

from enum import Enum


class TimeUnit(Enum):
    """Time period denominator for rates."""

    SECOND = 1
    MINUTE = 60
    HOUR = 3_600
    DAY = 86_400
    MONTH = 2_592_000
