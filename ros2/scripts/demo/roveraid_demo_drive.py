#!/usr/bin/env python3
"""D-02: bounded Pi-direct MDD10A/steering trials, defaulting to dry-run.

No ROS, Pico, TCP, or background service. See docs/demo_drive.md before hardware
use. Duty is not speed; software timeouts are not an independent emergency stop.
"""

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import signal
import sys
import time
import tomllib
import traceback
from types import ModuleType
from typing import Any


# Initial trial bounds, not measured mechanical/electrical safety limits.
MAX_DUTY_PCT = 20.0
MAX_DURATION_S = 3.0
MIN_PULSE_US = 1300
MAX_PULSE_US = 1700
MAX_OFFSET_US = 100
POLL_S = 0.02
MOTOR_PINS = {'left': (5, 6), 'right': (16, 20)}
SERVO_PINS = {'FL': 18, 'FR': 12, 'RL': 13, 'RR': 19}
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'demo_drive.toml'


@dataclass(frozen=True)
class Motor:
    pwm_bcm: int
    dir_bcm: int
    forward_level: int


@dataclass(frozen=True)
class Servo:
    gpio_bcm: int
    center_us: int


@dataclass(frozen=True)
class Settings:
    motors: dict[str, Motor]
    servos: dict[str, Servo]
    pwm_hz: int
    servo_hz: int
    settle_s: float
    steering_calibrated: bool


@dataclass(frozen=True)
class Plan:
    action: str
    duration_s: float
    duty_pct: float
    motor_levels: dict[str, int | None]
    servo_pulses: dict[str, int]
    settle_s: float


def table(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    """Reject missing/unknown settings instead of silently ignoring typos."""
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f'{name}: expected exactly {sorted(keys)}')
    return value


def integer(value: Any, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'{name}: expected integer {lower}..{upper}')
    return value


def finite(value: Any, name: str, lower: float, upper: float) -> float:
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not lower <= value <= upper):
        raise ValueError(f'{name}: expected finite number {lower}..{upper}')
    return float(value)


def load_settings(path: Path) -> Settings:
    with path.open('rb') as stream:
        data = tomllib.load(stream)
    table(data, {'motor', 'steering'}, 'config')
    motor = table(data['motor'], {'pwm_hz', *MOTOR_PINS}, 'motor')
    steering = table(data['steering'],
                     {'frequency_hz', 'settle_s', 'calibrated', *SERVO_PINS},
                     'steering')
    motors = {}
    for side, pins in MOTOR_PINS.items():
        item = table(motor[side], {'pwm_bcm', 'dir_bcm', 'forward_level'}, side)
        pwm = integer(item['pwm_bcm'], f'{side}.pwm_bcm', 0, 27)
        direction = integer(item['dir_bcm'], f'{side}.dir_bcm', 0, 27)
        if (pwm, direction) != pins:
            raise ValueError(f'{side}: confirmed BCM PWM/DIR pins are {pins}')
        motors[side] = Motor(pwm, direction,
                             integer(item['forward_level'],
                                     f'{side}.forward_level', -1, 1))
    servos = {}
    for wheel, pin in SERVO_PINS.items():
        item = table(steering[wheel], {'gpio_bcm', 'center_us'}, wheel)
        gpio = integer(item['gpio_bcm'], f'{wheel}.gpio_bcm', 0, 27)
        if gpio != pin:
            raise ValueError(f'{wheel}: confirmed BCM pin is {pin}')
        servos[wheel] = Servo(gpio, integer(item['center_us'],
                                           f'{wheel}.center_us',
                                           MIN_PULSE_US, MAX_PULSE_US))
    if type(steering['calibrated']) is not bool:
        raise ValueError('steering.calibrated must be true or false')
    return Settings(
        motors, servos, integer(motor['pwm_hz'], 'pwm_hz', 100, 10000),
        integer(steering['frequency_hz'], 'frequency_hz', 50, 50),
        finite(steering['settle_s'], 'settle_s', 0.3, 2.0),
        steering['calibrated'],
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('servo', 'motor', 'drive', 'stop'))
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--chip', type=int, default=0, help='Pi 4 gpiochip index')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='default; no GPIO import')
    mode.add_argument('--arm', action='store_true',
                      help='confirm wiring, power, GND, free travel and external cutoff')
    parser.add_argument('--wheel', choices=(*SERVO_PINS, 'ALL'),
                        help='servo action: select one wheel or ALL')
    parser.add_argument('--pulse', action='append', default=[], metavar='FL=1500',
                        help='servo/drive: raw pulse in us; repeat per wheel')
    parser.add_argument('--side', choices=tuple(MOTOR_PINS), help='motor action only')
    parser.add_argument('--dir-level', type=int, choices=(0, 1),
                        help='motor action: raw DIR level, not a forward claim')
    parser.add_argument('--direction', choices=('forward', 'reverse'),
                        help='drive action only; defaults to forward')
    parser.add_argument('--duty', type=float, help='motor/drive: percent, default 10')
    parser.add_argument('--seconds', type=float, help='trial duration, default 0.5')
    return parser


