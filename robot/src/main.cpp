// rover_controller: robot-side runtime (control / safety block).
//
//   laptop --TCP 9998 commands--> [Command Gate] -> [kinematics] -> [PicoBridge] --USB--> Pico
//   laptop <--TCP 9997 telemetry-- gate status + Pico telemetry + pose, and LiDAR scans
//   autonomy: odometry (encoders + IMU) -> map pose (laptop correction) -> PathFollower ->
//             Command Gate (source autonomous)
//
// The Pi connects out to the laptop (laptop runs the listeners).

#include "communication/FramedSocket.hpp"
#include "communication/Protocol.hpp"
#include "communication/TcpClient.hpp"
#include "control/CommandGate.hpp"
#include "control/Config.hpp"
#include "control/Kinematics.hpp"
#include "hardware/Imu.hpp"
#include "hardware/Lidar.hpp"
#include "hardware/PicoBridge.hpp"
#include "navigation/Odometry.hpp"
#include "navigation/PathFollower.hpp"
#include "navigation/Pose.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <deque>
#include <exception>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <variant>

#ifndef ROVER_DEFAULT_CONFIG
#define ROVER_DEFAULT_CONFIG "config/rover.json"
#endif

namespace {

using namespace std::chrono_literals;
using rover::control::Clock;

std::atomic<bool> stopRequested{false};

void requestStop(int) { stopRequested = true; }

struct Settings {
    std::string laptopHost;
    std::uint16_t commandPort = 9998;
    std::uint16_t telemetryPort = 9997;
    std::string pico = "/dev/ttyACM0";
    std::string configPath = ROVER_DEFAULT_CONFIG;
    double maxDuty = -1.0;  // < 0: use config
    std::string lidar;      // empty: /dev/ttyUSB0, or mock with --pico mock
    std::string imu;        // empty: config, "off" disables
};

void printUsage() {
    std::cout << "Usage: rover_controller --host LAPTOP_IP [--command-port 9998] "
                 "[--telemetry-port 9997]\n"
                 "                        [--pico /dev/ttyACM0|mock] [--config rover.json] "
                 "[--max-duty 0..0.6]\n"
                 "                        [--lidar /dev/ttyUSB0|mock|off] [--imu on|off]\n"
                 "  --max-duty 0 = dry run: steering moves, wheels never get duty.\n";
}

std::uint16_t parsePort(const std::string& text) {
    std::size_t used = 0;
    const unsigned long value = std::stoul(text, &used);
    if (used != text.size() || value == 0 || value > 65535) {
        throw std::invalid_argument("Port must be between 1 and 65535");
    }
    return static_cast<std::uint16_t>(value);
}

bool parseArgs(int argc, char* argv[], Settings& settings) {
    for (int i = 1; i < argc; ++i) {
        const std::string option = argv[i];
        if (option == "--help") {
            printUsage();
            return false;
        }
        if (i + 1 >= argc) {
            throw std::invalid_argument("Missing value for " + option);
        }
        const std::string value = argv[++i];
        if (option == "--host") {
            settings.laptopHost = value;
        } else if (option == "--command-port") {
            settings.commandPort = parsePort(value);
        } else if (option == "--telemetry-port") {
            settings.telemetryPort = parsePort(value);
        } else if (option == "--pico") {
            settings.pico = value;
        } else if (option == "--config") {
            settings.configPath = value;
        } else if (option == "--max-duty") {
            settings.maxDuty = std::stod(value);
            if (!std::isfinite(settings.maxDuty) || settings.maxDuty < 0.0 ||
                settings.maxDuty > 0.6) {
                throw std::invalid_argument("--max-duty must be in [0, 0.6]");
            }
        } else if (option == "--lidar") {
            settings.lidar = value;
        } else if (option == "--imu") {
            if (value != "on" && value != "off") throw std::invalid_argument("--imu on|off");
            settings.imu = value;
        } else {
            throw std::invalid_argument("Unknown option: " + option);
        }
    }
    if (settings.laptopHost.empty()) {
        throw std::invalid_argument("--host must be the laptop address");
    }
    return true;
}

class Runtime {
public:
    Runtime(Settings settings, rover::control::RoverConfig config)
        : settings_(std::move(settings)),
          config_(config),
          gate_(config.geometry, config.gate),
          pico_(rover::hardware::makePicoLink(settings_.pico)),
          odometry_(config.odometry) {
        namespace hw = rover::hardware;
        follower_.config = config.follower;
        follower_.maxCurvature = rover::control::maxCurvature(config.geometry);
        const bool mockPico = settings_.pico == "mock";
        std::string lidar = settings_.lidar;
        if (lidar.empty()) lidar = mockPico ? "mock" : "/dev/ttyUSB0";
        if (lidar == "mock") {
            lidar_ = std::make_unique<hw::MockLidar>([this] { return truePose(); },
                                                     hw::defaultMockWorld());
        } else if (lidar != "off") {
            lidar_ = std::make_unique<hw::RplidarC1>(lidar, config.lidar);
        }
        const bool imuOn = settings_.imu.empty() ? (config.imu.enabled && !mockPico)
                                                 : settings_.imu == "on";
        if (imuOn) imu_ = std::make_unique<hw::Bno055>(config.imu);
    }

