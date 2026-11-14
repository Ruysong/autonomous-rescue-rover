#include "control/Config.hpp"

#include <cmath>
#include <fstream>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
#include <vector>

namespace rover::control {
namespace {

using Json = nlohmann::json;

double number(const Json& node, const char* key, double fallback, double lo, double hi) {
    if (!node.contains(key)) {
        return fallback;
    }
    const double value = node.at(key).get<double>();
    if (!std::isfinite(value) || value < lo || value > hi) {
        throw std::invalid_argument(std::string("config: ") + key + " out of range");
    }
    return value;
}

template <std::size_t N>
std::array<double, N> numbers(const Json& node, const char* key, std::array<double, N> fallback,
                              double lo, double hi) {
    if (!node.contains(key)) {
        return fallback;
    }
    const auto values = node.at(key).get<std::vector<double>>();
    if (values.size() != N) {
        throw std::invalid_argument(std::string("config: ") + key + " needs " +
                                    std::to_string(N) + " values");
    }
    std::array<double, N> out{};
    for (std::size_t i = 0; i < N; ++i) {
        if (!std::isfinite(values[i]) || values[i] < lo || values[i] > hi) {
            throw std::invalid_argument(std::string("config: ") + key + " out of range");
        }
        out[i] = values[i];
    }
    return out;
}

std::chrono::milliseconds millis(const Json& node, const char* key,
                                 std::chrono::milliseconds fallback) {
    return std::chrono::milliseconds(
        static_cast<long>(number(node, key, static_cast<double>(fallback.count()), 0, 10000)));
}

}  // namespace

RoverConfig loadConfig(const std::string& path) {
    std::ifstream file(path);
    if (!file) {
        throw std::runtime_error("config: cannot open " + path);
    }
    const Json root = Json::parse(file);
    RoverConfig config;

    if (root.contains("geometry")) {
        const Json& g = root["geometry"];
        Geometry& out = config.geometry;
        out.xFront = number(g, "x_front_m", out.xFront, 0.05, 1.0);
        out.xRear = number(g, "x_rear_m", out.xRear, -1.0, -0.05);
        out.yFront = number(g, "y_front_m", out.yFront, 0.05, 1.0);
        out.yRear = number(g, "y_rear_m", out.yRear, 0.05, 1.0);
        out.yMid = number(g, "y_mid_m", out.yMid, 0.05, 1.0);
    }
    if (root.contains("drive")) {
        const Json& d = root["drive"];
        Geometry& out = config.geometry;
        out.maxWheelSpeed = number(d, "max_wheel_speed_mps", out.maxWheelSpeed, 0.01, 5.0);
        out.maxDuty = number(d, "max_duty", out.maxDuty, 0.0, 0.6);
        out.motorSign = numbers<2>(d, "motor_sign", out.motorSign, -1.0, 1.0);
    }
    if (root.contains("steering")) {
        const Json& s = root["steering"];
        Geometry& out = config.geometry;
        out.maxSteerDeg = number(s, "max_deg", out.maxSteerDeg, 1.0, 90.0);
        out.spinEnabled = s.value("spin_enabled", out.spinEnabled);
        out.steerSign = numbers<4>(s, "sign", out.steerSign, -1.0, 1.0);
        out.steerTrimDeg = numbers<4>(s, "trim_deg", out.steerTrimDeg, -15.0, 15.0);
    }
    if (root.contains("gate")) {
        const Json& g = root["gate"];
        GateConfig& out = config.gate;
        out.commandTimeout = millis(g, "command_timeout_ms", out.commandTimeout);
        out.brakeTime = millis(g, "brake_ms", out.brakeTime);
        out.steerRateDegPerSec = number(g, "steer_rate_deg_s", out.steerRateDegPerSec, 1, 1000);
        out.steerMargin = millis(g, "steer_margin_ms", out.steerMargin);
        out.idleAfterStop = millis(g, "idle_after_stop_ms", out.idleAfterStop);
    }
    config.odometry.yMid = config.geometry.yMid;
    if (root.contains("odometry")) {
        const Json& o = root["odometry"];
        auto& out = config.odometry;
        out.countsPerMeter = number(o, "counts_per_meter", out.countsPerMeter, 10, 1e6);
        out.useImu = o.value("use_imu", out.useImu);
    }
    if (root.contains("lidar")) {
        const Json& l = root["lidar"];
        auto& out = config.lidar;
        out.x = number(l, "x_m", out.x, -1, 1);
        out.y = number(l, "y_m", out.y, -1, 1);
        out.yawOffsetDeg = number(l, "yaw_offset_deg", out.yawOffsetDeg, -360, 360);
        out.direction = number(l, "direction", out.direction, -1, 1);
        out.minRangeM = number(l, "min_range_m", out.minRangeM, 0, 2);
        out.maxRangeM = number(l, "max_range_m", out.maxRangeM, 1, 40);
    }
    if (root.contains("imu")) {
        const Json& i = root["imu"];
        auto& out = config.imu;
        out.enabled = i.value("enabled", out.enabled);
        out.bus = i.value("bus", out.bus);
        out.address = static_cast<int>(number(i, "address", out.address, 0x08, 0x77));
        out.axisMapConfig = static_cast<int>(number(i, "axis_map_config", out.axisMapConfig, 0, 0x3F));
        out.axisMapSign = static_cast<int>(number(i, "axis_map_sign", out.axisMapSign, 0, 7));
        out.yawSign = number(i, "yaw_sign", out.yawSign, -1, 1);
    }
    if (root.contains("follower")) {
        const Json& f = root["follower"];
        auto& out = config.follower;
        out.lookahead = number(f, "lookahead_m", out.lookahead, 0.1, 3);
        out.cruiseSpeed = number(f, "cruise_speed_mps", out.cruiseSpeed, 0.01, 0.5);
        out.minSpeed = number(f, "min_speed_mps", out.minSpeed, 0.01, 0.5);
        out.reverseSpeed = number(f, "reverse_speed_mps", out.reverseSpeed, 0.01, 0.3);
        out.goalTolerance = number(f, "goal_tolerance_m", out.goalTolerance, 0.05, 2);
        out.turnAroundDeg = number(f, "turn_around_deg", out.turnAroundDeg, 60, 180);
        out.resumeForwardDeg = number(f, "resume_forward_deg", out.resumeForwardDeg, 5, 90);
        out.maxReverseM = number(f, "max_reverse_m", out.maxReverseM, 0.1, 3);
        out.bodyFront = number(f, "body_front_m", out.bodyFront, 0, 2);
        out.bodyRear = number(f, "body_rear_m", out.bodyRear, -2, 0);
        out.bodyHalfWidth = number(f, "body_half_width_m", out.bodyHalfWidth, 0.05, 2);
        out.stopDistance = number(f, "stop_distance_m", out.stopDistance, 0.05, 2);
        out.scanMaxAge = number(f, "scan_max_age_s", out.scanMaxAge, 0.1, 5);
    }
    return config;
}

}  // namespace rover::control
