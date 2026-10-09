import json
import socket
import struct
import time
import unittest

from laptop.communication.framing import FramingError, recv_framed, send_framed
from laptop.communication.manager import CommunicationManager
from laptop.communication.messages import ProtocolError, decode_message, make_drive, make_stop


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FramingTests(unittest.TestCase):
    def test_fragmented_header_and_payload(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        payload = json.dumps(make_drive(0.5, 0.0)).encode("utf-8")
        frame = struct.pack("!I", len(payload)) + payload
        for byte in frame:
            writer.sendall(bytes([byte]))
        self.assertEqual(recv_framed(reader), payload)

    def test_round_trip_and_truncated_payload(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        send_framed(writer, json.dumps(make_stop()).encode("utf-8"))
        self.assertEqual(decode_message(recv_framed(reader))["type"], "stop")
        writer.sendall(struct.pack("!I", 4) + b"x")
        writer.shutdown(socket.SHUT_WR)
        with self.assertRaises(FramingError):
            recv_framed(reader)

    def test_oversized_frame_rejected(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        writer.sendall(struct.pack("!I", 65537))
        with self.assertRaises(FramingError):
            recv_framed(reader)


class MessageTests(unittest.TestCase):
    def test_invalid_commands_rejected(self):
        invalid = [
            b"not JSON",
            b'{"_version":"1.0","type":"unknown"}',
            b'{"_version":"1.0","type":"drive","throttle":2,"steer":0}',
            b'{"_version":"1.0","type":"drive","throttle":true,"steer":0}',
            b'{"_version":"2.0","type":"stop"}',
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                decode_message(payload)

    def test_telemetry_validation(self):
        telemetry = {
            "_version": "1.0", "type": "telemetry", "timestamp": 123.0,
            "motor_speed": 0.5, "mode": "mock", "safety_state": "normal",
            "last_command_age": 0.2,
        }
        self.assertEqual(decode_message(json.dumps(telemetry).encode())["type"], "telemetry")
        telemetry["motor_speed"] = "0.5"
        with self.assertRaises(ProtocolError):
            decode_message(json.dumps(telemetry).encode())


class ManagerTests(unittest.TestCase):
    def test_command_telemetry_and_reconnect(self):
        received = []
        command_port, telemetry_port = free_port(), free_port()
        self.assertNotEqual(command_port, telemetry_port)
        manager = CommunicationManager(
            "127.0.0.1", command_port, telemetry_port, received.append
        )
        manager.start()
        self.addCleanup(manager.stop)

        def connect_command():
            connection = socket.create_connection(("127.0.0.1", command_port), timeout=2)
            connection.settimeout(2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with manager._command_lock:
                    accepted = manager._command_socket
                    current_port = accepted.getpeername()[1] if accepted is not None else None
                if current_port == connection.getsockname()[1]:
                    self.assertTrue(manager.send_drive(0.5))
                    return connection
                time.sleep(0.01)
            self.fail("Laptop did not accept Pi command connection")

        first = connect_command()
        self.assertEqual(decode_message(recv_framed(first))["type"], "drive")
        first.close()

        second = connect_command()
        self.addCleanup(second.close)
        self.assertEqual(decode_message(recv_framed(second))["type"], "drive")

        with socket.create_connection(("127.0.0.1", telemetry_port), timeout=2) as telemetry:
            payload = json.dumps({
                "_version": "1.0", "type": "telemetry", "timestamp": 123.0,
                "motor_speed": 0.5, "mode": "mock", "safety_state": "normal",
                "last_command_age": 0.1,
            }).encode()
            frame = struct.pack("!I", len(payload)) + payload
            for byte in frame:
                telemetry.sendall(bytes([byte]))
            deadline = time.monotonic() + 2
            while not received and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["motor_speed"], 0.5)


if __name__ == "__main__":
    unittest.main()