    rover::navigation::Pose2D truePose() const {
        std::lock_guard<std::mutex> lock(poseMutex_);
        return truth_;
    }

    void applyCommand(const rover::protocol::Command& command) {
        using namespace rover::protocol;
        namespace rc = rover::control;
        const auto now = Clock::now();
        std::visit([&](const auto& value) {
            using T = std::decay_t<decltype(value)>;
            if constexpr (std::is_same_v<T, DriveCommand>) {
                // v1.0 compatibility: throttle = duty fraction, steer = fraction of max curvature.
                rc::Motion motion;
                motion.v = value.throttle * gate_.geometry().maxWheelSpeed;
                motion.kappa = value.steer * rc::maxCurvature(gate_.geometry());
                gate_.applyMotion(rc::Source::manual, motion, now);
            } else if constexpr (std::is_same_v<T, MotionCommand>) {
                rc::Motion motion;
                rc::Source source;
                rc::steerModeFrom(value.steerMode, motion.mode);
                rc::sourceFrom(value.source, source);
                motion.v = value.v;
                motion.kappa = value.kappa;
                motion.wz = value.wz;
                gate_.applyMotion(source, motion, now);
            } else if constexpr (std::is_same_v<T, SetModeCommand>) {
                rc::ControlMode mode;
                rc::controlModeFrom(value.mode, mode);
                gate_.setControlMode(mode, now);
                std::cout << "[Gate] Control mode: " << value.mode << '\n';
            } else if constexpr (std::is_same_v<T, ProtectionCommand>) {
                gate_.protection(value.hold,value.speedLimit,now);
                if (value.hold) { std::lock_guard<std::mutex> lock(poseMutex_); follower_.clear(); }
            } else if constexpr (std::is_same_v<T, PathCommand>) {
                std::lock_guard<std::mutex> lock(poseMutex_);
                if (value.points.empty()) {
                    follower_.clear();
                    gate_.stop(now);
                } else {
                    follower_.setPath(value.points, value.speed, value.id);
                }
                std::cout << "[Follow] Path " << value.id << " (" << value.points.size()
                          << " points)\n";
            } else if constexpr (std::is_same_v<T, PoseCorrectionCommand>) {
                std::lock_guard<std::mutex> lock(poseMutex_);
                correction_ = {value.x, value.y, value.yaw};
            } else if constexpr (std::is_same_v<T, EstopCommand>) {
                {
                    std::lock_guard<std::mutex> lock(poseMutex_);
                    follower_.clear();
                }
                gate_.estop(now);
                std::cout << "[Gate] E-stop latched\n";
            } else if constexpr (std::is_same_v<T, ResumeCommand>) {
                gate_.resume(now);
                std::cout << "[Gate] E-stop cleared\n";
            } else {
                // stop (mission stop request): also cancels the autonomous path.
                {
                    std::lock_guard<std::mutex> lock(poseMutex_);
                    follower_.clear();
                }
                gate_.stop(now);
            }
        }, command);
    }

