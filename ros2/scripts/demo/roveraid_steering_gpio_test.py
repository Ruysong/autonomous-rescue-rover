#!/usr/bin/env python3
"""RoverAid: ONE hobby steering servo calibration via Raspberry Pi 4 GPIO.

For Ubuntu 24.04 / Raspberry Pi 4 with python3-lgpio installed.
The steering SIGNAL wires connect directly to Pi GPIO; no PCA9685 is used.
NO motor power is supplied by a GPIO. Use an appropriate EXTERNAL servo supply,
with shared ground between Raspberry Pi and the supply.

User-provided wiring (BCM GPIO / physical header pin):
    FL: 18 / 12, FR: 12 / 32, RL: 13 / 33, RR: 19 / 35.
The wheel selects its GPIO. Optional --gpio must match that wheel's assignment.
Verify actual SIGNAL wiring before touching hardware.

Examples (only when wiring is verified and servo horn/linkage is detached):
    python3 scripts/demo/roveraid_steering_gpio_test.py FL center --arm
    python3 scripts/demo/roveraid_steering_gpio_test.py FR test --arm
    python3 scripts/demo/roveraid_steering_gpio_test.py RL test --invert --arm
    python3 scripts/demo/roveraid_steering_gpio_test.py RR hold --arm
    python3 scripts/demo/roveraid_steering_gpio_test.py FL test --gpio 18 --dry-run

All pulses end when the program exits; holding torque is not maintained.
This file deliberately DOES NOT command any DC drive motors.
"""

import argparse
import time

MIN_PULSE_US = 1300
MAX_PULSE_US = 1700
DEFAULT_CENTER_US = 1500
DEFAULT_STEP_US = 50
PWM_FREQUENCY_HZ = 50

# Values are (BCM GPIO number, physical header pin), not PCA9685 channels.
WHEEL_PINS: dict[str, tuple[int, int]] = {
    'FL': (18, 12),
    'FR': (12, 32),
    'RL': (13, 33),
    'RR': (19, 35),
}


def build_sequence(
    mode: str, center: int, step: int, invert: bool,
) -> list[tuple[str, int]]:
    if mode == 'center' or mode == 'hold':
        return [("center", center)]
    direction = -1 if invert else 1
    return [
        ("center", center),
        ("positive delta", center + direction * step),
        ("center", center),
        ("negative delta", center - direction * step),
        ("center", center),
    ]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description='Test one steering SERVO attached by its SIGNAL wire to Pi GPIO.'
    )
    parser.add_argument('wheel', choices=tuple(WHEEL_PINS))
    parser.add_argument('mode', choices=('center', 'test', 'hold'))
    parser.add_argument('--gpio', type=int,
                        help='Optional BCM GPIO check; must match the selected wheel. '
                             'Defaults: FL=18, FR=12, RL=13, RR=19. '
                             'Do not pass the physical header pin number.')
    parser.add_argument('--chip', type=int, default=0,
                        help='Linux gpiochip number; Pi 4 commonly uses 0')
    parser.add_argument('--center-us', type=int, default=DEFAULT_CENTER_US)
    parser.add_argument('--step-us', type=int, default=DEFAULT_STEP_US)
    parser.add_argument('--invert', action='store_true',
                        help='Reverse the sign of the test displacement')
    parser.add_argument('--pause', type=float, default=1.5,
                        help='Seconds per step in center/test mode (default: 1.5)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print planned pulses without accessing GPIO')
    parser.add_argument('--arm', action='store_true',
                        help='Acknowledge hardware/power/ground/linkage safety checks')
    args = parser.parse_args(argv)

    gpio_bcm, physical_pin = WHEEL_PINS[args.wheel]
    if args.gpio is not None and args.gpio != gpio_bcm:
        parser.error(f'{args.wheel} is assigned BCM GPIO {gpio_bcm} '
                     f'(physical header pin {physical_pin}); '
                     f'--gpio {args.gpio} does not match. '
                     'Use the wheel assignment, not a physical pin number.')
    args.gpio = gpio_bcm
    if not MIN_PULSE_US <= args.center_us <= MAX_PULSE_US:
        parser.error(f'--center-us must be {MIN_PULSE_US}..{MAX_PULSE_US}')
    if not 1 <= args.step_us <= 100:
        parser.error('--step-us must be 1..100 us')
    if not 0.3 <= args.pause <= 10:
        parser.error('--pause must be 0.3..10 seconds')

    sequence = build_sequence(args.mode, args.center_us, args.step_us, args.invert)
    for label, pulse in sequence:
        if not MIN_PULSE_US <= pulse <= MAX_PULSE_US:
            parser.error(f'{label}: {pulse} us outside test window '
                         f'{MIN_PULSE_US}..{MAX_PULSE_US}; reduce step or center')

    print(f'Wheel {args.wheel} | gpiochip{args.chip}, BCM GPIO {args.gpio} '
          f'(physical header pin {physical_pin}) '
          f'| {args.mode} | {PWM_FREQUENCY_HZ} Hz')
    for label, pulse in sequence:
        print(f'  {label:>15}: {pulse} us')

    if args.dry_run:
        print('DRY RUN: no GPIO accessed.')
        return
    if not args.arm:
        parser.error('Refusing to send PWM until --arm is provided after '
                     'confirming servo SIGNAL wiring, external power, common GND, '
                     'wheels elevated, and free steering travel.')

    try:
        import lgpio
    except ImportError as exc:
        raise SystemExit('lgpio missing: sudo apt install python3-lgpio') from exc

    handle = None
    claimed = False
    try:
        handle = lgpio.gpiochip_open(args.chip)
        lgpio.gpio_claim_output(handle, args.gpio, 0)
        claimed = True
        if args.mode == 'hold':
            pulse = args.center_us
            lgpio.tx_servo(handle, args.gpio, pulse, PWM_FREQUENCY_HZ)
            print('Holding commanded center while this process runs. '
                  'Ctrl+C to stop PWM (servo will not hold after exit).')
            while True:
                time.sleep(0.5)
        else:
            for label, pulse in sequence:
                print(f'Sending {label}: {pulse} us')
                lgpio.tx_servo(handle, args.gpio, pulse, PWM_FREQUENCY_HZ)
                time.sleep(args.pause)
            print('Sequence complete. Releasing PWM output.')
    except KeyboardInterrupt:
        print('\nInterrupted. Releasing PWM output.')
    finally:
        if handle is not None:
            if claimed:
                try:
                    lgpio.tx_servo(handle, args.gpio, 0)  # 0 us stops PWM.
                    time.sleep(0.05)
                    lgpio.gpio_write(handle, args.gpio, 0)
                finally:
                    lgpio.gpio_free(handle, args.gpio)
            lgpio.gpiochip_close(handle)


if __name__ == '__main__':
    main()
