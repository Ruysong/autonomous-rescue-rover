"""Laptop listeners for command and telemetry TCP channels."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from typing import Any

from .framing import FramingError, recv_framed, send_framed
from .messages import ProtocolError, decode_message, encode_message, make_drive, make_stop


class CommunicationManager:
    def __init__(
        self,
        bind_host: str,
        command_port: int,
        telemetry_port: int,
        on_telemetry: Callable[[dict[str, Any]], None],
    ) -> None:
        self._bind_host = bind_host
        self._command_port = command_port
        self._telemetry_port = telemetry_port
        self._on_telemetry = on_telemetry
        self._running = threading.Event()
        self._command_lock = threading.Lock()
        self._command_socket: socket.socket | None = None
        self._telemetry_socket: socket.socket | None = None
        self._listeners: list[socket.socket] = []
        self._threads: list[threading.Thread] = []

    @staticmethod
    def _listen(host: str, port: int) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, port))
            listener.listen(1)
            listener.settimeout(0.5)
        except OSError:
            listener.close()
            raise
        return listener

    @staticmethod
    def _close(sock: socket.socket | None) -> None:
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def start(self) -> None:
        command_listener = self._listen(self._bind_host, self._command_port)
        try:
            telemetry_listener = self._listen(self._bind_host, self._telemetry_port)
        except OSError:
            command_listener.close()
            raise

        self._listeners = [command_listener, telemetry_listener]
        self._running.set()
        self._threads = [
            threading.Thread(target=self._accept_commands, args=(command_listener,), daemon=True),
            threading.Thread(target=self._accept_telemetry, args=(telemetry_listener,), daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        print(f"[Command] Listening on {self._bind_host}:{self._command_port}")
        print(f"[Telemetry] Listening on {self._bind_host}:{self._telemetry_port}")

    def stop(self) -> None:
        self._running.clear()
        for listener in self._listeners:
            listener.close()
        with self._command_lock:
            self._close(self._command_socket)
            self._command_socket = None
        self._close(self._telemetry_socket)
        for thread in self._threads:
            thread.join(timeout=1.0)

    def _accept_commands(self, listener: socket.socket) -> None:
        while self._running.is_set():
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with self._command_lock:
                self._close(self._command_socket)
                self._command_socket = connection
            print(f"[Command] Pi connected from {address[0]}")

    def _accept_telemetry(self, listener: socket.socket) -> None:
        while self._running.is_set():
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            self._telemetry_socket = connection
            print(f"[Telemetry] Pi connected from {address[0]}")
            try:
                while self._running.is_set():
                    payload = recv_framed(connection)
                    if payload is None:
                        break
                    try:
                        message = decode_message(payload)
                        if message["type"] != "telemetry":
                            raise ProtocolError("Expected telemetry message")
                    except ProtocolError as error:
                        print(f"[Telemetry] Rejected: {error}")
                        continue
                    self._on_telemetry(message)
            except (OSError, FramingError) as error:
                if self._running.is_set():
                    print(f"[Telemetry] Connection lost: {error}")
            finally:
                self._close(connection)
                self._telemetry_socket = None
                if self._running.is_set():
                    print("[Telemetry] Waiting for Pi to reconnect")

    def _send(self, message: dict[str, object]) -> bool:
        payload = encode_message(message)
        with self._command_lock:
            if self._command_socket is None:
                print("[Command] Pi is not connected")
                return False
            try:
                send_framed(self._command_socket, payload)
            except OSError as error:
                print(f"[Command] Send failed: {error}")
                self._close(self._command_socket)
                self._command_socket = None
                return False
        return True

    def send_drive(self, throttle: float, steer: float = 0.0) -> bool:
        return self._send(make_drive(throttle, steer))

    def send_stop(self) -> bool:
        return self._send(make_stop())
