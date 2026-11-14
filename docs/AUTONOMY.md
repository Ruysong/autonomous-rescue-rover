# 자율 탐색 (navigation + mission stop)

작성 2026-11-15 · 강연우. frontier 탐색으로 지도 없는 실내를 돌다가 사람을 찾으면 멈추는 MVP.

```text
robot (Pi, rover_controller)                               laptop (run_explore.py)
 RPLIDAR C1 ─▶ Lidar ──────── scan (10 Hz) ──────────────▶ ScanSlam (occupancy grid + scan matching)
 Pico 엔코더 ┐                                               │ map, pose
 BNO055 ─────┴▶ Odometry ─ odom_pose ─┘                      ▼
                  │                     ◀─ pose_correction ─ Explorer: frontier(id) → selector → path
                  ▼                     ◀─ path ───────────┘      ▲
 PathFollower (Pure Pursuit, LiDAR 정지 상자) ─ motion(autonomous) ─▶ CommandGate ─▶ Pico
                                                            camera ─▶ PersonDetector → MissionController → stop
```

- laptop → robot 경계는 path, pose_correction, stop만 넘는다 (속도 명령은 Pi 안에서 만든다). 프로토콜 v1.2: [`shared/protocol/PROTOCOL.md`](../shared/protocol/PROTOCOL.md).
- Wi-Fi가 끊기면 Gate가 정지 (기존 규칙). path 추종 중에도 LiDAR가 0.5 s 넘게 안 오면 정지 (`no_scan`).

## 실행

```bash
# laptop (먼저). OpenCV에 창 기능이 없으면 지도는 logs/live_map.png로 1초마다 저장된다.
python -m laptop.navigation.run_explore --gamepad                     # Start 버튼으로 자율 시작
python -m laptop.navigation.run_explore --autostart                   # 바로 자율 시작
python -m laptop.navigation.run_explore --gamepad --person-source http://<로버>:8080/stream   # 사람 발견 시 정지

# robot (Pi)
./build/rover_controller --host <노트북 주소>                          # 실물 Pico + /dev/ttyUSB0 라이다 + BNO055
./build/rover_controller --host <노트북 주소> --pico mock              # 모의 세계 (라이다 · Pico 모두 mock)
```

- `--gamepad`: 수동 모드 조작 그대로 (LB deadman, Back E-stop). Start = manual ↔ autonomous, autonomous가 되면 탐색 시작.
- 기록: `logs/explore_<시각>.jsonl` (scan, telemetry, 보낸 명령 전부), 종료 시 `_map.png` / `_map.npz`. 사람 발견 시 `logs/person_<시각>.jpg/.json` (지도 좌표 포함).
- 오프라인 재실행: `--replay logs/explore_....jsonl` (SLAM), `--replay ... --no-match` (odometry만). 같은 로그로 두 지도를 비교하면 SLAM이 얼마나 보정하는지 보인다.

## 구성

| 부분 | 파일 | 내용 |
|---|---|---|
| Sensor Drivers | `robot/src/hardware/Lidar.cpp` | C1 standard scan (460800), 한 바퀴를 robot frame 360 bin으로 (마운트 위치 · 방향 보정). mock: 벽 선분 ray cast |
| | `robot/src/hardware/Imu.cpp` | BNO055 IMU 모드 (지자기 끔), axis remap (뒤집힌 장착), heading unwrap, I2C 깨진 값 거름 |
| state estimation | `robot/src/navigation/Odometry.cpp` | 중간 바퀴 엔코더 평균 = 이동 거리, yaw는 IMU (없으면 엔코더 차). EKF · optical flow는 다음 단계 |
| path tracking | `robot/src/navigation/PathFollower.cpp` | Pure Pursuit (lookahead 0.5 m), 목표가 뒤면 반대로 꺾고 후진 (3-point turn), LiDAR 정지 상자 (차체 앞 / 뒤 0.25 m) |
| mapping | `laptop/navigation/slam.py` | log-odds occupancy grid 5 cm, likelihood field에 correlative scan matching (coarse → fine, bilinear). loop closure 없음 |
| planning | `laptop/navigation/planner.py` | 장애물 0.40 m inflation, 벽에서 0.7 m 떨어지도록 비용, Dijkstra 한 번으로 frontier 도달 거리와 경로 |
| 탐색 | `laptop/navigation/explorer.py` | frontier에 id, selector(LLM 자리)가 id 선택, 목록에 없는 id는 가장 가까운 것으로 대체. 막힌 frontier 3회면 제외 |
| 실행 | `laptop/navigation/run_explore.py` | 위를 묶고 gamepad · 사람 정지 · 기록 · 지도 표시 |
| 설정 | `robot/config/rover.json` | `odometry`, `lidar`, `imu`, `follower` 절 |

