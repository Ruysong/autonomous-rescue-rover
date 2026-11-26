from types import SimpleNamespace
import time

import numpy as np
import pytest

from laptop.mission.person_alert import PersonAlerts
from laptop.mission.protection_link import ProtectionLink
from laptop.mission.sensor_policy import SensorPolicy, VisionRecovery
from laptop.mission.visual_guard import VisualGuard
from laptop.perception.person_detector import posture


def frame(at, luma):
    return SimpleNamespace(at=at, luma=luma)


class RecordedThread:
    jobs = []

    def __init__(self, target, args, **kwargs):
        self.jobs.append(lambda: target(*args))

    def start(self):
        pass


@pytest.fixture
def sensor(monkeypatch):
    RecordedThread.jobs = []
    monkeypatch.setattr("laptop.mission.sensor_policy.Thread", RecordedThread)
    events = []
    policy = SensorPolicy(lambda _: "lidar_only", event=lambda kind, data: events.append((kind, data)))
    return policy, events


def test_low_light_requires_real_frames_and_ai_then_waits_for_inference(sensor):
    policy, events = sensor
    assert policy.update(1, frame(1, 100), True, True, True).mode == "VISION_SEARCH"
    assert policy.update(2, frame(2, 10), False, True, True).pause
    assert policy.update(2.2, frame(2, 10), False, True, True).mode == "CHECK_LIGHTING"
    assert policy.update(2.9, frame(2.9, 15), False, True, True).mode == "AI_REVIEW"
    assert len(RecordedThread.jobs) == 1
    RecordedThread.jobs.pop()()
    slow = policy.update(3.0, frame(3.0, 15), False, True, True)
    assert (slow.mode, slow.speed_limit) == ("LIDAR_ONLY", .05)
    assert policy.update(3.1, frame(3.1, 95), False, True, True).mode == "LIDAR_ONLY"
    assert policy.update(4.0, frame(4.0, 95), False, True, True).mode == "LIDAR_ONLY"
    assert policy.update(4.2, frame(4.2, 95), False, True, True).pause
    assert policy.update(4.25, frame(4.2, 95), True, True, True).mode == "VISION_SEARCH"
    assert [kind for kind, _ in events].count("sensor_ai_request") == 1


@pytest.mark.parametrize("replacement", ["stream_loss", "lidar_loss", "operator", "expired"])
def test_stale_or_disarmed_switch_decision_cannot_authorize_motion(sensor, replacement):
    policy, _ = sensor
    policy.update(1, frame(1, 5), False, True, True)
    policy.update(1.9, frame(1.9, 5), False, True, True)
    RecordedThread.jobs.pop()()
    args = {
        "stream_loss": (3.0, frame(1.9, 5), False, True, True),
        "lidar_loss": (2.0, frame(2.0, 5), False, False, True),
        "operator": (2.0, frame(2.0, 5), False, True, False),
        "expired": (25, frame(25, 5), False, True, True),
    }[replacement]
    assert policy.update(*args).speed_limit == 0
    assert not policy.allowed


def test_video_jitter_pauses_without_clearing_existing_latch():
    elapsed = [10.0]
    guard = VisualGuard(clock=lambda: elapsed[0])
    assert guard.update([], None, latch_stale=False) == "observing"
    assert not guard.latched
    assert guard.update([], None, latch_stale=True) == "hold"
    assert guard.update([], None, latch_stale=False) == "hold"
    assert not guard.resume()
    guard.update([], 10.0)
    elapsed[0] = 10.5
    guard.update([], 10.5)
    elapsed[0] = 11.01
    guard.update([], 11.0)
    assert guard.resume()


def test_camera_recovery_waits_for_stable_fresh_results():
    recovery = VisionRecovery()
    assert not recovery.update(10.0)
    assert not recovery.update(10.0)  # the last successful frame does not count twice
    assert not recovery.update(10.3)
    assert recovery.update(10.75)
    assert not recovery.update(None)
    assert not recovery.update(12.0)
    assert not recovery.update(12.80)  # two isolated good frames are insufficient
    assert not recovery.update(None)
    assert not recovery.update(13.0)
    assert not recovery.update(13.25)
    assert recovery.update(13.80)


def test_person_alert_uses_new_frames_and_one_track_id(tmp_path):
    logs = []
    alerts = PersonAlerts(lambda kind, data: logs.append(kind), tmp_path)
    person = {"track_id": 9, "posture": "lying_candidate", "side": "ahead", "confidence": .8}
    observation = SimpleNamespace(sequence=1, frame_at=1, people=[person],
                                  annotated=np.zeros((50, 50, 3), dtype=np.uint8))
    for _ in range(6):
        alerts.update(observation, [1, 2, 0])
    assert alerts.latest is None
    for i in range(2, 6):
        observation.sequence = i
        observation.frame_at = 1 + .1 * i
        alerts.update(observation, [1, 2, 0])
    assert alerts.count == 1
    assert alerts.latest["posture"] == "lying_candidate"
    assert alerts.latest["rover_observation_pose"] == [1, 2, 0]
    assert (tmp_path / "person_001.jpg").is_file()
    assert logs == ["person_found"]


def test_missing_keypoints_do_not_claim_lying():
    xy = np.zeros((17, 2))
    xy[[11, 12], 0] = 40
    conf = np.ones(17)
    assert posture(xy, conf) == "lying_candidate"
    conf[12] = .1
    assert posture(xy, conf) == "unknown"


def test_protection_sender_expires_when_main_loop_stalls():
    sent = []
    link = ProtectionLink(lambda message: sent.append(message) or True, interval=.01, lease=.05)
    link.start()
    try:
        link.publish(False, .05, immediate=True)
        time.sleep(.09)
        count = len(sent)
        time.sleep(.08)
        assert count >= 1 and len(sent) == count
        link.publish(True, 0, immediate=True)
        assert sent[-1]["hold"] and sent[-1]["speed_limit"] == 0
    finally:
        link.close()
