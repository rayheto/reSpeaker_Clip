/*
 * Copyright (c) 2025 Seeed Technology Co., Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <string.h>
#include "rtc_stream.h"
#include "audio.h"
#include "ble.h"
#include "transport_ble.h"
#include "clip_event.h"

LOG_MODULE_REGISTER(rtc_stream, CONFIG_CLIP_LOG_LEVEL);

/* One queue slot: a single encoded Opus frame (20 ms). */
struct rtc_frame {
    uint16_t len;
    uint8_t data[CONFIG_CLIP_RTC_FRAME_MAX_BYTES];
};

K_MSGQ_DEFINE(rtc_frame_msgq, sizeof(struct rtc_frame),
              CONFIG_CLIP_RTC_QUEUE_FRAMES, 4);

/* Producer staging slot. The audio thread is the only producer, so a single
 * static frame keeps the ~400 byte struct off its stack. */
static struct rtc_frame push_frame;

/* Wakes the consumer thread; count tracks queued frames. */
static K_SEM_DEFINE(frame_sem, 0, CONFIG_CLIP_RTC_QUEUE_FRAMES);

static atomic_t session_active;
static atomic_t stream_active;
static atomic_t stream_paused;

static char rtc_session_id[32];

static struct k_work_delayable start_timeout_work;
static struct k_work disconnect_work;

/* Session statistics (logged on session end) */
static uint32_t frames_pushed;
static uint32_t frames_dropped_queue;  /* queue full: oldest evicted / oversize */
static uint32_t frames_dropped_tx;     /* consumer side: no link or backpressure */
static uint32_t frames_consumed;       /* successfully notified STREAM_DATA frames */

/* ----------------------------------------------------------------------- */
/* DIAG: stall instrumentation (RAM ring + 1s stats + gap detector)        */
/*                                                                         */
/* The ring keeps the last ~256 sampled events for post-mortem; the 1 Hz   */
/* stat line and GAP detector answer "producer starvation vs consumer/TX   */
/* stall" live. All measurement is non-blocking.                           */
/* ----------------------------------------------------------------------- */

#define RTC_DIAG_RING_SIZE 256 /* entries; must be a power of two */

#define RTC_DIAG_EV_PUSH   0 /* sampled producer push (1 of 16) */
#define RTC_DIAG_EV_DROP_Q 1 /* queue full: oldest evicted */
#define RTC_DIAG_EV_POP    2 /* sampled consumer pop (1 of 16) */
#define RTC_DIAG_EV_TX_ERR 3 /* notify failed; qdepth holds min(250,-ret) */
#define RTC_DIAG_EV_GAP    4 /* consumer saw a >60 ms inter-frame gap */

struct rtc_diag_rec {
    uint32_t ts_ms;  /* k_uptime_get_32() at event */
    uint16_t seq;    /* frames_pushed at event time */
    uint8_t qdepth;  /* queue depth at event time */
    uint8_t ev;      /* RTC_DIAG_EV_* */
};

static struct rtc_diag_rec diag_ring[RTC_DIAG_RING_SIZE];
static atomic_t diag_head;

static void diag_record(uint8_t ev, uint16_t seq, uint8_t qdepth)
{
    uint32_t idx = (uint32_t)atomic_inc(&diag_head) & (RTC_DIAG_RING_SIZE - 1);
    struct rtc_diag_rec *rec = &diag_ring[idx];

    rec->ts_ms = k_uptime_get_32();
    rec->seq = seq;
    rec->qdepth = qdepth;
    rec->ev = ev;
}

/* 1 Hz stats window (stat_work) */
static struct k_work_delayable stat_work;
static uint32_t stat_pushed, stat_consumed, stat_drop_q, stat_drop_tx;
static uint32_t stat_tx_err, stat_tick;
static uint8_t win_q_min = 0xFF, win_q_max;
static uint32_t win_send_max_ms;

/* Consumer-side gap detection; last_pop_ts == 0 disables it (after flush) */
static uint32_t last_pop_ts;
static uint32_t last_pop_pushed;
static uint32_t last_pop_dtx;

static void diag_q_sample(uint8_t qd)
{
    if (qd < win_q_min) {
        win_q_min = qd;
    }
    if (qd > win_q_max) {
        win_q_max = qd;
    }
}

