"""Minimal Windows keyboard client for the rover TCP server."""

import argparse
import os
import socket
import sys


DEFAULT_HOST = os.environ.get("ROVER_PI_HOST", "192.168.50.2")
DEFAULT_PORT = 9998


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send basic drive commands to the rover.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Raspberry Pi IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Raspberry Pi TCP port")
    return parser.parse_args()


def main() -> int:
    if os.name != "nt":
        print("This initial keyboard client is intended for Windows.", file=sys.stderr)
        return 1

    import msvcrt

    args = parse_args()
    commands = {
        "w": "FORWARD",
        "s": "BACKWARD",
        " ": "STOP",
    }

    try:
        with socket.create_connection((args.host, args.port), timeout=5) as client:
            print(f"Connected to rover at {args.host}:{args.port}")
            print("W: forward | S: backward | Space: stop | Q: quit")

            while True:
                key = msvcrt.getwch().lower()

                if key == "q":
                    client.sendall(b"STOP\n")
                    print("\nSent: STOP")
                    return 0

                command = commands.get(key)
                if command is None:
                    continue

                client.sendall(f"{command}\n".encode("utf-8"))
                print(f"\nSent: {command}")
    except OSError as error:
        print(f"Connection failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
