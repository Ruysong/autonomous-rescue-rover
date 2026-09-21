#include "communication/TcpServer.hpp"

#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <unistd.h>

namespace {

void closeSocket(int socketFd) {
    if (socketFd >= 0) {
        close(socketFd);
    }
}

}  // namespace

TcpServer::TcpServer(std::uint16_t port) : port_(port) {}

void TcpServer::run(const CommandHandler& onCommand, const DisconnectHandler& onDisconnect) {
    const int serverFd = socket(AF_INET, SOCK_STREAM, 0);
    if (serverFd < 0) {
        throw std::runtime_error("Unable to create TCP socket: " + std::string(std::strerror(errno)));
    }

    int reuseAddress = 1;
    if (setsockopt(serverFd, SOL_SOCKET, SO_REUSEADDR, &reuseAddress, sizeof(reuseAddress)) < 0) {
        closeSocket(serverFd);
        throw std::runtime_error("Unable to configure TCP socket: " + std::string(std::strerror(errno)));
    }

    sockaddr_in serverAddress{};
    serverAddress.sin_family = AF_INET;
    serverAddress.sin_addr.s_addr = htonl(INADDR_ANY);
    serverAddress.sin_port = htons(port_);

    if (bind(serverFd, reinterpret_cast<sockaddr*>(&serverAddress), sizeof(serverAddress)) < 0) {
        closeSocket(serverFd);
        throw std::runtime_error("Unable to bind TCP port: " + std::string(std::strerror(errno)));
    }

    if (listen(serverFd, 1) < 0) {
        closeSocket(serverFd);
        throw std::runtime_error("Unable to listen on TCP port: " + std::string(std::strerror(errno)));
    }

    std::cout << "[TcpServer] Listening on 0.0.0.0:" << port_ << '\n';

    while (true) {
        sockaddr_in clientAddress{};
        socklen_t clientAddressLength = sizeof(clientAddress);
        const int clientFd = accept(
            serverFd,
            reinterpret_cast<sockaddr*>(&clientAddress),
            &clientAddressLength);

        if (clientFd < 0) {
            std::cerr << "[TcpServer] Accept failed: " << std::strerror(errno) << '\n';
            continue;
        }

        char clientIp[INET_ADDRSTRLEN]{};
        inet_ntop(AF_INET, &clientAddress.sin_addr, clientIp, sizeof(clientIp));
        std::cout << "[TcpServer] Client connected: " << clientIp << '\n';

        std::string pending;
        char buffer[512];
        ssize_t received = 0;

        while ((received = recv(clientFd, buffer, sizeof(buffer), 0)) > 0) {
            pending.append(buffer, static_cast<std::size_t>(received));

            std::size_t newline = 0;
            while ((newline = pending.find('\n')) != std::string::npos) {
                std::string command = pending.substr(0, newline);
                pending.erase(0, newline + 1);

                if (!command.empty() && command.back() == '\r') {
                    command.pop_back();
                }

                if (!command.empty()) {
                    onCommand(command);
                }
            }
        }

        if (received < 0) {
            std::cerr << "[TcpServer] Receive failed: " << std::strerror(errno) << '\n';
        }

        closeSocket(clientFd);
        std::cout << "[TcpServer] Client disconnected\n";
        onDisconnect();
    }
}
