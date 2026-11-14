#pragma once

// 2D pose and frame helpers. Frames:
//   odom  - where the robot was at start-up; drifts (encoders + IMU)
//   map   - the laptop SLAM frame; map_pose = correction (map <- odom) * odom_pose
//   robot - x forward, y left, origin at the middle-axle centre

#include <cmath>

namespace rover::navigation {

struct Pose2D {
    double x = 0.0;
    double y = 0.0;
    double yaw = 0.0;  // rad, CCW
};

inline double wrapAngle(double a) {
    return std::atan2(std::sin(a), std::cos(a));
}

// a * b (apply b in a's frame)
inline Pose2D compose(const Pose2D& a, const Pose2D& b) {
    const double c = std::cos(a.yaw);
    const double s = std::sin(a.yaw);
    return {a.x + c * b.x - s * b.y, a.y + s * b.x + c * b.y, wrapAngle(a.yaw + b.yaw)};
}

// Point (px, py) given in frame `pose` -> into the parent frame.
inline void toParent(const Pose2D& pose, double px, double py, double& outX, double& outY) {
    const double c = std::cos(pose.yaw);
    const double s = std::sin(pose.yaw);
    outX = pose.x + c * px - s * py;
    outY = pose.y + s * px + c * py;
}

// Parent-frame point -> frame `pose`.
inline void toLocal(const Pose2D& pose, double px, double py, double& outX, double& outY) {
    const double c = std::cos(pose.yaw);
    const double s = std::sin(pose.yaw);
    const double dx = px - pose.x;
    const double dy = py - pose.y;
    outX = c * dx + s * dy;
    outY = -s * dx + c * dy;
}

}  // namespace rover::navigation
