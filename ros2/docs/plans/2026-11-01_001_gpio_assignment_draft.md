# Plan — 2026-11-01_001_gpio_assignment_draft

## Goal
- 사용자가 제공한 FL/FR/RL/RR GPIO·핀 번호 배치안을 하드웨어 사양 초안에 기록한다.

## Files
- docs/hardware_specs.md
- README.md (사양 초안 상태와 링크)
- docs/plans/2026-11-01_001_gpio_assignment_draft.md
- docs/daily_notes/2026-11-01.md

## Changes
- 사용자 원문 배치 FL GPIO18/12, FR GPIO12/32, RL GPIO13/33, RR GPIO19/35를 표로 기록한다.
- 보드·번호 체계·신호 용도·위치 기준·실물 검증 여부를 확인 전까지 미확정으로 둔다.
- 기존 Pico/PCA9685 참고 설계와의 관계를 확인 대상으로 남기며 제어권이나 실제 결선을 변경하지 않는다.
- README에 신규 하드웨어 사양 초안을 연결한다.

## Impact
- 문서 기록에 한정한다. A 소유 코드·역할 문서·기존 ROS 코드·AGENTS.md는 수정하지 않는다.

## Risk
- 보드와 신호 용도를 추정하면 다른 핀 배치나 기존 제어 구조와 혼동할 수 있다. 사용자 제공 초안(PROPOSED)과 확인 대기를 명시한다.

## Validation
- 표의 네 대응쌍을 사용자 원문과 비교하고 초안/미확정 표시, 링크 및 공백을 확인한다.
- 비대상 파일의 SHA-256이 유지됐는지 확인한다.
- 실제 보드 핀맵 대조·결선·GPIO 출력·하드웨어 접속은 수행하지 않는다.

## Approval
- 사용자가 대략적인 인터페이스와 네 핀 대응쌍을 제공하고 적절한 위치에 기록하도록 요청했다.
