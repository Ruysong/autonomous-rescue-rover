import math
import time
import unittest

import numpy as np

from laptop.communication.messages import (
    ProtocolError, decode_message, encode_message, make_path, make_pose_correction,
)
from laptop.navigation.explorer import Explorer
from laptop.navigation.planner import build_plan, nearest_frontier, path_to
from laptop.navigation.slam import ScanSlam, compose, inverse

# Same world as the robot MockLidar (robot/src/hardware/Lidar.cpp defaultMockWorld).
WORLD = [
    (-1.0, -1.5, 5.0, -1.5), (5.0, -1.5, 5.0, 5.5), (5.0, 5.5, 3.0, 5.5),
    (3.0, 5.5, 3.0, 2.5), (3.0, 2.5, -1.0, 2.5), (-1.0, 2.5, -1.0, -1.5),
    (1.6, 0.2, 2.2, 0.2), (2.2, 0.2, 2.2, 0.8), (2.2, 0.8, 1.6, 0.8), (1.6, 0.8, 1.6, 0.2),
]


def cast(pose):
    a = pose[2] + np.deg2rad(np.arange(360))
    dx, dy = np.cos(a), np.sin(a)
    best = np.full(360, np.inf)
    for x1, y1, x2, y2 in WORLD:
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        with np.errstate(divide="ignore", invalid="ignore"):
            qx, qy = x1 - pose[0], y1 - pose[1]
            t = (qx * ey - qy * ex) / den
            u = (qx * dy - qy * dx) / den
        ok = (np.abs(den) > 1e-9) & (t > 0) & (u >= 0) & (u <= 1)
        best = np.where(ok & (t < best), t, best)
    return [int(round(r * 1000)) if np.isfinite(r) and r <= 12 else 0 for r in best]


def drive(slam, steps, v=0.05, w=0.0, scale=1.05, yaw_scale=1.04, start=(0.0, 0.0, 0.0)):
    """Moves the true pose; odometry under-reports by `scale` / `yaw_scale`."""
    truth, odom = start, (0.0, 0.0, 0.0)
    for _ in range(steps):
        truth = compose(truth, (v, 0.0, w))
        odom = compose(odom, (v / scale, 0.0, w / yaw_scale))
        slam.process(odom, cast(truth))
    return truth, odom


class SlamTests(unittest.TestCase):
    def test_matching_corrects_odometry_drift(self):
        slam = ScanSlam()
        start = (0.0, -0.6, 0.0)      # clear of the box
        truth, odom = drive(slam, 60, v=0.05, w=0.004, start=start)
        truth = compose(inverse(start), truth)   # map frame = where the robot started
        odom_err = math.hypot(odom[0] - truth[0], odom[1] - truth[1])
        slam_err = math.hypot(slam.pose[0] - truth[0], slam.pose[1] - truth[1])
        self.assertGreater(odom_err, 0.12)
        self.assertLess(slam_err, 0.06)        # grid quantisation, does not grow
        self.assertLess(abs(slam.pose[2] - truth[2]), math.radians(0.5))
        self.assertGreater(slam.matches, 50)

    def test_correction_maps_odom_to_map(self):
        slam = ScanSlam()
        drive(slam, 20, v=0.05)
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(
            compose(slam.correction, slam.trajectory and (0, 0, 0)), slam.correction)))
        c = compose(inverse(slam.correction), slam.correction)
        self.assertAlmostEqual(c[0], 0.0)
        self.assertAlmostEqual(c[2], 0.0)

    def test_map_has_walls_and_free_space(self):
        slam = ScanSlam()
        drive(slam, 5, v=0.0)
        p = slam.probability()
        ix, iy = slam.to_cell(0.5, 0.0)
        self.assertLess(p[iy, ix], 0.4)       # free in front (planner free threshold)
        ix, iy = slam.to_cell(-1.0, 0.0)
        self.assertGreater(p[iy, ix], 0.6)    # wall behind