/* Thread */
K_THREAD_STACK_DEFINE(rtc_stream_stack, CONFIG_CLIP_RTC_STACK_SIZE);
static struct k_thread rtc_stream_thread_data;

/* ----------------------------------------------------------------------- */
/* Internal helpers                                                        */
/* ----------------------------------------------------------------------- */

static void rtc_stream_flush(void)
{
    k_msgq_purge(&rtc_frame_msgq);
    k_sem_reset(&frame_sem);
    last_pop_ts = 0; /* no gap detection across a flush */
}

/* Producer: runs in the audio recording thread (highest app priority).
 * Must be O(1) and never block. */
static void rtc_stream_push(const uint8_t *data, size_t len, void *user_data)
{
    ARG_UNUSED(user_data);

    if (!atomic_get(&session_active)) {
        return;
    }

    frames_pushed++;

    if (len > sizeof(push_frame.data)) {
        frames_dropped_queue++;
        return;
    }

    push_frame.len = (uint16_t)len;
    memcpy(push_frame.data, data, len);

    if (k_msgq_put(&rtc_frame_msgq, &push_frame, K_NO_WAIT) != 0) {
        /* Queue full — RTC semantics: drop the oldest frame. */
        struct rtc_frame discard;

        (void)k_msgq_get(&rtc_frame_msgq, &discard, K_NO_WAIT);
        if (k_msgq_put(&rtc_frame_msgq, &push_frame, K_NO_WAIT) != 0) {
            frames_dropped_queue++;
            diag_record(RTC_DIAG_EV_DROP_Q, (uint16_t)frames_pushed,
                        (uint8_t)k_msgq_num_used_get(&rtc_frame_msgq));
            return;
        }
        frames_dropped_queue++;
        diag_record(RTC_DIAG_EV_DROP_Q, (uint16_t)frames_pushed,
                    (uint8_t)k_msgq_num_used_get(&rtc_frame_msgq));
    }

    k_sem_give(&frame_sem);

    diag_q_sample((uint8_t)k_msgq_num_used_get(&rtc_frame_msgq));
    if ((frames_pushed & 0xF) == 0) {
        diag_record(RTC_DIAG_EV_PUSH, (uint16_t)frames_pushed,
                    (uint8_t)k_msgq_num_used_get(&rtc_frame_msgq));
    }
}

/* Consumer: drains the queue and emits STREAM_DATA over BLE. */
static void rtc_stream_thread_fn(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);
    struct rtc_frame frame;

    while (true) {
        k_sem_take(&frame_sem, K_FOREVER);

        if (k_msgq_get(&rtc_frame_msgq, &frame, K_NO_WAIT) != 0) {
            continue; /* stale wake after flush */
        }

        uint32_t now = k_uptime_get_32();
        uint8_t qd = (uint8_t)k_msgq_num_used_get(&rtc_frame_msgq);

        diag_q_sample(qd);

        if (last_pop_ts != 0) {
            uint32_t gap = now - last_pop_ts;

            if (gap > 60) {
                diag_record(RTC_DIAG_EV_GAP, (uint16_t)frames_pushed, qd);
                /* prod+0 during the gap => producer (audio thread)
                 * starvation; prod+N with q>0 => frames waited for the
                 * consumer/TX path. */
                LOG_WRN("RTC GAP %ums: prod+%u q=%u dtx+%u",
                        gap, frames_pushed - last_pop_pushed, qd,
                        frames_dropped_tx - last_pop_dtx);
            }
        }
        last_pop_ts = now;
        last_pop_pushed = frames_pushed;
        last_pop_dtx = frames_dropped_tx;

        if (!atomic_get(&stream_active) || atomic_get(&stream_paused)) {
            continue; /* consume & drop: stream not started or paused */
        }

        if (!ble_is_connected() || !ble_is_file_data_notify_enabled()) {
            frames_dropped_tx++;
            continue;
        }

        uint32_t t0 = k_uptime_get_32();
        int ret = transport_ble_send_stream_data(frame.data, frame.len);
        uint32_t dt = k_uptime_get_32() - t0;

        if (dt > win_send_max_ms) {
            win_send_max_ms = dt;
        }

        if (ret < 0) {
            /* -ENOMEM/-EAGAIN/-EBUSY: BLE TX backpressure.
             * -ENOTCONN/-EMSGSIZE: link gone / frame too large.
             * RTC never retries or blocks — drop and move on. */
            frames_dropped_tx++;
            stat_tx_err++;
            diag_record(RTC_DIAG_EV_TX_ERR, (uint16_t)frames_pushed,
                        (uint8_t)MIN(250, -ret));
            if ((frames_dropped_tx % 100) == 1) {
                LOG_WRN("DIAG tx drop #%u ret=%d", frames_dropped_tx, ret);
            }
        } else {
            frames_consumed++;
            if ((frames_consumed & 0xF) == 0) {
                diag_record(RTC_DIAG_EV_POP, (uint16_t)frames_pushed, qd);
            }
        }
    }
}

