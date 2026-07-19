#include "apan_capture.h"

#include <string.h>

static uint16_t magnitude16(int16_t value)
{
    if (value == INT16_MIN)
    {
        return 32768U;
    }
    return (uint16_t)((value < 0) ? -value : value);
}

static uint16_t difference_magnitude(int16_t a, int16_t b)
{
    int32_t difference = (int32_t)a - (int32_t)b;
    if (difference < 0)
    {
        difference = -difference;
    }
    return (uint16_t)((difference > 65535L) ? 65535L : difference);
}

static void push_history(ApanCapture *capture, int16_t sample)
{
    capture->history[capture->history_write] = sample;
    capture->history_write = (uint16_t)((capture->history_write + 1U) % APAN_PRETRIGGER_SAMPLES);
    if (capture->history_count < APAN_PRETRIGGER_SAMPLES)
    {
        capture->history_count++;
    }
}

static void begin_event(ApanCapture *capture, int16_t trigger_sample)
{
    int32_t baseline_sum = 0L;
    uint16_t i;
    uint16_t read = capture->history_write;

    for (i = 0U; i < APAN_PRETRIGGER_SAMPLES; i++)
    {
        capture->event.samples[i] = capture->history[read];
        baseline_sum += capture->history[read];
        read = (uint16_t)((read + 1U) % APAN_PRETRIGGER_SAMPLES);
    }

    capture->candidate_baseline = (int16_t)(baseline_sum / (int32_t)APAN_PRETRIGGER_SAMPLES);
    capture->candidate_peak_deviation = difference_magnitude(
        trigger_sample, capture->candidate_baseline);
    capture->candidate_confirmed =
        (capture->config.confirmation_threshold == 0U) ||
        (capture->candidate_peak_deviation >= capture->config.confirmation_threshold);
    capture->event.trigger_index = APAN_PRETRIGGER_SAMPLES;
    capture->event.samples[APAN_PRETRIGGER_SAMPLES] = trigger_sample;
    capture->event.peak_abs = magnitude16(trigger_sample);
    capture->event_write = APAN_PRETRIGGER_SAMPLES + 1U;
    capture->collecting = true;
}

void ApanCaptureInit(ApanCapture *capture, const ApanCaptureConfig *config)
{
    memset(capture, 0, sizeof(*capture));
    capture->config = *config;
    capture->target_samples = APAN_COLLECTION_SAMPLES;
}

void ApanCaptureReset(ApanCapture *capture)
{
    ApanCaptureConfig config = capture->config;
    uint16_t target_samples = capture->target_samples;
    ApanCaptureInit(capture, &config);
    capture->target_samples = target_samples;
}

bool ApanCaptureSetTargetSamples(ApanCapture *capture, uint16_t target_samples)
{
    if (capture->collecting || capture->ready ||
        (target_samples < (APAN_PRETRIGGER_SAMPLES + 1U)) ||
        (target_samples > APAN_EVENT_CAPACITY))
    {
        return false;
    }
    capture->target_samples = target_samples;
    return true;
}

void ApanCaptureFeed(ApanCapture *capture, const int16_t *samples, size_t count)
{
    size_t i;

    for (i = 0U; i < count; i++)
    {
        int16_t sample = samples[i];

        if (capture->ready)
        {
            return;
        }

        if (capture->collecting)
        {
            uint16_t magnitude = magnitude16(sample);
            uint16_t deviation = difference_magnitude(sample, capture->candidate_baseline);
            capture->event.samples[capture->event_write++] = sample;
            if (magnitude > capture->event.peak_abs)
            {
                capture->event.peak_abs = magnitude;
            }
            if (deviation > capture->candidate_peak_deviation)
            {
                capture->candidate_peak_deviation = deviation;
            }
            if (deviation >= capture->config.confirmation_threshold)
            {
                capture->candidate_confirmed = true;
            }
            if (!capture->candidate_confirmed &&
                (capture->config.confirmation_samples > 0U) &&
                (capture->event_write >=
                 (APAN_PRETRIGGER_SAMPLES + capture->config.confirmation_samples)))
            {
                /* Preserve the original 1000-LSB crossing as the model's
                   trigger index, but reject low-energy noise before a full
                   event buffer is committed. */
                capture->collecting = false;
                capture->event_write = 0U;
                capture->event.sample_count = 0U;
                capture->event.peak_abs = 0U;
            }
            if (capture->event_write == capture->target_samples)
            {
                capture->collecting = false;
                capture->event.sample_count = capture->event_write;
                capture->ready = true;
                push_history(capture, sample);
                capture->previous_sample = sample;
                capture->has_previous_sample = true;
                return;
            }
        }
        else if ((capture->history_count == APAN_PRETRIGGER_SAMPLES) &&
                 capture->has_previous_sample &&
                 (difference_magnitude(sample, capture->previous_sample) >= capture->config.jerk_threshold) &&
                 (magnitude16(sample) >= capture->config.level_threshold))
        {
            begin_event(capture, sample);
        }

        push_history(capture, sample);
        capture->previous_sample = sample;
        capture->has_previous_sample = true;
    }
}

bool ApanCaptureForceBlock(ApanCapture *capture, const int16_t *samples, size_t count)
{
    size_t i;
    uint16_t peak = 0U;

    if ((count == 0U) || (count > APAN_EVENT_CAPACITY) ||
        capture->ready || capture->collecting)
    {
        return false;
    }
    for (i = 0U; i < count; i++)
    {
        uint16_t magnitude = magnitude16(samples[i]);
        capture->event.samples[i] = samples[i];
        if (magnitude > peak)
        {
            peak = magnitude;
        }
        push_history(capture, samples[i]);
    }
    capture->previous_sample = samples[count - 1U];
    capture->has_previous_sample = true;
    capture->event.trigger_index = 0U;
    capture->event.peak_abs = peak;
    capture->event_write = (uint16_t)count;
    capture->event.sample_count = (uint16_t)count;
    capture->ready = true;
    return true;
}

bool ApanCaptureForceFeed(ApanCapture *capture, const int16_t *samples, size_t count)
{
    size_t i;
    if ((samples == NULL) || capture->ready || capture->collecting ||
        (capture->target_samples == 0U))
    {
        return false;
    }
    for (i = 0U; i < count; i++)
    {
        uint16_t magnitude;
        if (capture->event_write >= capture->target_samples) { break; }
        magnitude = magnitude16(samples[i]);
        capture->event.samples[capture->event_write++] = samples[i];
        if (magnitude > capture->event.peak_abs) { capture->event.peak_abs = magnitude; }
        push_history(capture, samples[i]);
        capture->previous_sample = samples[i];
        capture->has_previous_sample = true;
    }
    capture->event.trigger_index = 0U;
    if (capture->event_write == capture->target_samples)
    {
        capture->event.sample_count = capture->event_write;
        capture->ready = true;
    }
    return true;
}

bool ApanCaptureEventReady(const ApanCapture *capture)
{
    return capture->ready;
}

const ApanEvent *ApanCaptureGetEvent(const ApanCapture *capture)
{
    return capture->ready ? &capture->event : NULL;
}

void ApanCaptureReleaseEvent(ApanCapture *capture)
{
    capture->ready = false;
    capture->event_write = 0U;
    capture->event.sample_count = 0U;
    capture->event.peak_abs = 0U;
}
