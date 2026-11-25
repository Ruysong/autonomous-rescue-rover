"""Ultralytics adapter: return the strongest COCO person detection per frame."""

from __future__ import annotations

import math


def posture(points, confidence):
    """Classify torso orientation in image space when shoulders and hips are visible."""
    torso = (5, 6, 11, 12)
    if len(points) <= max(torso) or any(confidence[i] < .4 for i in torso):
        return "unknown"
    dx = (points[11][0] + points[12][0] - points[5][0] - points[6][0]) / 2
    dy = (points[11][1] + points[12][1] - points[5][1] - points[6][1]) / 2
    if math.hypot(dx, dy) < 8:
        return "unknown"
    angle = math.degrees(math.atan2(abs(dy), abs(dx)))
    return "lying_candidate" if angle <= 30 else "upright" if angle >= 60 else "unknown"


class PersonDetector:
    def __init__(self, model_path: str, confidence: float = 0.4, *, tracking: bool = False) -> None:
        if not 0 < confidence <= 1:
            raise ValueError("confidence must be in (0, 1]")
        import torch
        torch.set_num_threads(4)
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        if self.model.names[0] != "person":
            raise ValueError("The model must use COCO class 0 for person")
        self.confidence = confidence
        self.tracking = tracking

    def observe(self, frame):
        """Return normalized person boxes and the annotated image."""
        options = {"classes": [0], "conf": self.confidence, "verbose": False}
        if self.tracking:
            result = self.model.track(frame, persist=True, tracker="bytetrack.yaml", **options)[0]
        else:
            result = self.model.predict(frame, **options)[0]
        people = []
        if result.boxes is not None:
            tracks = result.boxes.id
            tracks = tracks.int().tolist() if tracks is not None else [None] * len(result.boxes)
            keypoints = result.keypoints
            poses = keypoints.xy.tolist() if keypoints is not None else []
            scores = keypoints.conf.tolist() if keypoints is not None and keypoints.conf is not None else []
            for i, (box, score) in enumerate(zip(result.boxes.xyxyn.tolist(), result.boxes.conf.tolist())):
                center = (box[0] + box[2]) / 2
                person = {'box': box, 'confidence': float(score), 'track_id': tracks[i],
                          'side': 'left' if center < .4 else 'right' if center > .6 else 'ahead',
                          'posture': posture(poses[i], scores[i]) if i < min(len(poses), len(scores)) else 'unknown'}
                people.append(person)
        return people, result.plot()

    def detect(self, frame):
        people, annotated = self.observe(frame)
        return max((p['confidence'] for p in people),default=None), annotated
