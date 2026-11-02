# Plan — 2026-10-31_001_readme_agents_status

## Goal
- 실제 존재하는 AGENTS.md에 맞게 README의 상태 표시를 바로잡는다.

## Files
- README.md
- docs/plans/2026-10-31_001_readme_agents_status.md
- docs/daily_notes/2026-10-31.md

## Changes
- 사용자가 승인한 README의 AGENTS.md 항목에서 `예정: `만 제거한다.
- AGENTS.md의 작업 절차에 따라 계획과 변경 기록을 남긴다.

## Impact
- README의 폴더 구조 설명에만 영향을 준다.

## Risk
- 코드, 공개 인터페이스, 실물 제어 변경 없음.

## Validation
- Python 읽기 전용 검사로 해당 문구 변경과 README의 나머지 내용 보존을 확인한다.
- AGENTS.md의 기존 SHA-256과 비교해 변경되지 않았는지 확인한다.
- 문서 표시 수정이므로 ROS 빌드 및 Pi/실물 시험은 필요하지 않다.

## Approval
- 사용자: “1. 예정문구 지워도 돼.”
