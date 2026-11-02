# D-01~D-03 — 영상 촬영용 최소 구동 시험

## 현재 상태

| ID | 범위 | 상태 |
|---|---|---|
| D-01 | 배선·드라이버·제어 주체 | 사용자 확인: Pi 직결, MDD10A 사용, 아래 핀 배치, Pico 미사용. 전원/GND·극성·정지 성능은 실물 확인 대기 |
| D-02 | 제한 시간 수동 시험 코드 | 구현 및 dry-run/가짜 GPIO 자동 검증. 실물 출력 미검증 |
| D-03 | 바퀴 공중 시험·저속 촬영 | NOT TESTED ON HARDWARE. 별도 승인 및 현장 확인 필요 |

현재 촬영용 시험 구조는 `Pi → MDD10A → DC 모터`, `Pi → 서보 신호선`이다(2026-11-03 사용자 확인). ROS 2·Pico·TCP·Odometry·TF는 사용하지 않는다. 기존 B 로드맵과 최종 운용 제어/안전 계약은 별도로 유지한다. A/C 코드도 변경하지 않았다.

## 배선과 출력 의미

| 기능 | BCM GPIO | Pi 물리 헤더 핀 |
|---|---|---|
| MDD10A PWM1 / 좌측 | 5 | 29 |
| MDD10A DIR1 / 좌측 | 6 | 31 |
| MDD10A PWM2 / 우측 | 16 | 36 |
| MDD10A DIR2 / 우측 | 20 | 38 |
| 조향 FL | 18 | 12 |
| 조향 FR | 12 | 32 |
| 조향 RL | 13 | 33 |
| 조향 RR | 19 | 35 |

