#include "fan_hal.h"

void v3_fan_apply(v3_fan_hal *hal, uint16_t target_raw) {
    if (target_raw > 10000) target_raw = 10000;
    hal->applied = hal->fault & 1u ? 0 : target_raw;
    hal->actual_rpm = hal->fault ? 0 : (uint16_t)(hal->applied / 2u);
}
