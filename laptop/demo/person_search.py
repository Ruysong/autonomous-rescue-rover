"""Video -> person candidate -> mission stop request -> optional Pi command.

Run from the repository root: python -m laptop.demo.person_search --source VIDEO.mp4
The default mode simulates navigation and rover command delivery. Use
--send-to-pi only with the current MockMotor runtime on the Raspberry Pi.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from laptop.communication.messages import make_stop
from laptop.mission.controller import MissionController, PersonObservation


class DemoNavigator:
    def cancel_goal(self) -> bool:
        print("[Navigation] Goal cancelled (simulated; Nav2 is not connected)")
        return True


class DemoRover:
    def send_stop(self) -> bool:
        print(f"[Command] Would send {json.dumps(make_stop())} (simulated)")
        return True


def source_value(value: str) -> str | int:
    if value.isdecimal():
        return int(value)
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Video file not found: {path}")
    return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=source_value, required=True)
    parser.add_argument("--output", type=Path, default=Path("output/person-search"))
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--confidence", type=float, default=0.4)
    parser.add_argument("--confirm-frames", type=int, default=3)
    parser.add_argument("--render-height", type=int, default=720,
                        help="Maximum preview and saved-video height in pixels (0 keeps original size)")
    parser.add_argument("--send-to-pi", action="store_true", help="Send stop through the existing laptop command server")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--command-port", type=int, default=9998)
    parser.add_argument("--telemetry-port", type=int, default=9997)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if not 0 < args.confidence <= 1:
        parser.error("--confidence must be in (0, 1]")
    if args.confirm_frames < 1:
        parser.error("--confirm-frames must be at least 1")
    if args.render_height < 0 or (args.render_height != 0 and
                                  (args.render_height < 240 or args.render_height % 2)):
        parser.error("--render-height must be 0 or an even number of at least 240")
    return args


def run(args: argparse.Namespace) -> dict[str, object]:
    try:
        import cv2
    except ImportError as exc:
        raise SystemExit("OpenCV is required for the video demo") from exc

    from laptop.perception.person_detector import PersonDetector

    detector = PersonDetector(args.model, args.confidence)
    args.output.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        raise SystemExit(f"Could not open video source: {args.source}")
    orientation_auto = getattr(cv2, "CAP_PROP_ORIENTATION_AUTO", None)
    if orientation_auto is not None:
        capture.set(orientation_auto, 1)
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(fps) or fps <= 0:
        fps = 20.0

    communication = None
    rover = DemoRover()
    if args.send_to_pi:
        from laptop.communication.manager import CommunicationManager

        def show_telemetry(message: dict[str, object]) -> None:
            print(f"[Telemetry] speed={message['motor_speed']} safety={message['safety_state']}")

        communication = CommunicationManager(
            args.bind, args.command_port, args.telemetry_port, show_telemetry
        )
        communication.start()
        rover = communication
        input("Start the Pi runtime, wait for '[Command] Pi connected', then press Enter...")

    mission = MissionController(DemoNavigator(), rover, args.confirm_frames)
    video_path = args.output / "annotated.mp4"
    discovery_path = args.output / "discovery.jpg"
    discovery_path.unlink(missing_ok=True)
    writer = None
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_index += 1
            confidence, annotated = detector.detect(frame)
            height, width = annotated.shape[:2]
            if args.render_height and height > args.render_height:
                render_width = max(2, round(width * args.render_height / height))
                render_width -= render_width % 2
                annotated = cv2.resize(
                    annotated, (render_width, args.render_height), interpolation=cv2.INTER_AREA
                )
            observation = PersonObservation(
                frame_index, round((frame_index - 1) / fps, 2), confidence
            )
            just_confirmed = mission.observe(observation)
            if just_confirmed:
                print(f"[{observation.video_seconds:.2f}s] Person candidate confirmed")

            if mission.stop_requested:
                if not mission.stop_sent:
                    status = "PERSON FOUND - STOP PENDING"
                elif args.send_to_pi:
                    status = "PERSON FOUND - PI STOP SENT"
                else:
                    status = "PERSON FOUND - STOP (SIM)"
            elif mission.consecutive_frames:
                status = f"VERIFYING PERSON ({mission.consecutive_frames}/{mission.confirm_frames})"
            else:
                status = "SEARCHING"
            height, width = annotated.shape[:2]
            cv2.rectangle(annotated, (0, 0), (width, 82), (20, 20, 20), -1)
            cv2.putText(annotated, "MISSION: FIND PERSON", (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            text_width = cv2.getTextSize(status, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)[0][0]
            status_scale = 0.65 * min(1.0, (width - 28) / max(1, text_width))
            cv2.putText(annotated, status, (14, 65), cv2.FONT_HERSHEY_SIMPLEX,
                        status_scale, (0, 215, 255), 2)
            if writer is None:
                writer = cv2.VideoWriter(
                    str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
                )
                if not writer.isOpened():
                    raise RuntimeError(f"Could not create output video: {video_path}")
            writer.write(annotated)
            if just_confirmed:
                cv2.imencode(".jpg", annotated)[1].tofile(str(discovery_path))
            if args.show:
                cv2.imshow("Rover person search", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if args.show:
            cv2.destroyAllWindows()
        if communication is not None:
            communication.stop()

    if writer is None:
        raise SystemExit("No frames were read from the video source")
    report = {
        "source": str(args.source),
        "model": args.model,
        "processed_frames": frame_index,
        "rover_mode": "pi_command" if args.send_to_pi else "simulated",
        **mission.report(),
        "navigation_cancelled_simulated": mission.navigation_cancelled,
        "pi_stop_command_sent": args.send_to_pi and mission.stop_sent,
        "simulated_stop_signal": not args.send_to_pi and mission.stop_sent,
    }
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Annotated video: {video_path}")
    print(f"Mission report: {args.output / 'report.json'}")
    return report


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
