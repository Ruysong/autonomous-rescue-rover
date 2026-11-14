#pragma once

#include "control/CommandGate.hpp"
#include "control/Kinematics.hpp"

#include <string>

namespace rover::control {

struct RoverConfig {
    Geometry geometry;
    GateConfig gate;
};

// Loads robot/config/rover.json. Missing keys keep their defaults; bad values throw.
RoverConfig loadConfig(const std::string& path);

}  // namespace rover::control
