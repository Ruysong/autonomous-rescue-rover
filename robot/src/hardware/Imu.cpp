#include "hardware/Imu.hpp"

#include <cmath>
#include <iostream>

#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <sys/ioctl.h>
#include <unistd.h>

namespace rover::hardware {
namespace {

using namespace std::chrono_literals;
constexpr double kPi = 3.14159265358979323846;

constexpr std::uint8_t kChipId = 0x00;
constexpr std::uint8_t kEulerH = 0x1A;
constexpr std::uint8_t kCalibStat = 0x35;
constexpr std::uint8_t kOprMode = 0x3D;
constexpr std::uint8_t kAxisMapConfig = 0x41;
constexpr std::uint8_t kAxisMapSign = 0x42;
constexpr std::uint8_t kModeConfig = 0x00;
constexpr std::uint8_t kModeImu = 0x08;

bool writeReg(int fd, std::uint8_t reg, std::uint8_t value) {
    const std::uint8_t bytes[2] = {reg, value};
    return ::write(fd, bytes, 2) == 2;
}

bool readRegs(int fd, std::uint8_t reg, std::uint8_t* out, std::size_t n) {
    if (::write(fd, &reg, 1) != 1) return false;
    return ::read(fd, out, n) == static_cast<ssize_t>(n);
}

}  // namespace

bool HeadingUnwrapper::add(double headingDeg) {
    if (!std::isfinite(headingDeg) || headingDeg < 0.0 || headingDeg > 360.0) return false;
    if (!prev_) {
        prev_ = headingDeg;
        return true;
    }
    double d = headingDeg - *prev_;
    if (d > 180.0) d -= 360.0;
    if (d < -180.0) d += 360.0;
    // A real jump persists; a corrupted byte does not. Accept after 5 consistent rejections.
    if (std::fabs(d) > maxStep_ && rejectedInRow_ < 5) {
        ++rejectedInRow_;
        return false;
    }
    rejectedInRow_ = 0;
    total_ += d;
    prev_ = headingDeg;
    return true;
}

Bno055::Bno055(ImuConfig config) : config_(std::move(config)), thread_([this] { run(); }) {}

Bno055::~Bno055() {
    running_ = false;
    if (thread_.joinable()) thread_.join();
}

ImuReading Bno055::reading() const {
    std::lock_guard<std::mutex> lock(mutex_);
    ImuReading out = reading_;
    out.ok = reading_.ok && std::chrono::steady_clock::now() - lastOk_ < 300ms;
    return out;
}

bool Bno055::setup(int fd) {
    std::uint8_t id = 0;
    if (!readRegs(fd, kChipId, &id, 1) || id != 0xA0) return false;
    if (!writeReg(fd, kOprMode, kModeConfig)) return false;
    std::this_thread::sleep_for(30ms);
    writeReg(fd, kAxisMapConfig, static_cast<std::uint8_t>(config_.axisMapConfig));
    writeReg(fd, kAxisMapSign, static_cast<std::uint8_t>(config_.axisMapSign));
    if (!writeReg(fd, kOprMode, kModeImu)) return false;
    std::this_thread::sleep_for(30ms);
    return true;
}

void Bno055::run() {
    double baseDeg = 0.0;  // heading carried over a reconnect (fusion restarts at 0)
    double lastDeg = 0.0;
    while (running_) {
        const int fd = ::open(config_.bus.c_str(), O_RDWR);
        if (fd < 0 || ::ioctl(fd, I2C_SLAVE, config_.address) < 0 || !setup(fd)) {
            if (fd >= 0) ::close(fd);
            {
                std::lock_guard<std::mutex> lock(mutex_);
                ++reading_.errors;
            }
            std::this_thread::sleep_for(1s);
            continue;
        }
        std::cout << "[IMU] BNO055 IMU mode on " << config_.bus << '\n';
        HeadingUnwrapper unwrap(config_.maxStepDeg);
        int failures = 0;
        unsigned loops = 0;
        while (running_ && failures < 50) {
            std::uint8_t b[2];
            if (!readRegs(fd, kEulerH, b, 2)) {
                ++failures;
                std::lock_guard<std::mutex> lock(mutex_);
                ++reading_.errors;
            } else {
                failures = 0;
                const auto raw = static_cast<std::int16_t>(b[0] | (b[1] << 8));
                const bool accepted = unwrap.add(raw / 16.0);
                std::uint8_t cal = 0;
                if (++loops % 50 == 0) readRegs(fd, kCalibStat, &cal, 1);
                std::lock_guard<std::mutex> lock(mutex_);
                if (accepted) {
                    reading_.ok = true;
                    lastDeg = baseDeg + unwrap.totalDeg();
                    reading_.yaw = config_.yawSign * lastDeg * kPi / 180.0;
                    lastOk_ = std::chrono::steady_clock::now();
                } else {
                    ++reading_.dropped;
                }
                if (loops % 50 == 0) reading_.calibration = cal;
            }
            std::this_thread::sleep_for(20ms);
        }
        ::close(fd);
        std::cerr << "[IMU] Lost the BNO055; reconnecting\n";
        baseDeg = lastDeg;  // rotation while disconnected is lost
        std::this_thread::sleep_for(500ms);
    }
}

}  // namespace rover::hardware
