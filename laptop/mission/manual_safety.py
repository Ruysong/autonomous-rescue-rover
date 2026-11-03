"""Manual drive authority for the live person-stop demonstration.

The operator is the only source of drive commands. Detection and faults can
only remove that authority; they never generate a drive command.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DriveDecision:
    throttle: float
    reason: str


class ManualSafetyController:
    def __init__(self, drive_throttle: float = 0.1, max_frame_age: float = 0.75) -> None:
        if not 0 < drive_throttle <= 1:
            raise ValueError("drive_throttle must be in (0, 1]")
        if max_frame_age <= 0:
            raise ValueError("max_frame_age must be positive")
        self.drive_throttle = drive_throttle
        self.max_frame_age = max_frame_age
        self.armed = False
        self.held_direction = 0
        self.lockout_reason: str | None = None
        self.last_frame_at: float | None = None
        self.last_person_confidence: float | None = None
        self.detected_person_confidence: float | None = None

    def observe(self, frame_at: float, person_confidence: float | None) -> None:
        self.last_frame_at = frame_at
        self.last_person_confidence = person_confidence
        if person_confidence is not None:
            self.detected_person_confidence = person_confidence
            self.lockout_reason = "PERSON DETECTED"
            self.armed = False
            self.held_direction = 0

    def set_direction(self, direction: int) -> None:
        if direction not in (-1, 0, 1):
            raise ValueError("direction must be -1, 0, or 1")
        # Reversing requires releasing the first direction before pressing the other.
        if direction and self.held_direction and direction != self.held_direction:
            self.held_direction = 0
            return
        self.held_direction = direction

    def release(self, direction: int) -> None:
        if self.held_direction == direction:
            self.held_direction = 0

    def manual_stop(self) -> None:
        self.fault_stop("MANUAL STOP")

    def fault_stop(self, reason: str) -> None:
        self.lockout_reason = reason
        self.armed = False
        self.held_direction = 0

    def reset(self, now: float) -> bool:
        if (self.held_direction or self.last_person_confidence is not None
                or not self._fresh(now)):
            return False
        self.lockout_reason = None
        self.detected_person_confidence = None
        self.armed = False
        return True

    def enable(self, now: float) -> bool:
        if (self.lockout_reason is not None or self.held_direction
                or self.last_person_confidence is not None or not self._fresh(now)):
            return False
        self.armed = True
        return True

    def _fresh(self, now: float) -> bool:
        return (self.last_frame_at is not None and 0 <= now - self.last_frame_at
                <= self.max_frame_age)

    def decide(self, now: float, pi_ready: bool = True) -> DriveDecision:
        if self.lockout_reason is not None:
            return DriveDecision(0.0, self.lockout_reason)
        if not self._fresh(now):
            return DriveDecision(0.0, "VIDEO NOT FRESH")
        if not pi_ready:
            return DriveDecision(0.0, "PI NOT READY")
        if not self.armed:
            return DriveDecision(0.0, "CONTROL PAUSED")
        if self.held_direction == 0:
            return DriveDecision(0.0, "KEY RELEASED")
        direction = "FORWARD" if self.held_direction > 0 else "REVERSE"
        return DriveDecision(self.held_direction * self.drive_throttle, direction)
