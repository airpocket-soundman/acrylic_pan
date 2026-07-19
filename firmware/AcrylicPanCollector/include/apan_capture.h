#ifndef APAN_CAPTURE_H
#define APAN_CAPTURE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define APAN_INFERENCE_SAMPLES   (512U)
#define APAN_COLLECTION_SAMPLES  (2048U)
#define APAN_EVENT_CAPACITY      APAN_COLLECTION_SAMPLES
#define APAN_EVENT_SAMPLES       APAN_INFERENCE_SAMPLES
#define APAN_PRETRIGGER_SAMPLES  (64U)
#define APAN_POSTTRIGGER_SAMPLES (448U)
#define APAN_SAMPLE_RATE_HZ      (25600UL)

typedef struct
{
    uint16_t jerk_threshold;
    uint16_t level_threshold;
    uint16_t confirmation_threshold;
    uint16_t confirmation_samples;
} ApanCaptureConfig;

typedef struct
{
    int16_t samples[APAN_EVENT_CAPACITY];
    uint16_t sample_count;
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
    uint16_t target_samples;
    int16_t candidate_baseline;
    uint16_t candidate_peak_deviation;
    bool candidate_confirmed;
    ApanCaptureConfig config;
    ApanEvent event;
} ApanCapture;

void ApanCaptureInit(ApanCapture *capture, const ApanCaptureConfig *config);
void ApanCaptureReset(ApanCapture *capture);
bool ApanCaptureSetTargetSamples(ApanCapture *capture, uint16_t target_samples);
void ApanCaptureFeed(ApanCapture *capture, const int16_t *samples, size_t count);
bool ApanCaptureForceBlock(ApanCapture *capture, const int16_t *samples, size_t count);
bool ApanCaptureForceFeed(ApanCapture *capture, const int16_t *samples, size_t count);
bool ApanCaptureEventReady(const ApanCapture *capture);
const ApanEvent *ApanCaptureGetEvent(const ApanCapture *capture);
void ApanCaptureReleaseEvent(ApanCapture *capture);

#endif
