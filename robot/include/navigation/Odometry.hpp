#pragma once

// State estimation (step 1): middle-wheel encoders for distance, BNO055 for heading.
// The middle wheels never steer and the turn centre is always on their axle, so they roll
// without side slip: their mean is the distance of the robot origin. Heading comes from the IMU
// when it is healthy, otherwise from the encoder difference over the middle track.
// (A full EKF and optical flow come later; the laptop SLAM corrects the drift meanwhile.)

#include "navigation/Pose.hpp"

#include <optional>

namespace rover::navigation {

struct OdometryConfig {
    double countsPerMeter = 875.0;  // placeholder: x1 encoder, to be measured (odom_test)
    double yMid = 0.3125;           // half track of the middle wheels
    double maxStepM = 0.5;          // larger jumps = Pico reboot / counter reset; ignored
    bool useImu = true;
};

class Odometry {
public:
    explicit Odometry(OdometryConfig config) : config_(config) {}

    // One Pico sample. imuYaw: absolute (unwrapped) IMU yaw if healthy.
    void update(long encLeft, long encRight, std::optional<double> imuYaw);

    Pose2D pose() const { return pose_; }
    double lastDistance() const { return lastDs_; }   // m, last update
    double lastRotation() const { return lastDyaw_; } // rad, last update
    double distance() const { return travelled_; }
    bool usingImu() const { return usingImu_; }
    const OdometryConfig& config() const { return config_; }

private:
    OdometryConfig config_;
    Pose2D pose_;
    std::optional<long> prevLeft_;
    std::optional<long> prevRight_;
    std::optional<double> imuOffset_;  // pose yaw - imu yaw while the IMU is in use
    bool usingImu_ = false;
    double lastDs_ = 0.0;
    double lastDyaw_ = 0.0;
    double travelled_ = 0.0;
};

}  // namespace rover::navigation
