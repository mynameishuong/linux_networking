#include "image_io.h"
#include "image_protocol.h"
#include "net.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

static int receive_image(int fd, const char *output, int64_t deadline)
{
    uint32_t status = IMAGE_BAD_HEADER, size = 0, expected_crc = 0;
    uint32_t written = 0, actual_crc = 0, state = IMAGE_CRC_INITIAL;
    unsigned char header[IMAGE_HEADER_SIZE];
    FILE *file = NULL;
    char *temporary = NULL;
    int published = 0;

    int result = image_recv_exact(fd, header, sizeof(header), deadline);
    if (result != 1 || image_header_decode(header, &size, &expected_crc) < 0) {
        if (result == 0)
            errno = EPROTO;
        goto cleanup;
    }
    status = IMAGE_FILE_ERROR;
    const char suffix[] = ".part.XXXXXX";
    size_t path_size = strlen(output) + sizeof(suffix);
    temporary = malloc(path_size);
    if (!temporary)
        goto cleanup;
    (void)snprintf(temporary, path_size, "%s%s", output, suffix);
    int temp_fd = mkstemp(temporary);
    if (temp_fd < 0) {
        /* Do not unlink a path we did not create. */
        free(temporary);
        temporary = NULL;
        goto cleanup;
    }
    file = fdopen(temp_fd, "wb");
    if (!file) {
        int saved = errno;
        close(temp_fd);
        errno = saved;
        goto cleanup;
    }
    unsigned char buffer[IMAGE_CHUNK_SIZE];
    while (written < size) {
        uint32_t left = size - written;
        size_t wanted = left < sizeof(buffer) ? left : sizeof(buffer);
        status = IMAGE_BAD_STREAM;
        result = image_recv_exact(fd, buffer, wanted, deadline);
        if (result != 1) {
            if (result == 0)
                errno = EPROTO;
            goto cleanup;
        }
        status = IMAGE_FILE_ERROR;
        if (fwrite(buffer, 1, wanted, file) != wanted) {
            errno = EIO;
            goto cleanup;
        }
        written += (uint32_t)wanted;
        state = image_crc_update(state, buffer, wanted);
    }
    status = IMAGE_BAD_STREAM;
    unsigned char extra;
    result = image_recv_exact(fd, &extra, 1, deadline);
    if (result != 0) {
        if (result == 1)
            errno = EPROTO;
        goto cleanup;
    }
    actual_crc = image_crc_finish(state);
    status = IMAGE_BAD_CRC;
    if (actual_crc != expected_crc) {
        errno = EBADMSG;
        goto cleanup;
    }
    status = IMAGE_FILE_ERROR;
    if (fflush(file) != 0 || fsync(fileno(file)) < 0)
        goto cleanup;
    result = fclose(file);
    file = NULL;
    if (result != 0)
        goto cleanup;
    /* Atomic publication without replacing an existing destination. */
    if (link(temporary, output) < 0)
        goto cleanup;
    published = 1;
    status = IMAGE_OK;

cleanup: {
    int saved = errno;
    if (file)
        fclose(file);
    if (temporary) {
        if (unlink(temporary) < 0)
            perror("remove temporary file");
        free(temporary);
    }
    if (!published) {
        errno = saved;
        perror("receive image");
    }
    unsigned char ack[IMAGE_ACK_SIZE];
    image_ack_encode(ack, status, written, actual_crc);
    /* On timeout/disconnect this NACK may not reach the sender. */
    int sent = image_send_all(fd, ack, sizeof(ack), deadline);
    if (published)
        printf("Saved %s: bytes=%" PRIu32 " crc32=%08" PRIx32 "\n", output, size, actual_crc);
    if (sent < 0)
        perror("send image ACK (saved file may already exist)");
    return status == IMAGE_OK && sent == 0 ? 0 : -1;
}
}

int main(int argc, char **argv)
{
    uint32_t timeout = IMAGE_TIMEOUT_MS;
    struct sockaddr_in address;
    if ((argc != 4 && argc != 5) ||
        endpoint(argv[1], argv[2], &address) < 0 ||
        (argc == 5 && image_timeout_parse(argv[4], &timeout) < 0)) {
        fprintf(stderr, "Usage: %s BIND_IPV4 PORT OUTPUT_FILE [TIMEOUT_MS]\n", argv[0]);
        return 1;
    }
    struct stat info;
    if (lstat(argv[3], &info) == 0 || errno != ENOENT) {
        fprintf(stderr, "Output must be a new file; inspect destination: %s\n", argv[3]);
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
        listen(listener, 1) < 0) {
        perror("listen setup");
        close(listener);
        return 1;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("Image receiver listening on %s:%s (one image, then exit)\n", argv[1], argv[2]);
    int client;
    do {
        client = accept(listener, NULL, NULL);
    } while (client < 0 && errno == EINTR);
    close(listener);
    if (client < 0) {
        perror("accept");
        return 1;
    }
    int64_t deadline = image_deadline(timeout);
    int result = -1;
    if (deadline < 0 || image_nonblocking(client) < 0)
        perror("client setup");
    else
        result = receive_image(client, argv[3], deadline);
    close(client);
    return result < 0 ? 1 : 0;
}
