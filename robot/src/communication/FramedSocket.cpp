#include "communication/FramedSocket.hpp"

#include <arpa/inet.h>
#include <array>
#include <cerrno>
#include <cstring>
#include <string>
#include <sys/socket.h>
#include <utility>
#include <unistd.h>

namespace rover::communication {
namespace {

FrameResult recvExact(int socketFd, char* destination, std::size_t size) {
    std::size_t receivedTotal = 0;

    while (receivedTotal < size) {
        const ssize_t received = recv(socketFd, destination + receivedTotal, size - receivedTotal, 0);

        if (received == 0) {
            return {FrameStatus::disconnected, {}, "Connection closed"};
        }

        if (received < 0) {
            if (errno == EINTR) {
                continue;
            }

            return {FrameStatus::error, {}, std::strerror(errno)};
        }

        receivedTotal += static_cast<std::size_t>(received);
    }

    return {FrameStatus::received, {}, {}};
}

FrameStatus sendAll(int socketFd, const char* source, std::size_t size, std::string& error) {
    std::size_t sentTotal = 0;

    while (sentTotal < size) {
        const ssize_t sent = send(socketFd, source + sentTotal, size - sentTotal, MSG_NOSIGNAL);

        if (sent < 0) {
            if (errno == EINTR) {
                continue;
            }

            error = std::strerror(errno);
            return FrameStatus::error;
        }

        if (sent == 0) {
            error = "Connection closed";
            return FrameStatus::disconnected;
        }

        sentTotal += static_cast<std::size_t>(sent);
    }

    return FrameStatus::received;
}

}  // namespace

FrameStatus sendFrame(int socketFd, std::string_view payload, std::string& error) {
    if (payload.size() > kMaxFrameSize) {
        error = "Frame exceeds maximum size";
        return FrameStatus::too_large;
    }

    const std::uint32_t networkLength = htonl(static_cast<std::uint32_t>(payload.size()));
    std::array<char, sizeof(networkLength)> header{};
    std::memcpy(header.data(), &networkLength, sizeof(networkLength));

    FrameStatus status = sendAll(socketFd, header.data(), header.size(), error);
    if (status != FrameStatus::received) {
        return status;
    }

    return sendAll(socketFd, payload.data(), payload.size(), error);
}

FrameResult recvFrame(int socketFd) {
    std::array<char, sizeof(std::uint32_t)> header{};
    FrameResult headerResult = recvExact(socketFd, header.data(), header.size());
    if (headerResult.status != FrameStatus::received) {
        return headerResult;
    }

    std::uint32_t networkLength = 0;
    std::memcpy(&networkLength, header.data(), sizeof(networkLength));
    const std::uint32_t payloadLength = ntohl(networkLength);

    if (payloadLength > kMaxFrameSize) {
        return {FrameStatus::too_large, {}, "Frame exceeds maximum size"};
    }

    std::string payload(payloadLength, '\0');
    if (payload.empty()) {
        return {FrameStatus::received, {}, {}};
    }

    FrameResult payloadResult = recvExact(socketFd, payload.data(), payload.size());
    if (payloadResult.status != FrameStatus::received) {
        return payloadResult;
    }

    return {FrameStatus::received, std::move(payload), {}};
}

}  // namespace rover::communication
