import unittest

from laptop.communication.messages import decode_message, encode_message
from laptop.teleop.controller import (
    AXIS_LY, AXIS_RT, AXIS_RX, BTN_A, BTN_B, BTN_BACK, BTN_LB, BTN_START,
    PadState, TeleopController, shape,
)

REST = [0.0, 0.0, 0.0, 0.0, -1.0, -1.0]


def pad(buttons=(), **axes):
    values = list(REST)
    names = {"ly": AXIS_LY, "rx": AXIS_RX, "rt": AXIS_RT}
    for name, value in axes.items():
        values[names[name]] = value
    return PadState(connected=True, axes=tuple(values), buttons=frozenset(buttons))


def types(messages):
    for message in messages:
        decode_message(encode_message(message))  # every message must be valid on the wire
    return [m["type"] for m in messages]


class ShapeTests(unittest.TestCase):
    def test_deadzone_and_limits(self):
        self.assertEqual(shape(0.1, 0.12, 0.5), 0.0)
        self.assertAlmostEqual(shape(1.0, 0.12, 0.5), 1.0)
        self.assertAlmostEqual(shape(-1.0, 0.12, 0.5), -1.0)
        self.assertLess(shape(0.5, 0.12, 0.5), 0.5)


class TeleopTests(unittest.TestCase):
    def test_no_deadman_no_motion(self):
        c = TeleopController()
        self.assertEqual(c.step(pad(ly=-1.0)), [])

    def test_deadman_drives_forward_at_base_speed(self):
        c = TeleopController()
        out = c.step(pad([BTN_LB], ly=-1.0))
        self.assertEqual(types(out), ["motion"])
        self.assertAlmostEqual(out[0]["v"], 0.3 * c.limits.max_speed)
        self.assertEqual(out[0]["kappa"], 0.0)

    def test_boost_and_steer_right(self):
        c = TeleopController()
        out = c.step(pad([BTN_LB], ly=-1.0, rx=1.0, rt=1.0))
        self.assertAlmostEqual(out[0]["v"], c.limits.max_speed)
        self.assertAlmostEqual(out[0]["kappa"], -c.limits.max_curvature)

    def test_release_deadman_sends_one_stop(self):
        c = TeleopController()
        c.step(pad([BTN_LB], ly=-1.0))
        self.assertEqual(types(c.step(pad(ly=-1.0))), ["stop"])
        self.assertEqual(c.step(pad(ly=-1.0)), [])

    def test_estop_latched_until_start(self):
        c = TeleopController()
        self.assertEqual(types(c.step(pad([BTN_BACK, BTN_LB], ly=-1.0))), ["estop"])
        self.assertEqual(c.step(pad([BTN_LB], ly=-1.0)), [])
        self.assertEqual(types(c.step(pad([BTN_START, BTN_LB], ly=-1.0))), ["resume", "motion"])

    def test_start_toggles_control_mode(self):
        c = TeleopController()
        out = c.step(pad([BTN_START]))
        self.assertEqual(types(out), ["set_mode"])
        self.assertEqual(out[0]["mode"], "autonomous")
        self.assertEqual(c.step(pad([BTN_LB], ly=-1.0)), [])  # no manual drive in autonomous
        c.step(pad())
        self.assertEqual(c.step(pad([BTN_START]))[0]["mode"], "manual")

    def test_spin_refused_until_robot_allows(self):
        c = TeleopController()
        c.step(pad([BTN_B]))
        self.assertEqual(c.steer_mode, "normal")
        c.limits.update_from_telemetry({"limits": {"spin_enabled": True}})
        c.step(pad())
        c.step(pad([BTN_B]))
        self.assertEqual(c.steer_mode, "spin")
        out = c.step(pad([BTN_LB], rx=-1.0))
        self.assertEqual(out[0]["steer_mode"], "spin")
        self.assertGreater(out[0]["wz"], 0.0)  # stick left = CCW
        c.step(pad([BTN_A]))
        self.assertEqual(c.steer_mode, "normal")

    def test_pad_unplugged_stops_once(self):
        c = TeleopController()
        c.step(pad([BTN_LB], ly=-1.0))
        self.assertEqual(types(c.step(PadState(connected=False))), ["stop"])
        self.assertEqual(c.step(PadState(connected=False)), [])
        self.assertFalse(c.driving)


if __name__ == "__main__":
    unittest.main()
