#include "ecu_core.h"
#include "fan_hal.h"
#include <stdlib.h>
#include <string.h>

#define SESSIONS 8u
#define CACHE_SIZE 16u
typedef struct { uint32_t id; uint8_t mode; uint64_t activity; } session;
typedef struct {
    int used; uint64_t client, at; uint32_t id; uint16_t duty, lease; uint8_t result;
} command_cache;

struct v3_ecu {
    int test_enable, seen, valid, thermal_on;
    unsigned fault;
    uint32_t s3_ms, stale_ms, samples, sequence, last_command;
    uint64_t created, received;
    int32_t temperature;
    uint16_t speed, target, applied, rpm;
    uint8_t counter, owner, routine, reason;
    uint32_t routine_connection;
    uint16_t routine_duty, routine_duration, result_rpm;
    uint64_t started, ends, good_since;
    int has_started, has_good;
    uint16_t service_duty;
    uint64_t service_until;
    session sessions[SESSIONS];
    command_cache cache[CACHE_SIZE];
    unsigned next_cache;
    v3_fan_hal actuator;
};

static uint64_t age(uint64_t now, uint64_t then) { return now >= then ? now - then : 0; }
static uint16_t be16(const uint8_t *p) { return (uint16_t)(((uint16_t)p[0] << 8) | p[1]); }
static void put16(uint8_t *p, uint16_t n) { p[0] = (uint8_t)(n >> 8); p[1] = (uint8_t)n; }
static void put32(uint8_t *p, uint32_t n) {
    p[0] = (uint8_t)(n >> 24); p[1] = (uint8_t)(n >> 16);
    p[2] = (uint8_t)(n >> 8); p[3] = (uint8_t)n;
}
static int fresh(const v3_ecu *e, uint64_t now) {
    return e->seen && e->valid && age(now, e->received) < e->stale_ms;
}
static unsigned quality(const v3_ecu *e, uint64_t now) {
    if (!e->seen || !e->valid) return 1;
    return age(now, e->received) >= e->stale_ms ? 2u : 0u;
}
static session *find_session(v3_ecu *e, uint32_t id) {
    for (unsigned i = 0; i < SESSIONS; ++i)
        if (e->sessions[i].id == id && id) return &e->sessions[i];
    return NULL;
}
static void finish(v3_ecu *e, uint8_t state, uint8_t reason) {
    e->routine = state; e->reason = reason; e->result_rpm = e->rpm;
}
static void expire_sessions(v3_ecu *e, uint64_t now) {
    for (unsigned i = 0; i < SESSIONS; ++i) {
        session *s = &e->sessions[i];
        if (s->id && s->mode == 3 && age(now, s->activity) >= e->s3_ms) {
            s->mode = 1;
            if (e->routine == V3_RUNNING && e->routine_connection == s->id)
                finish(e, V3_ABORTED, V3_SESSION);
        }
    }
}

static void apply_actuator(v3_ecu *e) {
    e->actuator.fault = e->fault;
    v3_fan_apply(&e->actuator, e->target);
    e->applied = e->actuator.applied;
    e->rpm = e->actuator.actual_rpm;
}

v3_ecu *v3_create(int test_enable, uint32_t s3_ms, uint32_t stale_ms, uint64_t now) {
    if (!s3_ms || !stale_ms) return NULL;
    v3_ecu *e = calloc(1, sizeof(*e));
    if (e) {
        e->test_enable = !!test_enable; e->s3_ms = s3_ms;
        e->stale_ms = stale_ms; e->created = now;
    }
    return e;
}
void v3_destroy(v3_ecu *e) { free(e); }

int v3_signal(v3_ecu *e, const uint8_t *p, size_t size, uint64_t now) {
    if (!e || !p || size != 8 || (p[1] & 0xf0u) || (p[5] & 0xfeu) || p[6] || p[7]) return 3;
    uint16_t raw = (uint16_t)(p[0] | ((uint16_t)(p[1] & 15u) << 8));
    uint16_t speed = (uint16_t)(p[2] | ((uint16_t)p[3] << 8));
    if ((raw != 4095 && raw > 1900) || (speed != 65535 && speed > 30000)) return 3;
    if (e->seen && age(now, e->received) < e->stale_ms) {
        uint8_t delta = (uint8_t)(p[4] - e->counter);
        if (!delta) return 1;
        if (delta >= 128) return 2;
    }
    e->seen = 1; e->received = now; e->counter = p[4]; ++e->samples;
    e->valid = (p[5] & 1u) && raw != 4095 && speed != 65535;
    e->temperature = raw == 4095 ? 0 : (int32_t)raw * 10 - 4000;
    e->speed = speed;
    if (e->valid) {
        if (e->temperature >= 6000) e->thermal_on = 1;
        else if (e->temperature <= 5500) e->thermal_on = 0;
    }
    return 0;
}

