# B-01 — 직선 모의 Wheel Odometry

## 범위와 구현 전 점검

이 문서의 인터페이스는 **PROPOSED / MOCK ONLY**다. 실물 엔코더·6륜/4륜 조향·팀 간 최종 토픽/TF 계약을 확정하지 않는다. 현재 D 데모의 Pi 직결 구동과 독립적이며 PWM 듀티를 이동 거리로 환산하지 않는다.

A에게 질문하거나 현장에서 측정할 정보는 [실물 연동 준비 확인표](b01_hardware_checklist.md)에 수집 순서·기록란·코드 반영 위치를 정리했다. 모의 설정을 실제 값으로 바꾸기 전에 해당 자료를 확보하고 실물 입력/운동학 설계를 별도로 검토한다.

| 점검 항목 | 확인 사실 및 이번 처리 |
|---|---|
| 모의 입력 | 기존 mock_encoder는 10Hz로 두 누적 카운트를 각각 +10 발행. 순서는 [left, right]이며 실물 계약으로 확대하지 않음 |
| 카운트 정의 | wheel counts/rev에는 quadrature 체배·감속비가 이미 반영돼야 함. PPR, 모터축 CPR, 바퀴축 CPR을 혼용하지 않음 |
| 기구 파라미터 | 실측 반지름/해상도/엔코더 위치 미확정. 역할 참고 문서의 약 0.052mm/count 등을 복사하지 않음 |
| 운동학 | 참고안의 차동구동 회전 모델은 실제 6륜/4륜 조향 계약으로 확정되지 않음. 같은 좌우 증분의 직진·정지·직선 후진만 계산 |
| 시간 | 입력에 Header/측정시각 없음. 순수 계산은 명시적 ns, ROS에서는 콜백 수신 ROS 시각 사용. 측정 속도/수신 지연 정확도는 보장하지 않음 |
| 리셋/역방향 | 음수 차분을 곧바로 리셋으로 취급하면 후진이 깨짐. 작은 리셋과 후진은 현재 입력만으로 식별 불가 |
| 값/누락 | 입력 길이·int64 범위·시간 간격·차분 상한·불지원 좌우 차이를 검사하고 잘못된 구간은 적분하지 않음 |
| 프레임/TF | 모의 전용 프레임으로 격리. B-01은 TF 발행 없음. B-02에서 별도 설계 |
| Odometry | 위치는 parent frame, 속도는 child frame. 단위 m·m/s·s, quaternion=(0,0,0,1). 6x6 공분산은 양수 대각값으로 설정 |
| 공분산 | 0으로 확신하지 않으며 비관측 축과 불연속 위치에는 큰 모의 분산을 사용. 실물 오차/튜닝 결과 아님 |

