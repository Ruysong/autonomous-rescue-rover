"""Ultralytics adapter: return the strongest COCO person detection per frame."""

from __future__ import annotations


class PersonDetector:
    def __init__(self, model_path: str, confidence: float = 0.4) -> None:
        if not 0 < confidence <= 1:
            raise ValueError("confidence must be in (0, 1]")
        import torch
        torch.set_num_threads(4)
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        if self.model.names[0] != "person":
            raise ValueError("The model must use COCO class 0 for person")
        self.confidence = confidence

    def observe(self, frame):
        """Return normalized person boxes and the annotated image."""
        result = self.model.predict(
            frame, classes=[0], conf=self.confidence, verbose=False
        )[0]
        people = []
        if result.boxes is not None:
            for box, score in zip(result.boxes.xyxyn.tolist(), result.boxes.conf.tolist()):
                people.append({'box':box,'confidence':float(score),
                    'side':'left' if (box[0]+box[2])/2 < .4 else 'right' if (box[0]+box[2])/2 > .6 else 'ahead'})
        return people, result.plot()

    def detect(self, frame):
        people, annotated = self.observe(frame)
        return max((p['confidence'] for p in people),default=None), annotated
