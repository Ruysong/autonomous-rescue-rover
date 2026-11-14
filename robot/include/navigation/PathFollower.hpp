#pragma once

// Path tracking: Pure Pursuit on a map-frame path from the laptop planner -> (v, kappa).
// The robot cannot turn on the spot (spin off, min radius ~0.77 m), so when the lookahead
// point is behind it backs up on full lock the other way (a 3-point turn) until it faces it.
// A LiDAR box in front of (or behind, when reversing) the body stops it: state "blocked".

#include "control/Kinematics.hpp"
#include "hardware/Lidar.hpp"
#include "navigation/Pose.hpp"

#include <optional>
#include <string>
#include <vector>

namespace rover::navigation {

struct FollowerConfig {
    double lookahead = 0.5;        // m
    double cruiseSpeed = 0.10;     // m/s
    double minSpeed = 0.05;        // m/s near the goal / on full lock
    double reverseSpeed = 0.06;    // m/s during a 3-point turn
    double goalTolerance = 0.25;   // m
    double turnAroundDeg = 100.0;  // lookahead further off the nose than this -> back up
    double resumeForwardDeg = 45.0;
    double maxReverseM = 0.8;      // per back-up manoeuvre, then try forward again
    // Body box (robot frame) for the LiDAR guard; distances beyond it that count as blocked.
    double bodyFront = 0.36;
    double bodyRear = -0.32;
    double bodyHalfWidth = 0.36;
    double stopDistance = 0.25;    // m in the direction of travel
    double scanMaxAge = 0.5;       // s; older scan = no guard = stop
};

enum class FollowState { idle, following, reversing, blocked, reached, noScan };
const char* toString(FollowState state);

struct FollowOutput {
    bool active = false;          // false: nothing to send
    bool stop = false;            // send a stop (state just changed to a halt)
    control::Motion motion;
};

class PathFollower {
public:
    FollowerConfig config;
    double maxCurvature = 1.3;

    void setPath(std::vector<std::pair<double, double>> points, double speed, int id);
    void clear();

    // pose: map frame. scan: robot frame, ageSec: how old it is.
    FollowOutput step(const Pose2D& pose, const hardware::LaserScan& scan, double scanAgeSec);

    FollowState state() const { return state_; }
    int pathId() const { return pathId_; }
    std::size_t index() const { return index_; }
    double remaining() const { return remaining_; }
    std::size_t size() const { return path_.size(); }

    // Exposed for tests.
    static bool blockedByScan(const FollowerConfig& c, const hardware::LaserScan& scan,
                              bool forward);

private:
    FollowOutput halt(FollowState next);

    std::vector<std::pair<double, double>> path_;
    double speed_ = 0.1;
    int pathId_ = -1;
    std::size_t index_ = 0;
    double remaining_ = 0.0;
    FollowState state_ = FollowState::idle;
    double reversed_ = 0.0;
    std::optional<Pose2D> reverseStart_;
};

}  // namespace rover::navigation
