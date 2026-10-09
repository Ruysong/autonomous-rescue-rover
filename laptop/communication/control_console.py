"""Windows keyboard console for the laptop-side communication manager."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

from .manager import CommunicationManager


def main() -> int:
    if os.name != "nt":
        print("The keyboard console currently requires Windows.", file=sys.stderr)
        return 1

    import msvcrt

    parser = argparse.ArgumentParser(description="Operate the mock rover from the laptop.")
    parser.add_argument("--bind", default="0.0.0.0", help="Laptop interface to listen on")
    parser.add_argument("--command-port", type=int, default=9998)
    parser.add_argument("--telemetry-port", type=int, default=9997)
    args = parser.parse_args()

    last_display: tuple[float, str] | None = None
    last_printed_at = 0.0

    def display_telemetry(message: dict[str, Any]) -> None:
        nonlocal last_display, last_printed_at
        current = (message["motor_speed"], message["safety_state"])
        now = time.monotonic()
        if current != last_display or now - last_printed_at >= 1.0:
            print(f"[Telemetry] speed={current[0]:.2f} safety={current[1]} "
                  f"last_command_age={message['last_command_age']:.2f}s")
            last_display = current
            last_printed_at = now

    manager = CommunicationManager(
        args.bind, args.command_port, args.telemetry_port, display_telemetry
    )
    try:
        manager.start()
    except OSError as error:
        print(f"Could not start listeners: {error}", file=sys.stderr)
        return 1

    print("W: forward | S: backward | Space: stop | Q: stop and quit")
    try:
        while True:
            key = msvcrt.getwch().lower()
            if key == "q":
                manager.send_stop()
                return 0
            if key == "w":
                manager.send_drive(0.5)
            elif key == "s":
                manager.send_drive(-0.5)
            elif key == " ":
                manager.send_stop()
    except KeyboardInterrupt:
        manager.send_stop()
        return 0
    finally:
        manager.stop()


if __name__ == "__main__":
    raise SystemExit(main())
