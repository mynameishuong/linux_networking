#include "net.h"

#include <arpa/inet.h>
#include <errno.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    struct sockaddr_in address;
    if (argc != 3 || endpoint(argv[1], argv[2], &address) < 0) {
        fprintf(stderr, "Usage: %s BIND_IPV4 PORT\n", argv[0]);
        return 1;
    }
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) {
        perror("socket");
        return 1;
    }
    int reuse = 1;
    if (setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) < 0 ||
        bind(listener, (struct sockaddr *)&address, sizeof(address)) < 0 ||
        listen(listener, 8) < 0) {
        perror("listen setup");
        close(listener);
        return 1;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("Gateway listening on %s:%s (one active client at a time)\n", argv[1], argv[2]);
    for (;;) {
        struct sockaddr_in peer;
        socklen_t peer_size = sizeof(peer);
        int client = accept(listener, (struct sockaddr *)&peer, &peer_size);
        if (client < 0) {
            if (errno == EINTR)
                continue;
            perror("accept");
            close(listener);
            return 1;
        }
        char peer_ip[INET_ADDRSTRLEN] = "unknown";
        (void)inet_ntop(AF_INET, &peer.sin_addr, peer_ip, sizeof(peer_ip));
        printf("Connected %s:%u\n", peer_ip, (unsigned)ntohs(peer.sin_port));
        if (socket_timeout(client) < 0) {
            perror("socket timeout");
            close(client);
            continue;
        }
        unsigned char payload[FRAME_MAX];
        size_t length;
        int result;
        while ((result = recv_frame(client, payload, &length)) == 1) {
            printf("RX %zu bytes: %.*s\n", length, (int)length, (const char *)payload);
            /* Echo confirms application-level reception; it is not a TCP ACK. */
            if (send_frame(client, payload, length) < 0) {
                perror("send frame");
                break;
            }
        }
        if (result < 0)
            perror("receive frame");
        close(client);
        puts("Disconnected");
    }
}
