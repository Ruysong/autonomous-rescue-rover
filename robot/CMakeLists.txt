cmake_minimum_required(VERSION 3.16)

project(autonomous_rescue_rover_robot LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(nlohmann_json REQUIRED)
find_package(Threads REQUIRED)
option(ROVER_WITH_GPIO "Build the opt-in Pi GPIO motor adapter" OFF)

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

if(ROVER_WITH_GPIO)
    find_path(LGPIO_INCLUDE_DIR lgpio.h)
    find_library(LGPIO_LIBRARY lgpio)
    if(NOT LGPIO_INCLUDE_DIR OR NOT LGPIO_LIBRARY)
        message(FATAL_ERROR "GPIO build requires liblgpio-dev; install it on the Pi first")
    endif()
    target_sources(rover_controller PRIVATE src/motor/GpioMotor.cpp)
    target_include_directories(rover_controller PRIVATE ${LGPIO_INCLUDE_DIR})
    target_link_libraries(rover_controller PRIVATE ${LGPIO_LIBRARY})
    target_compile_definitions(rover_controller PRIVATE ROVER_WITH_GPIO=1)
endif()

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
