# Hardware Interface (Pi · Pico · sensors)

갱신: 2026-11-07 · 작성: 강연우 (하드웨어·Pico 펌웨어 담당)

소프트웨어 쪽에서 실물 로버에 붙을 때 필요한 정보만 모았다. "확인됨"과 "미확인"을 구분한다. 결선이 바뀌면 이 문서부터 고친다.

## 1. 바뀐 점 (11/7) — 먼저 읽을 것

- **모터·서보는 이제 Pi GPIO가 아니라 Pico 2가 구동한다.** 11/3 데모 때의 Pi 직결 핀(MDD10A BCM 5/6/16/20, 서보 BCM 18/12/13/19)은 **더 이상 연결돼 있지 않다.**
  - 그래서 `robot/` 의 `GpioMotor`, `ros2/scripts/demo/roveraid_demo_drive.py`, `roveraid_steering_gpio_test.py` 처럼 Pi GPIO를 직접 쓰는 코드는 실물에서 아무것도 움직이지 않는다.
  - 대신 Pi가 USB 시리얼로 Pico에 명령을 보낸다 (3절 프로토콜).
- 엔코더 2개, 초음파 4개도 Pico에 붙어 있고 값은 Pico 텔레메트리로 받는다.
- BNO055, RPLIDAR, 웹캠은 그대로 Pi에 붙어 있다.

## 2. 연결 구성

```text
Raspberry Pi 4 (2GB, Ubuntu 24.04)   전원: USB-C 충전기 (주행 시 별도 결정)
├── USB ── Pico 2 (RP2350, MicroPython)   /dev/ttyACM0, USB CDC
│   ├── MDD10A ── 왼쪽 모터 ×3 (채널1) / 오른쪽 모터 ×3 (채널2)
│   ├── 서보 MG996R ×4 (FL, FR, RL, RR 조향)
│   ├── 엔코더 ×2 (좌·우 중간 바퀴)
│   └── 초음파 HC-SR04 ×4 (네 모서리)
├── I2C1 (GPIO2 SDA / GPIO3 SCL) ── BNO055, 주소 0x29
├── USB ── RPLIDAR C1   /dev/ttyUSB0
└── USB ── 웹캠 Logitech C270   /dev/video0 (마이크 포함, ALSA card 1)
```

- 배터리(3S LiPo)는 평소 빼 둔다. **배터리가 없으면 모터·서보는 신호를 보내도 움직이지 않는다.** 배터리는 로버 옆에 사람이 있을 때만 꽂는다.
- Pi는 상시 켜 둔다. 접속은 팀 Tailscale (장치 이름 `rover`), 계정·비밀번호는 따로 공유.

## 3. Pi ↔ Pico 시리얼 프로토콜

USB CDC, 한 줄 = 한 메시지, `\n` 끝. 펌웨어 소스는 [`pico/main.py`](../pico/main.py).

### Pi → Pico

| 명령 | 형식 | 의미 |
|---|---|---|
| Drive | `C,<v_left>,<v_right>,<a_fl>,<a_fr>,<a_rl>,<a_rr>` | v: duty −1~1 (+ = 전진), a: 조향각 deg (0 = 직진) |
| E-stop | `E` | 모든 출력 끔, `R` 전까지 유지 |
| Clear | `R` | E-stop 해제 |

### Pico → Pi

| 메시지 | 형식 | 주기 |
|---|---|---|
| Telemetry | `S,<ms>,<enc_l>,<enc_r>,<d_fl>,<d_fr>,<d_rl>,<d_rr>,<flags>` | 20 Hz |
| Reject | `X,<reason>` (`unknown`, `parse`) | 잘못된 명령마다 |

- `ms`: Pico 부팅 후 ms. `enc_l/enc_r`: 누적 카운트 (x1 디코딩, A상 상승 엣지). `d_*`: 초음파 거리 cm, 응답 없으면 −1. 초음파는 한 틱에 1개씩 돌아가며 재므로 각 값은 5 Hz로 갱신된다.
- `flags` (bit OR): 1 링크 타임아웃, 2 E-stop, 4 마지막 명령이 클램프됨, 8 부팅 후 명령 없음.

### 안전 동작 (Pico가 보장)

- 부팅 후 첫 `C` 전까지 출력 없음 (서보도 힘 빠진 상태).
- 마지막 유효 `C` 후 **300 ms** 지나면 속도 0, 조향 0으로 복귀. 계속 움직이려면 `C`를 주기적으로 (20 Hz 권장) 보내야 한다.
- duty 상한 0.6, 변화율 2.0/s. 조향 ±30° 소프트 리미트, 180°/s.
- 상위(Pi/노트북)의 판단과 관계없이 위 동작은 Pico에서 항상 걸린다.

### 시리얼 동시 접근 주의