    void outputLoop() {
        // The previous run leaves the Pico latched (see below); the gate starts unlatched.
        pico_->clearEstop();
        bool picoEstop = false;
        auto next = Clock::now();
        auto lastSync = next;
        while (!stopRequested) {
            next += 50ms;
            const auto now = Clock::now();
            const auto status = gate_.status(now);
            const auto picoState = pico_->telemetry();
            const bool picoLatched = picoState.valid && picoState.age < 0.5 &&
                                     (picoState.flags & 2) != 0;
            const bool outOfSync = picoLatched != status.estop && now - lastSync >= 1s;
            if (status.estop != picoEstop || outOfSync) {
                status.estop ? pico_->estop() : pico_->clearEstop();
                picoEstop = status.estop;
                lastSync = now;
            }
            updatePose(now);
            if (auto command = gate_.tick(now)) {
                pico_->send(*command);
            }
            std::this_thread::sleep_until(next);
        }
        // Leave the Pico stopped; its own 300 ms timeout is the backstop.
        pico_->send(rover::control::holdAngles(gate_.status(Clock::now()).output));
        pico_->estop();
    }

    // Odometry + autonomous path following, once per output tick (20 Hz).
    void updatePose(Clock::time_point now) {
        namespace nav = rover::navigation;
        const auto p = pico_->telemetry();
        std::optional<double> imuYaw;
        if (imu_) {
            const auto r = imu_->reading();
            if (r.ok) imuYaw = r.yaw;
        }
        const auto scan = lidar_ ? lidar_->latest() : rover::hardware::LaserScan{};
        const double scanAge =
            scan.valid ? std::chrono::duration<double>(now - scan.stamp).count() : 1e9;

        // LiDAR stop box per direction: a blocked side only stops motion toward it, so the
        // rover can still back away (manual and autonomous). A stale scan blocks everything.
        const bool stale = !scan.valid || scanAge > config_.follower.scanMaxAge;
        gate_.localBlocked(nav::PathFollower::blockedByScan(config_.follower, scan, true),
                           nav::PathFollower::blockedByScan(config_.follower, scan, false),
                           stale, now);
        nav::FollowOutput out;
        {
            std::lock_guard<std::mutex> lock(poseMutex_);
            if (p.valid) odometry_.update(p.encLeft, p.encRight, imuYaw);
            // Mock world: the "true" pose drifts from odometry (4 % distance, 3 % rotation)
            // so the laptop SLAM has something to correct.
            const double ds = odometry_.lastDistance() * 1.04;
            const double mid = truth_.yaw + 0.5 * odometry_.lastRotation() * 1.03;
            truth_.x += ds * std::cos(mid);
            truth_.y += ds * std::sin(mid);
            truth_.yaw = nav::wrapAngle(truth_.yaw + odometry_.lastRotation() * 1.03);

            history_.push_back({now, odometry_.pose()});
            while (history_.size() > 60) history_.pop_front();

            if (gate_.status(now).controlMode == rover::control::ControlMode::autonomous) {
                const auto mapPose = nav::compose(correction_, odometry_.pose());
                out = follower_.step(mapPose, scan, scanAge);
            }
        }
        if (out.active) {
            gate_.applyMotion(rover::control::Source::autonomous, out.motion, now);
        } else if (out.stop) {
            gate_.stop(now);
        }
    }

    // Odom pose closest in time to `t` (scan stamps lag the output loop slightly).
    rover::navigation::Pose2D odomAt(Clock::time_point t) const {
        std::lock_guard<std::mutex> lock(poseMutex_);
        if (history_.empty()) return odometry_.pose();
        auto best = history_.back();
        for (const auto& h : history_) {
            if (std::chrono::abs(h.first - t) < std::chrono::abs(best.first - t)) best = h;
        }
        return best.second;
    }

