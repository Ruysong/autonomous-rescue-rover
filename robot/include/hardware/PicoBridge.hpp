#pragma once

// PicoBridge: the only process that opens the Pico serial port.
// Protocol: docs/HARDWARE_INTERFACE.md section 3 (C / E / R out, S / X in).

#include "control/Kinematics.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace rover::hardware {

struct PicoTelemetry {
    bool valid = false;            // at least one S line received
    long picoMs = 0;
    long encLeft = 0;
    long encRight = 0;
    std::array<int, 4> distCm{-1, -1, -1, -1};  // FL, FR, RL, RR; -1 = no echo
    int flags = 0;                 // 1 link timeout, 2 e-stop, 4 clamped, 8 no command since boot
    double age = 1e9;              // s since the last S line
    unsigned rejects = 0;
    std::string lastReject;
    bool portOpen = false;
};

std::string formatDriveLine(const control::WheelCommand& command);
// Parses one S line. Returns false (and leaves `out` untouched) if malformed.
bool parseTelemetryLine(const std::string& line, PicoTelemetry& out);

class PicoLink {
public:
    virtual ~PicoLink() = default;
    virtual void send(const control::WheelCommand& command) = 0;
    virtual void estop() = 0;
    virtual void clearEstop() = 0;
    virtual PicoTelemetry telemetry() const = 0;
    virtual std::string name() const = 0;
};

// Real Pico over USB CDC. Re-opens the port if it disappears.
class SerialPico final : public PicoLink {
public:
    explicit SerialPico(std::string path);
    ~SerialPico() override;

    void send(const control::WheelCommand& command) override;
    void estop() override;
    void clearEstop() override;
    PicoTelemetry telemetry() const override;
    std::string name() const override { return path_; }

private:
    void writeLine(const std::string& line);
    void readLoop();
    bool openPort();
    void closePortLocked();
    void handleLine(const std::string& line);

    std::string path_;
    mutable std::mutex mutex_;
    int fd_ = -1;
    PicoTelemetry telemetry_;
    std::chrono::steady_clock::time_point lastRx_{};
    std::atomic<bool> running_{true};
    std::thread reader_;
};

// Simulated Pico for tests and laptop-only runs. Encoders follow the duty.
class MockPico final : public PicoLink {
public:
    explicit MockPico(double countsPerSecondAtFullDuty = 1500.0);

    void send(const control::WheelCommand& command) override;
    void estop() override;
    void clearEstop() override;
    PicoTelemetry telemetry() const override;
    std::string name() const override { return "mock"; }

    control::WheelCommand lastCommand() const;

private:
    void advanceLocked(std::chrono::steady_clock::time_point now);

    double countsPerSecond_;
    mutable std::mutex mutex_;
    control::WheelCommand last_;
    bool estop_ = false;
    bool commanded_ = false;
    double encLeft_ = 0.0;
    double encRight_ = 0.0;
    std::chrono::steady_clock::time_point start_;
    std::chrono::steady_clock::time_point lastSend_;
    std::chrono::steady_clock::time_point lastAdvance_;
};

// "mock" -> MockPico, anything else is a serial device path.
std::unique_ptr<PicoLink> makePicoLink(const std::string& spec);

}  // namespace rover::hardware
