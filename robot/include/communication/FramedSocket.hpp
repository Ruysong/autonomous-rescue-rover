#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace rover::communication {

inline constexpr std::uint32_t kMaxFrameSize = 64 * 1024;

enum class FrameStatus {
    received,
    disconnected,
    too_large,
    error,
};

struct FrameResult {
    FrameStatus status;
    std::string payload;
    std::string error;
};

FrameStatus sendFrame(int socketFd, std::string_view payload, std::string& error);
FrameResult recvFrame(int socketFd);

}  // namespace rover::communication