## 현장 확인 (실물 첫 주행 전, 순서대로)

배터리는 사람이 옆에 있을 때만. [`TELEOP.md`](TELEOP.md) 3절(조향 부호 · 모터 방향 · 속도)을 먼저 끝낸다.

1. **LiDAR 방향**: 로버 정면 1 m에 상자를 두고 `rover_controller --max-duty 0` + `run_explore` → `logs/live_map.png`에서 상자가 로버 앞 (화살표 방향)에 보이는지. 좌우가 뒤집히면 `lidar.direction` 부호, 회전돼 있으면 `lidar.yaw_offset_deg`.
2. **IMU yaw 부호**: 바퀴를 띄우지 않고 손으로 로버를 반시계로 90° 돌린다 → telemetry `odom_pose` yaw가 +1.57 근처로 가는지. 반대면 `imu.yaw_sign`. `sensors.imu`가 false면 I2C 확인 (`i2c_arm_baudrate=10000` 적용 여부).
3. **counts_per_meter**: `odom_test.py`로 직진 2 m 실측 → `odometry.counts_per_meter` (지금 875는 가정값). 확인: 2 m 밀었을 때 `odom_pose` x가 2.0.
4. **정지 상자**: 수동 모드에서 바로 앞 30 cm에 손 → autonomous로 경로를 줬을 때 `follow.state`가 `blocked`.
5. 넓은 곳에서 `--autostart`, 속도 0.10 m/s 그대로.

## 알려진 한계

- LiDAR 스캔면이 바닥에서 73 cm라 그보다 낮은 장애물(상자, 의자 다리)은 안 보인다 → 시험장은 벽 · 높은 장애물로 구성. 초음파 4개는 위 · 아래를 보게 달려 있어 정지 판단에 쓰지 않았다.
- planner는 회전 반경(약 0.77 m)을 모른다. 좁은 곳에서는 3-point turn을 반복할 수 있다.
- loop closure가 없어 긴 순환 경로(수십 m)를 돌아오면 벽이 두 겹이 될 수 있다. 그때 slam_toolbox로 바꾼다: 입력 (scan, odom_pose) · 출력 (pose_correction) 형식을 유지하면 laptop 안에서만 바뀐다.
- 사람 정지 경로(`--person-source`)는 이 노트북에 ultralytics가 없어 실행 시험 전. 판단 로직은 기존 `MissionController` 그대로 (연속 3프레임).
- 움직이는 물체 추적(moving object tracker)은 아직 없음 (사람이 지나간 자리가 지도에 남을 수 있다).

## 확인 기록

- 2026-11-15: 로버 Pi에서 빌드 · `robot_navigation_tests` / `robot_control_tests` / `robot_runtime_tests` 통과. 노트북 `pytest tests` 31개 통과.
- 2026-11-15: 전체 루프 (노트북 run_explore ↔ SSH 터널 ↔ Pi rover_controller `--pico mock`, 모의 L자 방 6 × 4 m + 통로, 상자 1개, odometry 오차 거리 4 % · 회전 3 % 주입): 경로 19개로 방 전체 지도 완성 후 "no reachable frontier"로 종료, 약 2분. 같은 로그 재실행에서 SLAM 지도는 벽이 한 줄, odometry만 쓴 지도는 벽이 번지고 기울어짐. scan당 약 30 ms.
- 실물 LiDAR · IMU · 엔코더로는 아직 (위 현장 확인 순서).
