#pragma once

#include "control/CommandGate.hpp"
#include "control/Kinematics.hpp"
#include "hardware/Imu.hpp"
#include "hardware/Lidar.hpp"
#include "navigation/Odometry.hpp"
#include "navigation/PathFollower.hpp"

#include <string>

namespace rover::control {

struct RoverConfig {
    Geometry geometry;
    GateConfig gate;
    navigation::OdometryConfig odometry;
    hardware::LidarMount lidar;
    hardware::ImuConfig imu;
    navigation::FollowerConfig follower;
};

// Loads robot/config/rover.json. Missing keys keep their defaults; bad values throw.
RoverConfig loadConfig(const std::string& path);

}  // namespace rover::control