int v3_diag_open(v3_ecu *e, uint32_t id, uint64_t now) {
    if (!e || !id || find_session(e, id)) return -1;
    for (unsigned i = 0; i < SESSIONS; ++i) {
        if (!e->sessions[i].id) {
            e->sessions[i] = (session){id, 1, now};
            return 0;
        }
    }
    return -1;
}
void v3_diag_close(v3_ecu *e, uint32_t id) {
    session *s = find_session(e, id);
    if (s) memset(s, 0, sizeof(*s));
    if (e->routine == V3_RUNNING && e->routine_connection == id)
        finish(e, V3_ABORTED, V3_DISCONNECT);
}
int v3_session(v3_ecu *e, uint32_t id) {
    session *s = find_session(e, id);
    return s ? s->mode : 0;
}
void v3_fault(v3_ecu *e, unsigned flags) { e->fault = flags & 3u; }

static int negative(uint8_t *out, uint8_t sid, uint8_t code) {
    out[0] = 0x7f; out[1] = sid; out[2] = code; return 3;
}
int v3_uds(v3_ecu *e, uint32_t id, const uint8_t *p, size_t n,
           uint8_t *out, size_t capacity, uint64_t now) {
    if (!e || !p || !n || !out || capacity < 32) return -1;
    expire_sessions(e, now);
    session *s = find_session(e, id);
    uint8_t sid = p[0];
    if (!s) return negative(out, sid, 0x22);
    s->activity = now;
    if (sid == 0x10 || sid == 0x3e) {
        if (n != 2) return negative(out, sid, 0x13);
        uint8_t sub = p[1] & 0x7fu;
        if (sid == 0x3e) {
            if (sub) return negative(out, sid, 0x12);
            out[0] = 0x7e; out[1] = 0;
            return (p[1] & 0x80u) ? 0 : 2;
        }
        if (sub != 1 && sub != 3) return negative(out, sid, 0x12);
        if (sub == 1 && e->routine == V3_RUNNING && e->routine_connection == id)
            finish(e, V3_ABORTED, V3_SESSION);
        s->mode = sub;
        out[0] = 0x50; out[1] = sub;
        put16(out + 2, 50); put16(out + 4, 500); /* P2 ms; P2* units 10 ms. */
        return (p[1] & 0x80u) ? 0 : 6;
    }
    if (sid == 0x22) {
        if (n != 3) return negative(out, sid, 0x13); /* One DID per request in this lab. */
        uint16_t did = be16(p + 1);
        out[0] = 0x62; out[1] = p[1]; out[2] = p[2];
        if (did == 0xf200) {
            put16(out + 3, (uint16_t)e->temperature);
            out[5] = (uint8_t)quality(e, now);
            uint64_t ms = e->seen ? age(now, e->received) : 65535;
            put16(out + 6, (uint16_t)(ms > 65535 ? 65535 : ms));
            return 8;
        }
        if (did == 0xf201) { v3_fan_status(e, out + 3); return 19; }
        return negative(out, sid, 0x31);
    }
    if (sid != 0x31) return negative(out, sid, 0x11);
    if (n < 4) return negative(out, sid, 0x13);
    uint8_t sub = p[1] & 0x7fu;
    if (sub < 1 || sub > 3) return negative(out, sid, 0x12);
    if (be16(p + 2) != 0xf001) return negative(out, sid, 0x31);
    if (n != (sub == 1 ? 8u : 4u)) return negative(out, sid, 0x13);
    if (sub == 1) {
        if (s->mode != 3) return negative(out, sid, 0x7e);
        uint16_t duty = be16(p + 4), duration = be16(p + 6);
        if (duty > 10000 || duration < 100 || duration > 2000) return negative(out, sid, 0x31);
        if (!e->test_enable || !fresh(e, now) || e->speed || e->fault & 1u)
            return negative(out, sid, 0x22);
        if (e->routine == V3_RUNNING) return negative(out, sid, 0x24);
        e->routine = V3_RUNNING; e->reason = V3_NONE; e->routine_connection = id;
        e->routine_duty = duty; e->routine_duration = duration;
        e->has_started = 0; e->has_good = 0; e->result_rpm = 0;
        e->service_until = 0; /* A previous normal service command must not resume later. */
    } else if (sub == 2) {
        if (e->routine != V3_RUNNING || e->routine_connection != id)
            return negative(out, sid, 0x24);
        finish(e, V3_ABORTED, V3_STOP);
    } else if (e->routine == V3_IDLE) return negative(out, sid, 0x24);
    out[0] = 0x71; out[1] = sub; out[2] = 0xf0; out[3] = 1; out[4] = e->routine;
    if (sub == 3) put16(out + 5, e->routine == V3_RUNNING ? e->rpm : e->result_rpm);
    return (p[1] & 0x80u) ? 0 : (sub == 3 ? 7 : 5);
}

