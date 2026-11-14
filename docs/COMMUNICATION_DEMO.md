# Communication demo and handoff

This milestone runs with `MockMotor`. No physical motor, steering, camera, sensor, or LTE connection is required. `motor_speed` is the requested throttle, not measured wheel speed. `steer` is validated but not actuated.

## Network and startup

Put the laptop and Raspberry Pi on the same Wi-Fi or wired network. Find the laptop's reachable IPv4 address with `ipconfig`. Allow inbound TCP ports `9998` and `9997` on the laptop's private network when Windows Firewall prompts. Keep both terminals open.

From the repository root on the **laptop**, run first:

```powershell
python -m laptop.communication.control_console --bind 0.0.0.0
```

From the repository root on the **Pi**, install the build dependency if needed, then build and run:

```bash
sudo apt install cmake g++ nlohmann-json3-dev
cmake -S robot -B build
cmake --build build
ctest --test-dir build --output-on-failure
./build/rover_controller --host LAPTOP_IPV4
```

Replace `LAPTOP_IPV4` with the actual laptop address. The Pi is the TCP client on both channels: command `9998` and telemetry `9997`. To change ports, set `--command-port` and `--telemetry-port` to matching values on both programs. Do not use `0.0.0.0` as the Pi's `--host`.

## Demo sequence

| Step | Laptop action | Expected Pi result |
| --- | --- | --- |
| 1 | Start laptop console, then Pi runtime | Both channels connect; telemetry appears on laptop |
| 2 | Press `W` | `[MockMotor] speed = 0.5`; telemetry speed `0.5` |
| 3 | Press Space | `[MockMotor] stop`; telemetry speed `0.0` |
| 4 | Press `S` | `[MockMotor] speed = -0.5` |
| 5 | Send no more keys for 3 seconds | Timeout stops mock motor; telemetry safety state `timeout` |
| 6 | Press `W` again | Valid command restores safety state `normal` |
| 7 | Quit laptop console with `Q` or Ctrl+C | Pi stops on command disconnect and retries connection |
| 8 | Restart laptop console | Pi reconnects without restarting; `W` and telemetry work again |

The Windows console sends one command per key press. Hold or repeat `W`/`S` to keep driving; a single press intentionally expires after three seconds. `Q` sends `stop` before closing. Unexpected connection loss is handled independently by the Pi.

## Verification and evidence

Run laptop protocol tests from the repository root:

```powershell
python -m unittest discover -s tests -v
```

Run the Pi build and `ctest` command above. Capture the **actual** Pi terminal output showing connection, drive, stop, timeout, disconnect, and reconnect. For a log file on Pi, run the controller through `tee`:

```bash
./build/rover_controller --host LAPTOP_IPV4 2>&1 | tee ~/rover-communication-demo.log
```

Before sharing logs, remove local IP addresses or other private network details if necessary. No physical Pi result should be marked complete until it has been observed on the device.

| Check | Evidence to hand over |
| --- | --- |
| Pi build and CTest | Build result and test summary |
| Command to `MockMotor` | Pi log after `W`, `S`, and Space |
| Telemetry to laptop | Laptop terminal output with speed and safety state |
| Invalid command and fragmented frame | Automated test output |
| Timeout, disconnect, reconnect | Pi log and short demo recording |

LTE, physical motors, and real sensor values are later integration work, not claims of this demo.