- GPIO는 신호 출력이다. DC 모터 전력은 MDD10A의 외부 전원, 서보 전력은 적절한 별도 전원으로 공급하며 제어계와 공통 GND를 확인한다. 각 채널에 묶인 모터의 수·부하 전류는 이 코드가 확인하지 못한다.
- MDD10A는 **sign-magnitude** 방식으로 사용한다. PWM에는 듀티, DIR에는 0/1을 보내며 정지 출력은 PWM을 끄고 LOW로 쓰는 것이다. locked-antiphase 방식이나 서보 펄스를 모터 입력에 사용하지 않는다. [Cytron 공식 설명](https://www.cytron.io/tutorial/using-mdd10a-arduino-uno)
- PWM=LOW 출력 성공과 실제 차체 정지·정지 거리·경사로 정지 유지 성능은 구분한다. 실물에서 확인하기 전에는 제동 성능을 보장하지 않는다.

## 파일 및 초기 설정

사람이 실행하는 실물 시험 도구는 `scripts/demo/`, 설정은 `config/`, 가짜 GPIO 자동 검증은 `tests/`에 둔다. 2026-11-03 경로 정리 이전 기록의 루트 실행 경로는 현재 `scripts/demo/` 경로로 바꿔 사용한다.

- [roveraid_demo_drive.py](../scripts/demo/roveraid_demo_drive.py): CLI, 설정 검증, 기한 제어, lgpio 출력 및 종료 처리.
- [config/demo_drive.toml](../config/demo_drive.toml): 확인된 GPIO, 초기 PWM 주파수/서보 중립값, 측정 후 입력할 극성/보정 여부.
- [test_demo_drive.py](../tests/test_demo_drive.py): 가짜 lgpio와 가짜 단조 시계만 사용하는 테스트.
- [기존 단일 서보 시험](../scripts/demo/roveraid_steering_gpio_test.py)은 그대로 보존했다. 두 도구를 동시에 실행하지 않는다.

기본 설정은 **초기 시험값**이며 확정된 하드웨어 사양이 아니다.

| 항목 | 값·제한 | 의미 |
|---|---|---|
| 모터 PWM | 1000Hz 초기값, 설정 허용 100~10000Hz | lgpio 소프트웨어 PWM 시험 설정 |
| 듀티 | 기본 10%, 허용 0.1~20% | 실제 속도 아님. 회전/저속/기동 토크 보장 없음 |
| 출력 시간 | 기본 0.5초, 허용 0.1~3초 | 한 명령의 기간. drive는 조향 선행 대기 추가 |
| 서보 | 50Hz, 중립 초기값 각 1500µs | 실제 직진/중립값으로 가정하지 않음 |
| 서보 범위 | 1300~1700µs, 설정 중립 대비 ±100µs 이내 | 초기 시험 창이며 기구 안전 범위 보장 아님 |
| drive 조향 선행 대기 | 기본 0.5초, 설정 허용 0.3~2초 | 실제 조향 완료 피드백 없음 |
| 전진 DIR | 좌·우 각각 `-1` | 미확인 표시. 개별 시험 후 0 또는 1로 기록 |
| 조향 보정 | `calibrated = false` | 네 중립·사용할 펄스 범위 확인 후 true로 기록 |

Pi에서 실제 실행하려면 `lgpio` 모듈(통상 Ubuntu의 `python3-lgpio` 패키지), GPIO 장치 접근 권한 및 올바른 gpiochip 식별이 필요하다. 현재 개발환경에는 lgpio가 없으며 이 작업에서 설치·권한·OS 설정을 변경하지 않았다. `--chip` 기본값은 0이며 Pi 4 실물에서 확인한다. dry-run에는 lgpio가 필요 없다.

## WSL2에서 출력 계획 확인

저장소 루트에서 실행한다. 기본 동작이 dry-run이므로 `--arm` 없이 하드웨어에 접근하지 않는다.

```bash
python3 -B scripts/demo/roveraid_demo_drive.py servo --wheel FL --dry-run
python3 -B scripts/demo/roveraid_demo_drive.py servo --wheel FL --pulse FL=1550 --seconds 0.5 --dry-run
python3 -B scripts/demo/roveraid_demo_drive.py motor --side left --dir-level 0 --duty 10 --seconds 0.5 --dry-run
python3 -B scripts/demo/roveraid_demo_drive.py motor --side right --dir-level 1 --duty 10 --seconds 0.5 --dry-run
python3 -B scripts/demo/roveraid_demo_drive.py drive --seconds 0.5 --dry-run
python3 -B scripts/demo/roveraid_demo_drive.py stop --dry-run
```

`motor`는 한쪽 채널만 선택하고 raw DIR=0/1을 시험한다. 그 방향을 전진이라고 주장하지 않는다. 반대 채널의 PWM도 LOW로 초기화·정리한다. `servo` 역시 양쪽 DC PWM을 LOW로 유지하며 선택 서보만 출력한다.

`drive`는 네 서보를 지정 펄스(생략 시 각 설정 중립값)로 보낸 뒤 대기하고 양쪽 모터를 한 번 구동한다. 기본 방향은 forward이며 `--direction reverse`는 확인된 좌우 전진 DIR을 각각 반전한다. dry-run에서는 미확인 극성을 `UNSET`으로 표시한다. 실제 drive는 전진 DIR 두 값 및 `steering.calibrated=true`를 모두 요구한다.

`--pulse FL=1550 --pulse FR=1450`처럼 바퀴마다 절대 펄스를 지정할 수 있다. **숫자의 증감은 좌/우 회전 방향이나 조향각을 의미하지 않는다.** 전후륜 위상·링크 방향·기구학을 가정한 left/right 프리셋은 없다. 요청하지 않은 서보는 drive에서 각 설정 중립값을 사용한다. 중심으로 자동 복귀하는 동작도 없다.

기본 설정은 실행 작업 디렉터리와 관계없이 저장소의 `config/demo_drive.toml`을 사용한다. 다른 설정 파일은 `--config /path/to/local_demo.toml`로 선택한다. GPIO는 승인된 배치와 일치해야 하며 키 누락·오타·물리 핀 번호 오입력·NaN/무한대·범위 초과는 GPIO import 전에 거부한다. `--arm`과 `--dry-run`은 동시에 사용할 수 없다.

## 실물 시험 순서 — 실행 전 별도 승인

아래는 현장에서 사용할 절차다. 이번 작업에서는 실제 실행하지 않았다.

1. 모터 전원을 끈 상태에서 배선·모터/서보 전원 정격·공통 GND·MDD10A 입력 방식을 확인한다. Pi 부팅/재부팅·GPIO 해제 상태에서 모터 입력이 떠서 구동되지 않도록 하는 하드웨어 대책은 A와 확인한다.
2. 즉시 사용할 수 있는 모터 전원 차단 수단과 담당자를 확보하고 바퀴를 지면에서 띄운다. 첫 서보 시험은 링크를 분리하거나 간섭 없는 범위를 확보한다.
3. `servo --wheel FL` 등 한 서보씩 중립과 작은 펄스 차이를 확인한다. 실제 중립값을 해당 `center_us`에 적고 네 바퀴의 사용할 범위를 확인한 뒤 `calibrated=true`로 기록한다. 처음부터 네 서보를 동시에 움직이지 않는다.
4. `motor --side left --dir-level 0`, `motor --side right --dir-level 0` 등 한쪽씩 짧게 시험한다. 필요한 경우 정지 확인 후 DIR=1을 별도 시험한다. 확인한 각 전진 레벨을 `motor.left/right.forward_level`에 적는다. 낮은 듀티에서 돌지 않으면 즉시 중단하고 부하·전류·전원을 확인한다.
5. 바퀴가 떠 있는 상태에서 기한 만료, Ctrl+C, 종료 신호 시 양쪽 정지를 확인한다. 정지 출력의 실제 반응·서보 PWM 해제 후 거동·전원 차단 기능을 확인한다.
6. 확인된 설정으로 `drive`의 짧은 전진부터 시험한다. 후진과 회전은 전진/정지 확인 후 별도로 진행한다. 처음 지상 시험은 평탄하고 통제된 공간에서 수행한다.

실제 출력은 위 명령의 `--dry-run`을 제거하고 `--arm`을 지정했을 때만 발생한다. `--arm`은 물리 조건을 검증하는 센서 기능이 아니라 작업자의 확인이다. 설정 파일의 보정값 또한 실제 측정을 대신하지 않는다.

## 정지와 타임아웃의 정확한 범위

- 각 명령은 입력을 계속 기다리지 않고 한 번 실행해 종료한다. `time.monotonic()` 기한을 사용하며 입력 부재로 무기한 구동하는 루프는 없다. 모터 출력 직전에도 남은 시간을 검사한다.
- lgpio에는 유한 `pulse_cycles`를 전달한다. 모터 사이클 수는 남은 기간에서 계산하며 0(무한 반복)은 구동 요청에 사용하지 않는다. 일반적인 주 제어 루프 지연에도 PWM을 무한 갱신하지 않도록 한 것이다.
- Ctrl+C/SIGINT, SIGTERM, SIGHUP 및 예외 시 **양쪽 모터 PWM 중지·LOW를 우선 시도 → 서보 PWM 해제 → 자원 반환** 순서로 정리한다. 한 GPIO 호출이 실패해도 다른 정지·해제 호출은 시도하며 오류를 보고한다.
- `stop`은 GPIO를 새로 확보할 수 있을 때 양쪽 PWM에 정지 출력을 보내는 단독 명령이다. **이미 실행 중인 다른 프로세스의 GPIO를 빼앗거나 원격으로 중단하지 못한다.** 실행 중인 시험은 해당 프로세스에 Ctrl+C/종료 신호를 보내고, 실제 비상상황에서는 외부 전원 차단 수단을 사용한다. GPIO가 사용 중이면 실패를 보고한다.
- SSH 단절 자체를 감지하는 heartbeat는 없다. SIGHUP이 전달되면 종료 처리하고, 전달되지 않아도 원래 한 번의 제한 시간 후 종료를 시도한다. Pi 정지·스케줄러 정지·SIGKILL·전원 상실에서 소프트웨어 정지는 보장되지 않는다. **독립 하드웨어 워치독/비상정지는 구현하지 않았다.**
- 종료 시 서보 PWM을 해제하므로 조향 유지 토크가 보장되지 않는다. PWM은 소프트웨어 타이밍이므로 지터와 부하 영향을 실물에서 확인해야 한다. 무인·지속 운전용 프로그램이 아니다.

API 확인 출처: [lgpio 공식 Python 소스](https://github.com/joan2937/lg/blob/master/PY_LGPIO/lgpio_extra.py), [PWM 송신 구현](https://github.com/joan2937/lg/blob/master/lgPthTx.c). 웹 문서 접근은 403이어서 공식 저장소의 API 정의와 유한 사이클 종료 시 LOW 동작을 확인했다. 개발환경에는 실제 모듈이 없어 설치 버전/API·전기적 출력 대조는 NOT RUN이다.

## 검증과 촬영 기록

```bash
python3 -B -m unittest discover -s tests -v
python3 -B scripts/demo/roveraid_demo_drive.py --help
```

모의 검증에는 명령 범위·보정 전 구동 차단·선택 핀·조향 선행·유한 PWM·시간 만료·초기화/출력 실패·종료 신호·정리 중 오류 시 다른 채널의 정지 시도를 포함한다. 모의 PASS는 실물 주행 성공을 의미하지 않는다.

D-03에서는 일시, 실행 명령, 커밋(없으면 미커밋 표시), 설정 파일, 각 DIR의 실제 방향, 서보 중립/시험 펄스, 듀티·시간, 정지 반응, 전원 차단 확인, 영상 보관 위치를 Daily Notes에 남긴다. 이번 소스에는 촬영 영상이나 접속 정보를 포함하지 않는다.
