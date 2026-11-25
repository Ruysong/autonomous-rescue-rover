"""Confirm person observations from distinct inference results and retain the latest alert."""
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import cv2


class PersonAlerts:
    def __init__(self, event, directory=None, confirm_frames=3):
        self.event = event
        self.directory = Path(directory) if directory else None
        self.confirm_frames = confirm_frames
        self.last_sequence = None
        self.last_at = None
        self.streaks = {}
        self.reported = OrderedDict()
        self.latest = None
        self.count = 0

    def update(self, observation, pose):
        if observation.sequence == self.last_sequence:
            return
        self.last_sequence = observation.sequence
        if self.last_at is not None and observation.frame_at - self.last_at > 1.1:
            self.streaks.clear()
        self.last_at = observation.frame_at
        current = {}
        for person in observation.people:
            track = person.get("track_id")
            if track is None:
                continue
            current[track] = self.streaks.get(track, 0) + 1
            if current[track] < self.confirm_frames or track in self.reported:
                continue
            self.count += 1
            self.reported[track] = None
            if len(self.reported) > 512:
                self.reported.popitem(last=False)
            alert = {
                "event_id": self.count,
                "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "label": "PERSON FOUND",
                "track_id": track,
                "posture": person.get("posture", "unknown"),
                "confidence": round(person["confidence"], 3),
                "image_side": person["side"],
                "rover_observation_pose": list(pose),
                "image": "",
            }
            if self.directory is not None:
                try:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    ok, jpg = cv2.imencode(".jpg", observation.annotated)
                    if ok:
                        path = self.directory / f"person_{self.count:03d}.jpg"
                        path.write_bytes(jpg.tobytes())
                        alert["image"] = str(path)
                except OSError as error:
                    self.event("person_image_error", {"error": str(error)})
            self.latest = alert
            self.event("person_found", alert)
        self.streaks = current

    def clear_pending(self):
        self.streaks.clear()