/* 1 Hz session stats: frame accounting deltas, queue extremes, worst notify
 * latency, negotiated interval, stack headroom. One WRN line per second. */
static void stat_work_fn(struct k_work *work)
{
    ARG_UNUSED(work);

    if (!atomic_get(&session_active)) {
        return;
    }

    size_t rtc_stack_free = 0;

    (void)k_thread_stack_space_get(&rtc_stream_thread_data, &rtc_stack_free);

    LOG_WRN("RTC stat: p=%u c=%u dq=%u dtx=%u err=%u q=%u..%u smax=%ums int=%u stk=%u",
            frames_pushed - stat_pushed, frames_consumed - stat_consumed,
            frames_dropped_queue - stat_drop_q, frames_dropped_tx - stat_drop_tx,
            stat_tx_err,
            win_q_min == 0xFF ? 0 : win_q_min, win_q_max,
            win_send_max_ms, ble_get_conn_interval(),
            (unsigned int)rtc_stack_free);

    if ((stat_tick++ % 5) == 4) {
        LOG_WRN("RTC stack: audio=%u",
                (unsigned int)audio_thread_stack_free());
    }

    stat_pushed = frames_pushed;
    stat_consumed = frames_consumed;
    stat_drop_q = frames_dropped_queue;
    stat_drop_tx = frames_dropped_tx;
    stat_tx_err = 0;
    win_q_min = 0xFF;
    win_q_max = 0;
    win_send_max_ms = 0;

    k_work_reschedule(&stat_work, K_SECONDS(1));
}

/* ----------------------------------------------------------------------- */
/* Timeout & disconnect work                                               */
/* ----------------------------------------------------------------------- */

static void start_timeout_fn(struct k_work *work)
{
    ARG_UNUSED(work);

    if (!atomic_get(&session_active) || atomic_get(&stream_active)) {
        return;
    }

    LOG_WRN("no DOWNLOAD within %us, aborting RTC session",
            CONFIG_CLIP_RTC_START_TIMEOUT_SEC);
    ble_notify_event("rtc", "timeout");
    rtc_stream_session_end();
    clip_post_event(CLIP_EVENT_STOP);
}

static void disconnect_work_fn(struct k_work *work)
{
    ARG_UNUSED(work);

    if (!atomic_get(&session_active)) {
        return;
    }

    LOG_INF("BLE disconnected, ending RTC session");
    rtc_stream_session_end();
    clip_post_event(CLIP_EVENT_STOP);
}

/* Wait for tight connection parameters to be applied before sending the
 * first frame, so the stream never starts on a slow default link. Polls
 * the negotiated interval and falls back to starting anyway after the max
 * wait (the central may refuse the tight params). */
#define RTC_PARAM_WAIT_POLL_MS 100
#define RTC_PARAM_WAIT_MAX_MS  5000

static struct k_work_delayable param_wait_work;
static atomic_t start_pending;
static uint32_t param_wait_ms;

static bool rtc_conn_params_tight(void)
{
    uint16_t interval = ble_get_conn_interval();

    return interval != 0 && interval <= 12;
}

