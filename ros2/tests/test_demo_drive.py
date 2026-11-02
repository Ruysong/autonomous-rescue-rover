"""D-02 safety behavior with fake GPIO/time; never imports real lgpio."""

import builtins
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.demo import roveraid_demo_drive as demo


class FakeClock:
    """Advance simulated monotonic time on sleep; no wall-clock waits."""

    def __init__(self):
        self.now = 100.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class DemoDriveTest(unittest.TestCase):
    def setUp(self):
        self.settings = demo.load_settings(demo.DEFAULT_CONFIG)
        self.clock = FakeClock()
        self.driver = Mock(spec=[
            'gpiochip_open', 'gpio_claim_output', 'gpio_write',
            'tx_pwm', 'tx_servo', 'gpio_free', 'gpiochip_close',
        ])
        for name in self.driver._mock_methods:
            getattr(self.driver, name).return_value = 0
        self.driver.gpiochip_open.return_value = 7

    def plan(self, *argv, settings=None):
        args = demo.make_parser().parse_args(argv)
        return demo.build_plan(args, settings or self.settings)

    def calibrated(self):
        return replace(
            self.settings, steering_calibrated=True,
            motors={side: replace(motor, forward_level=level)
                    for (side, motor), level in zip(self.settings.motors.items(), (0, 1))},
        )

    def run_plan(self, plan, settings=None):
        settings = settings or self.settings
        gpio = demo.GPIO(self.driver, settings, 0)
        demo.run_plan(plan, settings, gpio, self.clock.clock, self.clock.sleep)

    def cli_without_driver(self, argv):
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == 'lgpio':
                raise AssertionError('unexpected real GPIO driver import')
            return real_import(name, *args, **kwargs)

        out = io.StringIO()
        with patch('builtins.__import__', side_effect=guarded_import):
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                status = demo.main(argv)
        return status, out.getvalue()

    def assert_both_stopped(self):
        for pin in (5, 16):
            self.driver.tx_pwm.assert_any_call(7, pin, 0, 0)
            self.driver.gpio_write.assert_any_call(7, pin, 0)

    def test_default_dry_run_all_actions_never_import_gpio(self):
        for argv in (
            ['stop'], ['servo', '--wheel', 'ALL'],
            ['motor', '--side', 'left', '--dir-level', '0'], ['drive'],
        ):
            with self.subTest(argv=argv):
                status, output = self.cli_without_driver(argv)
                self.assertEqual(status, 0)
                self.assertIn('DRY RUN: no GPIO imported or accessed.', output)
        _, output = self.cli_without_driver(['drive'])
        self.assertIn('UNSET (calibrate first)', output)

    def test_invalid_arguments_rejected_before_driver_import(self):
        for extra in (
            ['--duty', '0'], ['--duty', '20.01'], ['--duty', 'nan'],
            ['--duty', 'inf'], ['--seconds', '0'], ['--seconds', '-1'],
            ['--seconds', '3.01'], ['--seconds', 'nan'], ['--chip', '-1'],
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.cli_without_driver(
                    ['motor', '--side', 'left', '--dir-level', '0', '--arm', *extra])
        for argv in (
            ['motor'], ['servo'], ['stop', '--duty', '1'],
            ['servo', '--wheel', 'FL', '--dir-level', '0'],
            ['drive', '--side', 'left'], ['motor', '--side', 'left', '--dir-level', '2'],
            ['stop', '--arm', '--dry-run'],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.cli_without_driver(argv)

    def test_armed_drive_requires_polarity_and_servo_calibration(self):
        with self.assertRaises(SystemExit):
            self.cli_without_driver(['drive', '--arm'])
        plan = self.plan('drive')
        with self.assertRaises(ValueError):
            self.run_plan(plan)
        self.driver.gpiochip_open.assert_not_called()
        settings = replace(self.calibrated(), steering_calibrated=False)
        with self.assertRaises(ValueError):
            self.run_plan(self.plan('drive', settings=settings), settings)
        self.driver.gpiochip_open.assert_not_called()

    def test_config_rejects_typos_wrong_pins_types_and_nonfinite_values(self):
        original = demo.DEFAULT_CONFIG.read_text()
        bad = (
            original.replace('pwm_bcm = 5', 'pwm_bcm = 29'),
            original.replace('gpio_bcm = 18', 'gpio_bcm = 17'),
            original.replace('forward_level = -1', 'forward_level = 2'),
            original.replace('pwm_hz = 1000', 'pwm_hz = true'),
            original.replace('pwm_hz = 1000', 'pwm_hz = 20000'),
            original.replace('settle_s = 0.5', 'settle_s = nan'),
            original.replace('center_us = 1500', 'center_us = 2000'),
            original.replace('calibrated = false', 'calibrated = "false"'),
            original + '\nextra_setting = 1\n',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.toml'
            for content in bad:
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    demo.load_settings(path)

    def test_servo_raw_pulses_and_selection_validated(self):
        for pulses in (
            ['FL=1701'], ['FL=1601'], ['FR=1500'], ['FL=1500', 'FL=1510'],
            ['FL=nan'], ['FL'], ['12=1500'],
        ):
            argv = ['servo', '--wheel', 'FL', '--arm']
            for pulse in pulses:
                argv.extend(['--pulse', pulse])
            with self.subTest(pulses=pulses), self.assertRaises(SystemExit):
                self.cli_without_driver(argv)

    def test_individual_servo_holds_both_motors_low_and_releases_one_servo(self):
        self.run_plan(self.plan('servo', '--wheel', 'RR', '--pulse', 'RR=1550'))
        claims = [item.args for item in self.driver.gpio_claim_output.call_args_list]
        self.assertEqual(claims, [(7, pin, 0) for pin in (5, 16, 6, 20, 19)])
        calls = [item.args for item in self.driver.tx_servo.call_args_list]
        self.assertEqual(calls, [(7, 19, 1550, 50, 0, 25), (7, 19, 0)])
        self.assert_both_stopped()
        self.assertFalse(any(item.args[2] for item in self.driver.tx_pwm.call_args_list))

    def test_one_motor_uses_raw_direction_and_positive_finite_cycles(self):
        self.run_plan(self.plan('motor', '--side', 'right', '--dir-level', '1',
                                '--duty', '12', '--seconds', '0.25'))
        self.driver.gpio_write.assert_any_call(7, 20, 1)
        self.driver.tx_pwm.assert_any_call(7, 16, 1000, 12.0, 0, 250)
        self.assertAlmostEqual(self.clock.now, 100.25)
        self.assert_both_stopped()
        self.assertEqual(self.driver.tx_servo.call_count, 0)
        self.driver.gpiochip_close.assert_called_once_with(7)

    def test_combined_drive_prepositions_then_drives_then_stops_before_servo_release(self):
        settings = self.calibrated()
        motor_started = []

        def pwm(*args):
            if args[2] != 0:
                motor_started.append(self.clock.now)
            return 0

        self.driver.tx_pwm.side_effect = pwm
        plan = self.plan('drive', '--seconds', '0.5', '--pulse', 'FL=1550',
                         settings=settings)
        self.run_plan(plan, settings)
        self.assertEqual(motor_started, [100.5, 100.5])
        self.assertAlmostEqual(self.clock.now, 101.0)
        self.driver.gpio_write.assert_any_call(7, 6, 0)
        self.driver.gpio_write.assert_any_call(7, 20, 1)
        self.assert_both_stopped()
        calls = self.driver.mock_calls
        stop_indices = [i for i, call in enumerate(calls)
                        if call[0] == 'tx_pwm' and call.args[2] == 0]
        servo_release = [i for i, call in enumerate(calls)
                         if call[0] == 'tx_servo' and call.args[2] == 0]
        self.assertLess(max(stop_indices), min(servo_release))
        for call in self.driver.tx_pwm.call_args_list:
            if call.args[2] != 0:
                self.assertGreater(call.args[-1], 0)

    def test_reverse_flips_each_calibrated_side_without_guessing_symmetry(self):
        settings = self.calibrated()
        forward = self.plan('drive', settings=settings)
        reverse = self.plan('drive', '--direction', 'reverse', settings=settings)
        self.assertEqual(forward.motor_levels, {'left': 0, 'right': 1})
        self.assertEqual(reverse.motor_levels, {'left': 1, 'right': 0})

    def test_late_setup_never_starts_motors_after_command_deadline(self):
        settings = self.calibrated()

        def delayed_servo(*args):
            if args[2]:
                self.clock.now += 5
            return 0

        self.driver.tx_servo.side_effect = delayed_servo
        with self.assertRaises(TimeoutError):
            self.run_plan(self.plan('drive', settings=settings), settings)
        self.assertFalse(any(call.args[2] for call in self.driver.tx_pwm.call_args_list))
        self.assert_both_stopped()

    def test_timeout_between_channels_prevents_starting_second_channel(self):
        settings = self.calibrated()

        def delayed_pwm(*args):
            if args[2]:
                self.clock.now += 5
            return 0

        self.driver.tx_pwm.side_effect = delayed_pwm
        with self.assertRaises(TimeoutError):
            self.run_plan(self.plan('drive', settings=settings), settings)
        active = [call.args for call in self.driver.tx_pwm.call_args_list if call.args[2]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1], 5)
        self.assert_both_stopped()

    def test_delayed_direction_write_does_not_start_pwm_after_expiry(self):
        def delayed_write(*args):
            if args == (7, 6, 1):
                self.clock.now += 5
            return 0

        self.driver.gpio_write.side_effect = delayed_write
        with self.assertRaises(TimeoutError):
            self.run_plan(self.plan('motor', '--side', 'left', '--dir-level', '1'))
        self.assertFalse(any(call.args[2] for call in self.driver.tx_pwm.call_args_list))
        self.assert_both_stopped()

    def test_sleep_exception_and_keyboard_interrupt_stop_both_motors(self):
        for failure in (RuntimeError('fake sleep failed'), KeyboardInterrupt()):
            self.setUp()
            gpio = demo.GPIO(self.driver, self.settings, 0)
            with self.subTest(failure=failure), self.assertRaises(type(failure)):
                demo.run_plan(self.plan('motor', '--side', 'left', '--dir-level', '0'),
                              self.settings, gpio, self.clock.clock,
                              Mock(side_effect=failure))
            self.assert_both_stopped()
            self.driver.gpiochip_close.assert_called_once_with(7)

    def test_partial_claim_failure_cleans_only_owned_pins(self):
        self.driver.gpio_claim_output.side_effect = [0, 0, -79]
        with self.assertRaises(RuntimeError):
            self.run_plan(self.plan('servo', '--wheel', 'FL'))
        self.assert_both_stopped()
        self.assertEqual([call.args for call in self.driver.gpio_free.call_args_list],
                         [(7, 16), (7, 5)])
        self.driver.tx_servo.assert_not_called()
        self.driver.gpiochip_close.assert_called_once_with(7)

    def test_chip_open_failure_does_not_use_invalid_handle(self):
        self.driver.gpiochip_open.return_value = -78
        with self.assertRaises(RuntimeError):
            self.run_plan(self.plan('stop'))
        self.driver.gpio_claim_output.assert_not_called()
        self.driver.gpiochip_close.assert_not_called()

    def test_second_motor_start_failure_stops_first_and_second(self):
        settings = self.calibrated()

        def pwm(*args):
            return -96 if args[1] == 16 and args[2] else 0

        self.driver.tx_pwm.side_effect = pwm
        with self.assertRaises(RuntimeError):
            self.run_plan(self.plan('drive', settings=settings), settings)
        self.assert_both_stopped()
        self.driver.gpiochip_close.assert_called_once_with(7)

    def test_cleanup_error_does_not_skip_other_motor_or_resource_release(self):
        def pwm(*args):
            return -1 if args == (7, 5, 0, 0) else 0

        self.driver.tx_pwm.side_effect = pwm
        self.driver.gpio_free.side_effect = RuntimeError('fake free failed')
        with self.assertRaises(ExceptionGroup) as failure:
            self.run_plan(self.plan('stop'))
        self.assertEqual(len(failure.exception.exceptions), 5)
        self.assert_both_stopped()
        self.assertEqual(self.driver.gpio_free.call_count, 4)
        self.driver.gpiochip_close.assert_called_once_with(7)

    def test_explicit_stop_never_sends_nonzero_pwm_or_servo(self):
        self.run_plan(self.plan('stop'))
        self.assert_both_stopped()
        self.assertEqual(self.driver.tx_pwm.call_count, 2)
        self.driver.tx_servo.assert_not_called()

    def test_signal_handler_stops_running_trial_and_restores_previous_handlers(self):
        original = {item: signal.getsignal(item)
                    for item in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

        def hangup(seconds):
            signal.getsignal(signal.SIGHUP)(signal.SIGHUP, None)

        gpio = demo.GPIO(self.driver, self.settings, 0)
        with self.assertRaises(demo.StopRequested) as caught:
            with demo.stop_signals():
                demo.run_plan(self.plan('motor', '--side', 'left', '--dir-level', '0'),
                              self.settings, gpio, self.clock.clock, hangup)
        self.assertEqual(caught.exception.signum, signal.SIGHUP)
        self.assert_both_stopped()
        self.assertEqual(original, {item: signal.getsignal(item) for item in original})

    def test_armed_cli_backend_error_returns_failure_and_attempts_cleanup(self):
        self.driver.tx_servo.side_effect = RuntimeError('fake output failed')
        with patch.dict(sys.modules, {'lgpio': self.driver}):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                status = demo.main(['servo', '--wheel', 'FL', '--arm'])
        self.assertEqual(status, 1)
        self.assert_both_stopped()
        self.driver.gpiochip_close.assert_called_once_with(7)


if __name__ == '__main__':
    unittest.main()
