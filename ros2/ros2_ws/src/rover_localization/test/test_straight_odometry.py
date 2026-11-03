"""Analytical straight-line examples and ambiguous/invalid input boundaries."""

import math
from dataclasses import replace

import pytest

from rover_localization.straight_odometry import (
    INT64_MAX, INT64_MIN, MockGeometry, StraightOdometry, diagonal_covariance,
)


GEOMETRY = MockGeometry(wheel_radius_m=0.05, counts_per_wheel_rev=1000)
STEP_M = 10 * math.tau * 0.05 / 1000


def test_forward_stop_reverse_and_nonzero_start_counts():
    """Compare signed distance and speed with an analytical straight path."""
    estimator = StraightOdometry(GEOMETRY)
    assert estimator.update([1000, 1000], 1_000_000_000).motion is None
    forward = estimator.update([1010, 1010], 1_100_000_000).motion
    assert forward.x_m == pytest.approx(STEP_M)
    assert forward.delta_m == pytest.approx(STEP_M)
    assert forward.linear_velocity_mps == pytest.approx(STEP_M / 0.1)
    stopped = estimator.update([1010, 1010], 1_200_000_000).motion
    assert stopped.x_m == pytest.approx(STEP_M)
    assert stopped.linear_velocity_mps == 0
    reverse = estimator.update([1000, 1000], 1_300_000_000).motion
    assert reverse.x_m == pytest.approx(0)
    assert reverse.linear_velocity_mps == pytest.approx(-STEP_M / 0.1)
    assert reverse.continuous


def test_encoder_signs_are_applied_to_counts_not_motor_duty():
    """Normalize encoder polarity independently of actuator wiring."""
    estimator = StraightOdometry(replace(GEOMETRY, right_encoder_sign=-1))
    estimator.update([0, 0], 1_000_000_000)
    assert estimator.update([10, -10], 1_100_000_000).motion.x_m == pytest.approx(STEP_M)


@pytest.mark.parametrize('counts', [
    [], [1], [1, 2, 3], [True, 0], [1.5, 0],
    [INT64_MAX + 1, 0], [INT64_MIN - 1, 0],
])
def test_malformed_counts_drop_baseline_without_motion(counts):
    """Require two new valid samples after an invalid payload."""
    estimator = StraightOdometry(GEOMETRY)
    estimator.update([0, 0], 1_000_000_000)
    assert estimator.update(counts, 1_100_000_000).status == 'invalid_sample'
    assert estimator.update([100, 100], 1_200_000_000).status == 'baseline'
    motion = estimator.update([110, 110], 1_300_000_000).motion
    assert motion.x_m == pytest.approx(STEP_M)
    assert not motion.continuous


@pytest.mark.parametrize('stamp', [-1, True, 1.5, INT64_MAX + 1])
def test_invalid_timestamps_produce_no_motion(stamp):
    """Reject invalid timestamp values before doing arithmetic."""
    estimator = StraightOdometry(GEOMETRY)
    assert estimator.update([0, 0], stamp).status == 'invalid_sample'


@pytest.mark.parametrize(('next_ns', 'counts', 'reason'), [
    (1_000_000_000, [10, 10], 'duplicate_time'),
    (1_000_000_001, [10, 10], 'dt_too_small'),
    (2_000_000_000, [10, 10], 'gap'),
    (1_100_000_000, [1001, 1001], 'count_jump'),
    (1_100_000_000, [10, 9], 'unsupported_turn'),
])
def test_rejected_interval_rebases_without_inventing_its_displacement(
        next_ns, counts, reason):
    """Retain only observable segments and flag loss of continuity."""
    estimator = StraightOdometry(GEOMETRY)
    estimator.update([0, 0], 1_000_000_000)
    update = estimator.update(counts, next_ns)
    assert update.status == reason
    assert update.motion is None
    motion = estimator.update([count + 10 for count in counts], next_ns + 100_000_000).motion
    assert motion.x_m == pytest.approx(STEP_M)
    assert not motion.continuous


def test_tick_rate_boundary_and_one_full_wheel_revolution():
    """Accept the configured rate boundary and convert one full wheel turn."""
    estimator = StraightOdometry(GEOMETRY)
    estimator.update([0, 0], 1_000_000_000)
    motion = estimator.update([1000, 1000], 1_100_000_000).motion
    assert motion.x_m == pytest.approx(math.tau * 0.05)


def test_int64_wrap_is_rejected_without_guessing_counter_width():
    """Treat wrapping as an unknown discontinuity rather than forward motion."""
    estimator = StraightOdometry(GEOMETRY)
    estimator.update([INT64_MAX, INT64_MAX], 1_000_000_000)
    assert estimator.update([INT64_MIN, INT64_MIN], 1_100_000_000).status == 'count_jump'


def test_zero_clock_and_time_reversal_start_a_new_mock_trajectory():
    """Reset the origin on simulated-clock restart without a velocity spike."""
    estimator = StraightOdometry(GEOMETRY)
    assert estimator.update([100, 100], 0).status == 'clock_unstarted'
    assert estimator.update([100, 100], 1_000_000_000).status == 'baseline'
    assert estimator.update([110, 110], 1_100_000_000).motion.x_m == pytest.approx(STEP_M)
    assert estimator.update([0, 0], 500_000_000).status == 'clock_restarted'
    assert estimator.x_m == 0
    assert estimator.update([10, 10], 600_000_000).motion.x_m == pytest.approx(STEP_M)
    assert estimator.update([0, 0], 0).status == 'clock_unstarted'
    assert estimator.x_m == 0


def test_known_reset_is_explicit_and_small_unknown_reset_is_ambiguous():
    """Document the reset information missing from the current input."""
    estimator = StraightOdometry(GEOMETRY)
    estimator.update([100, 100], 1_000_000_000)
    # This could equally be a real reverse move. No fabricated reset detection.
    assert estimator.update([0, 0], 1_100_000_000).motion.linear_velocity_mps < 0
    estimator.reset()
    assert estimator.update([0, 0], 1_200_000_000).status == 'baseline'
    assert estimator.update([10, 10], 1_300_000_000).motion.x_m == pytest.approx(STEP_M)


@pytest.mark.parametrize('parameters', [
    {'wheel_radius_m': 0}, {'wheel_radius_m': float('nan')},
    {'wheel_radius_m': float('inf')}, {'wheel_radius_m': True},
    {'counts_per_wheel_rev': 0}, {'counts_per_wheel_rev': 1000.5},
    {'counts_per_wheel_rev': True}, {'left_encoder_sign': 0},
    {'right_encoder_sign': True}, {'min_dt_s': 0}, {'max_gap_s': 0},
    {'max_gap_s': 1e-7}, {'max_tick_rate_per_s': -1},
])
def test_bad_geometry_rejected(parameters):
    """Validate geometry, sign, resolution and timing configuration."""
    with pytest.raises(ValueError):
        replace(GEOMETRY, **parameters)


def test_covariance_layout_and_positive_variance_validation():
    """Check diagonal indexes and reject zero/invalid covariance entries."""
    covariance = diagonal_covariance([1, 2, 3, 4, 5, 6])
    assert len(covariance) == 36
    assert [covariance[index] for index in (0, 7, 14, 21, 28, 35)] == [1, 2, 3, 4, 5, 6]
    assert sum(value != 0 for value in covariance) == 6
    for variances in ([1] * 5, [0] * 6, [-1] * 6, [math.inf] * 6, [math.nan] * 6):
        with pytest.raises(ValueError):
            diagonal_covariance(variances)
