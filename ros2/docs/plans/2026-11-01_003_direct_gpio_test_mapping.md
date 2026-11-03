# Plan — 2026-11-01_003_direct_gpio_test_mapping

## Goal
- PCA9685 없이 Pi에 직접 연결된 조향 서보의 GPIO 테스트를 사용자 핀 배치와 일치시킨다.

## Files
- roveraid_steering_gpio_test.py
- roveraid_steering_test.py (PCA9685 이전 시험용이라는 안내만 추가)
- tests/test_steering_gpio.py
- docs/hardware_specs.md
- README.md
- docs/plans/2026-11-01_003_direct_gpio_test_mapping.md
- docs/daily_notes/2026-11-01.md

## Changes
- GPIO 테스트의 wheel 인자를 FL=BCM18/물리12, FR=BCM12/물리32, RL=BCM13/물리33, RR=BCM19/물리35에 연결한다.
- --gpio는 기존 호출 호환용 선택 인자로 유지하되 wheel의 배치와 다른 값은 오류 처리한다. 기존 GPIO17 예시는 현재 배치로 교체한다.
- 선택한 BCM/물리 핀을 함께 출력하고, 기존 --arm/--dry-run 및 펄스 범위·단일 서보 시험 동작은 유지한다.
- PCA9685 파일은 삭제/대체하지 않고 현재 직결 시험에서 사용하지 않는다는 안내만 추가한다.
- 사양 문서에 사용자 확인 직결 구성을 기록하고 README에 무출력 시험 예시와 이전 PCA 파일 구분을 추가한다.
- 가짜 lgpio 모듈을 사용하는 자동 테스트를 추가한다.

## Impact / Migration
- ROS/Pico 프로토콜이나 실제 제어권을 변경하지 않는다. Pi 측 수동 시험 도구만 보완한다.
- 기존 FL test --gpio 18 호출은 유지된다. --gpio 생략 시 바퀴 매핑을 사용한다. 배치와 불일치하는 --gpio는 더 이상 허용하지 않는다.
- sw_layers_assignment.md는 수정하지 않는다. 원문의 Pico/PCA9685 참고 구성과 현재 Pi 직결 시험의 차이는 사양 문서에 기록한다.

## Risk
- 실제 GPIO를 다루는 경로에 영향을 주는 변경이다. 잘못된 바퀴/핀 조합을 거부하며 기존 arm·범위 검사를 유지한다.
- 기존 50Hz·1500us 중심·1300~1700us 범위는 시험 도구의 초기값이며 실제 서보의 보정/안전 범위로 확정하지 않는다.
- 이번 구현 승인은 하드웨어 실행·의존성 설치·Pi 접속 승인이 아니다.

## Validation
- Python AST 구문 검사, unittest의 가짜 lgpio 호출 검증과 실제 CLI --dry-run/--help를 수행한다.
- 네 바퀴 매핑, 명시 GPIO 일치/불일치, 물리 번호 오입력, arm 미지정 차단, 펄스 제한, dry-run 무접근, 단일 핀 출력 및 종료 정리를 검사한다.
- 테스트에는 실제 lgpio 대신 가짜 모듈만 제공한다. 실제 모터·서보·센서·포트·Pi에는 접근하지 않는다.
- 문서/코드 핀 대응, 상대 링크, 공백과 비대상 파일 해시를 확인한다.

## Approval
- 사용자: GPIO 테스트 예정이므로 전달한 인터페이스와 불일치하는 부분 수정 요청. PCA9685 없이 Pi 직결 상태임을 명시했다.
