#include "communication/TcpClient.hpp"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <string>
#include <sys/socket.h>
#include <unistd.h>

namespace rover::communication {

TcpClient::~TcpClient() {
    close();
}

bool TcpClient::connectTo(const std::string& host, std::uint16_t port, std::string& error) {
    close();

    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    addrinfo* addresses = nullptr;
    const std::string portText = std::to_string(port);
    const int lookup = getaddrinfo(host.c_str(), portText.c_str(), &hints, &addresses);
    if (lookup != 0) {
        error = gai_strerror(lookup);
        return false;
    }

    for (addrinfo* address = addresses; address != nullptr; address = address->ai_next) {
        const int candidate = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
        if (candidate < 0) {
            error = std::strerror(errno);
            continue;
        }

        const int flags = fcntl(candidate, F_GETFL, 0);
        if (flags < 0 || fcntl(candidate, F_SETFL, flags | O_NONBLOCK) < 0) {
            error = std::strerror(errno);
            ::close(candidate);
            continue;
        }

        int result = ::connect(candidate, address->ai_addr, address->ai_addrlen);
        if (result < 0 && errno == EINPROGRESS) {
            pollfd readiness{candidate, POLLOUT, 0};
            do {
                result = poll(&readiness, 1, 2000);
            } while (result < 0 && errno == EINTR);

            if (result > 0) {
                int socketError = 0;
                socklen_t errorSize = sizeof(socketError);
                result = getsockopt(candidate, SOL_SOCKET, SO_ERROR, &socketError, &errorSize);
                if (result == 0 && socketError != 0) {
                    errno = socketError;
                    result = -1;
                }
            } else {
                errno = result == 0 ? ETIMEDOUT : errno;
                result = -1;
            }
        }

        if (result == 0 && fcntl(candidate, F_SETFL, flags) == 0) {
            fd_ = candidate;
            break;
        }

        error = std::strerror(errno);
        ::close(candidate);
    }

    freeaddrinfo(addresses);
    return fd_ >= 0;
}

void TcpClient::close() {
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
}

}  // namespace rover::communication
