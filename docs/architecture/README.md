# RoverAid 시스템 아키텍처 — 검토용

작성 2026-11-14 · 강연우 · 기준 main `4c5a4ef`, `jh/live-person-stop` `791f82b`

로버가 해야 하는 일(기능)로 블록을 나누고, 블록 안의 각 부분에 실행 기기(`laptop` · `robot` · `pico`)를 표시했다. 개발 기준이라 **기능 도면만** 그린다.

## 도면 체계

| 레이어 | 내용 | 박스 이름 |
|---|---|---|
| **L0** | 시스템 최상위: 기능 블록 5개와 그 사이의 흐름 | 블록 이름 |
| **L1** | 블록 하나의 내부 (L0 박스 하나를 펼침) | 파일 · 클래스 이름 (아직 없으면 `<<planned>>`) |
| **L2** | 세부 동작 | **함수 이름** |

- 도면 번호 `L<레이어>-<번호>`. 오른쪽 아래 title block에 상위 도면 · 펼친 박스 · 기준 커밋.
- 하위 도면은 상위 박스 이름만 참조하고 (L1에서 점선 박스 = 이웃 L0 블록), 같은 정보는 한 도면에만 둔다. 자주 바뀌는 것(파일 · 함수 · 주기)은 가장 낮은 레이어에만.
- 박스 이름과 코드가 다르면 코드를 기준으로 도면을 고친다. L2는 구현하면서 추가.
- PlantUML 소스, VS Code `jebbs.plantuml` 확장으로 미리보기(Alt+D). 렌더링한 그림은 [`png/`](png/) (소스를 고치면 다시 렌더링해서 같이 커밋).

| 도면 | 내용 |
|---|---|
| [L0-1_system](L0-1_system.puml) | operator → mission / perception → navigation → control / safety → rover |
| [L1-1_operator](L1-1_operator.puml) | 임무 할당 · 결과 검토, 시험용 수동 조작 |
| [L1-2_mission_perception](L1-2_mission_perception.puml) | 사람 관측, 멈출지 · 어디로 갈지 결정 |
| [L1-3_navigation](L1-3_navigation.puml) | 상태 추정, mapping, planning, 경로 추종 |
| [L1-4_control_safety](L1-4_control_safety.puml) | Command Gate, 역기구학, Hardware Interface, Realtime Controller |

![L0-1](png/L0-1_system.png)

## 1. L0 블록 사이의 흐름

| 관계 | 아래로 (명령) | 위로 (관측 · 보고) |
|---|---|---|
| operator ↔ mission / perception | 임무 할당 | 발견 결과 보고 |
| operator ↔ control / safety | 수동 주행 명령 (gamepad), 모드 전환, E-stop (시연용 수동 모드) | 텔레메트리 |
| mission / perception ↔ navigation | 목표 frontier id, 움직이는 물체 목록 (track) | frontier 후보 목록 (id), 지도, pose |
| mission / perception → control / safety | stop 요청 (navigation을 거치지 않음) | 영상, LiDAR scan, 로버 상태 |
| navigation ↔ control / safety | 움직임 명령 (v, ω) | 센서 데이터, 로버 상태 |
| control / safety ↔ rover | 모터 · 조향 출력 | 센서 원본 (카메라, LiDAR, 엔코더, IMU, 초음파) |

## 2. 블록별 (L1)

**operator** — 임무를 주고 결과를 검토한다. 자율 모드에서는 운전을 시스템이 한다. 최종 시연에서 자율 주행이 안 될 때를 대비해 **gamepad 수동 모드**(`laptop/teleop/`)를 둔다: 패드 → `motion`(source manual) → Command Gate. 사용법은 [`../TELEOP.md`](../TELEOP.md). `live_person_stop.py`, `control_console.py`는 시험용.

**mission / perception**
- perception: 영상에서 사람이 있는가와 확률을 관측 (`PersonDetector`).
- perception (만들 것): moving object tracker. 360° LiDAR scan에서 지도에 없는(벽이 아닌) 점 군집을 SORT 같은 추적기로 프레임 간 묶어 움직이는 물체 목록(track)을 만든다. 쓰는 곳 세 군데:
  1. 사람 후보: 움직이는 track을 사람 후보로 보고 그쪽으로 돌아 카메라로 확인. LLM 목표 선택의 입력으로도 쓴다. 정면만 보는 카메라의 사각을 메운다.
  2. 지도 보호: mapping 입력에서 움직이는 점을 빼서, 사람이 지나간 자리에 가짜 벽이 남지 않게 한다.
  3. 동적 장애물: planning costmap에서 움직이는 물체 주변에 여유를 크게 준다.
  - 움직이지 않는 사람(의식 없이 누운 경우)은 잡지 못한다. 그 경우는 카메라 탐지가 주 수단. 근거리 LiDAR 점은 거리 기준으로 거르지 않는다 (11/2 결정).
- mission: 관측을 보고 멈출지, 어디로 갈지 정한다 (`MissionController`). 현재 구현: 연속 3프레임 사람 → 후보, 목표 취소 + 정지. 탐색과 연결: `run_explore.py --person-source` (탐색 중단 → stop).
- 만들 것: navigation이 SLAM 지도에서 뽑은 frontier 후보에 id를 붙여 올리면, LLM이 추가 관측까지 보고 하나를 골라 그 id를 목표로 내린다. 목록에 없는 id는 검증에서 거른다. 자리는 마련됨: `Explorer(selector=...)`, 없거나 틀리면 가장 가까운 frontier.

