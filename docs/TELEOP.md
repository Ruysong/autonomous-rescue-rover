# Gamepad 수동 조작 (operator → control / safety)

작성 2026-11-14 · 강연우. 최종 시연에서 자율 주행이 안 될 때를 대비한 수동 모드.

```text
gamepad ── laptop/teleop/run_gamepad.py ──TCP 9998 motion──▶ robot rover_controller ──USB──▶ Pico
                     ▲                    ◀──TCP 9997 telemetry──  (CommandGate → Kinematics → PicoBridge)
```

## 조작

| 입력 | 동작 |
|---|---|
| **LB (누르고 있기)** | deadman. 떼면 정지 1회 후 명령을 보내지 않음 → 로버 Gate가 0.5 s 뒤 timeout |
| 왼쪽 스틱 (하나로 전부) | 위 / 아래 = 전진 / 후진, 좌 / 우 = 조향 (normal: 회전 곡률, spin: 회전 속도). 대각선이면 둘 다 (게임처럼, 11/19 변경) |
| RT | 속도 증가. 놓으면 최대의 30 %, 끝까지 당기면 100 % |
| A | 조향 모드 normal (앞 · 뒤 바퀴 반대로, 이 로버의 Ackermann) |
| B | 조향 모드 spin (제자리 회전). 로버가 `spin_enabled`를 보내기 전에는 거부 |
| Back | **E-stop** (로버에서 래치) |
| Start | E-stop 중이면 해제, 아니면 manual ↔ autonomous 전환 |

- 수동 모드에서는 대시보드(OpenCV 창 · Foxglove `/mission`)에 이 키맵이 표시된다.
- 확인한 패드: Flydigi 2.4G (X360 모드, XInput 순서). 다른 XInput 패드도 같은 번호. C · Z · M1~M4 같은 추가 버튼은 PC에 안 보이므로, 원하면 Flydigi 앱에서 뒷면 버튼을 LB로 매핑해 deadman으로 쓴다.
- 조향 모드가 바뀌면 로버가 스스로 정지 → 조향 → 구동 순서를 지킨다 (약 0.6 s).
- 패드가 빠지면 정지 1회. 노트북 · Wi-Fi가 끊기면 로버 Gate가 정지, Pico는 300 ms 뒤 자체 정지.

## 실행

### 1. 노트북 (먼저)

```bash
pip install pygame
python -m laptop.teleop.run_gamepad            # 0.0.0.0:9998 / 9997 대기
```

Windows 방화벽이 9998 · 9997 TCP 수신을 막으면 허용 규칙을 추가한다 (Tailscale 인터페이스만 허용해도 됨).

### 2. 로버 (Pi)

```bash
# 빌드 (처음 한 번, 의존성 nlohmann-json3-dev)
cmake -S robot -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4
./build/robot_control_tests

# 실행
./build/rover_controller --host <노트북 주소>                 # 실물 Pico (/dev/ttyACM0)
./build/rover_controller --host <노트북 주소> --max-duty 0    # dry run: 조향만, 바퀴 duty 0
./build/rover_controller --host <노트북 주소> --pico mock     # Pico 없이
```

- Pico 시리얼은 `rover_controller`(PicoBridge) 하나만 연다. `pico_teleop.py` 등과 동시에 켜지 않는다.
- 종료하면 Pico에 E-stop(`E`)을 걸어 두고 나온다. 다음 `rover_controller` 실행 때 자동 해제. `pico_teleop.py`를 쓸 때는 `R`을 먼저 보낸다.
- 노트북 방화벽을 열기 어려우면 노트북에서 SSH 역방향 터널을 쓴다:
  `ssh -R 9998:127.0.0.1:9998 -R 9997:127.0.0.1:9997 <로버>` 후 로버에서 `--host 127.0.0.1`.

### 3. 첫 실주행 순서 (배터리는 사람이 옆에 있을 때만)

1. 바퀴를 띄운 상태 (박스 위)에서 `--max-duty 0`: 왼쪽 스틱 좌우로 조향 방향 · 중립 확인 → `robot/config/rover.json`의 `steering.sign`, `trim_deg` 수정. 11/16 확인: 서보 결선이 좌우 반대라 `pico/main.py` `SERVO` GP 번호를 맞바꿈 (FL 18, FR 6, RL 19, RR 7), 네 서보 모두 + = 탑뷰 시계라 `sign` −1. 남은 것은 `trim_deg`와 바뀐 펌웨어로 재확인.
2. 바퀴를 띄운 채 `--max-duty 0.2`: 전진에서 좌우 바퀴가 앞으로 도는지 → `drive.motor_sign`.
3. 바닥에서 낮은 속도로 직진 · 회전. 엔코더로 `max_wheel_speed_mps` 실측 후 수정.
4. 서보 실제 범위를 재고 45° 이상이면 `steering.max_deg`, `spin_enabled` 조정 (Pico `SERVO` 한계도 같이).

## 코드

| 파일 | 내용 |
|---|---|
| `laptop/teleop/controller.py` | 버튼 · 스틱 → 메시지 (pygame 없음, `tests/test_teleop.py`) |
| `laptop/teleop/gamepad.py` | pygame으로 패드 읽기, 뽑고 꽂기 처리 |
| `laptop/teleop/run_gamepad.py` | 20 Hz 루프, 상태 한 줄 표시 |
| `robot/src/control/CommandGate.cpp` | 권한 · timeout · E-stop · 모드 전환 순서 |
| `robot/src/control/Kinematics.cpp` | (v, κ / ω) → duty L/R + 조향 4개 |
| `robot/src/hardware/PicoBridge.cpp` | Pico 시리얼 (C / E / R, S / X), mock |
| `robot/config/rover.json` | 기구 치수, 부호, 중립 보정, 한계 |
| `robot/tests/control_tests.cpp` | 위 셋의 단위 시험 |

## 확인 기록

- 2026-11-14: 로버 Pi에서 빌드 · 단위 시험 통과. 노트북 → 로버 → Pico(mock) 전체 경로, 실물 Pico dry run (duty 0): Pico가 C 명령 수락 (flags 8 → 0), E-stop (2), timeout (1) 확인. 실주행 전 (배터리 · 바퀴 방향 미확인).
