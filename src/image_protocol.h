#ifndef ELN_IMAGE_PROTOCOL_H
#define ELN_IMAGE_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define IMAGE_MAX_SIZE (10u * 1024u * 1024u)
#define IMAGE_CHUNK_SIZE 4096u
#define IMAGE_HEADER_SIZE 12u
#define IMAGE_ACK_SIZE 16u
#define IMAGE_CRC_INITIAL UINT32_C(0xffffffff)
#define IMAGE_TIMEOUT_MS 30000u

enum image_status {
    IMAGE_OK = 0,
    IMAGE_BAD_HEADER = 1,
    IMAGE_BAD_CRC = 2,
    IMAGE_FILE_ERROR = 3,
    IMAGE_BAD_STREAM = 4
};

/* CRC-32/ISO-HDLC: reflected polynomial 0xedb88320; finalize with XOR. */
uint32_t image_crc_update(uint32_t state, const void *data, size_t length);
uint32_t image_crc_finish(uint32_t state);
void image_header_encode(unsigned char header[IMAGE_HEADER_SIZE], uint32_t size, uint32_t crc);
int image_header_decode(const unsigned char header[IMAGE_HEADER_SIZE], uint32_t *size, uint32_t *crc);
void image_ack_encode(unsigned char ack[IMAGE_ACK_SIZE], uint32_t status, uint32_t size, uint32_t crc);
int image_ack_decode(const unsigned char ack[IMAGE_ACK_SIZE], uint32_t *status, uint32_t *size, uint32_t *crc);

#endif
