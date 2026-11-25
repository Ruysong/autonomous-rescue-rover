"""JSON message helpers for Robot Communication Protocol v1.2 (v1.0 / v1.1 still accepted)."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

PROTOCOL_VERSION = "1.2"
SUPPORTED_VERSIONS = {"1.0", "1.1", "1.2"}
V11_TYPES = {"motion", "set_mode", "estop", "resume"}
V12_TYPES = {"protection", "path", "pose_correction", "scan"}
STEER_MODES = {"normal", "spin"}
CONTROL_MODES = {"manual", "autonomous"}


class ProtocolError(ValueError):
    """Raised when a protocol message is malformed or unsupported."""


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ProtocolError(f"{name} must be a finite number") from error

    if not math.isfinite(number):
        raise ProtocolError(f"{name} must be a finite number")

    return number


def _finite_in_range(name: str, value: object) -> float:
    number = _finite_number(name, value)
    if not -1.0 <= number <= 1.0:
        raise ProtocolError(f"{name} must be in [-1.0, 1.0]")

    return number


def make_drive(throttle: float, steer: float) -> dict[str, object]:
    return {
        "_version": PROTOCOL_VERSION,
        "type": "drive",
        "throttle": _finite_in_range("throttle", throttle),
        "steer": _finite_in_range("steer", steer),
    }


def make_stop() -> dict[str, str]:
    return {
        "_version": PROTOCOL_VERSION,
        "type": "stop",
    }


def _bounded(name: str, value: object, bound: float) -> float:
    number = _finite_number(name, value)
    if abs(number) > bound:
        raise ProtocolError(f"{name} must be within +-{bound}")
    return number


def make_motion(
    v: float = 0.0,
    kappa: float = 0.0,
    wz: float = 0.0,
    steer_mode: str = "normal",
    source: str = "manual",
) -> dict[str, object]:
    """v [m/s] and kappa [1/m, + = left] for normal mode; wz [rad/s, + = CCW] for spin."""
    message = {
        "_version": PROTOCOL_VERSION,
        "type": "motion",
        "source": source,
        "steer_mode": steer_mode,
        "v": v,
        "kappa": kappa,
        "wz": wz,
    }
    validate_message(message)
    return message


def make_set_mode(mode: str) -> dict[str, str]:
    message = {"_version": PROTOCOL_VERSION, "type": "set_mode", "mode": mode}
    validate_message(message)
    return message


def make_estop() -> dict[str, str]:
    return {"_version": PROTOCOL_VERSION, "type": "estop"}


def make_resume() -> dict[str, str]:
    return {"_version": PROTOCOL_VERSION, "type": "resume"}


def make_path(points, path_id: int, speed: float = 0.0) -> dict[str, object]:
    """Map-frame waypoints [[x, y], ...] for the robot Pure Pursuit; [] cancels the path."""
    message = {
        "_version": PROTOCOL_VERSION,
        "type": "path",
        "id": int(path_id),
        "speed": float(speed),
        "points": [[round(float(x), 3), round(float(y), 3)] for x, y in points],
    }
    validate_message(message)
    return message


def make_pose_correction(x: float, y: float, yaw: float) -> dict[str, object]:
    """map <- odom transform from SLAM."""
    message = {"_version": PROTOCOL_VERSION, "type": "pose_correction",
               "x": float(x), "y": float(y), "yaw": float(yaw)}
    validate_message(message)
    return message


def encode_message(message: Mapping[str, Any]) -> bytes:
    validate_message(message)
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_message(payload: bytes) -> dict[str, Any]:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError(f"Invalid JSON payload: {error}") from error

    validate_message(message)
    return message


def validate_message(message: Mapping[str, Any]) -> None:
    if not isinstance(message, Mapping):
        raise ProtocolError("Message must be a JSON object")

    version = message.get("_version")
    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError("Unsupported protocol version")

    message_type = message.get("type")
    if not isinstance(message_type, str):
        raise ProtocolError("Message type must be a string")

    if message_type in V11_TYPES and version not in ("1.1", "1.2"):
        raise ProtocolError(f"{message_type} needs protocol 1.1")
    if message_type in V12_TYPES and version != "1.2":
        raise ProtocolError(f"{message_type} needs protocol 1.2")

    if message_type == "drive":
        _finite_in_range("throttle", message.get("throttle"))
        _finite_in_range("steer", message.get("steer"))
    elif message_type in ("stop", "estop", "resume"):
        return
    elif message_type == "motion":
        if message.get("source") not in CONTROL_MODES:
            raise ProtocolError("source must be manual or autonomous")
        if message.get("steer_mode") not in STEER_MODES:
            raise ProtocolError("steer_mode must be normal or spin")
        _bounded("v", message.get("v"), 2.0)
        _bounded("kappa", message.get("kappa", 0.0), 20.0)
        _bounded("wz", message.get("wz", 0.0), 5.0)
    elif message_type == "set_mode":
        if message.get("mode") not in CONTROL_MODES:
            raise ProtocolError("mode must be manual or autonomous")
    elif message_type == "protection":
        if type(message.get('hold')) is not bool: raise ProtocolError('hold must be boolean')
        speed = _finite_number('speed_limit',message.get('speed_limit'))
        if not 0 <= speed <= .5: raise ProtocolError('speed_limit must be 0..0.5')
    elif message_type == "path":
        _bounded("id", message.get("id"), 1e9)
        speed = _finite_number("speed", message.get("speed", 0.0))
        if not 0.0 <= speed <= 0.5:
            raise ProtocolError("speed must be in [0, 0.5]")
        points = message.get("points")
        if not isinstance(points, list) or len(points) > 2000:
            raise ProtocolError("points must be a list of [x, y] (max 2000)")
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ProtocolError("points must be a list of [x, y]")
            _bounded("x", point[0], 1000.0)
            _bounded("y", point[1], 1000.0)
    elif message_type == "pose_correction":
        _bounded("x", message.get("x"), 1000.0)
        _bounded("y", message.get("y"), 1000.0)
        _bounded("yaw", message.get("yaw"), 10.0)
    elif message_type == "scan":
        _finite_number("timestamp", message.get("timestamp"))
        pose = message.get("odom_pose")
        if not isinstance(pose, list) or len(pose) != 3:
            raise ProtocolError("odom_pose must be [x, y, yaw]")
        for value in pose:
            _finite_number("odom_pose", value)
        ranges = message.get("ranges_mm")
        if not isinstance(ranges, list) or len(ranges) != 360:
            raise ProtocolError("ranges_mm must have 360 values")
        if any(type(value) is not int or not 0 <= value <= 65535 for value in ranges):
            raise ProtocolError("ranges_mm must contain unsigned 16-bit millimeter values")
    elif message_type == "telemetry":
        _finite_number("timestamp", message.get("timestamp"))
        _finite_in_range("motor_speed", message.get("motor_speed"))

        if not isinstance(message.get("mode"), str):
            raise ProtocolError("mode must be a string")

        if not isinstance(message.get("safety_state"), str):
            raise ProtocolError("safety_state must be a string")

        last_command_age = _finite_number("last_command_age", message.get("last_command_age"))
        if last_command_age < 0.0:
            raise ProtocolError("last_command_age must be non-negative")
    else:
        raise ProtocolError(f"Unsupported message type: {message_type}")


def make_protection(hold=False, speed_limit=.5):
    message={'_version':'1.2','type':'protection','hold':hold,'speed_limit':speed_limit}
    validate_message(message)
    return message