int v3_command(v3_ecu *e, uint64_t client, uint32_t id, uint16_t duty,
               uint16_t lease, uint64_t now) {
    expire_sessions(e, now);
    for (unsigned i = 0; i < CACHE_SIZE; ++i) {
        command_cache *c = &e->cache[i];
        if (c->used && c->client == client && c->id == id && age(now, c->at) < 10000)
            return c->duty == duty && c->lease == lease ? c->result : 2;
    }
    int result = 0;
    if (!client || !id || duty > 10000 || lease < 100 || lease > 2000) result = 2;
    else if (e->fault & 1u) result = 3;
    else if (e->routine == V3_RUNNING) result = 1;
    if (!result) {
        e->service_duty = duty; e->service_until = now + lease; e->last_command = id;
    }
    e->cache[e->next_cache++ % CACHE_SIZE] = (command_cache){1, client, now, id, duty, lease, (uint8_t)result};
    return result;
}

/* Only this function owns the actuator output. Network handlers never write PWM. */
void v3_tick(v3_ecu *e, uint64_t now) {
    expire_sessions(e, now);
    if (e->routine == V3_RUNNING) {
        if (e->fault & 1u) finish(e, V3_FAILED, V3_DRIVER_FAULT);
        else if (!fresh(e, now)) finish(e, V3_ABORTED, V3_STALE);
        else if (e->speed) finish(e, V3_ABORTED, V3_MOVING);
    }
    e->target = 0; e->owner = V3_FALLBACK;
    if (!(e->fault & 1u)) {
        if (e->routine == V3_RUNNING) { e->owner = V3_DIAGNOSTIC; e->target = e->routine_duty; }
        else if (now < e->service_until) { e->owner = V3_SERVICE; e->target = e->service_duty; }
        else if (fresh(e, now)) { e->owner = V3_THERMAL; e->target = e->thermal_on ? 4000 : 0; }
    }
    apply_actuator(e);
    if (e->routine == V3_RUNNING) {
        if (!e->has_started) {
            e->has_started = 1; e->started = now; e->ends = now + e->routine_duration;
        }
        uint16_t expected = (uint16_t)(e->routine_duty / 2u);
        unsigned difference = e->rpm > expected ? e->rpm - expected : expected - e->rpm;
        if (difference <= expected / 10u) {
            if (!e->has_good) { e->has_good = 1; e->good_since = now; }
        } else e->has_good = 0;
        if (now >= e->ends) {
            finish(e, e->has_good && age(now, e->good_since) >= 100 ? V3_COMPLETED : V3_FAILED,
                   e->has_good && age(now, e->good_since) >= 100 ? V3_NONE : V3_FEEDBACK);
            /* Release in this same control tick, retaining the terminal RPM snapshot. */
            e->owner = fresh(e, now) ? V3_THERMAL : V3_FALLBACK;
            e->target = e->owner == V3_THERMAL && e->thermal_on ? 4000 : 0;
            apply_actuator(e);
        }
    }
    ++e->sequence;
}

void v3_fan_status(v3_ecu *e, uint8_t out[16]) {
    put32(out, e->sequence); put32(out + 4, e->last_command);
    put16(out + 8, e->target); put16(out + 10, e->applied); put16(out + 12, e->rpm);
    out[14] = e->owner; out[15] = e->routine;
}
void v3_telemetry(v3_ecu *e, uint32_t boot_id, uint8_t out[20], uint64_t now) {
    put32(out, boot_id); put32(out + 4, e->samples); put32(out + 8, (uint32_t)age(now, e->created));
    put16(out + 12, (uint16_t)e->temperature); put16(out + 14, e->rpm);
    put16(out + 16, (uint16_t)((fresh(e, now) ? 1u : 0u) | ((e->fault & 1u) ? 0u : 2u)));
    put16(out + 18, 0);
}
void v3_debug(v3_ecu *e, int64_t out[18], uint64_t now) {
    out[0] = e->temperature; out[1] = e->speed; out[2] = quality(e, now);
    out[3] = e->seen ? (int64_t)age(now, e->received) : 65535;
    out[4] = e->target; out[5] = e->applied; out[6] = e->rpm; out[7] = e->owner;
    out[8] = e->routine; out[9] = e->result_rpm; out[10] = e->reason;
    out[11] = e->counter; out[12] = e->samples; out[13] = e->fault;
    out[14] = e->sequence; out[15] = e->last_command; out[16] = e->test_enable;
    out[17] = e->routine_connection;
}
