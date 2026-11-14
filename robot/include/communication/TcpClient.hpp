#pragma once

#include <cstdint>
#include <string>

namespace rover::communication {

class TcpClient {
public:
    TcpClient() = default;
    ~TcpClient();

    TcpClient(const TcpClient&) = delete;
    TcpClient& operator=(const TcpClient&) = delete;

    bool connectTo(const std::string& host, std::uint16_t port, std::string& error);
    int fd() const { return fd_; }
    void close();

private:
    int fd_ = -1;
};

}  // namespace rover::communication