class PlannerTests(unittest.TestCase):
    def test_frontiers_and_path(self):
        slam = ScanSlam()
        drive(slam, 10, v=0.05)
        plan = build_plan(slam, slam.pose)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.frontiers)
        choice = nearest_frontier(plan.frontiers)
        goal = next(f for f in plan.frontiers if f.id == choice)
        points = path_to(plan, slam, (goal.x, goal.y))
        self.assertIsNotNone(points)
        self.assertLess(math.hypot(points[0][0] - slam.pose[0], points[0][1] - slam.pose[1]), 0.1)
        self.assertLess(math.hypot(points[-1][0] - goal.x, points[-1][1] - goal.y), 0.1)
        # Stays clear of the walls by the robot radius.
        for x, y in points:
            ix, iy = slam.to_cell(x, y)
            self.assertLess(slam.probability()[iy, ix], 0.65)
        decode_message(encode_message(make_path(points, 1, 0.1)))


class AsyncExplorerTests(unittest.TestCase):
    def test_waiting_ai_holds_then_validated_path(self):
        import threading
        from laptop.mission.ai_selector import AISelector
        release=threading.Event()
        def reason(snapshot):
            release.wait(2)
            return {'candidate_id':snapshot['candidates'][0]['id'],'reason':'Low travel cost'}
        selector=AISelector(reason)
        sent=[];ex=Explorer(ScanSlam(),lambda m:sent.append(m) or True,selector=selector)
        for _ in range(3):ex.on_scan({'odom_pose':[0,0,0],'ranges_mm':cast((0,0,0))})
        ex.on_telemetry({'control_mode':'autonomous','estop':False,'follow':{'state':'idle'}})
        ex.start();ex.tick()
        self.assertFalse(any(m['type']=='path' and m['points'] for m in sent))
        # Scan callbacks still run while the actual model worker is blocked.
        ex.on_scan({'odom_pose':[0,0,0],'ranges_mm':cast((0,0,0))})
        release.set()
        deadline=time.monotonic()+2
        while selector.results.empty() and time.monotonic()<deadline:time.sleep(.01)
        ex.tick()
        self.assertTrue(any(m['type']=='path' and m['points'] for m in sent))
        ex.pause();self.assertFalse(sent[-1]['points'])

class ExplorerTests(unittest.TestCase):
    def test_sends_path_only_in_autonomous_mode(self):
        sent = []
        slam = ScanSlam()
        ex = Explorer(slam, lambda m: sent.append(m) or True)
        for _ in range(3):
            ex.on_scan({"odom_pose": [0.0, 0.0, 0.0], "ranges_mm": cast((0.0, 0.0, 0.0))})
        self.assertTrue(any(m["type"] == "pose_correction" for m in sent))
        ex.start()
        ex.on_telemetry({"control_mode": "manual", "estop": False, "follow": {"state": "idle"}})
        ex.tick()
        self.assertFalse(any(m["type"] == "path" for m in sent))
        ex.on_telemetry({"control_mode": "autonomous", "estop": False,
                         "follow": {"state": "idle", "path_id": -1}})
        ex.tick()
        paths = [m for m in sent if m["type"] == "path"]
        self.assertEqual(len(paths), 1)
        self.assertGreater(len(paths[0]["points"]), 2)

    def test_bad_selector_choice_falls_back(self):
        sent = []
        slam = ScanSlam()
        ex = Explorer(slam, lambda m: sent.append(m) or True, selector=lambda f, ctx: 999)
        ex.on_scan({"odom_pose": [0.0, 0.0, 0.0], "ranges_mm": cast((0.0, 0.0, 0.0))})
        ex.start()
        ex.on_telemetry({"control_mode": "autonomous", "follow": {"state": "idle"}})
        ex.tick()
        self.assertTrue(any(m["type"] == "path" and m["points"] for m in sent))


class MessageTests(unittest.TestCase):
    def test_v12_messages(self):
        decode_message(encode_message(make_pose_correction(1.0, 2.0, 0.1)))
        decode_message(encode_message(make_path([], 3)))
        with self.assertRaises(ProtocolError):
            decode_message(b'{"_version":"1.1","type":"path","id":1,"points":[]}')
        scan = {"_version": "1.2", "type": "scan", "timestamp": time.time(),
                "odom_pose": [0, 0, 0], "ranges_mm": [0] * 360}
        decode_message(encode_message(scan))
        scan["ranges_mm"] = [0] * 10
        with self.assertRaises(ProtocolError):
            decode_message(encode_message(scan))


if __name__ == "__main__":
    unittest.main()
