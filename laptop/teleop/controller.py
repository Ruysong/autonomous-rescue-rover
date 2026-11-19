"""Gamepad teleop logic (operator block, manual mode). No pygame here, so it is testable.

Layout (Xbox / XInput order; checked on a Flydigi pad in X360 mode, 11/14):

    LB (hold)        deadman. Released -> one stop, then silence (robot gate times out).
    left stick       one stick like a driving game (11/19): up / down = forward / reverse,
                     left / right = steering (normal: curvature, spin: turn rate). Diagonal = both.
    RT               speed boost: 30 % of max at rest, 100 % fully pulled
    A                steer mode normal (front and rear steer opposite; Ackermann on this rover)
    B                steer mode spin (refused unless the robot reports spin_enabled)
    Back             E-stop (latched on the robot)
    Start            E-stop latched -> resume; otherwise toggle manual / autonomous

The robot's Command Gate is the authority. This class only builds requests; it does not decide
whether the robot moves (timeouts, braking before a mode change, limits are all on the robot).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from laptop.communication.messages import (
    make_estop,
    make_motion,
    make_resume,
    make_set_mode,
    make_stop,
)

# Shown on the dashboard / Foxglove while in manual mode.
KEYMAP = (
    ("LB (hold)", "deadman - nothing moves unless held"),
    ("Left stick", "up/down = forward/reverse, left/right = steer"),
    ("RT", "boost: 30 % released .. 100 % pulled"),
    ("A / B", "steer mode normal / spin (spin off until measured)"),
    ("Back", "E-STOP (latched)"),
    ("Start", "resume after E-stop / manual <-> autonomous"),
)

# Button / axis indices (SDL XInput order).
AXIS_LX, AXIS_LY, AXIS_RX, AXIS_RY, AXIS_LT, AXIS_RT = range(6)
BTN_A, BTN_B, BTN_X, BTN_Y, BTN_LB, BTN_RB, BTN_BACK, BTN_START = range(8)


@dataclass(frozen=True)
class PadState:
    connected: bool = True
    axes: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, -1.0, -1.0)
    buttons: frozenset[int] = frozenset()

    def axis(self, index: int) -> float:
        return self.axes[index] if index < len(self.axes) else 0.0

    def pressed(self, button: int) -> bool:
        return button in self.buttons


@dataclass
class RobotLimits:
    """Filled from robot telemetry (limits.*); defaults match robot/config/rover.json."""

    max_speed: float = 0.144        # m/s (0.24 m/s x duty 0.6)
    max_curvature: float = 1.30     # 1/m at 30 deg corners
    spin_enabled: bool = False
    y_mid: float = 0.3125           # m, for spin turn rate

    def update_from_telemetry(self, telemetry: dict) -> None:
        limits = telemetry.get("limits")
        if not isinstance(limits, dict):
            return
        try:
            self.max_speed = float(limits.get("max_speed", self.max_speed))
            self.max_curvature = float(limits.get("max_curvature", self.max_curvature))
            self.spin_enabled = bool(limits.get("spin_enabled", self.spin_enabled))
        except (TypeError, ValueError):
            pass


@dataclass
class TeleopConfig:
    deadzone: float = 0.12
    base_speed_fraction: float = 0.3
    expo: float = 0.5   # 0 = linear stick, 1 = cubic (finer control near centre)


def shape(value: float, deadzone: float, expo: float) -> float:
    """Deadzone with rescale, then expo. Output in [-1, 1]."""
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    scaled = min(1.0, (magnitude - deadzone) / (1.0 - deadzone))
    scaled = (1.0 - expo) * scaled + expo * scaled ** 3
    return scaled if value > 0 else -scaled


def circle_to_square(x: float, y: float) -> tuple[float, float]:
    """A stick moves inside a circle, so a full diagonal reads about (0.71, 0.71). Stretch it so
    a full diagonal gives full speed AND full steering, like a driving game. Axes unchanged."""
    m = max(abs(x), abs(y))
    if m <= 1e-9:
        return 0.0, 0.0
    r = min(1.0, (x * x + y * y) ** 0.5)
    return x / m * r, y / m * r


@dataclass
class TeleopController:
    config: TeleopConfig = field(default_factory=TeleopConfig)
    limits: RobotLimits = field(default_factory=RobotLimits)
    steer_mode: str = "normal"
    control_mode: str = "manual"
    estop: bool = False
    driving: bool = False
    notice: str = ""
    _previous: frozenset[int] = frozenset()
    _was_connected: bool = True

    def _edge(self, pad: PadState, button: int) -> bool:
        return pad.pressed(button) and button not in self._previous

    def step(self, pad: PadState) -> list[dict]:
        """One control tick (call at about 20 Hz). Returns messages to send, in order."""
        out: list[dict] = []

        if not pad.connected:
            if self._was_connected or self.driving:
                out.append(make_stop())
                self.notice = "GAMEPAD DISCONNECTED"
            self._was_connected = False
            self.driving = False
            self._previous = frozenset()
            return out
        if not self._was_connected:
            self.notice = "gamepad reconnected"
        self._was_connected = True

        if self._edge(pad, BTN_BACK):
            self.estop = True
            self.driving = False
            self.notice = "E-STOP (Start to resume)"
            out.append(make_estop())
        elif self._edge(pad, BTN_START):
            if self.estop:
                self.estop = False
                self.notice = "resumed"
                out.append(make_resume())
            else:
                self.control_mode = "autonomous" if self.control_mode == "manual" else "manual"
                self.driving = False
                self.notice = f"control: {self.control_mode}"
                out.append(make_set_mode(self.control_mode))

        if self._edge(pad, BTN_A) and self.steer_mode != "normal":
            self.steer_mode = "normal"
            self.notice = "steer: normal"
        if self._edge(pad, BTN_B):
            if self.limits.spin_enabled:
                self.steer_mode = "spin"
                self.notice = "steer: spin"
            else:
                self.notice = "spin disabled on robot (steering range not measured)"

        self._previous = pad.buttons

        if self.estop or self.control_mode != "manual":
            return out

        if not pad.pressed(BTN_LB):
            if self.driving:
                out.append(make_stop())
                self.driving = False
            return out

        boost = (pad.axis(AXIS_RT) + 1.0) / 2.0  # trigger -1..1 -> 0..1
        base = self.config.base_speed_fraction
        speed_scale = base + (1.0 - base) * max(0.0, min(1.0, boost))
        lx, ly = circle_to_square(pad.axis(AXIS_LX), pad.axis(AXIS_LY))
        throttle = -shape(ly, self.config.deadzone, self.config.expo)
        steer = -shape(lx, self.config.deadzone, self.config.expo)  # right = -

        if self.steer_mode == "spin":
            wz_max = self.limits.max_speed / self.limits.y_mid
            out.append(make_motion(wz=steer * speed_scale * wz_max, steer_mode="spin"))
        else:
            out.append(make_motion(
                v=throttle * speed_scale * self.limits.max_speed,
                kappa=steer * self.limits.max_curvature,
                steer_mode="normal",
            ))
        self.driving = True
        return out