static void rtc_stream_go(void)
{
    /* RTC delivers "now": drop whatever queued while waiting. */
    rtc_stream_flush();

    int ret = transport_ble_send_stream_start(rtc_session_id);
    if (ret < 0) {
        LOG_ERR("STREAM_START send failed: %d", ret);
        return; /* start_pending stays set -> poll loop retries */
    }

    atomic_set(&start_pending, 0);
    atomic_set(&stream_active, 1);
    LOG_WRN("DIAG stream started: %s (param wait %u ms)", rtc_session_id,
            param_wait_ms);
}

static void param_wait_fn(struct k_work *work)
{
    ARG_UNUSED(work);

    if (!atomic_get(&start_pending) || !atomic_get(&session_active)) {
        atomic_set(&start_pending, 0);
        return;
    }

    param_wait_ms += RTC_PARAM_WAIT_POLL_MS;

    if (!rtc_conn_params_tight()) {
        if (param_wait_ms < RTC_PARAM_WAIT_MAX_MS) {
            k_work_reschedule(&param_wait_work,
                              K_MSEC(RTC_PARAM_WAIT_POLL_MS));
            return;
        }
        if (param_wait_ms == RTC_PARAM_WAIT_MAX_MS) {
            LOG_WRN("DIAG param wait timeout, starting on interval=%u",
                    ble_get_conn_interval());
        }
        if (param_wait_ms >= 2 * RTC_PARAM_WAIT_MAX_MS) {
            LOG_ERR("stream start retries exhausted, aborting");
            atomic_set(&start_pending, 0);
            return;
        }
    }

    rtc_stream_go();
    if (atomic_get(&start_pending)) {
        /* STREAM_START send failed; retry on next poll */
        k_work_reschedule(&param_wait_work, K_MSEC(RTC_PARAM_WAIT_POLL_MS));
    }
}

/* ----------------------------------------------------------------------- */
/* Public API                                                              */
/* ----------------------------------------------------------------------- */

int rtc_stream_init(void)
{
    k_work_init_delayable(&start_timeout_work, start_timeout_fn);
    k_work_init(&disconnect_work, disconnect_work_fn);
    k_work_init_delayable(&stat_work, stat_work_fn);
    k_work_init_delayable(&param_wait_work, param_wait_fn);

    audio_register_data_callback(rtc_stream_push, NULL);

    k_tid_t tid = k_thread_create(&rtc_stream_thread_data, rtc_stream_stack,
                                  K_THREAD_STACK_SIZEOF(rtc_stream_stack),
                                  rtc_stream_thread_fn, NULL, NULL, NULL,
                                  CONFIG_CLIP_RTC_THREAD_PRIORITY, 0, K_NO_WAIT);
    if (!tid) {
        LOG_ERR("failed to create rtc stream thread");
        return -ENOMEM;
    }
    k_thread_name_set(tid, "rtc_stream");

    return 0;
}

int rtc_stream_session_begin(const char *session_id)
{
    if (atomic_get(&session_active)) {
        return -EBUSY;
    }

    strncpy(rtc_session_id, session_id, sizeof(rtc_session_id) - 1);
    rtc_session_id[sizeof(rtc_session_id) - 1] = '\0';

    rtc_stream_flush();
    frames_pushed = 0;
    frames_dropped_queue = 0;
    frames_dropped_tx = 0;
    frames_consumed = 0;
    stat_pushed = 0;
    stat_consumed = 0;
    stat_drop_q = 0;
    stat_drop_tx = 0;
    stat_tx_err = 0;
    stat_tick = 0;
    win_q_min = 0xFF;
    win_q_max = 0;
    win_send_max_ms = 0;
    atomic_set(&diag_head, 0);
    memset(diag_ring, 0, sizeof(diag_ring));
    atomic_set(&stream_paused, 0);
    atomic_set(&stream_active, 0);
    atomic_set(&session_active, 1);

    /* Abort the session if the phone never starts the stream. */
    k_work_schedule(&start_timeout_work,
                    K_SECONDS(CONFIG_CLIP_RTC_START_TIMEOUT_SEC));
    k_work_schedule(&stat_work, K_SECONDS(1));

    LOG_INF("session %s (start timeout %us)", rtc_session_id,
            CONFIG_CLIP_RTC_START_TIMEOUT_SEC);
    return 0;
}

