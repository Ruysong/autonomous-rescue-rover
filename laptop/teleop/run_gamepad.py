"""Drive the rover with a gamepad.

    python -m laptop.teleop.run_gamepad [--bind 0.0.0.0] [--command-port 9998] [--telemetry-port 9997]

Start this first, then the robot runtime on the Pi:
    rover_controller --host <laptop address>
Layout: see laptop/teleop/controller.py (hold LB to drive, Back = E-stop, Start = resume).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import Any

from laptop.communication.manager import CommunicationManager

from .controller import RobotLimits, TeleopController
from .gamepad import Gamepad

PERIOD = 0.05  # 20 Hz; the robot gate times out after 0.5 s


class TelemetryView:
    def __init__(self, limits: RobotLimits) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, Any] | None = None
        self._at = 0.0
        self._limits = limits

    def __call__(self, message: dict[str, Any]) -> None:
        with self._lock:
            self._last = message
            self._at = time.monotonic()
            self._limits.update_from_telemetry(message)

    def line(self) -> str:
        with self._lock:
            t = self._last
            age = time.monotonic() - self._at
        if t is None or age > 1.0:
            return "robot: no telemetry"
        duty = t.get("duty", [0, 0])
        steer = t.get("steer_deg", [0, 0, 0, 0])
        pico = t.get("pico", {})
        sonar = t.get("ultrasonic_cm", [])
        return (
            f"robot: {t.get('control_mode', '?')}/{t.get('steer_mode', '?')} "
            f"{t.get('safety_state', '?'):<12} duty L{duty[0]:+.2f} R{duty[1]:+.2f} "
            f"steer {steer[0]:+5.1f} {steer[1]:+5.1f} {steer[2]:+5.1f} {steer[3]:+5.1f} "
            f"pico {'ok' if pico.get('ok') else 'NO'} sonar {sonar}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--command-port", type=int, default=9998)
    parser.add_argument("--telemetry-port", type=int, default=9997)
    parser.add_argument("--pad", type=int, default=0, help="joystick index")
    args = parser.parse_args(argv)

    controller = TeleopController()
    view = TelemetryView(controller.limits)
    comm = CommunicationManager(args.bind, args.command_port, args.telemetry_port, view)
    pad = Gamepad(args.pad)
    print(f"Gamepad: {pad.name or 'not found (waiting)'}")
    print("Hold LB + left stick to drive, right stick to steer, RT boost, "
          "A normal / B spin, Back E-stop, Start resume or manual/auto. Ctrl+C quits.")
    comm.start()

    next_tick = time.monotonic()
    try:
        while True:
            state = pad.read()
            messages = controller.step(state)
            if comm.robot_connected:
                for message in messages:
                    comm.send(message)
            status = (
                f"\r[{'pad' if state.connected else 'NO PAD'}] "
                f"{controller.control_mode}/{controller.steer_mode}"
                f"{' ESTOP' if controller.estop else ''}"
                f"{' DRIVE' if controller.driving else ''} | {view.line()} | {controller.notice}"
            )
            sys.stdout.write(status[:200].ljust(200))
            sys.stdout.flush()
            next_tick += PERIOD
            time.sleep(max(0.0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        if comm.robot_connected:
            comm.send_stop()
        comm.stop()
        pad.close()
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
