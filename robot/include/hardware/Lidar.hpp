#pragma once

// Sensor Drivers: RPLIDAR C1 (standard scan, 460800 baud) -> one LaserScan per revolution,
// already converted to the robot frame (x forward, y left, origin middle-axle centre).
// Bin i covers [i - 0.5, i + 0.5) deg CCW from +x; value = nearest range in mm, 0 = no return.

#include "navigation/Pose.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace rover::hardware {

inline constexpr int kScanBins = 360;

struct LaserScan {
    bool valid = false;
    std::chrono::steady_clock::time_point stamp{};
    unsigned sequence = 0;
    int points = 0;  // valid returns that went into the bins
    std::array<std::uint16_t, kScanBins> rangesMm{};
};

struct LidarMount {
    double x = 0.18;            // m, scan centre in the robot frame
    double y = -0.1275;
    double yawOffsetDeg = 0.0;  // sensor 0 deg direction, CCW from robot +x
    double direction = -1.0;    // -1: sensor angles grow clockwise (RPLIDAR), +1: CCW
    double minRangeM = 0.10;    // closer returns are dropped (sensor dead zone)
    double maxRangeM = 12.0;
};

struct ScanNode {
    bool start = false;
    int quality = 0;
    double angleDeg = 0.0;  // sensor frame
    double distMm = 0.0;    // 0 = no return
};

// One 5-byte standard-scan node. Returns false if the check bits are wrong.
bool decodeNode(const std::uint8_t* bytes, ScanNode& out);

// Collects nodes; returns true and fills `completed` when a new revolution starts.
class ScanAssembler {
public:
    explicit ScanAssembler(LidarMount mount);
    bool add(const ScanNode& node, std::chrono::steady_clock::time_point now,
             LaserScan& completed);

private:
    void addPoint(double angleDeg, double distMm);

    LidarMount mount_;
    LaserScan current_;
    bool started_ = false;
    unsigned sequence_ = 0;
};

class LidarSource {
public:
    virtual ~LidarSource() = default;
    virtual LaserScan latest() const = 0;
    virtual std::string name() const = 0;
};

// Reads the real sensor on its own thread; re-opens the port if it disappears.
class RplidarC1 final : public LidarSource {
public:
    RplidarC1(std::string path, LidarMount mount);
    ~RplidarC1() override;
    LaserScan latest() const override;
    std::string name() const override { return path_; }

private:
    void run();
    bool startScan(int fd);

    std::string path_;
    LidarMount mount_;
    mutable std::mutex mutex_;
    LaserScan latest_;
    std::atomic<bool> running_{true};
    std::thread thread_;
};

// Simulated sensor: casts rays from a "true" pose into a few wall segments (test / mock runs).
struct Segment {
    double x1, y1, x2, y2;
};

std::vector<Segment> defaultMockWorld();
LaserScan castScan(const navigation::Pose2D& pose, const std::vector<Segment>& world,
                   double maxRangeM);

class MockLidar final : public LidarSource {
public:
    MockLidar(std::function<navigation::Pose2D()> truePose, std::vector<Segment> world);
    ~MockLidar() override;
    LaserScan latest() const override;
    std::string name() const override { return "mock"; }

private:
    std::function<navigation::Pose2D()> truePose_;
    std::vector<Segment> world_;
    mutable std::mutex mutex_;
    LaserScan latest_;
    std::atomic<bool> running_{true};
    std::thread thread_;
};

}  // namespace rover::hardware
