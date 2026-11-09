#include "net.h"

#include <arpa/inet.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>

int endpoint(const char *ip, const char *port, struct sockaddr_in *address)
{
    char *end = NULL;
    errno = 0;
    long number = strtol(port, &end, 10);
    if (errno || end == port || *end || number < 1 || number > 65535) {
        errno = EINVAL;
        return -1;
    }
    memset(address, 0, sizeof(*address));
    address->sin_family = AF_INET;
    address->sin_port = htons((uint16_t)number);
    if (inet_pton(AF_INET, ip, &address->sin_addr) != 1) {
        errno = EINVAL;
        return -1;
    }
    return 0;
}

int socket_timeout(int fd)
{
    const struct timeval timeout = { .tv_sec = 5, .tv_usec = 0 };
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0)
        return -1;
    return setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
}

static int send_all(int fd, const void *data, size_t length)
{
    const unsigned char *bytes = data;
    size_t done = 0;
    while (done < length) {
        ssize_t count = send(fd, bytes + done, length - done, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0) {
            if (count == 0)
                errno = EPIPE;
            return -1;
        }
        done += (size_t)count;
    }
    return 0;
}

static int recv_exact(int fd, void *data, size_t length)
{
    unsigned char *bytes = data;
    size_t done = 0;
    while (done < length) {
        ssize_t count = recv(fd, bytes + done, length - done, 0);
        if (count < 0 && errno == EINTR)
            continue;
        if (count < 0)
            return -1;
        if (count == 0) {
            if (done == 0)
                return 0;
            errno = EPROTO;
            return -1;
        }
        done += (size_t)count;
    }
    return 1;
}

int send_frame(int fd, const void *payload, size_t length)
{
    if (length == 0 || length > FRAME_MAX) {
        errno = EMSGSIZE;
        return -1;
    }
    uint32_t header = htonl((uint32_t)length);
    if (send_all(fd, &header, sizeof(header)) < 0)
        return -1;
    return send_all(fd, payload, length);
}

int recv_frame(int fd, void *payload, size_t *length)
{
    uint32_t header;
    int result = recv_exact(fd, &header, sizeof(header));
    if (result != 1)
        return result;
    *length = ntohl(header);
    if (*length == 0 || *length > FRAME_MAX) {
        errno = EMSGSIZE;
        return -1;
    }
    result = recv_exact(fd, payload, *length);
    if (result == 0) {
        /* EOF after the header is a truncated frame, not a clean close. */
        errno = EPROTO;
        return -1;
    }
    return result;
}
