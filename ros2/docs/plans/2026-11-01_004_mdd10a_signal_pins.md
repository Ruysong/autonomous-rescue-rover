# Plan — 2026-11-01_004_mdd10a_signal_pins

## Goal
- 사용자 제공 이미지의 MDD10A PWM/DIR 신호 네 개를 하드웨어 사양 초안에 기록한다.

## Files
- docs/hardware_specs.md
- README.md
- docs/plans/2026-11-01_004_mdd10a_signal_pins.md
- docs/daily_notes/2026-11-01.md

## Changes
- PWM1(좌)=GPIO5/물리29, DIR1(좌)=GPIO6/물리31, PWM2(우)=GPIO16/물리36, DIR2(우)=GPIO20/물리38을 이미지 그대로 기록한다.
- 조향 서보 핀과 신호를 구분하고, 핀 중복 여부만 문서상 확인한다.
- PWM 주기·듀티·방향 극성·정지 방식·최종 구동 제어 주체는 핀 표만으로 확정하지 않는다.
- README에서 MDD10A 표도 동일 사양 문서에 기록됨을 안내한다.

## Impact / Risk
- 문서만 변경한다. 모터 제어 코드·GPIO 출력·Pi/Pico 통신·역할/안전 계약은 변경하지 않는다.
- 이미지 전사와 핀 중복 검사는 실제 결선·전기적 적합성·모터 동작 검증이 아니다.

## Validation
- 네 신호/GPIO/물리 핀 대응을 이미지와 비교하고 기존 서보 네 핀과의 중복 여부를 검사한다.
- 링크·공백 및 코드/역할 문서 등 비대상 파일 해시를 확인한다.
- 보드 핀맵 추가 검증·결선·GPIO/모터 시험은 실행하지 않는다.

## Approval
- 사용자가 MDD10A 신호선 네 개의 이미지를 제공하고 인터페이스에 반영하도록 요청했다.
