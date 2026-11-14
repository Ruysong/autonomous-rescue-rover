"""JSON message helpers for Robot Communication Protocol v1.1 (v1.0 still accepted)."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

PROTOCOL_VERSION = "1.1"
SUPPORTED_VERSIONS = {"1.0", "1.1"}
V11_TYPES = {"motion", "set_mode", "estop", "resume"}
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

    if message_type in V11_TYPES and version != "1.1":
        raise ProtocolError(f"{message_type} needs protocol 1.1")

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
