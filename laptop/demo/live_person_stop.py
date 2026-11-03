"""Visible, manually driven person-stop demo; defaults to simulation.

From the repository root:
  python -m laptop.demo.live_person_stop --source http://PI_IP:8080/stream

Use --pi-mock only with motor power disconnected. --pi-real requires explicit
physical readiness and a Pi runtime that reports mode=gpio. The default is
display-only simulation; no network drive command is sent.
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any

import cv2
from PIL import Image, ImageTk

from laptop.mission.manual_safety import DriveDecision, ManualSafetyController
from laptop.perception.person_detector import PersonDetector


REASON_TEXT = {
    "PERSON DETECTED": "사람 탐지 → 정지",
    "MANUAL STOP": "수동 STOP → 정지",
    "VIDEO NOT FRESH": "영상 지연·끊김 → 정지",
    "PI NOT READY": "Pi 연결 대기 → 정지",
    "CONTROL PAUSED": "조종 대기",
    "KEY RELEASED": "키를 놓아 정지",
    "FORWARD": "전진 명령 중",
    "REVERSE": "후진 명령 중",
    "COMMAND SEND FAILED": "명령 전송 실패 → 정지",
}


@dataclass(frozen=True)
class Observation:
    sequence: int
    frame_at: float
    confidence: float | None
    annotated: Any


class VideoWorker:
    """Keep decoding and inference off Tk's UI thread."""

    def __init__(self, source: str | int, model: str, confidence: float) -> None:
        self.source = source
        self.model = model
        self.confidence = confidence
        self.latest: Observation | None = None
        self.person_event: tuple[int, float, float] | None = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> tuple[Observation | None, tuple[int, float, float] | None, str | None]:
        with self._lock:
            return self.latest, self.person_event, self.error

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        capture = None
        try:
            detector = PersonDetector(self.model, self.confidence)
            capture = cv2.VideoCapture(self.source)
            if not capture.isOpened():
                raise RuntimeError(f"Cannot open video source: {self.source}")
            local_file = isinstance(self.source, str) and Path(self.source).is_file()
            fps = capture.get(cv2.CAP_PROP_FPS) if local_file else 0
            frame_interval = 1 / fps if 0 < fps <= 120 else 0
            sequence = 0
            while not self._stop.is_set():
                started = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError("Video stream ended or disconnected")
                frame_at = time.monotonic()
                confidence, annotated = detector.detect(frame)
                sequence += 1
                with self._lock:
                    self.latest = Observation(sequence, frame_at, confidence, annotated)
                    if confidence is not None:
                        self.person_event = (sequence, frame_at, confidence)
                if frame_interval:
                    self._stop.wait(max(0.0, frame_interval - (time.monotonic() - started)))
        except Exception as error:
            with self._lock:
                self.error = str(error)
        finally:
            if capture is not None:
                capture.release()


class LivePersonStopApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = tk.Tk()
        self.root.title("로버 | 수동 조종 + 사람 탐지 정지")
        self.root.configure(bg="#101820")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.controller = ManualSafetyController(args.throttle, args.max_frame_age)
        self.video = VideoWorker(args.source, args.model, args.confidence)
        self.last_observation_sequence = 0
        self.last_person_event_sequence = 0
        self.last_command: float | None = None
        self.last_drive_at = 0.0
        self.last_stop_at = 0.0
        self.last_reason = "STARTING"
        self.telemetry_queue: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self.last_telemetry: dict[str, Any] | None = None
        self.last_telemetry_at = 0.0
        self.communication = None
        self.closed = False
        self._build_ui()
        self._bind_inputs()

        if args.pi_mock or args.pi_real:
            from laptop.communication.manager import CommunicationManager

            self.communication = CommunicationManager(
                args.bind, args.command_port, args.telemetry_port,
                self.telemetry_queue.put,
            )
            self.communication.start()
        self.video.start()
        self.root.after(50, self._tick)

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg="#101820", padx=16, pady=16)
        outer.pack(fill="both", expand=True)
        self.video_label = tk.Label(
            outer, text="Waiting for video...", fg="white", bg="black",
            width=90, height=28,
        )
        self.video_label.pack(side="left", fill="both", expand=True)
        panel = tk.Frame(outer, bg="#101820", width=310, padx=16)
        panel.pack(side="right", fill="y")

        title = tk.Label(panel, text="로버 조종 · 사람 탐지", font=("Malgun Gothic", 19, "bold"),
                         fg="white", bg="#101820")
        title.pack(anchor="w", pady=(0, 18))
        mode_text = ("Pi 실모터 연결 대기" if self.args.pi_real else
                     "Pi Mock 연결 대기" if self.args.pi_mock else "화면 시뮬레이션")
        self.mode_label = self._label(panel, mode_text)
        self.reason_label = self._label(panel, "정지 · 시작 중", size=17)
        self.reason_label.configure(bg="#4a2020", fg="white", padx=10, pady=14)
        self.speed_label = self._label(
            panel, f"현재 요청 0% · 설정 {self.controller.drive_throttle * 100:.0f}%", size=17
        )
        self.speed_bar = ttk.Progressbar(panel, maximum=100, value=0)
        self.speed_bar.pack(fill="x", pady=(0, 8))
        self._label(panel, "명령 비율 표시 · 실제 주행속도는 아직 측정하지 않음", size=10)
        self.confidence_label = self._label(panel, "사람: 미탐지")
        self.telemetry_label = self._label(panel, "실제 모터 속도: 측정값 없음")
        self.error_label = self._label(panel, "", color="#ffad8f")

        self.enable_button = ttk.Button(panel, text="수동 조종 시작", command=self._enable)
        self.enable_button.pack(fill="x", pady=(16, 6))
        self.forward_button = ttk.Button(panel, text="누르고 있는 동안 전진 (W)")
        self.forward_button.pack(fill="x", pady=6)
        self.reverse_button = ttk.Button(panel, text="누르고 있는 동안 후진 (S)")
        self.reverse_button.pack(fill="x", pady=6)
        ttk.Button(panel, text="즉시 정지 (Space)", command=self._manual_stop).pack(fill="x", pady=(18, 6))
        ttk.Button(panel, text="정지 잠금 해제 (R)", command=self._reset).pack(fill="x", pady=6)
        self._label(panel, "사람 발견 시 주행 명령이 차단됩니다.\n"
                    "다시 움직이려면 잠금 해제 후 수동 조종을 시작하세요.", size=10)

    @staticmethod
    def _label(parent: tk.Widget, value: str, size: int = 12,
               color: str = "#dce8ef") -> tk.Label:
        label = tk.Label(parent, text=value, font=("Malgun Gothic", size), fg=color,
                         bg="#101820", justify="left", wraplength=290)
        label.pack(anchor="w", pady=6)
        return label

    def _bind_inputs(self) -> None:
        self.root.bind_all("<KeyPress-w>", lambda _: self.controller.set_direction(1))
        self.root.bind_all("<KeyPress-s>", lambda _: self.controller.set_direction(-1))
        self.root.bind_all("<KeyRelease-w>", lambda _: self.controller.release(1))
        self.root.bind_all("<KeyRelease-s>", lambda _: self.controller.release(-1))
        self.root.bind_all("<KeyPress-space>", lambda _: self._manual_stop())
        self.root.bind_all("<KeyPress-r>", lambda _: self._reset())
        self.root.bind("<FocusOut>", lambda _: self.controller.set_direction(0))
        self.forward_button.bind("<ButtonPress-1>", lambda _: self.controller.set_direction(1))
        self.reverse_button.bind("<ButtonPress-1>", lambda _: self.controller.set_direction(-1))
        self.root.bind_all("<ButtonRelease-1>", lambda _: self.controller.set_direction(0))

    def _manual_stop(self) -> None:
        self.controller.manual_stop()
        self._send_stop(time.monotonic())

    def _reset(self) -> None:
        self.controller.reset(time.monotonic())

    def _enable(self) -> None:
        self.controller.enable(time.monotonic())

    def _drain_telemetry(self, now: float) -> None:
        while True:
            try:
                self.last_telemetry = self.telemetry_queue.get_nowait()
            except queue.Empty:
                break
            self.last_telemetry_at = now

    def _pi_ready(self, now: float) -> bool:
        if not (self.args.pi_mock or self.args.pi_real):
            return True
        expected_mode = "gpio" if self.args.pi_real else "mock"
        return (self.last_telemetry is not None
                and self.last_telemetry.get("mode") == expected_mode
                and self.last_telemetry.get("safety_state") != "hardware_fault"
                and now - self.last_telemetry_at <= 1.0)

    def _send_stop(self, now: float) -> None:
        if self.communication is not None:
            self.communication.send_stop()
        self.last_command = 0.0
        self.last_stop_at = now

    def _apply(self, decision: DriveDecision, now: float, pi_ready: bool) -> None:
        if decision.throttle == 0:
            if self.last_command != 0.0 or (pi_ready and now - self.last_stop_at >= 0.25):
                self._send_stop(now)
            return
        if self.last_command == decision.throttle and now - self.last_drive_at < 0.1:
            return
        if self.communication is not None and not self.communication.send_drive(decision.throttle):
            self.controller.fault_stop("COMMAND SEND FAILED")
            self._send_stop(now)
            return
        self.last_command = decision.throttle
        self.last_drive_at = now

    def _display_frame(self, frame: Any) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((900, 620))
        photo = ImageTk.PhotoImage(image)
        self.video_label.configure(image=photo, text="", width=0, height=0)
        self.video_label.image = photo

    def _tick(self) -> None:
        if self.closed:
            return
        now = time.monotonic()
        observation, person_event, error = self.video.snapshot()
        if observation is not None and observation.sequence != self.last_observation_sequence:
            self.last_observation_sequence = observation.sequence
            self.controller.observe(observation.frame_at, observation.confidence)
            self._display_frame(observation.annotated)
        if person_event is not None and person_event[0] != self.last_person_event_sequence:
            self.last_person_event_sequence = person_event[0]
            self.controller.observe(person_event[1], person_event[2])
        self._drain_telemetry(now)
        pi_ready = self._pi_ready(now)
        decision = self.controller.decide(now, pi_ready)
        if self.controller.armed and decision.reason in ("VIDEO NOT FRESH", "PI NOT READY"):
            self.controller.fault_stop(decision.reason)
            decision = self.controller.decide(now, pi_ready)
        self._apply(decision, now, pi_ready)
        decision = self.controller.decide(now, pi_ready)

        if decision.reason != self.last_reason:
            print(f"[{time.strftime('%H:%M:%S')}] {decision.reason} throttle={decision.throttle:+.2f}", flush=True)
            self.last_reason = decision.reason
        moving = decision.throttle != 0
        reason_text = REASON_TEXT.get(decision.reason, decision.reason)
        self.reason_label.configure(
            text=f"{'주행 요청' if moving else '정지'} · {reason_text}",
            bg="#173c2b" if moving else "#4a2020",
        )
        self.speed_label.configure(
            text=f"현재 요청 {abs(decision.throttle) * 100:.0f}%"
                 + (" 전진" if decision.throttle > 0 else " 후진" if decision.throttle < 0 else "")
                 + f" · 설정 {self.controller.drive_throttle * 100:.0f}%"
        )
        self.speed_bar.configure(value=abs(decision.throttle) * 100)
        confidence = self.controller.detected_person_confidence or self.controller.last_person_confidence
        self.confidence_label.configure(
            text="사람: 미탐지" if confidence is None else f"사람 탐지 신뢰도: {confidence:.2f}"
        )
        if self.args.pi_mock or self.args.pi_real:
            mode = self.last_telemetry.get("mode") if self.last_telemetry else "no telemetry"
            kind = "실모터" if self.args.pi_real else "Mock"
            self.mode_label.configure(text=f"Pi {kind}: {mode} | {'연결' if pi_ready else '대기'}")
            if self.last_telemetry is not None:
                self.telemetry_label.configure(
                    text="Pi가 받은 명령: {:.2f} · 안전 상태: {} (실측 속도 아님)".format(
                        self.last_telemetry["motor_speed"], self.last_telemetry["safety_state"]
                    )
                )
        self.error_label.configure(text=error or "")
        self.root.after(50, self._tick)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.controller.manual_stop()
        self._send_stop(time.monotonic())
        if self.communication is not None:
            self.communication.stop()
        self.video.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="0",
                        help="Pi MJPEG /stream URL, local webcam number, or video file")
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument("--confidence", type=float, default=0.4)
    parser.add_argument("--throttle", type=float, default=0.1,
                        help="Normalized mock drive request, not physical speed")
    parser.add_argument("--max-frame-age", type=float, default=0.75)
    parser.add_argument("--pi-mock", action="store_true",
                        help="Send to Pi mock runtime; requires disconnected motor power")
    parser.add_argument("--confirm-no-motor-power", action="store_true",
                        help="Confirm the physical motor power is disconnected for --pi-mock")
    parser.add_argument("--pi-real", action="store_true",
                        help="Send to the opt-in GPIO motor runtime")
    parser.add_argument("--confirm-physical-stop-ready", action="store_true",
                        help="Confirm clear test area and a person ready to cut motor power")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--command-port", type=int, default=9998)
    parser.add_argument("--telemetry-port", type=int, default=9997)
    args = parser.parse_args()
    if isinstance(args.source, str) and args.source.isdecimal():
        args.source = int(args.source)
    if not 0 < args.confidence <= 1:
        parser.error("--confidence must be in (0, 1]")
    if not 0 < args.throttle <= 1:
        parser.error("--throttle must be in (0, 1]")
    if args.max_frame_age <= 0:
        parser.error("--max-frame-age must be positive")
    if args.pi_mock and not args.confirm_no_motor_power:
        parser.error("--pi-mock requires --confirm-no-motor-power")
    if args.pi_real and not args.confirm_physical_stop_ready:
        parser.error("--pi-real requires --confirm-physical-stop-ready")
    if args.pi_mock and args.pi_real:
        parser.error("Choose either --pi-mock or --pi-real")
    LivePersonStopApp(args).run()


if __name__ == "__main__":
    main()
