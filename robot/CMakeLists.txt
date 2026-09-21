cmake_minimum_required(VERSION 3.16)

project(autonomous_rescue_rover_robot LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(rover_controller
    src/main.cpp
    src/motor/MockMotor.cpp
)

target_include_directories(rover_controller PRIVATE include)
