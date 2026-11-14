#include "control/Kinematics.hpp"

#include <algorithm>
#include <cmath>

namespace rover::control {
namespace {

constexpr double kPi = 3.14159265358979323846;

double deg(double rad) { return rad * 180.0 / kPi; }
double rad(double degrees) { return degrees * kPi / 180.0; }

struct Corner {
    double x;
    double y;
};

std::array<Corner, 4> corners(const Geometry& g) {
    return {{{g.xFront, g.yFront}, {g.xFront, -g.yFront}, {g.xRear, g.yRear}, {g.xRear, -g.yRear}}};
}

void applySignAndTrim(const Geometry& g, WheelCommand& out) {
    for (std::size_t i = 0; i < 4; ++i) {
        const double value = g.steerSign[i] * out.steerDeg[i] + g.steerTrimDeg[i];
        out.steerDeg[i] = std::clamp(value, -g.maxSteerDeg, g.maxSteerDeg);
    }
}

void setDuty(const Geometry& g, double speedLeft, double speedRight, WheelCommand& out) {
    double left = speedLeft / g.maxWheelSpeed;
    double right = speedRight / g.maxWheelSpeed;
    const double peak = std::max(std::fabs(left), std::fabs(right));
    if (peak > g.maxDuty) {
        // Scale both sides together so the turn shape is kept.
        left *= g.maxDuty / peak;
        right *= g.maxDuty / peak;
        out.clamped = true;
    }
    out.dutyLeft = g.motorSign[0] * left;
    out.dutyRight = g.motorSign[1] * right;
}

}  // namespace

const char* toString(SteerMode mode) {
    return mode == SteerMode::spin ? "spin" : "normal";
}

bool steerModeFrom(const std::string& text, SteerMode& mode) {
    if (text == "normal") {
        mode = SteerMode::normal;
        return true;
    }
    if (text == "spin") {
        mode = SteerMode::spin;
        return true;
    }
    return false;
}

double maxCurvature(const Geometry& g) {
    // Corner angle for curvature k: atan(|x| k / (1 - |y| k)). Solve angle == limit for k.
    const double t = std::tan(rad(g.maxSteerDeg));
    double best = 1e9;
    for (const auto& c : corners(g)) {
        const double k = t / (std::fabs(c.x) + t * std::fabs(c.y));
        best = std::min(best, k);
    }
    // Inside the middle wheel the inner side would have to run backwards; never go there.
    best = std::min(best, 0.95 / g.yMid);
    return best;
}

std::array<double, 4> spinAnglesDeg(const Geometry& g) {
    std::array<double, 4> out{};
    const auto cs = corners(g);
    for (std::size_t i = 0; i < 4; ++i) {
        out[i] = deg(std::atan(-cs[i].x / cs[i].y));
    }
    return out;
}

WheelCommand solve(const Geometry& g, const Motion& motion) {
    WheelCommand out;
    if (!std::isfinite(motion.v) || !std::isfinite(motion.kappa) || !std::isfinite(motion.wz)) {
        out.rejected = true;
        return out;
    }

    if (motion.mode == SteerMode::spin) {
        const auto angles = spinAnglesDeg(g);
        bool fits = g.spinEnabled;
        for (double a : angles) {
            fits = fits && std::fabs(a) <= g.maxSteerDeg;
        }
        if (!fits) {
            out.rejected = true;
            return out;
        }
        out.steerDeg = angles;
        setDuty(g, -motion.wz * g.yMid, motion.wz * g.yMid, out);
        applySignAndTrim(g, out);
        return out;
    }

    const double kMax = maxCurvature(g);
    double k = motion.kappa;
    if (std::fabs(k) > kMax) {
        k = std::copysign(kMax, k);
        out.clamped = true;
    }

    const auto cs = corners(g);
    for (std::size_t i = 0; i < 4; ++i) {
        out.steerDeg[i] = deg(std::atan2(cs[i].x * k, 1.0 - cs[i].y * k));
    }
    setDuty(g, motion.v * (1.0 - g.yMid * k), motion.v * (1.0 + g.yMid * k), out);
    applySignAndTrim(g, out);
    return out;
}

WheelCommand holdAngles(const WheelCommand& previous) {
    WheelCommand out;
    out.steerDeg = previous.steerDeg;
    return out;
}

}  // namespace rover::control
