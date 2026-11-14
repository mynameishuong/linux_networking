#ifndef ELN_V3_FAN_HAL_H
#define ELN_V3_FAN_HAL_H
#include <stdint.h>

/* Simulator port. A hardware port can replace this implementation, not network handlers. */
typedef struct {
    uint16_t applied;
    uint16_t actual_rpm;
    unsigned fault; /* Bit 0: driver failure; bit 1: stalled simulated plant. */
} v3_fan_hal;
void v3_fan_apply(v3_fan_hal *hal, uint16_t target_raw);
#endif
