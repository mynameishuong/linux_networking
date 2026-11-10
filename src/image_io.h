#ifndef ELN_IMAGE_IO_H
#define ELN_IMAGE_IO_H

#include <stddef.h>
#include <stdint.h>

int image_timeout_parse(const char *text, uint32_t *timeout_ms);
int64_t image_deadline(uint32_t timeout_ms);
int image_nonblocking(int fd);
int image_connect(const char *ip, const char *port, int64_t deadline);
int image_send_all(int fd, const void *data, size_t length, int64_t deadline);
/* 1: all bytes; 0: EOF before any byte; -1: error or truncated read. */
int image_recv_exact(int fd, void *data, size_t length, int64_t deadline);

#endif
