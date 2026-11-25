"""Autonomous frontier exploration with live SLAM (laptop side).

    python -m laptop.navigation.run_explore [--autostart] [--gamepad] [--no-view]
    python -m laptop.navigation.run_explore --person-source http://<rover>:8080/stream [...]
    python -m laptop.navigation.run_explore --replay logs/explore_YYYYmmdd_HHMMSS.jsonl [--no-match]

Start this first, then on the Pi:  rover_controller --host <laptop address>
The robot follows paths only in autonomous mode: --autostart sends set_mode autonomous, or press
Start on the gamepad (--gamepad: LB-deadman manual driving, Back = E-stop, as in run_gamepad).

--person-source runs YOLO observations and an image-space caution/hold policy. A person
detection is reported after repeated observations. Transient video loss stops motion until
recovery; persistent loss and visual-zone occupancy latch a hold. With --llm, confirmed
darkness can trigger a bounded LiDAR-only mission while the camera reports fresh dark frames.

Map window keys: space = start / pause exploring, m = manual mode, s = save map, q = quit.
Every scan and telemetry message is logged (jsonl) so the run can be replayed offline:
--replay rebuilds the map from a log; --no-match maps with odometry only (to see what SLAM adds).
"""

from __future__ import annotations

import argparse
from queue import Empty, Full, Queue
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
from laptop.communication.messages import make_estop, make_set_mode, make_stop, make_resume
from laptop.mission.ai_selector import AISelector
from laptop.mission.local_reasoner import LocalReasoner
from laptop.mission.person_alert import PersonAlerts
from laptop.mission.protection_link import ProtectionLink
from laptop.mission.sensor_policy import SensorPolicy, SensorState
from laptop.mission.visual_guard import VisualGuard, GuardConfig
from .dashboard import Dashboard

from .explorer import Explorer, ExplorerConfig
from .slam import ScanSlam

LOG_DIR = Path("logs")
LIVE_MAP = LOG_DIR / "live_map.png"


