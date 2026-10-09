# Autonomous Rescue Rover

SKKU Software Capstone Design Project.

A 3D-printed, six-wheel rover project for indoor disaster-site exploration. The system separates high-level operation on a laptop from low-level robot control on a Raspberry Pi.

## System Architecture

```text
Laptop (Python)
- Operator interface
- Perception and navigation modules
- Command and telemetry services
            |
            | Command TCP 9998
            | Telemetry TCP 9997
            | Video UDP 9999 (planned)
            v
Raspberry Pi (C++17)
- Communication runtime
- Safety and robot state
- Hardware interfaces
            |
            v
Motors, steering, sensors, and camera
```

The laptop is the network server for command and telemetry channels. The Raspberry Pi connects as a client and remains responsible for local hardware control and communication safety.

## Repository Structure

```text
autonomous-rescue-rover/
|-- laptop/                         # Laptop-side Python software
|   `-- communication/             # Protocol, TCP listeners, operator console
|
|-- robot/                          # Raspberry Pi C++ runtime
|   |-- CMakeLists.txt
|   |-- include/
|   |   |-- communication/
|   |   |-- motor/
|   |   `-- state/
|   |-- src/
|   |   |-- communication/
|   |   |-- motor/
|   |   |-- state/
|   |   `-- main.cpp
|   `-- tests/                     # Pi-side runtime tests
|
|-- tests/                          # Laptop protocol tests
|-- docs/                           # Build and demo instructions
|
`-- shared/
    `-- protocol/
        `-- PROTOCOL.md            # Laptop <-> Pi communication contract
```

## Communication

| Channel | Transport | Port | Direction |
| --- | --- | ---: | --- |
| Command | TCP | 9998 | Laptop server -> Raspberry Pi client |
| Telemetry | TCP | 9997 | Raspberry Pi client -> Laptop server |
| Video | UDP | 9999 | Raspberry Pi -> Laptop (planned) |

The command and telemetry protocol uses a 4-byte big-endian length prefix followed by a UTF-8 JSON payload. The full message contract is documented in [shared/protocol/PROTOCOL.md](shared/protocol/PROTOCOL.md).

Build, run, and handoff steps are in [docs/COMMUNICATION_DEMO.md](docs/COMMUNICATION_DEMO.md).

## Technology

- Raspberry Pi runtime: C++17 and CMake
- Laptop-side tooling: Python
- Protocol JSON support: nlohmann/json