def build_plan(args: argparse.Namespace, settings: Settings) -> Plan:
    if args.chip < 0:
        raise ValueError('--chip must be non-negative')
    for flag, value, actions in (
        ('--wheel', args.wheel, ('servo',)),
        ('--pulse', args.pulse or None, ('servo', 'drive')),
        ('--side', args.side, ('motor',)),
        ('--dir-level', args.dir_level, ('motor',)),
        ('--direction', args.direction, ('drive',)),
        ('--duty', args.duty, ('motor', 'drive')),
        ('--seconds', args.seconds, ('servo', 'motor', 'drive')),
    ):
        if value is not None and args.action not in actions:
            raise ValueError(f'{flag} is not used by {args.action}')
    duration_s = finite(args.seconds if args.seconds is not None else 0.5,
                        '--seconds', 0.1, MAX_DURATION_S)
    duty_pct = finite(args.duty if args.duty is not None else 10,
                      '--duty', 0.1, MAX_DUTY_PCT)
    levels: dict[str, int | None] = {}
    wheels: list[str] = []
    if args.action == 'motor':
        if args.side is None or args.dir_level is None:
            raise ValueError('motor requires --side and --dir-level')
        levels[args.side] = args.dir_level
    elif args.action == 'drive':
        for side, motor in settings.motors.items():
            levels[side] = (None if motor.forward_level == -1 else
                            motor.forward_level ^ (args.direction == 'reverse'))
        wheels = list(settings.servos)
    elif args.action == 'servo':
        if args.wheel is None:
            raise ValueError('servo requires --wheel FL/FR/RL/RR/ALL')
        wheels = list(settings.servos) if args.wheel == 'ALL' else [args.wheel]
    pulses = {wheel: settings.servos[wheel].center_us for wheel in wheels}
    seen = set()
    for assignment in args.pulse:
        wheel, separator, raw = assignment.partition('=')
        if not separator or wheel not in pulses or wheel in seen:
            raise ValueError('--pulse needs a unique selected wheel, e.g. FL=1500')
        pulse_us = integer(int(raw), f'{wheel} pulse', MIN_PULSE_US, MAX_PULSE_US)
        if abs(pulse_us - settings.servos[wheel].center_us) > MAX_OFFSET_US:
            raise ValueError(f'{wheel}: pulse must be within 100us of configured center')
        pulses[wheel] = pulse_us
        seen.add(wheel)
    return Plan(args.action, duration_s, duty_pct, levels, pulses,
                settings.settle_s if args.action == 'drive' else 0.0)


def require_calibration(plan: Plan, settings: Settings) -> None:
    if plan.action == 'drive':
        if any(level is None for level in plan.motor_levels.values()):
            raise ValueError('drive needs both motor.forward_level values set to 0/1 '
                             'after individual lifted-wheel tests')
        if not settings.steering_calibrated:
            raise ValueError('drive needs steering.calibrated=true after checking '
                             'all centers and requested travel')


class GPIO:
    """Local lgpio backend; import/open occurs only after CLI validation and arm."""

    def __init__(self, driver: ModuleType, settings: Settings, chip: int):
        self.driver = driver
        self.settings = settings
        self.chip = chip
        self.handle: int | None = None
        self.claimed: list[int] = []

    def call(self, name: str, *args: Any) -> int:
        result = getattr(self.driver, name)(*args)
        if not isinstance(result, int) or result < 0:
            raise RuntimeError(f'lgpio.{name}{args}: status {result}')
        return result

    def open(self, wheels: dict[str, int]) -> None:
        self.handle = self.call('gpiochip_open', self.chip)
        # Hold BOTH PWM inputs LOW before claiming DIR or steering outputs.
        pins = [motor.pwm_bcm for motor in self.settings.motors.values()]
        pins += [motor.dir_bcm for motor in self.settings.motors.values()]
        pins += [self.settings.servos[wheel].gpio_bcm for wheel in wheels]
        for pin in pins:
            self.call('gpio_claim_output', self.handle, pin, 0)
            self.claimed.append(pin)

    def motor(self, side: str, level: int, duty_pct: float, deadline_s: float,
              clock: Callable[[], float]) -> None:
        motor = self.settings.motors[side]
        self.call('gpio_write', self.handle, motor.dir_bcm, level)
        # A delayed DIR write must not renew/extend the original motor lease.
        cycles = math.floor((deadline_s - clock()) * self.settings.pwm_hz)
        if cycles < 1:
            raise TimeoutError('deadline expired before motor PWM submission')
        self.call('tx_pwm', self.handle, motor.pwm_bcm,
                  self.settings.pwm_hz, duty_pct, 0, cycles)

    def servo(self, wheel: str, pulse_us: int, remaining_s: float) -> None:
        cycles = max(1, math.floor(remaining_s * self.settings.servo_hz))
        self.call('tx_servo', self.handle, self.settings.servos[wheel].gpio_bcm,
                  pulse_us, self.settings.servo_hz, 0, cycles)

    def close(self) -> None:
        """Try every stop/free/close even when another operation fails."""
        if self.handle is None:
            return
        errors = []

        def attempt(name: str, *args: Any) -> None:
            try:
                self.call(name, *args)
            except Exception as exc:
                errors.append(exc)

        for motor in self.settings.motors.values():
            if motor.pwm_bcm in self.claimed:
                attempt('tx_pwm', self.handle, motor.pwm_bcm, 0, 0)
                attempt('gpio_write', self.handle, motor.pwm_bcm, 0)
        for servo in self.settings.servos.values():
            if servo.gpio_bcm in self.claimed:
                attempt('tx_servo', self.handle, servo.gpio_bcm, 0)
                attempt('gpio_write', self.handle, servo.gpio_bcm, 0)
        for pin in reversed(self.claimed):
            attempt('gpio_free', self.handle, pin)
        attempt('gpiochip_close', self.handle)
        self.claimed.clear()
        self.handle = None
        if errors:
            raise ExceptionGroup('GPIO cleanup failed; use external motor cutoff', errors)