void rtc_stream_session_end(void)
{
    if (!atomic_get(&session_active)) {
        return;
    }

    atomic_set(&session_active, 0);
    atomic_set(&stream_active, 0);
    atomic_set(&stream_paused, 0);
    atomic_set(&start_pending, 0);
    k_work_cancel_delayable(&start_timeout_work);
    k_work_cancel_delayable(&stat_work);
    k_work_cancel_delayable(&param_wait_work);
    rtc_stream_flush();
    rtc_session_id[0] = '\0';

    LOG_WRN("DIAG session end: pushed=%u drop_q=%u drop_tx=%u",
            frames_pushed, frames_dropped_queue, frames_dropped_tx);

    /* Dump the diag ring oldest->newest for offline decoding
     * (8-byte records: ts_ms u32, seq u16, qdepth u8, ev u8). */
    uint32_t head = (uint32_t)atomic_get(&diag_head);
    uint32_t n = MIN(head, RTC_DIAG_RING_SIZE);

    if (n > 0) {
        uint32_t start = (head - n) & (RTC_DIAG_RING_SIZE - 1);

        if (start + n <= RTC_DIAG_RING_SIZE) {
            LOG_HEXDUMP_WRN(&diag_ring[start],
                            n * sizeof(struct rtc_diag_rec), "diag:");
        } else {
            uint32_t first = RTC_DIAG_RING_SIZE - start;

            LOG_HEXDUMP_WRN(&diag_ring[start],
                            first * sizeof(struct rtc_diag_rec), "diag1:");
            LOG_HEXDUMP_WRN(&diag_ring[0],
                            (n - first) * sizeof(struct rtc_diag_rec),
                            "diag2:");
        }
    }
}

bool rtc_stream_session_active(void)
{
    return atomic_get(&session_active) != 0;
}

const char *rtc_stream_session_id(void)
{
    return rtc_session_id;
}


int rtc_stream_start(void)
{
    if (!atomic_get(&session_active)) {
        return -ENOENT;
    }
    if (atomic_get(&stream_active) || atomic_get(&start_pending)) {
        return 0; /* idempotent */
    }

    k_work_cancel_delayable(&start_timeout_work);
    atomic_set(&stream_paused, 0);

    /* Tight params were already requested when the RTC session began
     * (AT+START=RTC); ask again defensively. */
    ble_request_rtc_conn_params(true);

    param_wait_ms = 0;
    atomic_set(&start_pending, 1);

    if (rtc_conn_params_tight()) {
        rtc_stream_go();
        if (!atomic_get(&start_pending)) {
            return 0; /* started immediately */
        }
    } else {
        LOG_WRN("DIAG stream start deferred, waiting for tight conn params");
    }

    k_work_schedule(&param_wait_work, K_MSEC(RTC_PARAM_WAIT_POLL_MS));
    return 0;
}

void rtc_stream_stop(uint8_t reason)
{
    bool was_pending = atomic_get(&start_pending) != 0;

    atomic_set(&start_pending, 0);
    k_work_cancel_delayable(&param_wait_work);

    if (!atomic_get(&stream_active)) {
        if (was_pending) {
            ble_request_rtc_conn_params(false);
        }
        return;
    }

    atomic_set(&stream_active, 0);
    ble_request_rtc_conn_params(false);
    rtc_stream_flush();

    if (ble_is_connected() && ble_is_file_data_notify_enabled()) {
        (void)transport_ble_send_stream_end(reason);
    }

    LOG_WRN("DIAG stream stopped (reason=%u)", reason);
}

void rtc_stream_pause(void)
{
    atomic_set(&stream_paused, 1);
    rtc_stream_flush(); /* paused = discard everything queued */
    LOG_INF("stream paused");
}

void rtc_stream_resume(void)
{
    atomic_set(&stream_paused, 0);
    LOG_INF("stream resumed");
}

bool rtc_stream_is_streaming(void)
{
    return atomic_get(&stream_active) != 0;
}

bool rtc_stream_is_paused(void)
{
    return atomic_get(&stream_paused) != 0;
}

void rtc_stream_ble_disconnected(void)
{
    if (atomic_get(&session_active)) {
        k_work_submit(&disconnect_work);
    }
}
