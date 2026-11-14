"""Autonomous frontier exploration with live SLAM (laptop side).

    python -m laptop.navigation.run_explore [--autostart] [--gamepad] [--no-view]
    python -m laptop.navigation.run_explore --person-source http://<rover>:8080/stream [...]
    python -m laptop.navigation.run_explore --replay logs/explore_YYYYmmdd_HHMMSS.jsonl [--no-match]

Start this first, then on the Pi:  rover_controller --host <laptop address>
The robot follows paths only in autonomous mode: --autostart sends set_mode autonomous, or press
Start on the gamepad (--gamepad: LB-deadman manual driving, Back = E-stop, as in run_gamepad).

--person-source runs the YOLO person detector on the rover camera; the mission controller
(3 frames in a row) then cancels exploring and sends stop, and the map pose and image are saved.

Map window keys: space = start / pause exploring, m = manual mode, s = save map, q = quit.
Every scan and telemetry message is logged (jsonl) so the run can be replayed offline:
--replay rebuilds the map from a log; --no-match maps with odometry only (to see what SLAM adds).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from laptop.communication.manager import CommunicationManager
from laptop.communication.messages import make_estop, make_set_mode, make_stop
from laptop.mission.controller import MissionController, PersonObservation

from .explorer import Explorer, ExplorerConfig
from .slam import ScanSlam

LOG_DIR = Path("logs")
LIVE_MAP = LOG_DIR / "live_map.png"


def gui_available() -> bool:
    """opencv-python-headless has no windows; then the map goes to logs/live_map.png instead."""
    try:
        cv2.namedWindow("probe")
        cv2.destroyWindow("probe")
        return True
    except cv2.error:
        return False


class JsonlLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, direction: str, message: dict) -> None:
        line = json.dumps({"t": time.time(), "dir": direction, "msg": message}, separators=(",", ":"))
        with self._lock:
            self._file.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            self._file.close()


def render(slam: ScanSlam, explorer: Explorer | None = None, scale: int = 3) -> np.ndarray:
    bounds = slam.known_bounds(margin=20) or (slam.cells // 2 - 40, slam.cells // 2 + 40,
                                              slam.cells // 2 - 40, slam.cells // 2 + 40)
    iy0, iy1, ix0, ix1 = bounds
    p = slam.probability()[iy0:iy1, ix0:ix1]
    gray = (255 * (1.0 - p)).astype(np.uint8)          # white free, black occupied, grey unknown
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)

    def px(x, y):
        cx, cy = slam.to_cell(x, y)
        return (int((cx - ix0) * scale + scale // 2), int((cy - iy0) * scale + scale // 2))

    traj = slam.trajectory[-2000:]
    for a, b in zip(traj, traj[1:]):
        cv2.line(img, px(a[0], a[1]), px(b[0], b[1]), (160, 160, 160), 1)
    if explorer is not None:
        st = explorer.status
        for f in st.frontiers:
            cv2.circle(img, px(f.x, f.y), 4, (0, 0, 0), 1)
            cv2.putText(img, str(f.id), px(f.x + 0.1, f.y + 0.1), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                        (0, 0, 0), 1)
        for a, b in zip(st.path, st.path[1:]):
            cv2.line(img, px(*a), px(*b), (80, 80, 80), 2)
        if st.goal is not None:
            cv2.circle(img, px(st.goal.x, st.goal.y), 7, (0, 0, 0), 2)
    x, y, yaw = slam.pose
    cv2.circle(img, px(x, y), 6, (0, 0, 0), -1)
    cv2.line(img, px(x, y), px(x + 0.4 * math.cos(yaw), y + 0.4 * math.sin(yaw)), (0, 0, 0), 2)
    return cv2.flip(img, 0)  # +y up


def save_map(slam: ScanSlam, explorer: Explorer | None, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(stem.with_suffix(".png")), render(slam, explorer))
    np.savez_compressed(stem.with_suffix(".npz"), logodds=slam.logodds,
                        resolution=slam.config.resolution, origin=slam.origin,
                        trajectory=np.array(slam.trajectory))
    print(f"\nmap saved: {stem.with_suffix('.png')}")


def replay(path: Path, use_matching: bool, view: bool) -> int:
    view = view and gui_available()
    slam = ScanSlam(use_matching=use_matching)
    n = 0
    t0 = time.perf_counter()
    with open(path, encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            msg = entry.get("msg", {})
            if entry.get("dir") != "in" or msg.get("type") != "scan":
                continue
            slam.process(msg["odom_pose"], msg["ranges_mm"])
            n += 1
            if view and n % 5 == 0:
                cv2.imshow("replay", render(slam))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    dt = time.perf_counter() - t0
    print(f"scans {n}, inserted {slam.scans_inserted}, matched {slam.matches}, "
          f"rejected {slam.rejected_matches}, {1000 * dt / max(n, 1):.1f} ms/scan")
    print(f"final pose {slam.pose[0]:+.2f} {slam.pose[1]:+.2f} {math.degrees(slam.pose[2]):+.1f} deg, "
          f"correction {slam.correction[0]:+.2f} {slam.correction[1]:+.2f} "
          f"{math.degrees(slam.correction[2]):+.1f} deg")
    suffix = "_match" if use_matching else "_odom"
    save_map(slam, None, path.with_name(path.stem + "_replay" + suffix))
    if view:
        cv2.imshow("replay", render(slam))
        cv2.waitKey(0)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--command-port", type=int, default=9998)
    parser.add_argument("--telemetry-port", type=int, default=9997)
    parser.add_argument("--speed", type=float, default=0.10, help="m/s, path speed")
    parser.add_argument("--autostart", action="store_true",
                        help="switch the robot to autonomous and start exploring")
    parser.add_argument("--gamepad", action="store_true", help="gamepad manual / E-stop as well")
    parser.add_argument("--no-view", action="store_true")
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--no-match", action="store_true", help="replay: odometry only")
    parser.add_argument("--duration", type=float, default=0.0, help="s, quit after (tests)")
    parser.add_argument("--person-source", help="camera stream / video / index for person stop")
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--confidence", type=float, default=0.4)
    parser.add_argument("--confirm-frames", type=int, default=3)
    args = parser.parse_args(argv)

    if args.replay:
        return replay(args.replay, not args.no_match, not args.no_view)
    window = not args.no_view and gui_available()
    if not args.no_view and not window:
        print(f"OpenCV has no GUI here: the map is written to {LIVE_MAP} every second "
              "(open it in VS Code); keys are not available, use the gamepad or Ctrl+C.")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    log = JsonlLog(LOG_DIR / f"explore_{stamp}.jsonl")
    slam = ScanSlam()
    comm: CommunicationManager | None = None

    def send(message: dict) -> bool:
        if comm is None or not comm.robot_connected:
            return False
        log.write("out", message)
        return comm.send(message)

    explorer = Explorer(slam, send, ExplorerConfig(speed=args.speed))

    last_mode = {"value": None}
    mission_state = {"stopped": False}

    def on_telemetry(message: dict[str, Any]) -> None:
        log.write("in", message)
        explorer.on_telemetry(message)
        # Switching the robot to autonomous (gamepad Start, --autostart, space) starts exploring.
        mode = message.get("control_mode")
        if (mode == "autonomous" and last_mode["value"] != "autonomous"
                and not explorer.status.active and not mission_state["stopped"]):
            explorer.start()
        last_mode["value"] = mode
        if pad_controller is not None:
            pad_controller.limits.update_from_telemetry(message)
            # Keep Start (manual <-> autonomous toggle) in step with the robot's real mode.
            if message.get("control_mode") in ("manual", "autonomous"):
                pad_controller.control_mode = message["control_mode"]

    def on_scan(message: dict[str, Any]) -> None:
        log.write("in", message)
        explorer.on_scan(message)

    pad = pad_controller = None
    if args.gamepad:
        from laptop.teleop.controller import TeleopController
        from laptop.teleop.gamepad import Gamepad
        pad, pad_controller = Gamepad(0), TeleopController()
        print(f"Gamepad: {pad.name or 'not found (waiting)'}")

    comm = CommunicationManager(args.bind, args.command_port, args.telemetry_port,
                                on_telemetry, on_scan)
    comm.start()
    print(f"log: {log.path}")

    video = mission = None
    last_frame = 0
    if args.person_source:
        from laptop.demo.live_person_stop import VideoWorker

        class ExplorerNavigator:
            def cancel_goal(self) -> bool:
                mission_state["stopped"] = True
                explorer.pause("PERSON FOUND - stopped")
                return True

        class RoverStop:
            def send_stop(self) -> bool:
                return send(make_stop())

        source = int(args.person_source) if args.person_source.isdecimal() else args.person_source
        video = VideoWorker(source, args.model, args.confidence)
        video.start()
        mission = MissionController(ExplorerNavigator(), RoverStop(), args.confirm_frames)
        print(f"person stop: {args.person_source} (confirm {args.confirm_frames} frames)")
    autostart_pending = args.autostart

    next_tick = time.monotonic()
    end = next_tick + args.duration if args.duration > 0 else math.inf
    last_explore = 0.0
    try:
        while time.monotonic() < end:
            now = time.monotonic()
            if autostart_pending and comm.robot_connected and explorer.telemetry:
                send(make_set_mode("autonomous"))
                explorer.start()
                autostart_pending = False
            if pad is not None:
                for message in pad_controller.step(pad.read()):
                    send(message)
            if mission is not None:
                observation, _, video_error = video.snapshot()
                if observation is not None and observation.sequence != last_frame:
                    last_frame = observation.sequence
                    if mission.observe(PersonObservation(observation.sequence,
                                                         round(time.time(), 2),
                                                         observation.confidence)):
                        x, y, yaw = slam.pose
                        found = LOG_DIR / f"person_{time.strftime('%H%M%S')}"
                        cv2.imencode(".jpg", observation.annotated)[1].tofile(
                            str(found.with_suffix(".jpg")))
                        found.with_suffix(".json").write_text(json.dumps({
                            "map_pose": [x, y, yaw], "confidence": observation.confidence,
                            "time": time.time()}, indent=2), encoding="utf-8")
                        print(f"\nPERSON FOUND at map ({x:+.2f}, {y:+.2f}) "
                              f"conf {observation.confidence:.2f} -> {found}.jpg")
                if video_error and int(now) % 5 == 0 and int(now - 0.05) % 5 != 0:
                    print(f"\n[video] {video_error}")
            if now - last_explore >= 0.5:
                last_explore = now
                explorer.tick()
                t = explorer.telemetry
                r = explorer.last_result
                line = (f"\r{'EXPLORE' if explorer.status.active else 'idle   '} "
                        f"robot {t.get('control_mode', '?')}/{t.get('follow', {}).get('state', '?')} "
                        f"sensors {t.get('sensors', {})} | slam {slam.scans_inserted} ins "
                        f"{'match %.2f' % r.score if r and r.matched else 'odom'} | "
                        f"{explorer.status.note}")
                sys.stdout.write(line[:160].ljust(160))
                sys.stdout.flush()
            if not args.no_view and not window and int(now) != int(now - 0.05):
                with explorer.lock:
                    img = render(slam, explorer)
                LIVE_MAP.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(LIVE_MAP), img)
            if window and int(now * 5) != int((now - 0.05) * 5):
                with explorer.lock:
                    img = render(slam, explorer)
                cv2.imshow("RoverAid map", img)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord(" "):
                    if explorer.status.active:
                        explorer.pause()
                    else:
                        send(make_set_mode("autonomous"))
                        explorer.start()
                if key == ord("m"):
                    explorer.pause()
                    send(make_set_mode("manual"))
                if key == ord("e"):
                    explorer.pause("e-stop")
                    send(make_estop())
                if key == ord("s"):
                    with explorer.lock:
                        save_map(slam, explorer, LOG_DIR / f"map_{time.strftime('%H%M%S')}")
            next_tick += 0.05
            time.sleep(max(0.0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        explorer.pause("quit")
        if comm.robot_connected:
            comm.send_stop()
        with explorer.lock:
            save_map(slam, explorer, LOG_DIR / f"explore_{stamp}_map")
        comm.stop()
        log.close()
        if pad is not None:
            pad.close()
        if video is not None:
            video.stop()
        if window:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
