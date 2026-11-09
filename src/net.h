#ifndef ELN_NET_H
#define ELN_NET_H

#include <stddef.h>
#include <netinet/in.h>

#define FRAME_MAX 1024u

int endpoint(const char *ip, const char *port, struct sockaddr_in *address);
int socket_timeout(int fd);
int send_frame(int fd, const void *payload, size_t length);
/* 1: complete frame; 0: clean EOF; -1: error (errno explains why). */
int recv_frame(int fd, void *payload, size_t *length);

#endif