공식 기준: [Odometry 정의](https://github.com/ros2/common_interfaces/blob/jazzy/nav_msgs/msg/Odometry.msg), [공분산 순서](https://github.com/ros2/common_interfaces/blob/jazzy/geometry_msgs/msg/PoseWithCovariance.msg), [REP-103](https://github.com/ros-infrastructure/rep/blob/master/rep-0103.rst). docs.ros.org 접근이 제한되어 설치된 Jazzy 메시지와 공식 저장소 원문을 확인했다.

## 모의 인터페이스 — 최종 계약 아님

| 항목 | Publisher → Subscriber | 형식/의미 | 주기·시각·단위 | QoS |
|---|---|---|---|---|
| /wheel_ticks | 기존 mock_encoder 또는 명시적 mock 입력 → mock_wheel_odom | std_msgs/msg/Int64MultiArray, data=[left cumulative, right cumulative], 정확히 2개 int64, layout.dim 비어 있음/data_offset=0 | 기존 모의 10Hz, count, Header 없음 | KEEP_LAST 10, RELIABLE, VOLATILE |
| /mock/odom_wheel | mock_wheel_odom → 모의 probe/시각화 | nav_msgs/msg/Odometry, pose frame=mock_odom, child/twist frame=mock_base_link | 유효한 두 번째 표본부터 입력 시 발행. stamp는 해당 입력 수신 ROS 시각, x[m], vx[m/s] | KEEP_LAST 10, RELIABLE, VOLATILE |
| TF | 없음 | /tf 및 /tf_static 발행하지 않음 | 해당 없음 | 해당 없음 |

최대 입력 간격 기본 0.5초는 모의 유효 구간 판정값이다. 입력이 없으면 메시지를 발행하지 않으며 마지막 속도를 임의의 0으로 덮어쓰지 않는다. 소비자는 stamp의 최신성을 확인해야 한다. 측정시각과 리셋/sequence 정보가 포함된 실물 메시지는 추후 별도 합의가 필요하다.

설정 토픽·프레임을 변경하더라도 모의 계산이 실제 조향 모델로 바뀌지 않는다. `/odom_wheel`, `odom`, `base_link`의 최종 인터페이스와 TF 소유권은 별도 합의 대상이다.

## 계산 및 재기준화

```text
meters_per_tick = 2π × wheel_radius_m / counts_per_wheel_rev
delta_left = (left_now - left_previous) × left_encoder_sign
delta_right = (right_now - right_previous) × right_encoder_sign

delta_left == delta_right 일 때만:
  delta_x = delta_left × meters_per_tick
  x += delta_x
  vx = delta_x / dt
```

- 처음 받은 누적 카운트는 기준일 뿐 시작 전 거리를 더하지 않는다. 처음에는 발행하지 않고 다음 유효 구간에서 출력한다.
- y/z/roll/pitch/yaw는 직선 모의 가정으로 0, 방향 quaternion.w는 1이다. 관측한 회전/횡이동이라는 뜻이 아니며 비관측 축 공분산을 크게 둔다.
- 카운트가 같으면 정지(vx=0), 두 증분이 같은 음수면 직선 후진이다. 좌우 증분이 다르면 `unsupported_turn`으로 폐기하며 차동구동 yaw를 만들어내지 않는다. 두 카운트가 같더라도 실제 crab/조향/슬립은 식별하지 못하므로 모의 입력에만 사용한다.
- 시각 0은 /clock 미시작/원점 복귀로 보고 새 궤적을 준비한다. 양수 시각의 역행은 `clock_restarted`로 x=0, 기준 표본을 재설정한다. 이는 모의 bag/clock 재시작 정책이며 실물에서 연속 odom을 유지하는 정책이 아니다.
- 동일 시각, min_dt 미만, max_gap 초과, 허용 tick rate 초과, 불지원 좌우 차이는 출력 없이 해당 표본으로 재기준화한다. int64 wrap도 큰 점프로 처리하며 카운터 폭을 추정해서 보정하지 않는다.
- 형식/값 오류는 기준을 비운다. 다음 정상 입력은 새 기준, 그다음부터 유효 구간을 적분한다. 알 수 없는 누락 거리를 복원하지 않는다.
- 위 누락/오류 이후에는 연속성 손실을 유지하고 pose x 공분산을 discontinuity_variance로 높인다. 기존 x는 **계산 가능한 구간만의 합**이며 전체 이동 거리의 복원이 아니다. 새 궤적 초기화 전까지 정상 위치 확신으로 되돌리지 않는다.
- 작은 카운터 리셋은 정상 후진과 구별하지 못한다. 알려진 리셋에는 순수 추정기의 reset() 또는 ROS 노드 재시작으로 새 궤적을 시작한다. 자동 리셋 판별은 실물 계약이 없어 **BLOCKED**다.

## 파일과 모의 파라미터

- [순수 계산](../ros2_ws/src/rover_localization/rover_localization/straight_odometry.py): ROS/GPIO 의존성 없음.
- [ROS 어댑터](../ros2_ws/src/rover_localization/rover_localization/mock_wheel_odom.py): 수신/파라미터/Odometry 변환만 담당.
- [모의 YAML](../ros2_ws/src/rover_localization/config/mock_wheel_odom.yaml): mock_only=true, radius=0.05m, wheel counts/rev=1000, sign=+1/+1. **실측값 아님**.
- [계산 테스트](../ros2_ws/src/rover_localization/test/test_straight_odometry.py), [ROS 통합 테스트](../ros2_ws/src/rover_localization/test/test_mock_wheel_odom_ros.py).

공분산 대각값의 순서는 x/y/z/roll/pitch/yaw이고 위치/각도에는 m²/rad², 속도에는 (m/s)²/(rad/s)² 의미다. 초기 모의 값은 x와 vx 0.01, 비관측 축 1e6, 불연속 위치 1e6이다. 고정값은 시험 가정이며 누적 실측 오차 모델이 아니다. 형식/양수/유한값을 검증하며 공분산을 전부 0으로 발행하지 않는다.

입력/QoS/기구/프레임/공분산 파라미터는 시작 시 읽고 실행 중 변경을 거부한다. 변경하려면 새 설정으로 노드를 재시작한다. ROS use_sim_time은 /clock 시험을 위해 별도로 사용할 수 있다. mock_only=false는 실제 구현이 없는 모드를 켜지 않고 명시적으로 거부한다.

## 빌드·자동 검증

```bash
source /opt/ros/jazzy/setup.bash
cd ~/rover_aid/ros2_ws
colcon build --symlink-install --executor sequential --packages-select rover_localization
source install/setup.bash
export ROS_DOMAIN_ID=83
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_LOG_DIR=/tmp/roveraid_b01_ros_logs
colcon test --executor sequential --packages-select rover_localization --event-handlers console_direct+
colcon test-result --verbose
```

ROS 테스트의 대기에는 5초 기한이 있고 테스트가 만든 노드/실행기는 finally에서 정리한다. 기존 mock_encoder 소스를 변경 없이 로드해 새 Odometry 출력을 검증하며 기존 환경 구축/Pi PubSub 항목을 재검증하는 목적이 아니다.

## 수동 모의 실행

각 터미널에서 ROS 환경·workspace install과 위 domain/localhost 설정을 동일하게 적용한다. 아래 명령은 ros2_ws에서 실행하며 20초 뒤 종료한다.

```bash
# 터미널 1: 모의 Odometry
timeout --signal=INT --kill-after=2s 20s ros2 run rover_localization mock_wheel_odom --ros-args --params-file src/rover_localization/config/mock_wheel_odom.yaml

# 터미널 2: 기존 모의 엔코더 (패키지를 새로 빌드/수정하지 않고 실행)
timeout --signal=INT --kill-after=2s 20s python3 -B src/rover_sandbox/rover_sandbox/mock_encoder.py

# 터미널 3: 출력 한 번 확인
timeout --signal=INT --kill-after=2s 10s ros2 topic echo /mock/odom_wheel --once
```

mock 값에서 10count의 거리는 약 0.00314159m, 0.1초 간격이면 약 0.0314159m/s다. 실제 ROS 수신 간격은 달라질 수 있으므로 wall-time 시험에서는 속도의 정확한 소수값을 강제하지 않는다.

## /clock·bag·실물 한계

ROS 통합 테스트는 /clock=0, 유효 시각, 정지/후진, 불지원 입력, clock 일시정지 및 시각 역행 후 초기화를 검사한다. use_sim_time=true와 명시적 clock을 사용하는 **모의 시간 시험**이다.

현재 작업공간에 bag 파일이 없어 실제 `ros2 bag play --clock` 재생은 NOT RUN이다. 나중에 사용할 경우 입력이 mock인지, 타입/순서가 일치하는지, /clock 발행자가 하나인지 확인하고 노드에 use_sim_time=true를 설정한다. 타임스탬프 없는 입력의 수신시각 기반 출력은 지연/일시정지/재시작 영향을 받으므로 이를 실제 측정 속도의 기준 데이터로 사용하지 않는다.

하드웨어·실제 엔코더·Pi ARM64·조향 회전·횡이동·슬립 정확도는 **NOT TESTED ON HARDWARE**다. 실제 적용에는 실측 기구/해상도/부호, 엔코더 위치, 측정시각/리셋 정보, 조향 모델 및 출력/TF 계약을 합의해야 한다. D-02 모터 구동 시험의 성공만으로 이 조건이 충족되지는 않는다.
