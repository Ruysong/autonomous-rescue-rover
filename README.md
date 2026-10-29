# Autonomous Rescue Rover

SKKU Software Capstone Design Project · Fall 2026

We are building a 3D-printed, six-wheel rover for indoor disaster exploration. The goal is to explore an unfamiliar area, identify a person or other relevant situation, move or stop safely, and report what it found to an operator.

## System Architecture

```text
[Operator]  "Find a person" / review findings
     |
     v
+---------------- Laptop: perception and mission ----------------+
| Camera video -> person candidate -> observation                |
| Map + rover state -> choose next area / recheck / report       |
+----------------------------+-----------------------------------+
                             | exploration goal
                             v
+---------- Navigation: ROS 2 direction (host TBD) --------------+
| LiDAR + odometry -> map/pose -> route + obstacle handling      |
+----------------------------+-----------------------------------+
                             | motion command
                             v
+---------------- Raspberry Pi: control and safety --------------+
| Validate commands / return robot state / stop on timeout       |
| Motor and steering interface                                   |
+----------------------------+-----------------------------------+
                             v
                   [Six-wheel, four-steer rover]

Pi -> laptop/navigation: video, sensor data, robot state
Laptop -> Pi: stop request (without waiting for navigation)
```

AI selects what to inspect next; the navigation layer computes how to get there. The robot stops locally on communication failure. The exact ROS 2 packages, execution device, and possible Pico controller are still being decided. This is the target system, not a completed end-to-end implementation.

## Repository Structure

```text
autonomous-rescue-rover/
├── laptop/            # Laptop-side Python software
├── robot/             # Raspberry Pi C++17 software
└── shared/protocol/   # Laptop ↔ Pi message contract
```

## Communication

The laptop and Pi communicate over Wi-Fi. Protocol v1 defines TCP command and telemetry channels using length-prefixed JSON; see [PROTOCOL.md](shared/protocol/PROTOCOL.md). Video transport, mapping, autonomous navigation, and real motor control are later integration steps.

The current command and telemetry demo is documented in [COMMUNICATION_DEMO.md](docs/COMMUNICATION_DEMO.md).

## Person Search Demo

The laptop demo reads a local video or webcam, confirms a person candidate, and requests a stop. See `laptop/perception/requirements.txt` for dependencies and run `python -m laptop.demo.person_search --source VIDEO.mp4 --show` from the repository root. The preview and saved video are capped at 720px high by default (`--render-height 480` makes them smaller); detection uses the original frame. The default stop and navigation cancellation are simulated. `--send-to-pi` sends a stop over the existing TCP command channel, but does not verify physical stopping.

## Raspberry Pi Access

Connect the Pi and laptop to the same network, then use `ssh rover@rover-pi.local` (or the Pi's current IP address). The password is shared privately.
