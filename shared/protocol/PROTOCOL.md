# Robot Communication Protocol v1

## Scope

This protocol connects the Laptop control stack and the Raspberry Pi robot runtime. Version 1 supports command and telemetry traffic only. Video, camera, sensors, navigation, and real motor control are outside this protocol milestone.

## Roles and ports

| Channel | Transport | Port | Laptop role | Raspberry Pi role |
| --- | --- | ---: | --- | --- |
| Command | TCP | 9998 | Server | Client |
| Telemetry | TCP | 9997 | Server | Client |

The Laptop starts both listeners before the Pi runtime starts. Command and telemetry use independent TCP connections.

## Frame format

Every command and telemetry message uses:

```text
[4-byte unsigned payload length, big-endian][UTF-8 JSON payload]
```

The maximum payload size is 65,536 bytes. A receiver must read the complete header, validate the length, and then read the complete payload. A single TCP read must never be treated as a complete message.

## Common fields

Every JSON object includes:

```json
{
  "_version": "1.0",
  "type": "..."
}
```

Unsupported versions, malformed JSON, unknown types, and invalid field ranges are rejected and must not refresh the command timeout.

## Command messages

### Drive

```json
{
  "_version": "1.0",
  "type": "drive",
  "throttle": 0.5,
  "steer": 0.0
}
```

- `throttle`: number in `[-1.0, 1.0]`
- `steer`: number in `[-1.0, 1.0]`

In v1, the Pi applies `throttle` to `MotorInterface::setSpeed()`. It validates but does not actuate `steer` because steering hardware is not integrated yet.

### Stop

```json
{
  "_version": "1.0",
  "type": "stop"
}
```

The Pi immediately calls `MotorInterface::stop()`.

## Telemetry message

```json
{
  "_version": "1.0",
  "type": "telemetry",
  "timestamp": 12345.67,
  "motor_speed": 0.5,
  "mode": "mock",
  "safety_state": "normal",
  "last_command_age": 0.12
}
```

Telemetry is emitted by the Pi at about 5 Hz in the later Pi integration phase.

## Communication safety semantics

- Command connection disconnect: stop the motor and set a disconnected safety state.
- Command timeout: after 3.0 seconds without a valid command, stop the motor and set a timeout safety state.
- Telemetry disconnect does not itself authorize motor movement or stopping; the command channel remains the control authority.

## C++ dependency

The Pi build uses [nlohmann/json](https://github.com/nlohmann/json). On Raspberry Pi OS:

```bash
sudo apt install nlohmann-json3-dev
```