- `/dev/ttyACM0`는 잠그지 않는다 (동시에 접속할 일이 있을 수 있어서 팀 결정). 대신 **두 프로그램이 동시에 열면 텔레메트리 줄을 나눠 가져가고 명령도 섞인다.** 모터를 움직이는 프로그램은 한 번에 하나만, 시작 전에 단톡에 알린다.
- 장기적으로는 시리얼을 혼자 여는 bridge 프로세스 하나를 두고 나머지가 그 bridge에 붙는 구조로 간다 (미구현).

### 사용 예

```bash
# Pi에서 키보드 조종 (w/s 주행, a/d 조향, space 정지, q 종료)
ssh -t rover@<pi> "python3 ~/pico/pico_teleop.py"

# 펌웨어 갱신 (Pi에서; mpremote는 ~/pico/lib 에 설치돼 있음)
cd ~/pico && PYTHONPATH=lib python3 -m mpremote connect /dev/ttyACM0 cp main.py :main.py + reset
```

## 4. Pico 2 핀 배정

GPIO 번호 기준. Pico는 USB가 로버 전방, 왼쪽 열 = 로버 왼쪽.

| 기능 | GPIO | 비고 |
|---|---|---|
| MDD10A DIR1 / PWM1 (왼쪽 모터 ×3) | 4 / 5 | PWM 20 kHz, slice 2B. DIR 0 = 전진 (미확인) |
| MDD10A PWM2 / DIR2 (오른쪽 모터 ×3) | 20 / 21 | PWM 20 kHz, slice 2A |
| 서보 FL / RL | 6 / 7 | 50 Hz, slice 3A / 3B |
| 서보 FR / RR | 18 / 19 | 50 Hz, slice 1A / 1B |
| 엔코더 왼쪽 A / B | 8 / 9 | 3.3 V, 풀업 |
| 엔코더 오른쪽 A / B | 16 / 17 | 3.3 V, 풀업 |
| 초음파 FL (위, 천장) TRIG / ECHO | 0 / 1 | |
| 초음파 FR (아래, 앞 바닥) TRIG / ECHO | 2 / 3 | |
| 초음파 RL (아래, 뒤 바닥) TRIG / ECHO | 10 / 11 | |
| 초음파 RR (위, 천장) TRIG / ECHO | 12 / 13 | |
| 배터리 전압 분압 (예약) | 26 (ADC0) | 미결선 |
| 예비 | 14, 15, 22, 27, 28 | |

## 5. 센서·구동기 정보

| 항목 | 내용 | 상태 |
|---|---|---|
| 구동 모터 | JGB37-520 ×6, 12 V. 좌 3개 / 우 3개가 MDD10A 채널 하나씩 공유 (좌우 차동만 가능, 바퀴별 속도 제어 불가) | 결선 확인 |
| 엔코더 | 좌·우 **중간 바퀴**만. Hall 11 PPR (모터축), 2상. 감속비 미확인 (1:270 추정 → x1 기준 약 2,970 count/바퀴 1회전) | 카운트·부호 미확인 |
| 엔코더 모터 속도 | 엔코더 모터 37 RPM, 나머지 4개 35 RPM (무부하, 약 5.7% 차이) | 사양값 |
| 조향 | MG996R ×4, 앞 2·뒤 2. 펄스 = 1500 µs + deg × 11.1 µs. 중앙 4개 모두 1500 µs 가정 | 중립·부호 미확인 |
| 초음파 | HC-SR04 ×4, 네 모서리. FR·RL = 아래 방향 (바닥·단차 감시), FL·RR = 위 방향 (천장 감시) | 4개 응답 확인. RR이 37↔106 cm로 번갈아 나옴, 원인 미확인 |
| IMU | BNO055 (GY 보드), Pi I2C1 **0x29**. **뒤집혀 장착** (정지 시 acc z ≈ −9.7) → axis remap 필요 | 동작 확인. 레지스터 최상위 비트가 가끔 깨짐 (Pi I2C clock stretching 추정, `i2c_arm_baudrate=10000` 적용 예정) |
| LiDAR | RPLIDAR C1, 마스트 위. 460800 baud | 확인 (10회전 3.1 s, 유효 3480점) |
| 카메라 | C270, 1280x720 MJPEG 약 25 fps. 스트림: Pi `~/hw_test/cam_stream.py` → `http://<pi>:8080/` (640x480 15 fps 기본) | 확인 |
| 마이크 | C270 내장, ALSA card 1 | 신호 확인, 음성 청취 미확인 |

## 6. 아직 없는 값 (실측 예정)

바퀴 지름 (문서마다 Ø120~130 불일치), 감속비, 휠베이스, 트레드, 조향축 위치, 서보 중립·조향 한계, 모터 전진 방향, 엔코더 부호, 전체 무게. 측정하면 이 표에 채운다.