def camera_preview(image, state: SensorState, alert: dict | None) -> np.ndarray:
    """Keep Foxglove's video panel readable even when camera frames are missing."""
    banner = np.zeros((480, 640, 3), dtype=np.uint8)
    if image is not None:
        h, w = image.shape[:2]
        scale = min(640 / w, 412 / h)
        view = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))))
        x = (640 - view.shape[1]) // 2
        banner[34:34 + view.shape[0], x:x + view.shape[1]] = view
    cv2.putText(banner, state.mode.replace("_", " "), (14, 25), cv2.FONT_HERSHEY_SIMPLEX,
                .7, (120, 235, 255), 2)
    if alert and state.mode == "VISION_SEARCH":
        cv2.putText(banner, f"LAST ALERT: PERSON FOUND | {alert['posture'].replace('_', ' ').upper()}",
                    (14, 468), cv2.FONT_HERSHEY_SIMPLEX, .55, (145, 255, 150), 1)
    return banner


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
            if not self._file.closed:
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
    ok, encoded = cv2.imencode(".png", render(slam, explorer))
    if ok:
        stem.with_suffix(".png").write_bytes(encoded.tobytes())
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
    parser.add_argument("--person-source", help="camera stream / video / index for observation + visual protection")
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--confidence", type=float, default=0.4)
    parser.add_argument('--llm', action='store_true', help='Enable real local Qwen goal selection')
    parser.add_argument('--ai-endpoint',default='http://127.0.0.1:8091')
    parser.add_argument('--ai-assets',type=Path,help='Asset root containing runtime/ and models/; auto-start local Qwen')
    parser.add_argument('--mission',default='Find people and report observations')
    parser.add_argument('--guard-config',type=Path,default=Path('laptop/config/mission.json'))
    parser.add_argument('--foxglove', action='store_true',
                        help='also publish a live view for the Foxglove app (ws://127.0.0.1:8765)')
    parser.add_argument('--foxglove-port', type=int, default=8765)
    parser.add_argument('--mock-display', action='store_true',
                        help='Show the offline rehearsal label; never send movement requests')
    args = parser.parse_args(argv)

    if args.replay:
        return replay(args.replay, not args.no_match, not args.no_view)
    if args.mock_display and (args.autostart or args.gamepad):
        parser.error("--mock-display cannot arm the rover")
    if not math.isfinite(args.speed) or not 0 < args.speed <= .5:
        parser.error("--speed must be in (0, .5] m/s")
    window = not args.no_view and gui_available()
    # The dashboard is bounded independently of video/map resolution.
    if not args.no_view and not window:
        print(f"OpenCV has no GUI here: the map is written to {LIVE_MAP} every second "
              "(open it in VS Code); keys are not available, use the gamepad or Ctrl+C.")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    log = JsonlLog(LOG_DIR / f"explore_{stamp}.jsonl")
    slam = ScanSlam()
    comm: CommunicationManager | None = None
    moving = False
    live_telemetry = (None, 0.0, {})
    scan_at = 0.0
    scans = Queue(maxsize=1)
    stopping = threading.Event()
    def event(kind, data):
        if not stopping.is_set():
            log.write(kind, data)

    def send(message: dict) -> bool:
        if comm is None or not comm.robot_connected:
            return False
        kind = message.get("type")
        requests_motion = kind in ("motion", "drive") or (kind == "path" and bool(message.get("points")))
        if requests_motion and (not moving or not robot_healthy() or args.mock_display):
            return False
        log.write("out", message)
        return comm.send(message)

    settings = json.loads(args.guard_config.read_text(encoding="utf-8"))
    guard = VisualGuard(GuardConfig(**settings["visual_guard"]))
    local_ai = LocalReasoner(args.ai_endpoint)
    supervisor = None
    if args.llm and args.ai_assets:
        from laptop.mission.local_model import LocalModelServer
        supervisor = LocalModelServer(args.ai_assets, args.ai_endpoint)

    def reason(snapshot):
        if supervisor:
            supervisor.ensure_ready()
        return local_ai(snapshot)

    def decide_sensor(evidence):
        if supervisor:
            supervisor.ensure_ready()
        return local_ai.select_sensor_mode(evidence)

    selector = AISelector(reason, args.mission, event=event) if args.llm else None
    sensor = SensorPolicy(decide_sensor if args.llm else None, event=event)
    explorer = Explorer(slam, send, ExplorerConfig(speed=args.speed), selector=selector)
    alerts = PersonAlerts(event, LOG_DIR / f"explore_{stamp}_alerts")
    dashboard = Dashboard() if window else None
    if window:
        cv2.namedWindow("RoverAid", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("RoverAid", 1180, 860)
        cv2.setMouseCallback("RoverAid", dashboard.mouse)

    fox = None
    if args.foxglove:
        from .foxglove_view import FoxgloveView
        fox = FoxgloveView(port=args.foxglove_port)
        print(f"Foxglove: Open connection -> {fox.url}")

    def on_telemetry(message: dict[str, Any]) -> None:
        nonlocal live_telemetry
        received = time.monotonic()
        live_telemetry = (comm.command_generation, received, message)
        log.write("in", message)

    def robot_healthy() -> bool:
        generation, at, telemetry = live_telemetry
        return (bool(telemetry.get("protection_supported")) and comm.robot_connected
                and generation == comm.command_generation and time.monotonic() - at < 1.0)

    def on_scan(message: dict[str, Any]) -> None:
        nonlocal scan_at
        received = time.monotonic()
        scan_at = received if sum(100 < value < 15000 for value in message["ranges_mm"]) >= 8 else 0
        try:
            scans.put_nowait(message)
        except Full:
            try:
                scans.get_nowait()
            except Empty:
                pass
            scans.put_nowait(message)

    def map_scans():
        while not stopping.is_set():
            try:
                message = scans.get(timeout=.2)
            except Empty:
                continue
            log.write("in", message)
            explorer.on_scan(message)
            if fox:
                fox.publish_scan(message)

    pad = pad_controller = None
    if args.gamepad:
        from laptop.teleop.controller import TeleopController
        from laptop.teleop.gamepad import Gamepad
        pad, pad_controller = Gamepad(0), TeleopController()
        print(f"Gamepad: {pad.name or 'not found (waiting)'}")

    comm = CommunicationManager(args.bind, args.command_port, args.telemetry_port, on_telemetry, on_scan)
    comm.start()
    mapping = threading.Thread(target=map_scans, daemon=True, name="slam")
    mapping.start()
    link = ProtectionLink(send)
    link.start()
    print(f"log: {log.path}")

    def warm_model():
        try:
            info = supervisor.ensure_ready()
            print(f"Qwen ready ({info['backend']})")
            event("model_ready", info)
        except Exception as error:
            if not stopping.is_set():
                event("model_error", {"error": str(error)})
    if supervisor:
        threading.Thread(target=warm_model, daemon=True, name="model-warmup").start()

    video = None
    last_frame = 0
    if args.person_source:
        from laptop.perception.live_stream import LivePersonStream
        source = int(args.person_source) if args.person_source.isdecimal() else args.person_source
        video = LivePersonStream(source, args.model, args.confidence)
        video.start()
        print(f"Person search: {args.person_source}")

    state = SensorState("VISION_OFF", "Camera disabled", False, .5)
    desired = None
    guard_held = False
    generation = 0
    video_since = None
    was_operational = False
    autostart_pending = args.autostart
    last_explore = last_view = 0.0
    end = time.monotonic() + args.duration if args.duration > 0 else math.inf

    def arm(mode):
        nonlocal desired, guard_held
        # Clear old paths before issuing the only resume command. A health recovery alone
        # never clears a guard latch or E-stop.
        if args.mock_display or not lidar_ready or state.mode not in ("VISION_SEARCH", "VISION_OFF"):
            explorer.status.note = "Cannot arm: wait for healthy telemetry, LiDAR and camera"
            return
        if video and guard.latched and not guard.resume():
            explorer.status.note = "Cannot arm: wait for clear vision"
            return
        if not send(make_stop()) or not link.publish(False, 0, immediate=True):
            return
        if send(make_resume()) and send(make_set_mode(mode)):
            desired, guard_held = mode, False
            explorer.pause("Armed for " + mode)
            explorer.status.done = False
            event("operator_arm", {"mode": mode})

    def stop(action):
        nonlocal desired, moving
        desired, moving = None, False
        if action == "estop":
            send(make_estop())
        else:
            link.publish(guard_held, 0, immediate=True)
        send(make_stop())
        explorer.pause(f"Operator {action}")
        event("operator_stop", {"action": action})

    try:
        while time.monotonic() < end:
            now = time.monotonic()
            healthy = robot_healthy()
            _, _, telemetry = live_telemetry
            explorer.on_telemetry(telemetry if healthy else {})
            lidar_ready = (healthy and 0 <= now - scan_at <= .65
                           and bool(telemetry.get("sensors", {}).get("lidar")))
            current_generation = comm.command_generation
            if generation != current_generation or (was_operational and not healthy):
                generation = current_generation
                desired, moving = None, False
                explorer.pause("Link changed; operator must re-arm")
                event("command_link_changed", {"generation": generation})
            was_operational = healthy

            latest_frame = None
            visual_level = "observing"
            if video:
                camera, observation = video.camera, video.observation
                camera_now = time.monotonic()
                fresh = camera is not None and 0 <= camera_now - camera.at <= 1.0
                vision_ready = (fresh and observation is not None
                                and 0 <= camera_now - observation.frame_at <= guard.config.max_frame_age_s
                                and camera.luma > sensor.dark_luma
                                and observation.luma > sensor.dark_luma)
                can_switch = (desired == "autonomous" and not guard_held
                              and not telemetry.get("estop") and telemetry.get("safety_state") != "protection_hold")
                # Disable mode switching in manual and during any latched hold. Dark frames
                # still lead to a pause; old detections cannot stand in for current vision.
                state = sensor.update(camera_now, camera, vision_ready, lidar_ready, can_switch)
                link_loss = not fresh or (not vision_ready and state.mode == "WAIT_CAMERA"
                                           and camera.luma > sensor.dark_luma)
                video_since = (now if video_since is None else video_since) if link_loss else None
                stale_hold = desired is not None and video_since is not None and now - video_since >= 4.0
                frame_at = observation.frame_at if vision_ready else None
                people = observation.people if vision_ready else []
                duties = telemetry.get("duty") or [0, 0]
                visual_level = guard.update(people, frame_at, forward=sum(duties) >= 0,
                                            latch_stale=stale_hold)
                if guard.latched and not guard_held:
                    guard_held = True
                    desired = None
                    event("protection_hold", {"reason": guard.reason})
                if not vision_ready:
                    alerts.clear_pending()
                if selector and (not vision_ready or state.mode != "VISION_SEARCH"):
                    selector.observations.clear()
                if state.mode == "VISION_SEARCH" and vision_ready:
                    latest_frame = observation.annotated
                    if last_frame != observation.sequence:
                        last_frame = observation.sequence
                        if selector:
                            selector.observe(people, frame_at)
                        alerts.update(observation, slam.pose)
                        event("observation", {"people": people, "frame_at": frame_at})
                elif fresh:
                    latest_frame = camera.image
                if video.detector_error:
                    guard.reason = "Detector: " + video.detector_error[:80]
                elif not fresh and video.capture_error:
                    guard.reason = video.capture_error
            if healthy and not args.mock_display and (
                    telemetry.get("estop") or telemetry.get("safety_state") == "protection_hold"):
                # An onboard latch can arrive independently of the camera.
                if desired is not None and moving:
                    desired = None
                    event("onboard_hold", {"reason": telemetry.get("safety_state")})
            moving = (healthy and lidar_ready and desired is not None and not state.pause
                      and desired == telemetry.get("control_mode")
                      and not guard_held and not telemetry.get("estop")
                      and telemetry.get("safety_state") != "protection_hold")
            limit = min(state.speed_limit, guard.config.caution_speed if visual_level == "caution" else .5)
            link.publish(guard_held, limit if moving else 0, immediate=not moving and explorer.status.active)
            if not moving and explorer.status.active:
                explorer.pause(guard.reason if guard_held else state.reason if state.pause else "Waiting for rover")
            elif (moving and desired == "autonomous" and telemetry.get("control_mode") == "autonomous"
                  and not explorer.status.active and not explorer.status.done):
                explorer.start()

            if autostart_pending and lidar_ready and (not video or state.mode == "VISION_SEARCH"):
                arm("autonomous")
                autostart_pending = desired != "autonomous"
            if pad is not None:
                pad_controller.limits.update_from_telemetry(telemetry)
                if telemetry.get("control_mode") in ("manual", "autonomous"):
                    pad_controller.control_mode = telemetry["control_mode"]
                pad_controller.estop = bool(telemetry.get("estop") or guard_held)
                for message in pad_controller.step(pad.read()):
                    kind = message["type"]
                    if kind == "estop":
                        stop("estop")
                    elif kind == "resume":
                        arm(telemetry.get("control_mode", "manual"))
                    elif kind == "set_mode":
                        arm(message["mode"])
                    elif desired == "manual" and telemetry.get("control_mode") == "manual":
                        send(message)
            if now - last_explore >= 0.5 and desired == "autonomous":
                last_explore = now
                explorer.tick()

            if now - last_view >= .20:
                last_view = now
                mission_view = {
                    "sensor_mode": state.mode, "sensor_reason": state.reason,
                    "person_confirmation": state.mode == "VISION_SEARCH" if video else False,
                    "camera_luma": round(camera.luma, 1) if video and fresh else -1.0,
                    "rover_link_ok": healthy, "lidar_fresh": lidar_ready,
                    "speed_limit_mps": limit if moving else 0.0,
                    "hold": guard_held or telemetry.get("safety_state") == "protection_hold",
                    "person_alert": alerts.latest or {},
                    "person_alerts": alerts.count,
                    "rehearsal": args.mock_display,
                }
                image = latest_frame
                if fox:
                    with explorer.lock:
                        fox.publish_pose(slam, explorer)
                        if fox.due("map", .5):
                            fox.publish_map(slam)
                    fox.publish_status(explorer.telemetry, guard, video is not None, selector, explorer, mission_view)
                    if video:
                        fox.publish_camera(camera_preview(image, state, alerts.latest))
                if window:
                    with explorer.lock:
                        map_image = render(slam, explorer)
                    panel = dashboard.draw(map_image, image, explorer, selector, guard, args.llm,
                                           video is not None, mission_view)
                    cv2.imshow("RoverAid", panel)
                elif not args.no_view and fox is None:
                    with explorer.lock:
                        img = render(slam, explorer)
                    LIVE_MAP.parent.mkdir(parents=True, exist_ok=True)
                    ok, encoded = cv2.imencode(".png", img)
                    if ok:
                        LIVE_MAP.write_bytes(encoded.tobytes())
            if window:
                key = cv2.waitKey(1) & 0xFF
                action = dashboard.pop_action()
                if key == ord("q") or cv2.getWindowProperty("RoverAid", cv2.WND_PROP_VISIBLE) < 1:
                    break
                if key == ord("e"): action = "estop"
                if key == ord(" "): action = "pause" if desired == "autonomous" else "resume"
                if key == ord("m"): action = "manual"
                if action in ("pause", "estop"):
                    stop(action)
                if action in ("resume", "manual"):
                    arm("autonomous" if action == "resume" else "manual")
                if key == ord("s"):
                    with explorer.lock:
                        save_map(slam, explorer, LOG_DIR / f"map_{time.strftime('%H%M%S')}")
            time.sleep(max(0, .05 - (time.monotonic() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        moving = False
        link.close()
        if comm.robot_connected:
            comm.send_stop()
        explorer.pause("quit")
        stopping.set()
        comm.stop()
        mapping.join()
        if selector:
            selector.close()
        if supervisor:
            supervisor.close()
        if video:
            video.stop()
        with explorer.lock:
            save_map(slam, explorer, LOG_DIR / f"explore_{stamp}_map")
        log.close()
        if pad:
            pad.close()
        if window:
            cv2.destroyAllWindows()
        if fox:
            fox.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
