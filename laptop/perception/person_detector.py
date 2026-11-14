"""Ultralytics adapter: return the strongest COCO person detection per frame."""

from __future__ import annotations


class PersonDetector:
    def __init__(self, model_path: str, confidence: float = 0.4) -> None:
        if not 0 < confidence <= 1:
            raise ValueError("confidence must be in (0, 1]")
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        if self.model.names[0] != "person":
            raise ValueError("The model must use COCO class 0 for person")
        self.confidence = confidence

    def detect(self, frame):
        """Return (confidence or None, annotated frame)."""
        result = self.model.predict(
            frame, classes=[0], conf=self.confidence, verbose=False
        )[0]
        confidence = None
        if result.boxes is not None and len(result.boxes):
            confidence = max(float(value) for value in result.boxes.conf)
        return confidence, result.plot()