def wait_until(deadline_s: float, clock: Callable[[], float],
               sleep: Callable[[float], None]) -> None:
    while (remaining_s := deadline_s - clock()) > 0:
        sleep(min(POLL_S, remaining_s))


def run_plan(plan: Plan, settings: Settings, gpio: GPIO,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> None:
    require_calibration(plan, settings)
    try:
        gpio.open(plan.servo_pulses)
        if plan.action == 'stop':
            return
        start_s = clock()
        servo_deadline_s = start_s + plan.settle_s + plan.duration_s
        for wheel, pulse_us in plan.servo_pulses.items():
            remaining_s = servo_deadline_s - clock()
            if remaining_s <= 0:
                raise TimeoutError('deadline expired before servo output')
            gpio.servo(wheel, pulse_us, remaining_s)
        if plan.settle_s:
            wait_until(start_s + plan.settle_s, clock, sleep)
        # Motor lease begins after steering, but setup delays cannot extend it.
        deadline_s = start_s + plan.settle_s + plan.duration_s
        for side, level in plan.motor_levels.items():
            remaining_s = deadline_s - clock()
            if remaining_s <= 0:
                raise TimeoutError('deadline expired before motor output')
            gpio.motor(side, int(level), plan.duty_pct, deadline_s, clock)
        wait_until(deadline_s, clock, sleep)
    finally:
        gpio.close()


class StopRequested(Exception):
    def __init__(self, signum: int):
        super().__init__(f'stop requested by signal {signum}')
        self.signum = signum


@contextmanager
def stop_signals() -> Iterator[None]:
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {}

    def stop(signum: int, frame: Any) -> None:
        # A repeated termination signal must not interrupt the cleanup below.
        for item in signals:
            signal.signal(item, signal.SIG_IGN)
        raise StopRequested(signum)

    try:
        for item in signals:
            previous[item] = signal.signal(item, stop)
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


def print_plan(plan: Plan, settings: Settings) -> None:
    if plan.action == 'stop':
        print('D-02 stop: request both motor PWM inputs LOW (no servo output).')
    else:
        print(f'D-02 {plan.action}: {plan.duration_s:g}s')
    if plan.motor_levels:
        print(f'  motor duty {plan.duty_pct:g}% (cap {MAX_DUTY_PCT:g}%), '
              f'PWM {settings.pwm_hz}Hz')
    print('Motor stop output: PWM1 BCM5/pin29 LOW, PWM2 BCM16/pin36 LOW.')
    for side, level in plan.motor_levels.items():
        motor = settings.motors[side]
        print(f'  {side}: PWM BCM{motor.pwm_bcm}, DIR BCM{motor.dir_bcm}='
              f'{level if level is not None else "UNSET (calibrate first)"}')
    for wheel, pulse_us in plan.servo_pulses.items():
        print(f'  {wheel}: BCM{settings.servos[wheel].gpio_bcm}, '
              f'{pulse_us}us at {settings.servo_hz}Hz')
    if plan.settle_s:
        print(f'  steering pre-position delay: {plan.settle_s:g}s; '
              f'calibrated={settings.steering_calibrated}')
    print('End: attempt both motor stops, release selected servo pulses, close GPIO.')


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        plan = build_plan(args, settings)
        if args.arm:
            require_calibration(plan, settings)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print_plan(plan, settings)
    if not args.arm:
        print('DRY RUN: no GPIO imported or accessed. '
              'Uncalibrated settings are not hardware-ready.')
        return 0
    try:
        import lgpio
    except ImportError:
        print('ERROR: lgpio is missing; see docs/demo_drive.md for Pi prerequisites.',
              file=sys.stderr)
        return 1
    try:
        with stop_signals():
            run_plan(plan, settings, GPIO(lgpio, settings, args.chip))
    except StopRequested as exc:
        print(f'{exc}; cleanup attempted.', file=sys.stderr)
        return 128 + exc.signum
    except KeyboardInterrupt:
        print('Interrupted; cleanup attempted.', file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        return 1
    print('Trial ended; stop outputs sent. Physical stopping is not verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
