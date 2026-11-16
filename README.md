# Autonomous Rescue Rover

SKKU Software Capstone Design Project · Fall 2026

We are building a 3D-printed, six-wheel rover for indoor disaster exploration. The goal is to explore an unfamiliar area, identify a person or other relevant situation, move or stop safely, and report what it found to an operator.

## 사용 방법 (요약)

명령만 모았다. 이유 · 옵션 · 확인 기록은 각 줄의 상세 문서에 있다. 배터리(3S LiPo)는 로버 옆에 사람이 있을 때만 꽂는다.

### 1. 준비

노트북 (레포 루트에서):

```bash
pip install numpy opencv-python pygame          # teleop · navigation
pip install -r laptop/perception/requirements.txt   # 사람 정지까지 쓸 때 (ultralytics, torch)
python -m pytest tests                           # 노트북 쪽 단위 시험
```

로버 Pi (Ubuntu 24.04, 의존성 `nlohmann-json3-dev`):

```bash
cmake -S robot -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4
./build/robot_control_tests && ./build/robot_navigation_tests && ./build/robot_runtime_tests
```

Pico 펌웨어 갱신 (Pi에서, `pico/main.py`를 `~/pico/`로 복사한 뒤):

```bash
cd ~/pico && PYTHONPATH=lib python3 -m mpremote connect /dev/ttyACM0 cp main.py :main.py + reset
```

- 노트북 ↔ 로버 연결: 노트북 방화벽에서 TCP 9998 · 9997 수신을 열거나, 노트북에서 SSH 역방향 터널 `ssh -R 9998:127.0.0.1:9998 -R 9997:127.0.0.1:9997 <로버>` 후 로버에서 `--host 127.0.0.1`.
- 카메라 스트림 (사람 정지용, Pi 재부팅 뒤 자동 시작 안 됨): `cd ~/hw_test && nohup python3 cam_stream.py /dev/video0 1280x720 8080 15 > cam_stream.log 2>&1 &`
- Pico 시리얼(`/dev/ttyACM0`)은 한 번에 프로그램 하나만 연다.

### 2. 수동 조작 (gamepad)

```bash
# 노트북 (먼저)
python -m laptop.teleop.run_gamepad

# 로버
./build/rover_controller --host <노트북 주소>                 # 실물 Pico
./build/rover_controller --host <노트북 주소> --max-duty 0    # dry run: 조향만
./build/rover_controller --host <노트북 주소> --pico mock     # Pico 없이
```

LB를 누르고 있어야 움직인다(deadman). Back = E-stop, Start = 해제 또는 manual ↔ autonomous. 상세: [docs/TELEOP.md](docs/TELEOP.md)

### 3. 자율 탐색

```bash
# 노트북 (먼저)
python -m laptop.navigation.run_explore --gamepad          # Start 버튼으로 자율 시작, 수동 조작도 그대로
python -m laptop.navigation.run_explore --autostart        # 바로 자율 시작
python -m laptop.navigation.run_explore --gamepad --person-source http://<로버>:8080/stream   # 사람 발견 시 정지

# 로버
./build/rover_controller --host <노트북 주소>                 # 실물: Pico + LiDAR(/dev/ttyUSB0) + BNO055
./build/rover_controller --host <노트북 주소> --pico mock     # 모의 세계 (LiDAR · Pico 모두 mock)

# 기록 재실행 (노트북만)
python -m laptop.navigation.run_explore --replay logs/explore_<시각>.jsonl              # SLAM
python -m laptop.navigation.run_explore --replay logs/explore_<시각>.jsonl --no-match   # odometry만 (비교용)
```

기록은 `logs/`에 남는다(`explore_*.jsonl`, 지도 png/npz, 사람 발견 `person_*.jpg/.json`). 상세: [docs/AUTONOMY.md](docs/AUTONOMY.md)

### 4. 첫 실주행 확인 순서

1. 바퀴를 띄우고 `--max-duty 0`: 조향 방향 · 중립 → `robot/config/rover.json` `steering.sign`, `trim_deg`
2. 바퀴를 띄우고 `--max-duty 0.2`: 좌우 바퀴 전진 방향 → `drive.motor_sign`
3. 바닥에서 저속 직진 · 회전, 엔코더로 `max_wheel_speed_mps`
4. LiDAR 방향 (`lidar.direction`, `yaw_offset_deg`), IMU yaw 부호 (`imu.yaw_sign`)
5. `counts_per_meter` 실측 (직진 2 m), LiDAR 정지 상자
6. 넓은 곳에서 `--autostart`, 0.10 m/s

1~3은 [docs/TELEOP.md](docs/TELEOP.md) 3절, 4~6은 [docs/AUTONOMY.md](docs/AUTONOMY.md) "현장 확인".

### 상세 문서

| 문서 | 내용 |
|---|---|
| [docs/architecture/README.md](docs/architecture/README.md) | 시스템 구조 (PlantUML 도면) |
| [docs/HARDWARE_INTERFACE.md](docs/HARDWARE_INTERFACE.md) | Pi · Pico 결선, Pico 시리얼 프로토콜, 핀 배정, 센서 상태 |
| [docs/TELEOP.md](docs/TELEOP.md) | gamepad 수동 조작 |
| [docs/AUTONOMY.md](docs/AUTONOMY.md) | 자율 탐색 (SLAM, frontier, Pure Pursuit) |
| [shared/protocol/PROTOCOL.md](shared/protocol/PROTOCOL.md) | 노트북 ↔ Pi 메시지 (v1.2) |
| [docs/LIVE_PERSON_STOP.md](docs/LIVE_PERSON_STOP.md), [docs/COMMUNICATION_DEMO.md](docs/COMMUNICATION_DEMO.md) | 이전 데모 (Pi GPIO 직결 시절) |

아래 영어 절은 초기 설계 설명이다. 현재 구조는 위 architecture 문서가 기준이다.

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

Protocol v1 defines TCP command and telemetry channels using length-prefixed JSON; see [PROTOCOL.md](shared/protocol/PROTOCOL.md). The live demo uses a Pi webcam HTTP stream and an opt-in GPIO motor adapter. Mapping, autonomous navigation, and steering integration remain future work.

The current command and telemetry demo is documented in [COMMUNICATION_DEMO.md](docs/COMMUNICATION_DEMO.md).

## Person Search Demo

The laptop demo reads a local video or webcam, confirms a person candidate, and requests a stop. See `laptop/perception/requirements.txt` for dependencies and run `python -m laptop.demo.person_search --source VIDEO.mp4 --show` from the repository root. The preview and saved video are capped at 720px high by default (`--render-height 480` makes them smaller); detection uses the original frame. The default stop and navigation cancellation are simulated. `--send-to-pi` sends a stop over the existing TCP command channel, but does not verify physical stopping.

## Live Manual Drive Demo

`laptop.demo.live_person_stop` shows the Pi camera feed, detects people, and gates manual forward/reverse commands. The Pi C++ runtime can use `MockMotor` or an opt-in GPIO adapter. See [LIVE_PERSON_STOP.md](docs/LIVE_PERSON_STOP.md) for the architecture, build, and run steps. Physical stopping performance has not been measured in this repository.

## Raspberry Pi Access

Connect the Pi and laptop to the same network, then use `ssh rover@rover-pi.local` (or the Pi's current IP address). The password is shared privately.
