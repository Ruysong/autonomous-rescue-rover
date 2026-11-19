"""Live view in the Foxglove app (WebSocket, ws://localhost:8765). View only: driving and
E-stop stay on the gamepad / the OpenCV control window.

Topics (frames: map -> base_link):
  /map              Grid        SLAM occupancy, value 0 free .. 100 occupied, 255 unknown
  /tf               FrameTransform  map -> base_link (SLAM pose)
  /scan             LaserScan   latest LiDAR scan in base_link (robot frame, 1 deg bins)
  /markers          SceneUpdate robot body, trajectory, planned path, frontiers (id), goal
  /camera           CompressedImage  annotated camera frame (YOLO boxes, protection ROI)
  /rover/status     JSON        state, mode, follow, pose, duty, steer, Pico flags
  /rover/sensors    JSON        lidar / imu ok, ultrasonic cm (FL FR RL RR), encoders L R
  /mission          JSON        vision level and reason, AI state, explorer note
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np

import foxglove
from foxglove.messages import (
    Color, CompressedImage, CubePrimitive, FrameTransform, Grid, LaserScan, LinePrimitive,
    LinePrimitiveLineType, PackedElementField, PackedElementFieldNumericType, Point3, Pose,
    Quaternion, SceneEntity, SceneUpdate, SpherePrimitive, TextPrimitive, Timestamp, Vector2,
    Vector3,
)

from laptop.navigation.dashboard import PICO_FLAGS

BODY = (0.60, 0.60, 0.30)  # m, approximate box for the robot marker


def _stamp(t: float | None = None) -> Timestamp:
    t = time.time() if t is None else t
    return Timestamp(sec=int(t), nsec=int((t % 1) * 1e9))


def _quat(yaw: float) -> Quaternion:
    return Quaternion(x=0.0, y=0.0, z=math.sin(yaw / 2), w=math.cos(yaw / 2))


def _pose(x: float, y: float, z: float = 0.0, yaw: float = 0.0) -> Pose:
    return Pose(position=Vector3(x=x, y=y, z=z), orientation=_quat(yaw))


def _color(r, g, b, a=1.0) -> Color:
    return Color(r=r, g=g, b=b, a=a)


class FoxgloveView:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.server = foxglove.start_server(name="RoverAid", host=host, port=port)
        self.url = f"ws://{host}:{port}"
        self._last = {}

    def due(self, key: str, period_s: float) -> bool:
        now = time.monotonic()
        if now - self._last.get(key, 0.0) < period_s:
            return False
        self._last[key] = now
        return True

    def close(self) -> None:
        self.server.stop()

    # ---- map and robot
    def publish_map(self, slam) -> None:
        bounds = slam.known_bounds(margin=10)
        if bounds is None:
            return
        iy0, iy1, ix0, ix1 = bounds
        lo = slam.logodds[iy0:iy1, ix0:ix1]
        value = np.where(np.abs(lo) <= 0.05, 255,
                         np.clip(100.0 / (1.0 + np.exp(-lo)), 0, 100)).astype(np.uint8)
        rows, cols = value.shape
        res = slam.config.resolution
        x0, y0 = slam.origin + ix0 * res, slam.origin + iy0 * res
        foxglove.log("/map", Grid(
            timestamp=_stamp(), frame_id="map", pose=_pose(x0, y0),
            column_count=cols, cell_size=Vector2(x=res, y=res),
            row_stride=cols, cell_stride=1,
            fields=[PackedElementField(name="value", offset=0,
                                       type=PackedElementFieldNumericType.Uint8)],
            data=value.tobytes()))

    def publish_pose(self, slam, explorer) -> None:
        x, y, yaw = slam.pose
        now = _stamp()
        foxglove.log("/tf", FrameTransform(
            timestamp=now, parent_frame_id="map", child_frame_id="base_link",
            translation=Vector3(x=x, y=y, z=0.0), rotation=_quat(yaw)))
        entities = [SceneEntity(
            timestamp=now, frame_id="base_link", id="robot",
            cubes=[CubePrimitive(pose=_pose(0.0, 0.0, BODY[2] / 2),
                                 size=Vector3(x=BODY[0], y=BODY[1], z=BODY[2]),
                                 color=_color(0.2, 0.5, 0.9, 0.6))],
            arrows=[])]
        traj = slam.trajectory[-2000:]
        if len(traj) > 1:
            entities.append(self._line("trajectory", now, [(p[0], p[1]) for p in traj],
                                       _color(0.6, 0.6, 0.6, 0.8), 0.02))
        st = explorer.status
        if len(st.path) > 1:
            entities.append(self._line("path", now, st.path, _color(0.1, 0.8, 0.3), 0.05))
        spheres, texts = [], []
        for f in st.frontiers:
            spheres.append(SpherePrimitive(pose=_pose(f.x, f.y, 0.05), size=Vector3(x=0.15, y=0.15, z=0.15),
                                           color=_color(1.0, 0.6, 0.0, 0.9)))
            texts.append(TextPrimitive(pose=_pose(f.x, f.y, 0.35), billboard=True, font_size=14.0,
                                       scale_invariant=True, color=_color(1, 1, 1), text=str(f.id)))
        if st.goal is not None:
            spheres.append(SpherePrimitive(pose=_pose(st.goal.x, st.goal.y, 0.1),
                                           size=Vector3(x=0.35, y=0.35, z=0.35),
                                           color=_color(0.9, 0.1, 0.1, 0.9)))
            texts.append(TextPrimitive(pose=_pose(st.goal.x, st.goal.y, 0.6), billboard=True,
                                       font_size=16.0, scale_invariant=True,
                                       color=_color(1, 0.3, 0.3), text=f"goal {st.goal.id}"))
        entities.append(SceneEntity(timestamp=now, frame_id="map", id="frontiers",
                                    spheres=spheres, texts=texts))
        foxglove.log("/markers", SceneUpdate(entities=entities))

    @staticmethod
    def _line(name, now, points, color, width) -> SceneEntity:
        return SceneEntity(timestamp=now, frame_id="map", id=name, lines=[LinePrimitive(
            type=LinePrimitiveLineType.LineStrip, pose=_pose(0.0, 0.0), thickness=width,
            scale_invariant=False, points=[Point3(x=float(a), y=float(b), z=0.02) for a, b in points],
            color=color)])

    def publish_scan(self, message: dict) -> None:
        ranges = [r / 1000.0 if r > 0 else float("nan") for r in message.get("ranges_mm", [])]
        if not ranges:
            return
        step = math.radians(float(message.get("angle_increment_deg", 1.0)))
        foxglove.log("/scan", LaserScan(
            timestamp=_stamp(), frame_id="base_link", pose=_pose(0.0, 0.0, 0.0),
            start_angle=0.0, end_angle=step * (len(ranges) - 1), ranges=ranges, intensities=[]))

    # ---- camera and status
    def publish_camera(self, frame, quality: int = 70) -> None:
        if frame is None:
            return
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            foxglove.log("/camera", CompressedImage(timestamp=_stamp(), frame_id="camera",
                                                    data=jpg.tobytes(), format="jpeg"))

    def publish_status(self, telemetry: dict, guard=None, vision: bool = False, selector=None,
                       explorer=None) -> None:
        t = telemetry or {}
        flags = int((t.get("pico") or {}).get("flags", 0) or 0)
        pose = t.get("map_pose") or t.get("odom_pose") or [0.0, 0.0, 0.0]
        foxglove.log("/rover/status", {
            "state": t.get("safety_state", "not connected"),
            "mode": t.get("control_mode", "-"),
            "follow": (t.get("follow") or {}).get("state", "-"),
            "estop": bool(t.get("estop", False)),
            "x_m": pose[0], "y_m": pose[1], "yaw_deg": math.degrees(pose[2]),
            "odom_m": t.get("odom_distance", 0.0),
            "duty_l": (t.get("duty") or [0, 0])[0], "duty_r": (t.get("duty") or [0, 0])[1],
            "steer_deg": t.get("steer_deg", []),
            "steer_mode": t.get("steer_mode", "-"),
            "pico_ok": bool((t.get("pico") or {}).get("ok", False)),
            "pico_flags": flags,
            "pico_flag_text": ", ".join(n for b, n in PICO_FLAGS if flags & b) or "normal",
        })
        sonar = (t.get("ultrasonic_cm") or [-1, -1, -1, -1]) + [-1] * 4
        enc = (t.get("encoders") or [0, 0]) + [0, 0]
        sensors = t.get("sensors") or {}
        foxglove.log("/rover/sensors", {
            "lidar_ok": bool(sensors.get("lidar")), "imu_ok": bool(sensors.get("imu")),
            "ultrasonic_cm": {k: (v if v >= 0 else None) for k, v in zip(("FL", "FR", "RL", "RR"), sonar)},
            "encoder_l": enc[0], "encoder_r": enc[1],
        })
        foxglove.log("/mission", {
            "vision": (guard.level if vision else "off") if guard is not None else "off",
            "vision_reason": guard.reason if (guard is not None and vision) else "",
            "ai": selector.status if selector else "rule baseline (LLM off)",
            "explorer": str(explorer.status.note) if explorer is not None else "",
            "exploring": bool(explorer.status.active) if explorer is not None else False,
        })
