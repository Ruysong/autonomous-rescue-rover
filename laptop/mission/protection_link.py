"""Send independently of OpenCV rendering and mapping, using a fresh main-loop lease."""
from threading import Event, Lock, Thread
import time

from laptop.communication.messages import make_protection


class ProtectionLink:
    def __init__(self, send, interval=0.12, lease=0.35):
        self.send = send
        self.interval, self.lease = interval, lease
        self.current = None
        self.lock = Lock()
        self.closed = Event()
        self.worker = Thread(target=self.run, daemon=True, name="protection-link")

    def start(self):
        self.worker.start()

    def publish(self, hold, limit, *, immediate=False):
        with self.lock:
            self.current = (time.monotonic(), make_protection(hold, limit))
            if immediate:
                return self.send(self.current[1])
        return True

    def run(self):
        while not self.closed.wait(self.interval):
            with self.lock:
                current = self.current
                if current and time.monotonic() - current[0] < self.lease:
                    self.send(current[1])

    def close(self):
        self.closed.set()
        self.worker.join(timeout=1)
