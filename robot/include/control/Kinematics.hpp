#pragma once

// Inverse kinematics for the 6-wheel rocker-bogie (4 corner steering, middle wheels fixed).
//
// Robot frame: x forward, y left, origin = centre of the middle axle.
// Because the middle wheels cannot steer, every turn has its centre on the middle-axle line.
// That makes "Ackermann" and "symmetric 4WS" the same motion here (front and rear wheels steer
// opposite ways). Crab motion is impossible (middle wheels would be dragged sideways).
//
// Drive channels: the Pico has only two motor channels (left 3 wheels, right 3 wheels share one
// duty each), so the side speed is set from the middle wheel, which also carries the encoder.

#include <array>
#include <string>

namespace rover::control {

enum class SteerMode { normal, spin };

const char* toString(SteerMode mode);
bool steerModeFrom(const std::string& text, SteerMode& mode);

// Wheel order everywhere: FL, FR, RL, RR (same as the Pico C command).
struct Geometry {
    double xFront = 0.28;    // m, middle axle -> front steering axis
    double xRear = -0.25;    // m, middle axle -> rear steering axis (negative)
    double yFront = 0.2825;  // m, half track at the front corners
    double yRear = 0.2825;   // m, half track at the rear corners
    double yMid = 0.3125;    // m, half track at the middle wheels
    double maxSteerDeg = 30.0;      // same as the Pico soft limit
    double maxWheelSpeed = 0.24;    // m/s at duty 1.0 (37 rpm x pi x 0.125 m, to be measured)
    double maxDuty = 0.6;           // never ask the Pico for more than this
    bool spinEnabled = false;       // spin needs ~45 deg corners; off until servo range is measured
    std::array<double, 4> steerSign{1.0, 1.0, 1.0, 1.0};
    std::array<double, 4> steerTrimDeg{0.0, 0.0, 0.0, 0.0};
    std::array<double, 2> motorSign{1.0, 1.0};  // left, right
};

struct Motion {
    SteerMode mode = SteerMode::normal;
    double v = 0.0;      // m/s at the middle-axle centre (normal mode)
    double kappa = 0.0;  // 1/m, + = turn left (normal mode)
    double wz = 0.0;     // rad/s, + = counter-clockwise (spin mode)
};

struct WheelCommand {
    double dutyLeft = 0.0;
    double dutyRight = 0.0;
    std::array<double, 4> steerDeg{0.0, 0.0, 0.0, 0.0};  // after sign and trim
    bool clamped = false;    // curvature or duty was limited
    bool rejected = false;   // motion impossible (e.g. spin disabled); output is a stop
};

// Largest |kappa| for which no corner exceeds maxSteerDeg.
double maxCurvature(const Geometry& geometry);

// Corner angles (FL, FR, RL, RR, before sign/trim) that make every corner tangent to a circle
// around the middle-axle centre.
std::array<double, 4> spinAnglesDeg(const Geometry& geometry);

WheelCommand solve(const Geometry& geometry, const Motion& motion);

// A stop that keeps the given wheel angles (used while braking before a mode change).
WheelCommand holdAngles(const WheelCommand& previous);

}  // namespace rover::control