**navigation** — 구현 (yw/autonomy), 사용법 · 현장 확인 순서는 [`../AUTONOMY.md`](../AUTONOMY.md)
- state estimation (`robot/src/navigation/Odometry.cpp`): 중간 바퀴 엔코더 + IMU yaw. EKF와 optical flow(2단계)는 다음.
- mapping (`laptop/navigation/slam.py`): LiDAR scan + odom pose로 occupancy grid, scan-to-map matching으로 보정해 map ← odom 보정값을 로버에 보낸다. 구현 비용 때문에 slam_toolbox 대신 직접 구현 (loop closure 없음, 입출력이 같아 나중에 교체 가능). 움직이는 track의 점은 뺀다 (planned).
- planning (`laptop/navigation/planner.py`, `explorer.py`): frontier 후보에 id, 고른 frontier까지 costmap 위 최단 경로 (Dijkstra). 움직이는 물체 주변은 여유를 크게 (planned).
- path tracking (`robot/src/navigation/PathFollower.cpp`): 경로 + map pose로 Pure Pursuit → (v, κ). 목표가 뒤면 3-point turn, LiDAR 정지 상자.

**control / safety**
- Command Gate (`robot/src/control/CommandGate.cpp`): 위에서 온 명령을 검증 · 적용. manual / autonomous 중 현재 모드의 명령만 받는다. 0.5 s 안에 새 명령이 없으면 정지, 링크가 끊기면 정지, E-stop은 resume까지 래치. 조향 모드가 바뀌면 정지 → 조향 → 구동 순서.
- kinematics (`robot/src/control/Kinematics.cpp`): (v, κ) 또는 제자리 회전 ω → 좌우 duty + 바퀴 4개 조향각. 역기구학은 ROS 관례대로 여기서 (`robot`). 기구 값은 `robot/config/rover.json` (채널별 부호 · 중립 보정).
  - 중간 바퀴가 조향되지 않아 회전 중심은 항상 중간 축선 위 → Ackermann과 대칭 4WS가 같은 동작(앞 · 뒤 반대로). crab 불가.
  - ±30°에서 최소 회전 반경 약 0.77 m. 제자리 회전은 모서리 바퀴가 약 45° 필요해서 서보 범위 실측 전까지 꺼 둔다 (`spin_enabled`).
- Hardware Interface (`robot`): PicoBridge(`robot/src/hardware/PicoBridge.cpp`, 명령 → 시리얼 → Pico 20 Hz, Pico 센서 데이터를 위로, `mock` 모드 있음), Sensor Drivers(`Lidar.cpp` RPLIDAR C1 · mock, `Imu.cpp` BNO055, 카메라는 `cam_stream.py`).
- Realtime Controller (`pico`, `pico/main.py`): 모터 PWM, 서보, 엔코더 카운트, 소프트 리미트, 슬루 제한, 링크 끊기면 중립 복귀. 프로토콜은 [HARDWARE_INTERFACE.md](../HARDWARE_INTERFACE.md) (PR #5).

## 3. 실행 기기 배치

| 기기 | 부분 | 이유 |
|---|---|---|
| `laptop` | operator, mission / perception, mapping, planning | 계산이 크다 (Pi 4 2GB, 여유 약 1.4GB, swap 없음) |
| `robot` (Pi) | state estimation, path tracking, Command Gate, kinematics, Hardware Interface | 가볍고, Wi-Fi가 끊겨도 위치 추적과 정지 판단이 로버 안에서 계속돼야 함 |
| `pico` | Realtime Controller | ms 단위 제어와 마지막 보호 |

원칙
1. `laptop` → `robot` 경계는 **경로(waypoints), 위치 보정(pose correction)과 stop 요청만** 넘는다. 속도 명령은 Wi-Fi를 타지 않는다. 예외: 수동 모드(gamepad)의 `motion`은 Wi-Fi로 넘어가고, Gate timeout 0.5 s로 막는다.
2. 보호는 두 겹: Command Gate(명령 끊김 · stop 요청)와 Realtime Controller(출력 제한 · 링크 타임아웃).
3. Pico 시리얼은 PicoBridge 하나만 연다.

동작 규칙
- `laptop` ↔ `robot` 끊김: Command Gate가 정지, state estimation과 기록은 계속. 재연결되면 현재 pose를 넘기고 `laptop`이 SLAM으로 보정한 뒤 재계획.
- `robot` ↔ `pico` 끊김: Realtime Controller가 속도 0 · 조향 중립.

## 4. 이미 정한 것

- 시험 환경: 조명이 충분한 통제된 실내에서 기능을 하나씩 검증한다. 어둠은 고려하지 않는다.
- SLAM은 `laptop` (Pi 메모리 부족).
- optical flow: 웹캠(C270)을 아래로 20~30° 꺾어 화면 아래쪽 바닥 영역에서 계산. 카메라 높이 · 각도를 고정 · 보정하면 바닥 위 이동을 실제 거리로 환산 가능. **2단계** — EKF는 엔코더 + IMU로 먼저 만들고 다음에 붙인다. 화면 위쪽은 사람 탐지에 그대로 쓴다.
- Pico 펌웨어와 Pi ↔ Pico 프로토콜 (PR #5).
- 기구 실측: 트레드 중간 62.5 cm / 앞 · 뒤 56.5 cm, 휠베이스 53 cm (앞–중간 28, 중간–뒤 25).

## 5. 검토 요청

1. 블록 · 부분 구성에 빠지거나 불필요한 것.
2. 실행 기기 배치에서 바꿀 것.
3. 통신 두절 시: 즉시 정지 후 대기(초안) vs 마지막 연결 지점으로 복귀.
4. 사람 후보 확인 단계(접근 후 재탐지) 포함 여부.
5. 조향: 앞 · 뒤 반대로 꺾는 대칭 4WS 기본안.
