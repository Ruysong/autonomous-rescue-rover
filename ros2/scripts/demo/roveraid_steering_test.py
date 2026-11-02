#!/usr/bin/env python3
"""RoverAid: PCA9685 + four MG996R servo steering calibration test.

LEGACY PCA9685 ONLY: this file does not match the current Pi-direct wiring.
For the current FL=18, FR=12, RL=13, RR=19 BCM GPIO assignments, use
scripts/demo/roveraid_steering_gpio_test.py instead. No PCA9685 is installed for that test.

Execute on the Raspberry Pi with I2C enabled:
    python3 scripts/demo/roveraid_steering_test.py FL center
    python3 scripts/demo/roveraid_steering_test.py FL test
    python3 scripts/demo/roveraid_steering_test.py FL release
    python3 scripts/demo/roveraid_steering_test.py ALL center  # Only after each axis is calibrated.

This intentionally DOES NOT command any DC drive motors.
"""

import argparse
import time
from smbus2 import SMBus

I2C_BUS = 1
PCA9685_ADDR = 0x40
PWM_HZ = 50

# EXAMPLE channel assignments. Verify actual wiring before powering servos.
CHANNEL = {"FL": 0, "FR": 1, "RL": 2, "RR": 3}

# Calibrate these values only after confirming safe mechanical travel.
# 1500 us is an initial servo reference, NOT a guarantee that wheels are straight.
CENTER_US = {"FL": 1500, "FR": 1500, "RL": 1500, "RR": 1500}
DIRECTION = {"FL": +1, "FR": +1, "RL": +1, "RR": +1}
TEST_DELTA_US = 50  # Deliberately small for the first test.
MIN_PULSE_US, MAX_PULSE_US = 1300, 1700  # Conservative initial test window.

MODE1 = 0x00
MODE2 = 0x01
LED0_ON_L = 0x06
ALL_LED_OFF_H = 0xFD
PRE_SCALE = 0xFE


class PCA9685:
    def __init__(self):
        self.bus = SMBus(I2C_BUS)
        try:
            # Disable all channels before setting up the PWM frequency.
            self.bus.write_byte_data(PCA9685_ADDR, ALL_LED_OFF_H, 0x10)
            oldmode = self.bus.read_byte_data(PCA9685_ADDR, MODE1)
            self.bus.write_byte_data(PCA9685_ADDR, MODE1, (oldmode & 0x7F) | 0x10)
            prescale = round(25_000_000 / (4096 * PWM_HZ)) - 1
            self.bus.write_byte_data(PCA9685_ADDR, PRE_SCALE, prescale)
            self.bus.write_byte_data(PCA9685_ADDR, MODE2, 0x04)
            self.bus.write_byte_data(PCA9685_ADDR, MODE1, oldmode & ~0x10 & 0x7F)
            time.sleep(0.005)
            self.bus.write_byte_data(PCA9685_ADDR, MODE1, (oldmode & ~0x10) | 0xA0)
            # Off for every channel, so clearing global-off cannot start stale pulses.
            for ch in range(16):
                self.release(ch)
            self.bus.write_byte_data(PCA9685_ADDR, ALL_LED_OFF_H, 0x00)
        except Exception:
            self.bus.close()
            raise

    def set_pulse(self, channel: int, pulse_us: int) -> None:
        if not MIN_PULSE_US <= pulse_us <= MAX_PULSE_US:
            raise ValueError(f"Pulse {pulse_us} us outside initial safety range")
        off_count = round(pulse_us * PWM_HZ * 4096 / 1_000_000)
        self.bus.write_i2c_block_data(
            PCA9685_ADDR, LED0_ON_L + 4 * channel,
            [0x00, 0x00, off_count & 0xFF, (off_count >> 8) & 0x0F]
        )

    def release(self, channel: int) -> None:
        """Stop sending PWM: servo will stop actively holding its position."""
        self.bus.write_i2c_block_data(
            PCA9685_ADDR, LED0_ON_L + 4 * channel, [0, 0, 0, 0x10]
        )

    def close(self) -> None:
        self.bus.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="RoverAid steering servo calibration")
    parser.add_argument("wheel", choices=(*CHANNEL, "ALL"), help="FL, FR, RL, RR, ALL")
    parser.add_argument("mode", choices=("center", "test", "release"))
    args = parser.parse_args()

    wheel = args.wheel
    if wheel == "ALL":
        if args.mode != "center":
            parser.error("ALL is supported only with center mode")
        driver = PCA9685()
        try:
            for part, ch in CHANNEL.items():
                print(f"Centering {part} (channel {ch}) at {CENTER_US[part]} us")
                driver.set_pulse(ch, CENTER_US[part])
                time.sleep(0.8)
            print("All four steering axes commanded to configured centers.")
        finally:
            driver.close()
        return

    ch = CHANNEL[wheel]
    center = CENTER_US[wheel]
    print(f"wheel={wheel}, channel={ch}, center={center} us, mode={args.mode}")
    driver = PCA9685()
    try:
        if args.mode == "release":
            driver.release(ch)
            print("PWM released (servo no longer holds position).")
            return

        driver.set_pulse(ch, center)
        print("Center commanded; inspect wheel orientation.")
        time.sleep(1.5)

        if args.mode == "test":
            try:
                for delta in (+TEST_DELTA_US, 0, -TEST_DELTA_US, 0):
                    pulse = center + DIRECTION[wheel] * delta
                    driver.set_pulse(ch, pulse)
                    print(f"  logical delta={delta:+d} us; pulse={pulse} us")
                    time.sleep(1.5)
            finally:
                driver.set_pulse(ch, center)
        print("Finished at configured center. PWM is still active to hold it.")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
