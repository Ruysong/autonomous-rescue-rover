"""Keep the most recent camera frame independent of inference and GUI work."""
from dataclasses import dataclass
from threading import Event, Thread
import time

import cv2

from .person_detector import PersonDetector


@dataclass(frozen=True)
class CameraFrame:
    sequence: int
    at: float
    luma: float
    image: object


@dataclass(frozen=True)
class DetectionFrame:
    sequence: int
    frame_at: float
    luma: float
    annotated: object
    people: list


class LivePersonStream:
    def __init__(self, source, model, confidence):
        self.source, self.model, self.confidence = source, model, confidence
        self.camera = self.observation = None
        self.capture_error = self.detector_error = ""
        self.closed = Event()
        self.workers = [Thread(target=self.capture, daemon=True, name="camera"),
                        Thread(target=self.infer, daemon=True, name="person-detector")]

    def start(self):
        for worker in self.workers:
            worker.start()

    def stop(self):
        self.closed.set()
        for worker in self.workers:
            worker.join(1.5)

    def capture(self):
        sequence = 0
        remote = isinstance(self.source, str) and "://" in self.source
        live = remote or isinstance(self.source, int)
        while not self.closed.is_set():
            try:
                cap = (cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1600, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1200])
                    if remote else cv2.VideoCapture(self.source))
            except cv2.error as error:
                self.capture_error = str(error)
                self.closed.wait(.5)
                continue
            fps = cap.get(cv2.CAP_PROP_FPS)
            try:
                while not self.closed.is_set():
                    ok, image = cap.read()
                    if not ok:
                        self.capture_error = "Camera read failed; retrying" if live else "Video ended"
                        break
                    at = time.monotonic()
                    preview = cv2.resize(image, (80, 45), interpolation=cv2.INTER_AREA)
                    luma = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY).mean()
                    if image.shape[1] > 960:
                        image = cv2.resize(image, (960, round(image.shape[0] * 960 / image.shape[1])))
                    sequence += 1
                    self.camera = CameraFrame(sequence, at, float(luma), image)
                    self.capture_error = ""
                    if not live:
                        self.closed.wait(max(0, 1 / (fps if fps > 0 else 20) - (time.monotonic() - at)))
            finally:
                cap.release()
            if not live:
                return
            self.closed.wait(.4)

    def infer(self):
        try:
            detector = PersonDetector(self.model, self.confidence, tracking=True)
            last_sequence = 0
            while not self.closed.is_set():
                frame = self.camera
                if frame is None or frame.sequence == last_sequence:
                    self.closed.wait(.02)
                    continue
                last_sequence = frame.sequence
                people, image = detector.observe(frame.image)
                self.observation = DetectionFrame(frame.sequence, frame.at, frame.luma, image, people)
        except Exception as error:
            self.detector_error = str(error)
