"""Choose the mission sensor mode. Independent robot protection remains authoritative."""
from dataclasses import dataclass
from queue import SimpleQueue
from threading import Thread
import time


@dataclass(frozen=True)
class SensorState:
    mode: str
    reason: str
    pause: bool
    speed_limit: float


class VisionRecovery:
    """Require multiple fresh inference samples before releasing a vision pause."""

    def __init__(self, stable_seconds=0.75):
        self.stable_seconds = stable_seconds
        self.first = self.last = None
        self.frames = 0

    def update(self, frame_at):
        if frame_at is None:
            self.first = self.last = None
            self.frames = 0
            return False
        if frame_at != self.last:
            if self.first is None:
                self.first = frame_at
            self.last = frame_at
            self.frames += 1
        return self.frames >= 3 and self.last - self.first >= self.stable_seconds


class SensorPolicy:
    def __init__(self, decide, *, event, dark_luma=24, bright_luma=42,
                 dark_seconds=0.8, bright_seconds=1.0, lidar_speed=0.05):
        if not 0 <= dark_luma < bright_luma <= 255 or min(dark_seconds, bright_seconds) <= 0:
            raise ValueError("Invalid lighting thresholds")
        if not 0 < lidar_speed <= 0.1:
            raise ValueError("Invalid LiDAR mode speed limit")
        self.decide, self.event = decide, event
        self.dark_luma, self.bright_luma = dark_luma, bright_luma
        self.dark_seconds, self.bright_seconds = dark_seconds, bright_seconds
        self.lidar_speed = lidar_speed
        self.dark_since = self.bright_since = None
        self.light_frame = None
        self.previous = None
        self.allowed = False
        self.pending = False
        self.episode = 0
        self.retry_at = 0.0
        self.error = ""
        self.results = SimpleQueue()

    def update(self, now, camera, vision_ready, lidar_ready, enabled):
        fresh = camera is not None and 0 <= now - camera.at <= 1.0
        if not fresh or not enabled:
            self.reset_lighting()
            if fresh and vision_ready:
                return self.state("VISION_SEARCH", "Camera and LiDAR monitoring", False)
            return self.state("WAIT_CAMERA", "Waiting for usable camera", True)
        if camera.at != self.light_frame:
            if self.light_frame is not None and camera.at - self.light_frame > 1.0:
                self.reset_lighting()
            self.light_frame = camera.at
            if camera.luma <= self.dark_luma:
                self.dark_since = camera.at if self.dark_since is None else self.dark_since
                self.bright_since = None
            elif camera.luma >= self.bright_luma:
                self.bright_since = camera.at if self.bright_since is None else self.bright_since
                if self.dark_since is not None and not self.allowed:
                    self.episode += 1
                self.dark_since = None
            else:
                self.dark_since = self.bright_since = None
        if self.allowed and self.bright_since is not None and camera.at - self.bright_since >= self.bright_seconds:
            if vision_ready:
                self.reset_lighting()
                return self.state("VISION_SEARCH", "Lighting and person detection recovered", False)
            return self.state("WAIT_CAMERA", "Lighting restored; waiting for person detector", True)
        if self.allowed:
            if not lidar_ready:
                self.reset_lighting()
                return self.state("WAIT_LIDAR", "LiDAR or robot status unavailable", True)
            return self.state("LIDAR_ONLY", "Qwen selected LiDAR; visual confirmation unavailable", False,
                              self.lidar_speed)

        sustained_dark = self.dark_since is not None and camera.at - self.dark_since >= self.dark_seconds
        while not self.results.empty():
            token, started, answer, error = self.results.get_nowait()
            self.pending = False
            if token != self.episode or not sustained_dark or not lidar_ready or now - started > 20:
                continue
            if answer == "lidar_only" and error is None:
                self.allowed = True
                return self.state("LIDAR_ONLY", "Qwen selected LiDAR; visual confirmation unavailable",
                                  False, self.lidar_speed)
            self.retry_at = now + 3
            self.error = error or "Qwen requested hold"
        if self.dark_since is not None:
            if not sustained_dark:
                return self.state("CHECK_LIGHTING", "Low light; checking persistence", True)
            if not lidar_ready:
                return self.state("WAIT_LIDAR", "Low light; waiting for fresh LiDAR", True)
            if not self.decide:
                return self.state("WAIT_CAMERA", "Low light; local AI is disabled", True)
            if not self.pending and now >= self.retry_at:
                evidence = {"scene": "sustained_low_light", "luma_0_to_255": round(camera.luma, 1),
                            "dark_seconds": round(camera.at - self.dark_since, 2),
                            "lidar_fresh": True, "intent": "continue mapping and exploration",
                            "person_confirmation": "unavailable in darkness"}
                self.pending = True
                self.error = ""
                self.event("sensor_ai_request", evidence)
                Thread(target=self.work, args=(self.episode, now, evidence), daemon=True).start()
            return self.state("AI_REVIEW", self.error or "Qwen is checking a LiDAR-only transition", True)
        if not vision_ready:
            return self.state("WAIT_CAMERA", "Waiting for fresh person detection", True)
        return self.state("VISION_SEARCH", "LiDAR navigation + camera person search", False)

    def reset_lighting(self):
        if self.dark_since is not None or self.allowed:
            self.episode += 1
        self.allowed = False
        self.dark_since = self.bright_since = self.light_frame = None

    def work(self, token, started, evidence):
        try:
            mode = self.decide(evidence)
            self.results.put((token, started, mode, None))
        except Exception as error:
            self.results.put((token, started, None, str(error)[:160]))

    def state(self, mode, reason, pause, speed=0.5):
        state = SensorState(mode, reason, pause, 0.0 if pause else speed)
        if state != self.previous:
            self.event("sensor_mode", {"mode": mode, "reason": reason, "paused": pause,
                                       "speed_limit_mps": state.speed_limit})
            self.previous = state
        return state
