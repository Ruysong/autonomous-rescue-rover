#pragma once

#include <cstdint>
#include <functional>
#include <string>

class TcpServer {
public:
    using CommandHandler = std::function<void(const std::string& command)>;
    using DisconnectHandler = std::function<void()>;

    explicit TcpServer(std::uint16_t port);

    void run(const CommandHandler& onCommand, const DisconnectHandler& onDisconnect);

private:
    std::uint16_t port_;
};