    rover::protocol::Telemetry telemetry() const {
        const auto now = Clock::now();
        const auto s = gate_.status(now);
        const auto p = pico_->telemetry();
        const auto& g = gate_.geometry();
        rover::protocol::Telemetry t{};
        t.timestamp =
            std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch())
                .count();
        t.motorSpeed = std::clamp((s.output.dutyLeft + s.output.dutyRight) / 2.0, -1.0, 1.0);
        t.mode = pico_->name() == "mock" ? "mock" : "pico";
        t.safetyState = s.safetyState;
        t.lastCommandAge = s.lastCommandAge;
        t.controlMode = rover::control::toString(s.controlMode);
        t.steerMode = rover::control::toString(s.steerMode);
        t.estop = s.estop;
        t.dutyLeft = s.output.dutyLeft;
        t.dutyRight = s.output.dutyRight;
        t.steerDeg = s.output.steerDeg;
        t.picoOk = p.valid && p.age < 0.5;
        t.picoFlags = p.flags;
        t.encLeft = p.encLeft;
        t.encRight = p.encRight;
        t.ultrasonicCm = p.distCm;
        t.maxCurvature = rover::control::maxCurvature(g);
        t.maxSpeed = g.maxWheelSpeed * g.maxDuty;
        t.spinEnabled = g.spinEnabled;
        t.imuOk = imu_ && imu_->reading().ok;
        if (lidar_) {
            const auto scan = lidar_->latest();
            t.lidarOk = scan.valid && now - scan.stamp < 500ms;
        }
        std::lock_guard<std::mutex> lock(poseMutex_);
        const auto odom = odometry_.pose();
        const auto map = rover::navigation::compose(correction_, odom);
        t.odomPose = {odom.x, odom.y, odom.yaw};
        t.mapPose = {map.x, map.y, map.yaw};
        t.odomDistance = odometry_.distance();
        t.followState = rover::navigation::toString(follower_.state());
        t.pathId = follower_.pathId();
        t.pathRemaining = follower_.remaining();
        return t;
    }

    void runCommands() {
        while (!stopRequested) {
            rover::communication::TcpClient client;
            std::string error;
            if (!client.connectTo(settings_.laptopHost, settings_.commandPort, error)) {
                std::cerr << "[Command] Connect failed: " << error << '\n';
                std::this_thread::sleep_for(1s);
                continue;
            }
            std::cout << "[Command] Connected to laptop:" << settings_.commandPort << '\n';
            gate_.linkConnected(Clock::now());

            while (!stopRequested) {
                const auto frame = rover::communication::recvFrame(client.fd());
                if (frame.status != rover::communication::FrameStatus::received) {
                    if (frame.status != rover::communication::FrameStatus::disconnected) {
                        std::cerr << "[Command] Receive failed: " << frame.error << '\n';
                    }
                    break;
                }
                const auto parsed = rover::protocol::parseCommand(frame.payload);
                if (const auto* command = rover::protocol::commandFrom(parsed)) {
                    applyCommand(*command);
                } else {
                    std::cerr << "[Command] Rejected: " << *rover::protocol::errorFrom(parsed)
                              << '\n';
                }
            }
            gate_.linkDisconnected(Clock::now());
            { std::lock_guard<std::mutex> lock(poseMutex_); follower_.clear(); }
            std::cout << "[Safety] Command link lost; stopped\n";
            client.close();
            std::this_thread::sleep_for(1s);
        }
    }

