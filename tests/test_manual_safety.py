"""Safety decisions that must hold before a real motor adapter is connected."""

import unittest

from laptop.mission.manual_safety import ManualSafetyController


class ManualSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = ManualSafetyController(drive_throttle=0.1, max_frame_age=0.75)
        self.control.observe(10.0, None)
        self.assertTrue(self.control.enable(10.0))

    def test_manual_hold_release_and_stop(self) -> None:
        self.control.set_direction(1)
        self.assertEqual(self.control.decide(10.1).throttle, 0.1)
        self.control.release(1)
        self.assertEqual(self.control.decide(10.2).reason, "KEY RELEASED")
        self.control.set_direction(-1)
        self.assertEqual(self.control.decide(10.3).throttle, -0.1)
        self.control.manual_stop()
        self.assertEqual(self.control.decide(10.3).reason, "MANUAL STOP")
        self.assertFalse(self.control.enable(10.3))

    def test_person_detection_latches_stop(self) -> None:
        self.control.set_direction(1)
        self.control.observe(10.1, 0.8)
        self.assertEqual(self.control.decide(10.1).reason, "PERSON DETECTED")
        self.control.observe(10.2, None)
        self.assertEqual(self.control.decide(10.2).reason, "PERSON DETECTED")
        self.assertTrue(self.control.reset(10.2))
        self.assertEqual(self.control.decide(10.2).reason, "CONTROL PAUSED")
        self.assertTrue(self.control.enable(10.2))

    def test_stale_video_and_pi_disconnection_remove_drive(self) -> None:
        self.control.set_direction(1)
        self.assertEqual(self.control.decide(10.8).reason, "VIDEO NOT FRESH")
        self.control.observe(10.9, None)
        self.assertEqual(self.control.decide(10.9, pi_ready=False).reason, "PI NOT READY")

    def test_reverse_requires_release(self) -> None:
        self.control.set_direction(1)
        self.control.set_direction(-1)
        self.assertEqual(self.control.decide(10.1).reason, "KEY RELEASED")
        self.control.set_direction(-1)
        self.assertEqual(self.control.decide(10.2).throttle, -0.1)

    def test_reset_requires_fresh_person_free_frame_and_released_key(self) -> None:
        self.control.manual_stop()
        self.control.observe(10.1, 0.9)
        self.assertFalse(self.control.reset(10.1))
        self.control.observe(10.2, None)
        self.control.set_direction(1)
        self.assertFalse(self.control.reset(10.2))
        self.control.release(1)
        self.assertFalse(self.control.reset(11.0))
        self.assertTrue(self.control.reset(10.2))


if __name__ == "__main__":
    unittest.main()
