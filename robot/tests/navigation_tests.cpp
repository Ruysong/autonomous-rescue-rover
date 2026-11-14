#include "communication/Protocol.hpp"
#include "control/Config.hpp"
#include "hardware/Imu.hpp"
#include "hardware/Lidar.hpp"
#include "navigation/Odometry.hpp"
#include "navigation/PathFollower.hpp"
#include "navigation/Pose.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <string>
#include <variant>

namespace {

using namespace rover;
using namespace rover::navigation;

int failures = 0;
constexpr double kPi = 3.14159265358979323846;

void check(bool condition, const std::string& message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << '\n';
    }
}

bool near(double a, double b, double tol = 1e-6) { return std::fabs(a - b) <= tol; }

void testPose() {
    const Pose2D a{1.0, 2.0, kPi / 2};
    const Pose2D b{1.0, 0.0, 0.0};
    const auto c = compose(a, b);
    check(near(c.x, 1.0) && near(c.y, 3.0) && near(c.yaw, kPi / 2), "compose");
    double lx, ly;
    toLocal(a, 1.0, 3.0, lx, ly);
    check(near(lx, 1.0) && near(ly, 0.0), "toLocal");
}

void testDecodeNode() {
    // start bit, quality 15, angle 90 deg, dist 1000 mm
    const unsigned angleQ6 = 90 * 64;
    const unsigned distQ2 = 1000 * 4;
    const std::uint8_t bytes[5] = {
        static_cast<std::uint8_t>((15 << 2) | 0x1),
        static_cast<std::uint8_t>(((angleQ6 & 0x7F) << 1) | 1),
        static_cast<std::uint8_t>(angleQ6 >> 7),
        static_cast<std::uint8_t>(distQ2 & 0xFF),
        static_cast<std::uint8_t>(distQ2 >> 8)};
    hardware::ScanNode n;
    check(hardware::decodeNode(bytes, n), "node decodes");
    check(n.start && n.quality == 15 && near(n.angleDeg, 90.0) && near(n.distMm, 1000.0),
          "node fields");
    std::uint8_t bad[5] = {0x03, 0x01, 0, 0, 0};  // start == inverse
    check(!hardware::decodeNode(bad, n), "bad check bits rejected");
}

void testAssembler() {
    hardware::LidarMount m;
    m.x = 0.0;
    m.y = 0.0;
    m.yawOffsetDeg = 0.0;
    m.direction = -1.0;  // clockwise sensor
    hardware::ScanAssembler a(m);
    hardware::LaserScan done;
    const auto t = std::chrono::steady_clock::now();
    check(!a.add({true, 10, 0.0, 2000.0}, t, done), "first start opens a revolution");
    a.add({false, 10, 90.0, 1500.0}, t, done);  // sensor 90 deg clockwise = robot right
    check(a.add({true, 10, 0.5, 2000.0}, t, done), "second start closes it");
    check(done.valid && done.points == 2, "two points");
    check(done.rangesMm[0] == 2000, "front bin");
    check(done.rangesMm[270] == 1500, "clockwise 90 -> robot bin 270 (right)");
}

void testMockCast() {
    const auto scan = hardware::castScan({0, 0, 0}, hardware::defaultMockWorld(), 12.0);
    check(scan.valid && std::abs(scan.rangesMm[0] - 5000) <= 1, "mock: far wall 5 m ahead");
    check(std::abs(scan.rangesMm[90] - 2500) <= 1, "mock: wall 2.5 m left");
    check(std::abs(scan.rangesMm[180] - 1000) <= 1, "mock: wall 1 m behind");
}

void testUnwrap() {
    hardware::HeadingUnwrapper u(20.0);
    u.add(350.0);
    u.add(355.0);
    u.add(5.0);
    check(near(u.totalDeg(), 15.0), "unwrap across 0");
    check(!u.add(120.0), "glitch rejected");
    check(near(u.totalDeg(), 15.0), "glitch ignored");
    u.add(10.0);
    check(near(u.totalDeg(), 20.0), "continues after glitch");
}

void testOdometry() {
    OdometryConfig c;
    c.countsPerMeter = 1000.0;
    c.yMid = 0.3;
    Odometry o(c);
    o.update(0, 0, std::nullopt);
    for (long i = 1; i <= 10; ++i) o.update(i * 100, i * 100, std::nullopt);
    check(near(o.pose().x, 1.0) && near(o.pose().y, 0.0), "odom straight 1 m");
    // Left turn in place of the encoder difference: right wheel +0.3*pi/2 more
    const long d = static_cast<long>(std::lround(0.3 * kPi / 2 * 1000.0));
    o.update(1000 - d, 1000 + d, std::nullopt);
    check(near(o.pose().yaw, kPi / 2, 1e-3), "odom encoder yaw");
    // IMU takes over without a jump.
    o.update(1000 - d, 1000 + d, 5.0);
    check(near(o.pose().yaw, kPi / 2, 1e-3), "IMU handover keeps yaw");
    o.update(1000 - d, 1000 + d, 5.0 + 0.1);
    check(near(o.pose().yaw, kPi / 2 + 0.1, 1e-3), "IMU drives yaw");
    o.update(1000000, 0, 5.1);  // counter reset
    check(near(o.pose().x, 1.0, 1e-3), "counter jump ignored");
}

