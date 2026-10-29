"""Mission decision tests independent of YOLO and robot hardware."""

import socket
import time
import unittest

from laptop.communication.framing import recv_framed
from laptop.communication.manager import CommunicationManager
from laptop.communication.messages import decode_message
from laptop.mission.controller import MissionController, PersonObservation


class RecordingNavigator:
    def __init__(self):
        self.cancel_calls = 0

    def cancel_goal(self):
        self.cancel_calls += 1
        return True


class RecordingRover:
    def __init__(self, succeed=True):
        self.stop_calls = 0
        self.succeed = succeed

    def send_stop(self):
        self.stop_calls += 1
        return self.succeed


class MissionTests(unittest.TestCase):
    def test_confirms_after_consecutive_frames_and_latches(self):
        navigator = RecordingNavigator()
        rover = RecordingRover()
        mission = MissionController(navigator, rover, confirm_frames=3)
        observations = [None, 0.8, 0.8, None, 0.8, 0.9, 0.8, None]
        confirmed = [mission.observe(PersonObservation(i, i / 10, confidence))
                     for i, confidence in enumerate(observations)]
        self.assertEqual(confirmed, [False, False, False, False, False, False, True, False])
        self.assertEqual(navigator.cancel_calls, 1)
        self.assertEqual(rover.stop_calls, 1)
        self.assertTrue(mission.stop_requested)
        self.assertEqual(mission.report()["discovery"]["frame"], 6)

    def test_retries_stop_after_connection_failure(self):
        rover = RecordingRover(succeed=False)
        mission = MissionController(RecordingNavigator(), rover, confirm_frames=1)
        mission.observe(PersonObservation(1, 0.0, 0.9))
        self.assertFalse(mission.stop_sent)
        rover.succeed = True
        mission.observe(PersonObservation(2, 0.1, None))
        self.assertTrue(mission.stop_sent)
        self.assertEqual(rover.stop_calls, 2)

    def test_no_person_sends_no_stop(self):
        rover = RecordingRover()
        mission = MissionController(RecordingNavigator(), rover)
        mission.observe(PersonObservation(1, 0.0, None))
        self.assertFalse(mission.report()["person_candidate_found"])
        self.assertEqual(rover.stop_calls, 0)

    def test_confirmed_person_sends_existing_framed_stop_message(self):
        def free_port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return sock.getsockname()[1]

        command_port, telemetry_port = free_port(), free_port()
        while telemetry_port == command_port:
            telemetry_port = free_port()
        manager = CommunicationManager("127.0.0.1", command_port, telemetry_port, lambda _: None)
        manager.start()
        self.addCleanup(manager.stop)
        with socket.create_connection(("127.0.0.1", command_port), timeout=2) as pi_socket:
            pi_socket.settimeout(2)
            deadline = time.monotonic() + 2
            while manager._command_socket is None and time.monotonic() < deadline:
                time.sleep(0.01)
            mission = MissionController(RecordingNavigator(), manager, confirm_frames=1)
            mission.observe(PersonObservation(1, 0.0, 0.9))
            self.assertTrue(mission.stop_sent)
            self.assertEqual(decode_message(recv_framed(pi_socket))["type"], "stop")


if __name__ == "__main__":
    unittest.main()
