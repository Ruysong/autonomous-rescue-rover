# RoverAid 최종 시연 · 11월 27일

`jh/final-demo`는 11/20 주행에 사용한 `yw/demo`의 최신 코드와 그 안에 합쳐진 `jh/mission-llm`을 기반으로 한다. Pi의 보정값, Pico 펌웨어, LiDAR 정지 구역은 기존 `yw/demo`를 사용한다.

## 노트북에서 시작

기존 `roveraid-exploration` 환경과 모델이 옆에 있는 준혁 PC에서는 아래 명령으로 런처를 만들고 확인한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_final_demo.ps1
& '.\RoverAid Demo.exe' -Check
```

다른 PC에서는 `.venv`에 `laptop/requirements.txt`를 설치하고 `ROVER_AI_ASSETS`에 YOLO Pose, Qwen GGUF 두 파일, llama.cpp 런타임이 있는 폴더를 지정한다. 모델 파일명은 런처의 준비 검사에 나온다. 준비된 기존 Python에 `ROVER_PYTHON`을 지정해도 된다. 실행 파일은 이 체크아웃 루트에서 사용한다.

1. 게임패드를 연결한다. Pi에 기존 `~/rover_demo/build/rover_controller`, `~/hw_test/cam_stream.py`, 카메라·LiDAR·Pico가 준비되어 있어야 한다. Pi에 접속할 SSH 키와 네트워크도 연결한다.
2. 같은 로버를 제어하는 세션을 종료한 뒤, 로버 옆에서 물리 정지를 맡을 사람과 주행 구간을 확인한다. `RoverAid Demo.exe`를 열고 `LIVE`를 입력한다. 기본 SSH 대상 또는 디렉터리가 다르면 `-Rover`, `-RemoteDir`를 준다. 별도 영상 URL이 있으면 `-PersonSource`로 넘긴다.
3. 런처가 Pi 컨트롤러와 카메라를 연결하고 노트북에서 YOLO Pose, Qwen, Foxglove WebSocket을 함께 시작한다. Qwen의 `ready` 로그와 카메라·LiDAR 상태를 확인한다. 런처가 Foxglove Desktop에서 `ws://127.0.0.1:8765`를 열며, 새 설치에서는 로그인 후 `docs/foxglove/roveraid_layout.json`을 한 번 가져온다. 준비 중에도 RoverAid 창에서 영상, 지도와 제어 상태를 볼 수 있다.
4. 조작은 RoverAid 창에서 한다. **MANUAL (M)** → 게임패드 **LB**를 잡고 왼쪽 스틱, **START / AUTO (Space)** → 자율주행, **PAUSE** → 경로 취소, **E-STOP (E 또는 Back)** → 정지 잠금, **Q** → 종료. E-stop·보호 정지 또는 연결 재수립 후에는 상태를 확인하고 다시 모드를 직접 선택한다.

`-LaptopOnly -PersonSource <영상 파일>`는 노트북 영상·추론 화면을 확인하는 모드다. 설치할 때는 [Foxglove 공식 다운로드](https://foxglove.dev/download)를 사용한다.

## 촬영 순서

| 컷 | 준비 | 영상에서 확인할 것 |
| --- | --- | --- |
| 수동 주행 | 평탄한 구간에서 **MANUAL**. LB + 왼쪽 스틱으로 저속 전진·후진 | 상태가 MANUAL로 바뀌고 LB를 놓으면 구동이 멈춘다. |
| 험로 구간 | 차폭과 지상고에 여유가 있는 낮은 요철 또는 매트. 촬영 전에 동일 코스를 수동 저속으로 확인 | 차체를 옆에서 담고 지도에는 경로를 표시한다. 장애물을 넘어갈 수 있는 지형 판정은 이 컷의 목표에 포함하지 않는다. |
| 사람 탐색 | 통제된 복도와 열린 교실 입구를 연결한다. 사람 역할은 경로 밖에 위치하고 전신이 카메라에 들어오게 배치한다. | XY 지도·경로, Qwen이 선택한 후보 ID와 이유, YOLO 트랙, `PERSON FOUND` 및 `LYING CANDIDATE`를 순서대로 담는다. 화면 속 사람과의 측정 거리 또는 부상 판정은 제공하지 않는다. |
| 암전 전환 | 사람이 없는 넓은 통로에서 카메라 렌즈만 가린다. LiDAR·Pi·카메라 스트림은 켠 채 유지한다. | `VISION_SEARCH → CHECK_LIGHTING → AI_REVIEW → LIDAR_ONLY`. 전환 중 정지, Qwen 응답 이후 최신 LiDAR로 0.05 m/s 상한의 지도 탐색, 복광 시 사람 탐색 복귀를 담는다. |

사람 기록은 트래커가 같은 ID를 연속 세 번 관측할 때 만든다. 로그와 이미지에는 신뢰도, 화면 내 방향, 몸통 자세 후보, **관측 시점 로버 자세**가 남는다. 가까운 이미지 구역의 지속 점유는 기존 VisualGuard가 판단한다. 일반 자율 경로 요청 속도는 0.08 m/s, Pi 모터 duty 상한은 0.20으로 시작한다. 상한은 물리 속도나 제동 거리 실측값과 구분한다.

## 암전과 프레임 손실의 차이

```text
Fresh dark frames + fresh LiDAR
    -> zero-speed hold -> ask Qwen -> validate response and fresh LiDAR
    -> capped LiDAR exploration; visual search paused
Missing camera frames
    -> zero-speed hold -> recover when usable frames return
    -> persistent loss latches; operator must clear it
Onboard obstacle, stale scan, E-stop or command-link loss
    -> Pi / Pico safety remains in control
```

정지 원인은 Foxglove의 `/mission`과 `/rover/status`를 같이 확인한다. `WAIT_CAMERA`는 영상·추론 준비 문제, `AI_REVIEW`는 Qwen 응답 대기, `FOLLOW blocked`나 `no_scan`은 로버 쪽 경로 보호 문제다. 전진 시 복도 벽 때문에 막히면 먼저 차체 방향과 로버 전방의 LiDAR 여유를 확인한다. 통신 채널이 끊기면 복구 뒤 모드를 다시 직접 선택한다. 런처는 카메라를 명령·텔레메트리와 별도 SSH 연결로 열고 최신 프레임만 추론에 전달한다.

촬영 직전 **구동 출력·정지 버튼**, **영상 끊김·회복**, **암전과 복광**, **발견 알림·스냅샷**을 각각 저속으로 확인한다. 로컬 기록은 `logs/explore_*.jsonl`, 사람 스냅샷은 같은 이름의 `*_alerts`, SSH 출력은 `logs/pi_*.txt`에 남는다.
