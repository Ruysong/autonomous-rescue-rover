cmake_minimum_required(VERSION 3.16)

project(autonomous_rescue_rover_robot LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(nlohmann_json REQUIRED)

add_executable(rover_controller
    src/main.cpp
    src/communication/FramedSocket.cpp
    src/communication/Protocol.cpp
    src/communication/TcpServer.cpp
    src/motor/MockMotor.cpp
)

target_include_directories(rover_controller PRIVATE include)
target_link_libraries(rover_controller PRIVATE nlohmann_json::nlohmann_json)
