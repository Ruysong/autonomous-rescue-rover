// rover_controller: robot-side runtime (control / safety block).
//
//   laptop --TCP 9998 commands--> [Command Gate] -> [kinematics] -> [PicoBridge] --USB--> Pico
//   laptop <--TCP 9997 telemetry-- gate status + Pico telemetry
//
// The Pi connects out to the laptop (laptop runs the listeners).

#include "communication/FramedSocket.hpp"
#include "communication/Protocol.hpp"
#include "communication/TcpClient.hpp"
#include "control/CommandGate.hpp"
#include "control/Config.hpp"
#include "control/Kinematics.hpp"
#include "hardware/PicoBridge.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <exception>
#include <iostream>
#include <memory>
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
};

void printUsage() {
    std::cout << "Usage: rover_controller --host LAPTOP_IP [--command-port 9998] "
                 "[--telemetry-port 9997]\n"
                 "                        [--pico /dev/ttyACM0|mock] [--config rover.json] "
                 "[--max-duty 0..0.6]\n"
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
          gate_(config.geometry, config.gate),
          pico_(rover::hardware::makePicoLink(settings_.pico)) {}

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
            } else if constexpr (std::is_same_v<T, EstopCommand>) {
                gate_.estop(now);
                std::cout << "[Gate] E-stop latched\n";
            } else if constexpr (std::is_same_v<T, ResumeCommand>) {
                gate_.resume(now);
                std::cout << "[Gate] E-stop cleared\n";
            } else {
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
            if (auto command = gate_.tick(now)) {
                pico_->send(*command);
            }
            std::this_thread::sleep_until(next);
        }
        // Leave the Pico stopped; its own 300 ms timeout is the backstop.
        pico_->send(rover::control::holdAngles(gate_.status(Clock::now()).output));
        pico_->estop();
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
            while (!stopRequested) {
                const std::string payload = rover::protocol::serializeTelemetry(telemetry());
                const auto status = rover::communication::sendFrame(client.fd(), payload, error);
                if (status != rover::communication::FrameStatus::received) {
                    std::cerr << "[Telemetry] Send failed: " << error << '\n';
                    break;
                }
                std::this_thread::sleep_for(100ms);
            }
            client.close();
            std::this_thread::sleep_for(1s);
        }
    }

private:
    Settings settings_;
    rover::control::CommandGate gate_;
    std::unique_ptr<rover::hardware::PicoLink> pico_;
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
