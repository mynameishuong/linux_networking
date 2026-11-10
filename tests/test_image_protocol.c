#include "image_protocol.h"

#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    /* Published CRC-32 check vector, independent of our encoder. */
    const char text[] = "123456789";
    uint32_t crc = image_crc_finish(image_crc_update(IMAGE_CRC_INITIAL, text, 9));
    assert(crc == UINT32_C(0xcbf43926));
    assert(image_crc_finish(IMAGE_CRC_INITIAL) == 0);
    uint32_t split = image_crc_update(IMAGE_CRC_INITIAL, text, 4);
    split = image_crc_update(split, text + 4, 5);
    assert(image_crc_finish(split) == crc);

    unsigned char header[IMAGE_HEADER_SIZE];
    const unsigned char golden[] = {
        0x49, 0x4d, 0x47, 0x31, 0, 0, 0, 9, 0xcb, 0xf4, 0x39, 0x26
    };
    image_header_encode(header, 9, crc);
    assert(memcmp(header, golden, sizeof(golden)) == 0);
    uint32_t size, decoded_crc, status;
    assert(image_header_decode(golden, &size, &decoded_crc) == 0);
    assert(size == 9 && decoded_crc == crc);

    header[0] = 'X';
    assert(image_header_decode(header, &size, &decoded_crc) == -1 && errno == EPROTO);
    image_header_encode(header, 0, crc);
    assert(image_header_decode(header, &size, &decoded_crc) == -1 && errno == EMSGSIZE);
    image_header_encode(header, IMAGE_MAX_SIZE + 1u, crc);
    assert(image_header_decode(header, &size, &decoded_crc) == -1);
    image_header_encode(header, IMAGE_MAX_SIZE, crc);
    assert(image_header_decode(header, &size, &decoded_crc) == 0 && size == IMAGE_MAX_SIZE);

    unsigned char ack[IMAGE_ACK_SIZE];
    const unsigned char ack_golden[] = {
        'A', 'C', 'K', '1', 0, 0, 0, 0, 0, 0, 0, 9, 0xcb, 0xf4, 0x39, 0x26
    };
    image_ack_encode(ack, IMAGE_OK, 9, crc);
    assert(memcmp(ack, ack_golden, sizeof(ack)) == 0);
    assert(image_ack_decode(ack_golden, &status, &size, &decoded_crc) == 0);
    assert(status == IMAGE_OK && size == 9 && decoded_crc == crc);
    ack[0] = 'X';
    assert(image_ack_decode(ack, &status, &size, &decoded_crc) == -1);
    image_ack_encode(ack, IMAGE_BAD_STREAM + 1u, 0, 0);
    assert(image_ack_decode(ack, &status, &size, &decoded_crc) == -1);
    image_ack_encode(ack, IMAGE_OK, IMAGE_MAX_SIZE + 1u, 0);
    assert(image_ack_decode(ack, &status, &size, &decoded_crc) == -1);

    puts("Image protocol: CRC vector, chunked CRC, wire vectors and invalid headers/ACKs: OK");
    return 0;
}
