#include "navigation/Odometry.hpp"

#include <cmath>

namespace rover::navigation {

void Odometry::update(long encLeft, long encRight, std::optional<double> imuYaw) {
    lastDs_ = 0.0;
    lastDyaw_ = 0.0;
    if (!prevLeft_) {
        prevLeft_ = encLeft;
        prevRight_ = encRight;
        return;
    }
    const double dl = static_cast<double>(encLeft - *prevLeft_) / config_.countsPerMeter;
    const double dr = static_cast<double>(encRight - *prevRight_) / config_.countsPerMeter;
    prevLeft_ = encLeft;
    prevRight_ = encRight;
    if (std::fabs(dl) > config_.maxStepM || std::fabs(dr) > config_.maxStepM) {
        return;  // counter reset: re-reference without moving
    }

    const double ds = 0.5 * (dl + dr);
    double newYaw;
    if (config_.useImu && imuYaw) {
        if (!usingImu_ || !imuOffset_) {
            imuOffset_ = pose_.yaw - *imuYaw;  // keep continuity when the IMU (re)appears
        }
        usingImu_ = true;
        newYaw = *imuYaw + *imuOffset_;
    } else {
        usingImu_ = false;
        newYaw = pose_.yaw + (dr - dl) / (2.0 * config_.yMid);
    }
    const double dyaw = newYaw - pose_.yaw;  // unwrapped
    const double mid = pose_.yaw + 0.5 * dyaw;
    pose_.x += ds * std::cos(mid);
    pose_.y += ds * std::sin(mid);
    pose_.yaw = wrapAngle(newYaw);
    if (usingImu_) imuOffset_ = pose_.yaw - *imuYaw;  // re-wrap the offset
    lastDs_ = ds;
    lastDyaw_ = dyaw;
    travelled_ += std::fabs(ds);
}

}  // namespace rover::navigation
