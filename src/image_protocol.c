#include "image_protocol.h"

#include <errno.h>
#include <string.h>

static void put_u32(unsigned char *bytes, uint32_t value)
{
    bytes[0] = (unsigned char)(value >> 24);
    bytes[1] = (unsigned char)(value >> 16);
    bytes[2] = (unsigned char)(value >> 8);
    bytes[3] = (unsigned char)value;
}

static uint32_t get_u32(const unsigned char *bytes)
{
    return ((uint32_t)bytes[0] << 24) | ((uint32_t)bytes[1] << 16) |
           ((uint32_t)bytes[2] << 8) | (uint32_t)bytes[3];
}

uint32_t image_crc_update(uint32_t state, const void *data, size_t length)
{
    const unsigned char *bytes = data;
    for (size_t i = 0; i < length; ++i) {
        state ^= bytes[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            state = (state >> 1) ^ ((state & 1u) ? UINT32_C(0xedb88320) : 0u);
    }
    return state;
}

uint32_t image_crc_finish(uint32_t state)
{
    return state ^ IMAGE_CRC_INITIAL;
}

void image_header_encode(unsigned char header[IMAGE_HEADER_SIZE], uint32_t size, uint32_t crc)
{
    memcpy(header, "IMG1", 4);
    put_u32(header + 4, size);
    put_u32(header + 8, crc);
}

int image_header_decode(const unsigned char header[IMAGE_HEADER_SIZE], uint32_t *size, uint32_t *crc)
{
    if (memcmp(header, "IMG1", 4) != 0) {
        errno = EPROTO;
        return -1;
    }
    *size = get_u32(header + 4);
    *crc = get_u32(header + 8);
    if (*size == 0 || *size > IMAGE_MAX_SIZE) {
        errno = EMSGSIZE;
        return -1;
    }
    return 0;
}

void image_ack_encode(unsigned char ack[IMAGE_ACK_SIZE], uint32_t status, uint32_t size, uint32_t crc)
{
    memcpy(ack, "ACK1", 4);
    put_u32(ack + 4, status);
    put_u32(ack + 8, size);
    put_u32(ack + 12, crc);
}

int image_ack_decode(const unsigned char ack[IMAGE_ACK_SIZE], uint32_t *status, uint32_t *size, uint32_t *crc)
{
    if (memcmp(ack, "ACK1", 4) != 0) {
        errno = EPROTO;
        return -1;
    }
    *status = get_u32(ack + 4);
    *size = get_u32(ack + 8);
    *crc = get_u32(ack + 12);
    if (*status > IMAGE_BAD_STREAM || *size > IMAGE_MAX_SIZE) {
        errno = EPROTO;
        return -1;
    }
    return 0;
}
