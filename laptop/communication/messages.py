"""JSON message helpers for Robot Communication Protocol v1."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

PROTOCOL_VERSION = "1.0"


class ProtocolError(ValueError):
    """Raised when a protocol message is malformed or unsupported."""


def _finite_in_range(name: str, value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ProtocolError(f"{name} must be a finite number in [-1.0, 1.0]") from error

    if not math.isfinite(number) or not -1.0 <= number <= 1.0:
        raise ProtocolError(f"{name} must be a finite number in [-1.0, 1.0]")

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

    if message.get("_version") != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported protocol version")

    message_type = message.get("type")
    if not isinstance(message_type, str):
        raise ProtocolError("Message type must be a string")

    if message_type == "drive":
        _finite_in_range("throttle", message.get("throttle"))
        _finite_in_range("steer", message.get("steer"))
    elif message_type == "stop":
        return
