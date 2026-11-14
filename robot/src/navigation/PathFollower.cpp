#include "navigation/PathFollower.hpp"

#include <algorithm>
#include <cmath>

namespace rover::navigation {
namespace {
constexpr double kPi = 3.14159265358979323846;
constexpr double kDeg = kPi / 180.0;
}  // namespace

const char* toString(FollowState state) {
    switch (state) {
        case FollowState::idle: return "idle";
        case FollowState::following: return "following";
        case FollowState::reversing: return "reversing";
        case FollowState::blocked: return "blocked";
        case FollowState::reached: return "reached";
        case FollowState::noScan: return "no_scan";
    }
    return "idle";
}

void PathFollower::setPath(std::vector<std::pair<double, double>> points, double speed, int id) {
    path_ = std::move(points);
    speed_ = speed > 0 ? speed : config.cruiseSpeed;
    pathId_ = id;
    index_ = 0;
    remaining_ = 0.0;
    reverseStart_.reset();
    state_ = path_.empty() ? FollowState::idle : FollowState::following;
}

void PathFollower::clear() {
    path_.clear();
    state_ = FollowState::idle;
    reverseStart_.reset();
}

FollowOutput PathFollower::halt(FollowState next) {
    FollowOutput out;
    out.stop = state_ != next;  // one stop per transition; the gate then times out / idles
    state_ = next;
    return out;
}

bool PathFollower::blockedByScan(const FollowerConfig& c, const hardware::LaserScan& scan,
                                 bool forward) {
    for (int i = 0; i < hardware::kScanBins; ++i) {
        const auto mm = scan.rangesMm[static_cast<std::size_t>(i)];
        if (mm == 0) continue;
        const double r = mm / 1000.0;
        const double x = r * std::cos(i * kDeg);
        const double y = r * std::sin(i * kDeg);
        if (std::fabs(y) > c.bodyHalfWidth) continue;
        if (forward && x > c.bodyFront && x < c.bodyFront + c.stopDistance) return true;
        if (!forward && x < c.bodyRear && x > c.bodyRear - c.stopDistance) return true;
    }
    return false;
}

FollowOutput PathFollower::step(const Pose2D& pose, const hardware::LaserScan& scan,
                                double scanAgeSec) {
    if (path_.empty() || state_ == FollowState::idle || state_ == FollowState::reached) {
        return {};
    }

    // Progress: closest point at or after the current index (never goes back).
    double best = 1e18;
    for (std::size_t i = index_; i < path_.size(); ++i) {
        const double d = std::hypot(path_[i].first - pose.x, path_[i].second - pose.y);
        if (d < best) {
            best = d;
            index_ = i;
        }
    }
    remaining_ = std::hypot(path_[index_].first - pose.x, path_[index_].second - pose.y);
    for (std::size_t i = index_ + 1; i < path_.size(); ++i) {
        remaining_ += std::hypot(path_[i].first - path_[i - 1].first,
                                 path_[i].second - path_[i - 1].second);
    }
    const auto& goal = path_.back();
    if (std::hypot(goal.first - pose.x, goal.second - pose.y) < config.goalTolerance) {
        return halt(FollowState::reached);
    }

    // Lookahead point: first point at least `lookahead` away beyond the closest one.
    std::pair<double, double> target = goal;
    for (std::size_t i = index_; i < path_.size(); ++i) {
        if (std::hypot(path_[i].first - pose.x, path_[i].second - pose.y) >= config.lookahead) {
            target = path_[i];
            break;
        }
    }
    double lx, ly;
    toLocal(pose, target.first, target.second, lx, ly);
    const double alpha = std::atan2(ly, lx);
    const double ld = std::max(std::hypot(lx, ly), 1e-3);

    // 3-point turn bookkeeping.
    if (state_ != FollowState::reversing && std::fabs(alpha) > config.turnAroundDeg * kDeg) {
        state_ = FollowState::reversing;
        reverseStart_ = pose;
    } else if (state_ == FollowState::reversing &&
               (std::fabs(alpha) < config.resumeForwardDeg * kDeg ||
                (reverseStart_ && std::hypot(pose.x - reverseStart_->x,
                                             pose.y - reverseStart_->y) > config.maxReverseM))) {
        state_ = FollowState::following;
        reverseStart_.reset();
    }
    const bool forward = state_ != FollowState::reversing;

    if (!scan.valid || scanAgeSec > config.scanMaxAge) {
        const bool wasReversing = !forward;
        auto out = halt(FollowState::noScan);
        if (wasReversing) reverseStart_.reset();
        return out;
    }
    if (blockedByScan(config, scan, forward)) {
        return halt(FollowState::blocked);
    }
    if (state_ == FollowState::blocked || state_ == FollowState::noScan) {
        state_ = FollowState::following;
    }

    FollowOutput out;
    out.active = true;
    if (forward) {
        double kappa = std::clamp(2.0 * std::sin(alpha) / ld, -maxCurvature, maxCurvature);
        const double lock = std::fabs(kappa) / maxCurvature;  // 0 straight .. 1 full lock
        double v = speed_ * (1.0 - 0.5 * lock);
        v = std::min(v, std::max(config.minSpeed, remaining_ * 0.5));
        out.motion.v = std::max(v, config.minSpeed);
        out.motion.kappa = kappa;
    } else {
        // Backing up: yaw rate = v * kappa; v < 0, so kappa opposite to alpha turns the nose
        // towards the target.
        out.motion.v = -config.reverseSpeed;
        out.motion.kappa = alpha > 0 ? -maxCurvature : maxCurvature;
    }
    return out;
}

}  // namespace rover::navigation
