#include "net.h"

#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    struct sockaddr_in address;
    if (argc != 4 || endpoint(argv[1], argv[2], &address) < 0) {
        fprintf(stderr, "Usage: %s GATEWAY_IPV4 PORT 'telemetry text'\n", argv[0]);
        return 1;
    }
    size_t length = strlen(argv[3]);
    if (length == 0 || length > FRAME_MAX) {
        fprintf(stderr, "Payload must contain 1..%u bytes\n", FRAME_MAX);
        return 1;
    }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("socket");
        return 1;
    }
    if (socket_timeout(fd) < 0 ||
        connect(fd, (struct sockaddr *)&address, sizeof(address)) < 0 ||
        send_frame(fd, argv[3], length) < 0) {
        perror("connect/send");
        close(fd);
        return 1;
    }
    unsigned char response[FRAME_MAX];
    size_t received;
    int result = recv_frame(fd, response, &received);
    close(fd);
    if (result != 1) {
        if (result < 0)
            perror("receive");
        else
            fprintf(stderr, "Gateway closed before reply\n");
        return 1;
    }
    if (received != length || memcmp(response, argv[3], length) != 0) {
        fprintf(stderr, "Reply does not match request\n");
        return 1;
    }
    printf("Application ACK: %.*s\n", (int)received, (const char *)response);
    return 0;
}
