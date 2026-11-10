#include "image_io.h"
#include "image_protocol.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

/* First pass: compute size and CRC without loading the entire file. */
static int inspect_file(FILE *file, uint32_t *size, uint32_t *crc)
{
    struct stat info;
    if (fstat(fileno(file), &info) < 0)
        return -1;
    if (!S_ISREG(info.st_mode) || info.st_size <= 0 || info.st_size > IMAGE_MAX_SIZE) {
        errno = EINVAL;
        return -1;
    }
    *size = (uint32_t)info.st_size;
    uint32_t state = IMAGE_CRC_INITIAL;
    uint32_t total = 0;
    unsigned char buffer[IMAGE_CHUNK_SIZE];
    size_t count;
    while ((count = fread(buffer, 1, sizeof(buffer), file)) != 0) {
        if (count > IMAGE_MAX_SIZE - total) {
            errno = EFBIG;
            return -1;
        }
        total += (uint32_t)count;
        state = image_crc_update(state, buffer, count);
    }
    if (ferror(file) || total != *size) {
        errno = EIO;
        return -1;
    }
    if (fseek(file, 0, SEEK_SET) < 0)
        return -1;
    *crc = image_crc_finish(state);
    return 0;
}

static int transmit(int fd, FILE *file, uint32_t size, uint32_t crc, int64_t deadline)
{
    unsigned char header[IMAGE_HEADER_SIZE];
    image_header_encode(header, size, crc);
    if (image_send_all(fd, header, sizeof(header), deadline) < 0)
        return -1;
    uint32_t remaining = size;
    unsigned char buffer[IMAGE_CHUNK_SIZE];
    while (remaining) {
        size_t wanted = remaining < sizeof(buffer) ? remaining : sizeof(buffer);
        size_t count = fread(buffer, 1, wanted, file);
        if (count != wanted) {
            errno = EIO;
            return -1;
        }
        if (image_send_all(fd, buffer, count, deadline) < 0)
            return -1;
        remaining -= (uint32_t)count;
    }
    /* EOF proves there is exactly one body. The read half remains open for ACK. */
    if (shutdown(fd, SHUT_WR) < 0)
        return -1;
    unsigned char ack[IMAGE_ACK_SIZE];
    int result = image_recv_exact(fd, ack, sizeof(ack), deadline);
    if (result != 1) {
        if (result == 0)
            errno = EPROTO;
        return -1;
    }
    uint32_t status, received, actual_crc;
    if (image_ack_decode(ack, &status, &received, &actual_crc) < 0)
        return -1;
    if (status != IMAGE_OK) {
        fprintf(stderr, "Receiver rejected image: status=%" PRIu32 "\n", status);
        errno = EPROTO;
        return -1;
    }
    if (received != size || actual_crc != crc) {
        errno = EPROTO;
        return -1;
    }
    printf("Image ACK: bytes=%" PRIu32 " crc32=%08" PRIx32 "\n", size, crc);
    return 0;
}

int main(int argc, char **argv)
{
    uint32_t timeout = IMAGE_TIMEOUT_MS;
    if ((argc != 4 && argc != 5) ||
        (argc == 5 && image_timeout_parse(argv[4], &timeout) < 0)) {
        fprintf(stderr, "Usage: %s RECEIVER_IPV4 PORT INPUT_FILE [TIMEOUT_MS]\n", argv[0]);
        return 1;
    }
    FILE *file = fopen(argv[3], "rb");
    if (!file) {
        perror("open input");
        return 1;
    }
    uint32_t size, crc;
    if (inspect_file(file, &size, &crc) < 0) {
        perror("inspect input (regular file, 1 byte..10 MiB required)");
        fclose(file);
        return 1;
    }
    int64_t deadline = image_deadline(timeout);
    int fd = deadline < 0 ? -1 : image_connect(argv[1], argv[2], deadline);
    int result = -1;
    if (fd >= 0)
        result = transmit(fd, file, size, crc, deadline);
    if (result < 0)
        perror("image transfer");
    if (fd >= 0)
        close(fd);
    if (fclose(file) != 0) {
        perror("close input");
        return 1;
    }
    return result < 0 ? 1 : 0;
}
