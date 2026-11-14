#pragma once

// Sensor Drivers: BNO055 on the Pi I2C bus, IMU fusion mode (gyro + accel, no magnetometer:
// motor currents bend the field). Heading is relative to start-up and unwrapped (can exceed
// +-180 deg). The board is mounted upside down, so the axis remap registers are set first.

#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

namespace rover::hardware {

struct ImuConfig {
    bool enabled = true;
    std::string bus = "/dev/i2c-1";
    int address = 0x29;
    int axisMapConfig = 0x24;  // BNO055 default (X=X, Y=Y, Z=Z)
    int axisMapSign = 0x03;    // upside down: flip Y and Z
    double yawSign = -1.0;     // BNO055 heading grows clockwise; our yaw is CCW
    double maxStepDeg = 20.0;  // per 20 ms sample; bigger jumps are I2C corruption, dropped
};

struct ImuReading {
    bool ok = false;     // recent valid sample
    double yaw = 0.0;    // rad, CCW, relative to start, unwrapped
    unsigned errors = 0;
    unsigned dropped = 0;
    int calibration = 0;  // CALIB_STAT register
};

// Pure helper (tested): unwraps a 0..360 heading sequence into a continuous angle.
class HeadingUnwrapper {
public:
    explicit HeadingUnwrapper(double maxStepDeg) : maxStep_(maxStepDeg) {}
    // Returns false if the sample was rejected as a glitch.
    bool add(double headingDeg);
    double totalDeg() const { return total_; }

private:
    double maxStep_;
    std::optional<double> prev_;
    double total_ = 0.0;
    int rejectedInRow_ = 0;
};

class Bno055 {
public:
    explicit Bno055(ImuConfig config);
    ~Bno055();
    ImuReading reading() const;

private:
    void run();
    bool setup(int fd);

    ImuConfig config_;
    mutable std::mutex mutex_;
    ImuReading reading_;
    std::chrono::steady_clock::time_point lastOk_{};
    std::atomic<bool> running_{true};
    std::thread thread_;
};

}  // namespace rover::hardware