    void runTelemetry() {
        while (!stopRequested) {
            rover::communication::TcpClient client;
            std::string error;
            if (!client.connectTo(settings_.laptopHost, settings_.telemetryPort, error)) {
                std::this_thread::sleep_for(1s);
                continue;
            }
            std::cout << "[Telemetry] Connected to laptop:" << settings_.telemetryPort << '\n';
            unsigned lastScan = 0;
            unsigned tick = 0;
            while (!stopRequested) {
                if (tick++ % 2 == 0) {
                    if (!send(client, rover::protocol::serializeTelemetry(telemetry()), error)) {
                        break;
                    }
                }
                if (lidar_) {
                    const auto scan = lidar_->latest();
                    if (scan.valid && scan.sequence != lastScan) {
                        lastScan = scan.sequence;
                        if (!send(client, rover::protocol::serializeScan(scanMessage(scan)),
                                  error)) {
                            break;
                        }
                    }
                }
                std::this_thread::sleep_for(50ms);
            }
            client.close();
            std::this_thread::sleep_for(1s);
        }
    }

private:
    static bool send(rover::communication::TcpClient& client, const std::string& payload,
                     std::string& error) {
        const auto status = rover::communication::sendFrame(client.fd(), payload, error);
        if (status != rover::communication::FrameStatus::received) {
            std::cerr << "[Telemetry] Send failed: " << error << '\n';
            return false;
        }
        return true;
    }

    rover::protocol::ScanMessage scanMessage(const rover::hardware::LaserScan& scan) const {
        rover::protocol::ScanMessage m;
        // Wall-clock stamp of the scan (it completed `age` ago).
        const auto age = std::chrono::duration_cast<std::chrono::system_clock::duration>(
            Clock::now() - scan.stamp);
        m.timestamp = std::chrono::duration<double>(
                          (std::chrono::system_clock::now() - age).time_since_epoch())
                          .count();
        m.sequence = scan.sequence;
        const auto pose = odomAt(scan.stamp);
        m.odomPose = {pose.x, pose.y, pose.yaw};
        m.rangesMm.assign(scan.rangesMm.begin(), scan.rangesMm.end());
        return m;
    }

    Settings settings_;
    rover::control::RoverConfig config_;
    rover::control::CommandGate gate_;
    std::unique_ptr<rover::hardware::PicoLink> pico_;

    mutable std::mutex poseMutex_;  // odometry, follower, correction, truth, history
    rover::navigation::Odometry odometry_;
    rover::navigation::PathFollower follower_;
    rover::navigation::Pose2D correction_;  // map <- odom
    rover::navigation::Pose2D truth_;       // mock world only
    std::deque<std::pair<Clock::time_point, rover::navigation::Pose2D>> history_;

    // Declared last: their threads call back into the members above.
    std::unique_ptr<rover::hardware::LidarSource> lidar_;
    std::unique_ptr<rover::hardware::Bno055> imu_;
};

}  // namespace

int main(int argc, char* argv[]) {
    Settings settings;
    rover::control::RoverConfig config;
    try {
        if (!parseArgs(argc, argv, settings)) {
            return 0;
        }
        config = rover::control::loadConfig(settings.configPath);
        if (settings.pico == "mock") {
            // MockPico: 1500 counts/s at duty 1 = maxWheelSpeed.
            config.odometry.countsPerMeter = 1500.0 / config.geometry.maxWheelSpeed;
        }
        if (settings.maxDuty >= 0.0) {
            // Override in the geometry so telemetry shows what the wheels really get.
            config.geometry.maxDuty = std::min(config.geometry.maxDuty, settings.maxDuty);
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        printUsage();
        return 1;
    }

    std::cout.setf(std::ios::unitbuf);
    std::signal(SIGINT, requestStop);
    std::signal(SIGTERM, requestStop);
    std::signal(SIGHUP, requestStop);  // SSH session closed: stop cleanly (E-stop the Pico)
    std::signal(SIGPIPE, SIG_IGN);

    Runtime runtime(settings, config);
    std::cout << "RoverAid controller started (pico " << settings.pico << ", config "
              << settings.configPath << ", max curvature "
              << rover::control::maxCurvature(config.geometry) << " 1/m, max duty "
              << config.geometry.maxDuty << ")\n";

    std::thread output([&] { runtime.outputLoop(); });
    std::thread telemetry([&] { runtime.runTelemetry(); });
    std::thread commands([&] { runtime.runCommands(); });

    while (!stopRequested) {
        std::this_thread::sleep_for(100ms);
    }
    output.join();
    std::cout << "Stopped.\n";
    // Network threads may be blocked in connect/recv; exit without joining them.
    std::_Exit(0);
}
