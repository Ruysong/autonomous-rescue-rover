"""Small, hardware-independent mission decision for the person-search demo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Navigator(Protocol):
    def cancel_goal(self) -> bool: ...


class Rover(Protocol):
    def send_stop(self) -> bool: ...


@dataclass(frozen=True)
class PersonObservation:
    frame: int
    video_seconds: float
    confidence: float | None


class MissionController:
    """Confirm a person candidate, then latch the mission in stop mode.

    This class never issues a drive command. A future navigation controller must
    respect ``stop_requested`` before issuing any new drive command.
    """

    def __init__(self, navigator: Navigator, rover: Rover, confirm_frames: int = 3) -> None:
        if confirm_frames < 1:
            raise ValueError("confirm_frames must be at least 1")
        self.navigator = navigator
        self.rover = rover
        self.confirm_frames = confirm_frames
        self.consecutive_frames = 0
        self.stop_requested = False
        self.navigation_cancelled = False
        self.stop_sent = False
        self.discovery: PersonObservation | None = None

    def observe(self, observation: PersonObservation) -> bool:
        """Return True only for the frame that first confirms a candidate."""
        just_confirmed = False
        if not self.stop_requested:
            self.consecutive_frames = (
                self.consecutive_frames + 1 if observation.confidence is not None else 0
            )
            if self.consecutive_frames >= self.confirm_frames:
                self.stop_requested = True
                self.discovery = observation
                just_confirmed = True

        # Retry failed operations on later frames, but never resume the mission.
        if self.stop_requested:
            if not self.navigation_cancelled:
                self.navigation_cancelled = self.navigator.cancel_goal()
            if not self.stop_sent:
                self.stop_sent = self.rover.send_stop()
        return just_confirmed

    def report(self) -> dict[str, object]:
        discovery = self.discovery
        return {
            "person_candidate_found": discovery is not None,
            "discovery": None if discovery is None else {
                "frame": discovery.frame,
                "video_seconds": discovery.video_seconds,
                "confidence": discovery.confidence,
            },
            "navigation_cancelled": self.navigation_cancelled,
            "stop_request_accepted_by_adapter": self.stop_sent,
            "stop_requested": self.stop_requested,
        }
