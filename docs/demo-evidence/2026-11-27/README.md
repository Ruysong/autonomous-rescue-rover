# RoverAid 교실 주행 · 2026-11-27

같은 날 촬영한 두 교실 자율주행의 기록이다. CSV는 주요 사건을 한국시간과 주행 시작 기준 경과 시간으로 정리한다. 압축 JSONL은 지도 재생에 필요한 준비 구간의 LiDAR 스캔부터 주행 종료까지 포함한다.

| 실험 | 주행 구간 (KST) | 확인한 내용 | 자료 |
| --- | --- | --- | --- |
| 교실 일반 자율주행 | 13:35:43–13:38:51 | Qwen 목표 6회, 경로 전송, 사람 탐지 알림 1회와 작업자 E-STOP | [타임라인](classroom-autonomy.csv) · [리플레이 로그](classroom-autonomy.jsonl.gz) · [지도](classroom-autonomy-map.png) |
| 교실 암전·LiDAR 전환 | 13:52:23–13:54:18 | 암전 확인, Qwen의 LiDAR 단독 탐색 승인, 지도 탐색, 복광 후 사람 재탐지와 작업자 E-STOP | [타임라인](classroom-lidar-blackout.csv) · [리플레이 로그](classroom-lidar-blackout.jsonl.gz) · [지도](classroom-lidar-blackout-map.png) |

## 암전 전환에서 확인한 결과

13:52:34.719에 카메라 평균 밝기 1.8/255, 저조도 지속 0.92초, 최신 LiDAR 수신 상태를 Qwen에 보냈다. 약 0.73초 뒤 LIDAR_ONLY로 전환했고, 13:52:36.559에 전환 후 처음으로 0이 아닌 바퀴 출력이 기록됐다. 속도 상한 0.10 m/s로 탐색하며 미관측 구역 주변 목표를 다섯 번 선택했다. [암전 전환 타임라인](classroom-lidar-blackout.csv)

13:53:45.566에 VISION_SEARCH로 복귀했고, 약 2.58초 뒤 오른쪽에서 사람 탐지 알림이 기록됐다. 13:53:57에는 같은 트랙의 관측 방향이 후보 선택에 반영됐다. 위 시각과 값은 이 교실 주행에서 기록된 값이다. 바퀴 출력은 구동 명령을 뜻하며 실측 속도는 별도로 측정해야 한다. [암전 전환 타임라인](classroom-lidar-blackout.csv)

## 지도 재생

압축 파일을 푼 뒤 프로젝트의 Python 환경에서 다음 명령으로 재생할 수 있다.

```text
python -m laptop.navigation.run_explore --replay classroom-lidar-blackout.jsonl --no-view
```

공유용 로그는 LiDAR 스캔, 위치·바퀴 출력, 경로와 보호 명령, 센서 모드 및 Qwen 판단 등 시연 검증에 필요한 필드로 구성했다. 사람 알림에는 세션 내부 트랙 번호, 영상 내 방향과 탐지 점수가 남아 있다. 지도 PNG는 당시 앱이 저장한 결과이며 주행 종료 직후의 스캔도 포함할 수 있다.
