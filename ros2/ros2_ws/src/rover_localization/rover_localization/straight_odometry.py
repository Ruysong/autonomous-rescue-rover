"""Pure calculations for a straight-line MOCK, never a 6-wheel steering model."""

import math
from collections.abc import Sequence
from dataclasses import dataclass


INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
NS_PER_S = 1_000_000_000


def positive_finite(value: float, name: str) -> None:
    """Reject invalid dimensional parameters, including booleans."""
    if (type(value) not in (int, float) or not 0 < value < math.inf):
        raise ValueError(f'{name} must be finite and positive')


@dataclass(frozen=True)
class MockGeometry:
    """Explicit mock dimensions and input validity bounds."""

    wheel_radius_m: float
    counts_per_wheel_rev: int
    left_encoder_sign: int = 1
    right_encoder_sign: int = 1
    min_dt_s: float = 1e-6
    max_gap_s: float = 0.5
    max_tick_rate_per_s: float = 10000.0

    def __post_init__(self) -> None:
        """Validate dimensions before processing any samples."""
        positive_finite(self.wheel_radius_m, 'wheel_radius_m')
        positive_finite(self.min_dt_s, 'min_dt_s')
        positive_finite(self.max_gap_s, 'max_gap_s')
        positive_finite(self.max_tick_rate_per_s, 'max_tick_rate_per_s')
        if (type(self.counts_per_wheel_rev) is not int
                or self.counts_per_wheel_rev <= 0):
            raise ValueError('counts_per_wheel_rev must be a positive integer')
        for sign in (self.left_encoder_sign, self.right_encoder_sign):
            if type(sign) is not int or sign not in (-1, 1):
                raise ValueError('encoder signs must be integer -1 or +1')
        if self.min_dt_s >= self.max_gap_s:
            raise ValueError('min_dt_s must be smaller than max_gap_s')
        positive_finite(self.meters_per_tick, 'meters_per_tick')

    @property
    def meters_per_tick(self) -> float:
        """counts_per_wheel_rev already includes quadrature and gearing."""
        return math.tau * self.wheel_radius_m / self.counts_per_wheel_rev


@dataclass(frozen=True)
class Motion:
    """A valid straight segment; continuity is false after omitted motion."""

    stamp_ns: int
    x_m: float
    delta_m: float
    linear_velocity_mps: float
    continuous: bool


@dataclass(frozen=True)
class Update:
    """No motion output is produced for baselines or rejected samples."""

    status: str
    motion: Motion | None = None


class StraightOdometry:
    """Integrate equal signed wheel increments using explicit sample times."""

    def __init__(self, geometry: MockGeometry):
        """Own one mock trajectory and its input baseline."""
        self.geometry = geometry
        self.reset()

    def reset(self) -> None:
        """Start a new mock trajectory; callers must know when counters reset."""
        self.x_m = 0.0
        self.continuous = True
        self._previous: tuple[int, int, int] | None = None

    def invalidate(self) -> None:
        """Forget the count baseline without inventing missing displacement."""
        self._previous = None
        self.continuous = False

    def update(self, counts: Sequence[int], stamp_ns: int) -> Update:
        """Rebase invalid intervals and distinguish time restarts from reverse."""
        if (type(stamp_ns) is not int or not 0 <= stamp_ns <= INT64_MAX
                or len(counts) != 2
                or any(type(value) is not int or not INT64_MIN <= value <= INT64_MAX
                       for value in counts)):
            self.invalidate()
            return Update('invalid_sample')
        if stamp_ns == 0:
            self.reset()
            return Update('clock_unstarted')
        sample = (counts[0], counts[1], stamp_ns)
        previous = self._previous
        self._previous = sample
        if previous is None:
            return Update('baseline')
        delta_ns = stamp_ns - previous[2]
        if delta_ns < 0:
            self.reset()
            self._previous = sample
            return Update('clock_restarted')
        dt_s = delta_ns / NS_PER_S
        reason = None
        left_delta = (counts[0] - previous[0]) * self.geometry.left_encoder_sign
        right_delta = (counts[1] - previous[1]) * self.geometry.right_encoder_sign
        if delta_ns == 0:
            reason = 'duplicate_time'
        elif dt_s < self.geometry.min_dt_s:
            reason = 'dt_too_small'
        elif dt_s > self.geometry.max_gap_s:
            reason = 'gap'
        elif max(abs(left_delta), abs(right_delta)) > (
                self.geometry.max_tick_rate_per_s * dt_s):
            reason = 'count_jump'
        elif left_delta != right_delta:
            reason = 'unsupported_turn'
        if reason:
            self.continuous = False
            return Update(reason)
        delta_m = left_delta * self.geometry.meters_per_tick
        velocity_mps = delta_m / dt_s
        next_x_m = self.x_m + delta_m
        if not all(math.isfinite(value) for value in (delta_m, velocity_mps, next_x_m)):
            self.continuous = False
            return Update('nonfinite_motion')
        self.x_m = next_x_m
        return Update('ok', Motion(stamp_ns, self.x_m, delta_m,
                                   velocity_mps, self.continuous))


def diagonal_covariance(variances: Sequence[float]) -> list[float]:
    """Build row-major covariance in x/y/z/roll/pitch/yaw order."""
    if len(variances) != 6:
        raise ValueError('covariance needs exactly six diagonal variances')
    result = [0.0] * 36
    for index, value in enumerate(variances):
        positive_finite(value, 'covariance variance')
        result[index * 7] = float(value)
    return result
