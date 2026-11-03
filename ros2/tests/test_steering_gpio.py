"""Verify the Pi-direct test contract using a fake lgpio, never real GPIO."""

import builtins
from contextlib import redirect_stderr, redirect_stdout
import io
import sys
import unittest
from unittest.mock import Mock, patch

from scripts.demo import roveraid_steering_gpio_test as steering


class SteeringGPIOTest(unittest.TestCase):
    """Check user wiring, hardware gates, pulses and resource release."""

    wiring = {'FL': (18, 12), 'FR': (12, 32), 'RL': (13, 33), 'RR': (19, 35)}

    def invoke_without_gpio(self, argv: list[str]) -> tuple[str, str]:
        """Fail if a CLI-only path even attempts to import the GPIO driver."""
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == 'lgpio':
                raise AssertionError('CLI-only path attempted to import lgpio')
            return real_import(name, *args, **kwargs)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('builtins.__import__', side_effect=guarded_import):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                steering.main(argv)
        return stdout.getvalue(), stderr.getvalue()

    def fake_driver(self) -> Mock:
        """Expose only the driver calls used by this single-servo tool."""
        driver = Mock(spec=[
            'gpiochip_open', 'gpio_claim_output', 'tx_servo',
            'gpio_write', 'gpio_free', 'gpiochip_close',
        ])
        driver.gpiochip_open.return_value = 7
        return driver

    def test_default_and_explicit_matching_pins_dry_run(self):
        for wheel, (gpio, physical) in self.wiring.items():
            for explicit in ([], ['--gpio', str(gpio)]):
                with self.subTest(wheel=wheel, explicit=explicit):
                    output, _ = self.invoke_without_gpio(
                        [wheel, 'test', '--dry-run', *explicit])
                    self.assertIn(f'Wheel {wheel}', output)
                    self.assertIn(f'BCM GPIO {gpio} (physical header pin {physical})', output)
                    self.assertIn('DRY RUN: no GPIO accessed.', output)

    def test_mismatched_and_physical_numbers_rejected_before_driver_import(self):
        for wheel, (gpio, physical) in self.wiring.items():
            wrong_values = {pin for pin, _ in self.wiring.values()} - {gpio}
            wrong_values.update({physical, 17, -1, 40})
            for wrong in wrong_values:
                with self.subTest(wheel=wheel, wrong=wrong):
                    with self.assertRaises(SystemExit) as error:
                        self.invoke_without_gpio(
                            [wheel, 'center', '--gpio', str(wrong), '--arm'])
                    self.assertEqual(error.exception.code, 2)

    def test_every_mode_requires_arm_before_driver_import(self):
        for mode in ('center', 'test', 'hold'):
            with self.subTest(mode=mode):
                with self.assertRaises(SystemExit) as error:
                    self.invoke_without_gpio(['FL', mode])
                self.assertEqual(error.exception.code, 2)

    def test_invalid_pulse_and_pause_rejected_before_driver_import(self):
        cases = [
            ['--center-us', '1299'], ['--center-us', '1701'],
            ['--step-us', '0'], ['--step-us', '101'],
            ['--center-us', '1700', '--step-us', '1'],
            ['--center-us', '1300', '--step-us', '1', '--invert'],
            ['--pause', '0.2'], ['--pause', '10.1'], ['--pause', 'nan'],
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                with self.assertRaises(SystemExit) as error:
                    self.invoke_without_gpio(['FL', 'test', '--arm', *extra])
                self.assertEqual(error.exception.code, 2)

    def test_each_wheel_only_claims_and_pulses_its_assigned_pin(self):
        for wheel, (gpio, _) in self.wiring.items():
            with self.subTest(wheel=wheel):
                driver = self.fake_driver()
                with patch.dict(sys.modules, {'lgpio': driver}):
                    with patch.object(steering.time, 'sleep'), redirect_stdout(io.StringIO()):
                        steering.main([wheel, 'center', '--arm'])
                driver.gpiochip_open.assert_called_once_with(0)
                driver.gpio_claim_output.assert_called_once_with(7, gpio, 0)
                self.assertEqual(
                    [item.args for item in driver.tx_servo.call_args_list],
                    [(7, gpio, 1500, 50), (7, gpio, 0)],
                )
                driver.gpio_write.assert_called_once_with(7, gpio, 0)
                driver.gpio_free.assert_called_once_with(7, gpio)
                driver.gpiochip_close.assert_called_once_with(7)

    def test_sequence_and_inversion_use_one_pin_then_release(self):
        for flags, pulses in [
            ([], [1500, 1550, 1500, 1450, 1500]),
            (['--invert'], [1500, 1450, 1500, 1550, 1500]),
        ]:
            with self.subTest(flags=flags):
                driver = self.fake_driver()
                with patch.dict(sys.modules, {'lgpio': driver}):
                    with patch.object(steering.time, 'sleep'), redirect_stdout(io.StringIO()):
                        steering.main(['FR', 'test', '--arm', *flags])
                self.assertEqual(
                    [item.args for item in driver.tx_servo.call_args_list],
                    [(7, 12, pulse, 50) for pulse in pulses] + [(7, 12, 0)],
                )
                driver.gpio_claim_output.assert_called_once_with(7, 12, 0)
                driver.gpio_free.assert_called_once_with(7, 12)
                driver.gpiochip_close.assert_called_once_with(7)

    def test_hold_interrupt_releases_selected_pin_and_closes_chip(self):
        driver = self.fake_driver()
        with patch.dict(sys.modules, {'lgpio': driver}):
            with patch.object(steering.time, 'sleep', side_effect=[KeyboardInterrupt, None]):
                with redirect_stdout(io.StringIO()):
                    steering.main(['RR', 'hold', '--arm'])
        self.assertEqual(
            [item.args for item in driver.tx_servo.call_args_list],
            [(7, 19, 1500, 50), (7, 19, 0)],
        )
        driver.gpio_free.assert_called_once_with(7, 19)
        driver.gpiochip_close.assert_called_once_with(7)


if __name__ == '__main__':
    unittest.main()