hardware::LaserScan emptyScan() {
    hardware::LaserScan s;
    s.valid = true;
    return s;
}

void testFollower() {
    PathFollower f;
    f.maxCurvature = 1.3;
    std::vector<std::pair<double, double>> path;
    for (int i = 0; i <= 30; ++i) path.emplace_back(i * 0.1, 0.0);
    f.setPath(path, 0.1, 7);
    auto out = f.step({0, 0, 0}, emptyScan(), 0.0);
    check(out.active && out.motion.v > 0 && near(out.motion.kappa, 0.0, 1e-9), "straight path");

    out = f.step({0, -0.3, 0}, emptyScan(), 0.0);
    check(out.motion.kappa > 0, "right of path -> steer left");

    auto blocked = emptyScan();
    blocked.rangesMm[0] = 450;  // 0.45 m ahead, body front 0.36 + 0.25
    out = f.step({0, 0, 0}, blocked, 0.0);
    check(!out.active && out.stop && f.state() == FollowState::blocked, "blocked by LiDAR");
    out = f.step({0, 0, 0}, blocked, 0.0);
    check(!out.stop, "one stop per transition");
    out = f.step({0, 0, 0}, emptyScan(), 0.0);
    check(out.active && f.state() == FollowState::following, "unblocked resumes");

    out = f.step({0, 0, 0}, emptyScan(), 2.0);
    check(!out.active && f.state() == FollowState::noScan, "stale scan stops");

    out = f.step({0, 0, kPi}, emptyScan(), 0.0);
    check(out.active && out.motion.v < 0 && f.state() == FollowState::reversing,
          "path behind -> back up");

    out = f.step({2.9, 0, 0}, emptyScan(), 0.0);
    check(out.stop && f.state() == FollowState::reached, "goal reached");
    out = f.step({2.9, 0, 0}, emptyScan(), 0.0);
    check(!out.active && !out.stop, "idle after reaching");
}

void testReverseTurnsNose() {
    // Target behind-left: backing up on lock must increase yaw (v * kappa > 0).
    PathFollower f;
    f.maxCurvature = 1.3;
    f.setPath({{-1.0, 0.5}, {-2.0, 1.0}}, 0.1, 1);
    const auto out = f.step({0, 0, 0}, emptyScan(), 0.0);
    check(out.motion.v < 0 && out.motion.v * out.motion.kappa > 0, "reverse turns towards target");
}

void testProtocol() {
    using namespace rover::protocol;
    auto r = parseCommand(
        R"({"_version":"1.2","type":"path","id":3,"speed":0.1,"points":[[0,0],[1,0.5]]})");
    const auto* c = commandFrom(r);
    check(c && std::holds_alternative<PathCommand>(*c), "path parses");
    if (c) {
        const auto& p = std::get<PathCommand>(*c);
        check(p.id == 3 && p.points.size() == 2 && near(p.points[1].second, 0.5), "path fields");
    }
    check(!isValid(parseCommand(R"({"_version":"1.1","type":"path","id":1,"points":[]})")),
          "path needs 1.2");
    check(!isValid(parseCommand(R"({"_version":"1.2","type":"path","id":1,"points":[[1]]})")),
          "bad point rejected");
    check(isValid(parseCommand(R"({"_version":"1.2","type":"pose_correction","x":1,"y":2,"yaw":0.1})")),
          "pose_correction parses");
    check(isValid(parseCommand(
              R"({"_version":"1.1","type":"motion","source":"manual","steer_mode":"normal","v":0.1})")),
          "v1.1 still accepted");
    ScanMessage s;
    s.rangesMm.assign(360, 0);
    const auto json = serializeScan(s);
    check(json.find("\"scan\"") != std::string::npos && json.find("ranges_mm") != std::string::npos,
          "scan serializes");
}

void testConfig() {
    const auto c = control::loadConfig(ROVER_DEFAULT_CONFIG);
    check(c.odometry.countsPerMeter > 0 && near(c.odometry.yMid, c.geometry.yMid),
          "config odometry");
    check(near(c.lidar.direction, -1.0) && c.imu.address == 0x29, "config lidar / imu");
    check(c.follower.cruiseSpeed > 0, "config follower");
}

}  // namespace

int main() {
    try {
        testPose();
        testDecodeNode();
        testAssembler();
        testMockCast();
        testUnwrap();
        testOdometry();
        testFollower();
        testReverseTurnsNose();
        testProtocol();
        testConfig();
    } catch (const std::exception& error) {
        std::cerr << "exception: " << error.what() << '\n';
        return 1;
    }
    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "robot_navigation_tests: all checks passed\n";
    return 0;
}
