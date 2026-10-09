cmake_minimum_required(VERSION 3.16)

project(autonomous_rescue_rover_robot LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(nlohmann_json REQUIRED)
find_package(Threads REQUIRED)

add_executable(rover_controller
    src/main.cpp
    src/communication/FramedSocket.cpp
    src/communication/Protocol.cpp
    src/communication/TcpClient.cpp
    src/state/RobotState.cpp
    src/motor/MockMotor.cpp
)

target_include_directories(rover_controller PRIVATE include)
target_link_libraries(rover_controller PRIVATE nlohmann_json::nlohmann_json Threads::Threads)

include(CTest)
if(BUILD_TESTING)
    add_executable(robot_runtime_tests
        tests/runtime_tests.cpp
        src/communication/FramedSocket.cpp
        src/communication/Protocol.cpp
        src/state/RobotState.cpp
    )
    target_include_directories(robot_runtime_tests PRIVATE include)
    target_link_libraries(robot_runtime_tests PRIVATE nlohmann_json::nlohmann_json Threads::Threads)
    add_test(NAME robot_runtime_tests COMMAND robot_runtime_tests)
endif()
