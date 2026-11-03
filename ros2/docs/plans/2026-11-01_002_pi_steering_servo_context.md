# Plan — 2026-11-01_002_pi_steering_servo_context

## Goal
- 사용자가 확인한 Pi 측 조향 서보 인터페이스라는 맥락을 기존 핀 배치 초안에 반영한다.

## Files
- docs/hardware_specs.md
- README.md
- docs/plans/2026-11-01_002_pi_steering_servo_context.md
- docs/daily_notes/2026-11-01.md

## Changes
- 대상이 Pi 측이고 용도가 조향 서보임을 사용자 확인 사항으로 기록한다.
- 네 GPIO/핀 대응쌍은 그대로 유지한다.
- 번호 체계, 직접 출력/드라이버 경유 여부, 신호 설정 및 기존 Pico/PCA9685 설계와의 관계는 확인 대기로 남긴다.
- README의 보드/용도 확인 대기 표현을 현재 상태에 맞게 정리한다.

## Impact / Risk
- 문서 설명만 보완한다. 제어 구조·역할 문서·코드·안전 계약을 변경하지 않는다.

## Validation
- 사용자 확인 내용과 표 보존, 미확정 항목 구분, 링크·공백 및 비대상 파일 해시를 확인한다.
- 보드 핀맵 대조·GPIO 출력·하드웨어 시험은 수행하지 않는다.

## Approval
- 사용자: “조향서보랑 pi쪽 인터페이스야”. 앞선 기록 요청의 보완 정보로 반영한다.
