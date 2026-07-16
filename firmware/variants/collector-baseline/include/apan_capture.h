#ifndef APAN_CAPTURE_H
#define APAN_CAPTURE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define APAN_EVENT_SAMPLES       (512U)
#define APAN_PRETRIGGER_SAMPLES  (128U)
#define APAN_POSTTRIGGER_SAMPLES (384U)
#define APAN_SAMPLE_RATE_HZ      (25600UL)

typedef struct
{
    uint16_t jerk_threshold;
    uint16_t level_threshold;
} ApanCaptureConfig;

typedef struct
{
    int16_t samples[APAN_EVENT_SAMPLES];
    uint16_t trigger_index;
    uint16_t peak_abs;
} ApanEvent;

typedef struct
{
    int16_t history[APAN_PRETRIGGER_SAMPLES];
    uint16_t history_write;
    uint16_t history_count;
    int16_t previous_sample;
    bool has_previous_sample;
    bool collecting;
    bool ready;
    uint16_t event_write;
    ApanCaptureConfig config;
    ApanEvent event;
} ApanCapture;

void ApanCaptureInit(ApanCapture *capture, const ApanCaptureConfig *config);
void ApanCaptureReset(ApanCapture *capture);
void ApanCaptureFeed(ApanCapture *capture, const int16_t *samples, size_t count);
bool ApanCaptureForceBlock(ApanCapture *capture, const int16_t *samples, size_t count);
bool ApanCaptureEventReady(const ApanCapture *capture);
const ApanEvent *ApanCaptureGetEvent(const ApanCapture *capture);
void ApanCaptureReleaseEvent(ApanCapture *capture);

#endif
