#ifndef ELN_V3_CORE_H
#define ELN_V3_CORE_H
#include <stddef.h>
#include <stdint.h>
#ifdef _WIN32
#define V3_API __declspec(dllexport)
#else
#define V3_API
#endif

typedef struct v3_ecu v3_ecu;
enum v3_owner { V3_FALLBACK, V3_THERMAL, V3_SERVICE, V3_DIAGNOSTIC };
enum v3_routine { V3_IDLE, V3_RUNNING, V3_COMPLETED, V3_ABORTED, V3_FAILED };
enum v3_reason { V3_NONE, V3_STOP, V3_MOVING, V3_STALE, V3_SESSION,
                 V3_DISCONNECT, V3_DRIVER_FAULT, V3_FEEDBACK };

V3_API v3_ecu *v3_create(int test_enable, uint32_t s3_ms, uint32_t stale_ms, uint64_t now);
V3_API void v3_destroy(v3_ecu *ecu);
/* Signal result: 0 accepted, 1 duplicate, 2 reordered, 3 malformed/range. */
V3_API int v3_signal(v3_ecu *ecu, const uint8_t *payload, size_t size, uint64_t now);
V3_API void v3_tick(v3_ecu *ecu, uint64_t now);
V3_API void v3_fault(v3_ecu *ecu, unsigned flags);
V3_API int v3_diag_open(v3_ecu *ecu, uint32_t connection, uint64_t now);
V3_API void v3_diag_close(v3_ecu *ecu, uint32_t connection);
V3_API int v3_session(v3_ecu *ecu, uint32_t connection);
/* Response length, 0 for suppressed positive response, -1 for invalid API arguments. */
V3_API int v3_uds(v3_ecu *ecu, uint32_t connection, const uint8_t *request, size_t size,
                  uint8_t *response, size_t capacity, uint64_t now);
/* Result: 0 accepted, 1 busy diagnostic, 2 invalid, 3 fault. */
V3_API int v3_command(v3_ecu *ecu, uint64_t client, uint32_t id, uint16_t duty,
                      uint16_t lease_ms, uint64_t now);
V3_API void v3_fan_status(v3_ecu *ecu, uint8_t output[16]);
V3_API void v3_telemetry(v3_ecu *ecu, uint32_t boot_id, uint8_t output[20], uint64_t now);
V3_API void v3_debug(v3_ecu *ecu, int64_t output[18], uint64_t now);
#endif
