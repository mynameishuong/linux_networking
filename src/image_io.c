#include "image_io.h"
#include "net.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static int64_t now_ms(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
        return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

int image_timeout_parse(const char *text, uint32_t *timeout_ms)
{
    char *end;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno || end == text || *end || value < 100 || value > 600000) {
        errno = EINVAL;
        return -1;
    }
    *timeout_ms = (uint32_t)value;
    return 0;
}

int64_t image_deadline(uint32_t timeout_ms)
{
    int64_t now = now_ms();
    return now < 0 ? -1 : now + timeout_ms;
}

int image_nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int time_left(int64_t deadline)
{
    int64_t now = now_ms();
    if (now < 0)
        return -1;
    if (now >= deadline) {
        errno = ETIMEDOUT;
        return -1;
    }
    int64_t remaining = deadline - now;
    return remaining > INT_MAX ? INT_MAX : (int)remaining;
}

static int wait_ready(int fd, short events, int64_t deadline)
{
    for (;;) {
        int remaining = time_left(deadline);
        if (remaining < 0)
            return -1;
        struct pollfd item = { .fd = fd, .events = events, .revents = 0 };
        int result = poll(&item, 1, remaining);
        if (result < 0 && errno == EINTR)
            continue;
        if (result < 0)
            return -1;
        if (result == 0) {
            errno = ETIMEDOUT;
            return -1;
        }
        if (item.revents & POLLNVAL) {
            errno = EBADF;
            return -1;
        }
        /* HUP/ERR must also reach recv/send/getsockopt to obtain EOF/errno. */
        return 0;
    }
}

int image_connect(const char *ip, const char *port, int64_t deadline)
{
    struct sockaddr_in address;
    if (endpoint(ip, port, &address) < 0)
        return -1;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;
    if (image_nonblocking(fd) < 0)
        goto fail;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        if (errno != EINPROGRESS || wait_ready(fd, POLLOUT, deadline) < 0)
            goto fail;
        int error = 0;
        socklen_t length = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) < 0)
            goto fail;
        if (error) {
            errno = error;
            goto fail;
        }
    }
    return fd;
fail: {
    int saved = errno;
    close(fd);
    errno = saved;
    return -1;
}
}

int image_send_all(int fd, const void *data, size_t length, int64_t deadline)
{
    const unsigned char *bytes = data;
    size_t done = 0;
    while (done < length) {
        if (time_left(deadline) < 0)
            return -1;
        ssize_t count = send(fd, bytes + done, length - done, MSG_NOSIGNAL);
        if (count < 0 && errno == EINTR)
            continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (wait_ready(fd, POLLOUT, deadline) < 0)
                return -1;
            continue;
        }
        if (count <= 0) {
            if (count == 0)
                errno = EPIPE;
            return -1;
        }
        done += (size_t)count;
    }
    return 0;
}

int image_recv_exact(int fd, void *data, size_t length, int64_t deadline)
{
    unsigned char *bytes = data;
    size_t done = 0;
    while (done < length) {
        if (time_left(deadline) < 0)
            return -1;
        ssize_t count = recv(fd, bytes + done, length - done, 0);
        if (count < 0 && errno == EINTR)
            continue;
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (wait_ready(fd, POLLIN, deadline) < 0)
                return -1;
            continue;
        }
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
