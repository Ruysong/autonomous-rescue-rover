"""Length-prefixed TCP framing for Robot Communication Protocol v1."""

from __future__ import annotations

import socket
import struct

MAX_FRAME_SIZE = 64 * 1024


class FramingError(RuntimeError):
    """Raised when a peer violates the TCP frame contract."""


def send_framed(sock: socket.socket, payload: bytes) -> None:
    if len(payload) > MAX_FRAME_SIZE:
        raise FramingError(f"Frame exceeds {MAX_FRAME_SIZE} bytes")

    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_exact(sock: socket.socket, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size

    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            if not chunks:
                return None
            raise FramingError("Connection closed during a frame")

        chunks.append(chunk)
        remaining -= len(chunk)

    return b"".join(chunks)


def recv_framed(sock: socket.socket) -> bytes | None:
    header = recv_exact(sock, 4)
    if header is None:
        return None

    (payload_size,) = struct.unpack("!I", header)
    if payload_size > MAX_FRAME_SIZE:
        raise FramingError(f"Peer declared an oversized frame: {payload_size} bytes")

    return recv_exact(sock, payload_size)
