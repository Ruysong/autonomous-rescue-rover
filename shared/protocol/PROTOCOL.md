# Robot Communication Protocol v1 (v1.1 and v1.2 additions at the end)

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

The Pi runtime emits telemetry at about 5 Hz. In the current mock-hardware build, `motor_speed` reports the commanded throttle, not measured wheel speed.

## Communication safety semantics

- Command connection disconnect: stop the motor and set a disconnected safety state.
- Command timeout: after 3.0 seconds without a valid command, stop the motor and set a timeout safety state.
- Telemetry disconnect does not itself authorize motor movement or stopping; the command channel remains the control authority.

## C++ dependency

The Pi build uses [nlohmann/json](https://github.com/nlohmann/json). On Raspberry Pi OS:

```bash
sudo apt install nlohmann-json3-dev
```

## v1.1 additions (2026-11-14, yw/teleop)

`_version` may be `"1.0"` or `"1.1"`. `drive` and `stop` are accepted with either version; the types below require `"1.1"`. The robot answers with `"1.1"` telemetry. v1.0 readers can ignore the extra telemetry fields.

### Motion

```json
{"_version": "1.1", "type": "motion", "source": "manual", "steer_mode": "normal",
 "v": 0.10, "kappa": 0.5, "wz": 0.0}
```

- `source`: `manual` (gamepad) or `autonomous` (robot path tracker). The Command Gate only accepts the source that matches its current control mode.
- `steer_mode`: `normal` (front and rear corners steer opposite; turn centre on the middle axle) or `spin` (turn in place, refused while `limits.spin_enabled` is false).
- `v` [m/s, |v| <= 2] and `kappa` [1/m, + = left, |kappa| <= 20] for `normal`; `wz` [rad/s, + = CCW, |wz| <= 5] for `spin`. The robot clamps to its own limits (reported in telemetry).
- Must be repeated (20 Hz); without a fresh `motion` for 0.5 s the gate stops (`safety_state: timeout`).

### Control mode, E-stop

```json
{"_version": "1.1", "type": "set_mode", "mode": "autonomous"}
{"_version": "1.1", "type": "estop"}
{"_version": "1.1", "type": "resume"}
```

- `set_mode`: `manual` or `autonomous`. Every change starts from a stop.
- `estop`: latched; all motion is ignored until `resume`. `stop` stays one-shot (the next `motion` drives again).

### Telemetry (v1.1 fields)

```json
{"_version": "1.1", "type": "telemetry", "timestamp": 1.0, "motor_speed": 0.0, "mode": "pico",
 "safety_state": "normal", "last_command_age": 0.05,
 "control_mode": "manual", "steer_mode": "normal", "estop": false,
 "duty": [0.18, 0.18], "steer_deg": [0, 0, 0, 0],
 "pico": {"ok": true, "flags": 0}, "encoders": [0, 0], "ultrasonic_cm": [67, 366, -1, 50],
 "limits": {"max_curvature": 1.303, "max_speed": 0.144, "spin_enabled": false}}
```

- `mode`: `pico` (real Pico) or `mock`. `safety_state`: `normal`, `stopped`, `timeout`, `estop`, `disconnected`, `switching` (brake -> steer before a steer-mode change), `rejected`.
- `steer_deg` and `ultrasonic_cm` are in FL, FR, RL, RR order. `pico.flags` as in docs/HARDWARE_INTERFACE.md.
- Sent at 10 Hz.

## v1.2 additions (2026-11-15, yw/autonomy)

`_version` `"1.2"` is required for the types below; v1.0 / v1.1 messages are still accepted. The robot now sends `"1.2"` telemetry and a second message type, `scan`, on the same telemetry connection.

### Path (laptop -> robot)

```json
{"_version": "1.2", "type": "path", "id": 12, "speed": 0.10, "points": [[0.0, 0.0], [0.15, 0.02]]}
```

- Map-frame waypoints for the robot Pure Pursuit (max 2000, |x|, |y| <= 1000 m). `speed` [m/s, 0..0.5], 0 = robot default.
- Followed only while the control mode is `autonomous` (the tracker feeds the gate with `source: autonomous` at 20 Hz).
- An empty `points` list cancels the path and stops. `stop` and `estop` also cancel it.

### Pose correction (laptop -> robot)

```json
{"_version": "1.2", "type": "pose_correction", "x": 0.16, "y": -0.01, "yaw": 0.044}
```

- The map <- odom transform from the laptop SLAM: `map_pose = correction * odom_pose`. Sent about 5 Hz.

### Scan (robot -> laptop)

```json
{"_version": "1.2", "type": "scan", "timestamp": 1.0, "seq": 42, "odom_pose": [0.0, 0.0, 0.0],
 "angle_increment_deg": 1, "ranges_mm": [0, 1520, "... 360 values"]}
```

- One LiDAR revolution (~10 Hz) already in the robot frame: bin i = i deg CCW from +x (forward), origin at the middle-axle centre, nearest return in mm, 0 = no return.
- `odom_pose` [x, y, yaw] is the robot odometry at the scan time.

### Telemetry (v1.2 fields)

```json
{"odom_pose": [1.2, 0.3, 0.1], "map_pose": [1.25, 0.28, 0.12], "odom_distance": 4.2,
 "sensors": {"imu": true, "lidar": true},
 "follow": {"state": "following", "path_id": 12, "remaining": 2.3}}
```

- `follow.state`: `idle`, `following`, `reversing` (3-point turn: the next point is behind), `blocked` (LiDAR return in the stop box ahead / behind), `no_scan` (LiDAR stale, stopped), `reached`.
- Telemetry is now sent at 10 Hz alongside scans (the robot loop runs at 20 Hz and sends telemetry every other tick).


## Mission protection extension (jh/mission-llm)

Version 1.2 extension, advertised by `protection_supported: true` in telemetry.
Old robot builds do not support it; the integrated runner refuses motion without the capability.

```json
{"_version":"1.2","type":"protection","hold":false,"speed_limit":0.05}
```

Repeat every 150 ms. `hold` must be boolean; `speed_limit` finite in [0,0.5] m/s.
Once enabled, a missing heartbeat for 500 ms latches a protection hold. Setting hold=false alone
does not clear it: fresh protection + explicit `resume` are required. The cap affects manual
and autonomous motion (spin is limited through middle-wheel speed). Local blocked/stale LiDAR
and disconnected command link prevent output independently. `stop` remains a one-shot command;
GUI protection uses this latch and E-stop rather than relying on one-shot stop for safety.
