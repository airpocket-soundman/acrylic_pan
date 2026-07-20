#include <stdio.h>
#include <stdlib.h>

#include "apan_capture.h"
#include "apan_protocol.h"

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "check failed at line %d: %s\n", __LINE__, #condition); return 1; \
} } while (0)

int main(int argc, char **argv)
{
    ApanCapture capture;
    ApanCaptureConfig config = { 100U, 100U };
    int16_t first_block[512];
    int16_t second_block[512];
    uint8_t encoded[APAN_ENCODED_FRAME_CAPACITY];
    const ApanEvent *event;
    size_t encoded_size;
    size_t i;
    ApanProtocolDecoder decoder;
    ApanCommandFrame command = { 0 };
    uint8_t command_packet[64];
    size_t command_size;
    FILE *output;
    ApanCapture threshold_capture;
    ApanCaptureConfig production_threshold = { 700U, 200U, 3000U, 16U };
    int16_t gravity_history[APAN_PRETRIGGER_SAMPLES];

    CHECK(argc == 2);
    for (i = 0U; i < 512U; i++)
    {
        first_block[i] = 10;
        second_block[i] = (int16_t)i;
    }
    second_block[0] = 2000;

    ApanCaptureInit(&capture, &config);
    ApanCaptureFeed(&capture, first_block, 512U);
    CHECK(!ApanCaptureEventReady(&capture));

    /* Trigger is the first sample of the next block: validates boundary jerk. */
    ApanCaptureFeed(&capture, second_block, 512U);
    CHECK(!ApanCaptureEventReady(&capture));
    ApanCaptureFeed(&capture, first_block, 512U);
    CHECK(!ApanCaptureEventReady(&capture));
    ApanCaptureFeed(&capture, first_block, 512U);
    CHECK(!ApanCaptureEventReady(&capture));
    ApanCaptureFeed(&capture, first_block, 512U);
    CHECK(ApanCaptureEventReady(&capture));
    event = ApanCaptureGetEvent(&capture);
    CHECK(event != NULL);
    CHECK(event->trigger_index == APAN_PRETRIGGER_SAMPLES);
    CHECK(event->sample_count == APAN_COLLECTION_SAMPLES);
    CHECK(event->peak_abs == 2000U);
    for (i = 0U; i < APAN_PRETRIGGER_SAMPLES; i++)
    {
        CHECK(event->samples[i] == 10);
    }
    CHECK(event->samples[APAN_PRETRIGGER_SAMPLES] == 2000);
    CHECK(event->samples[APAN_PRETRIGGER_SAMPLES + 1U] == 1);
    CHECK(event->samples[511] == 447);
    CHECK(event->samples[2047] == 10);
    for (i = 0U; i < 4U; i++)
    {
        encoded_size = ApanProtocolEncodeEventChunk(
            event, 9U, (uint16_t)i, (uint32_t)(42U + i), 123456U,
            encoded, sizeof(encoded));
        CHECK(encoded_size > 0U);
        CHECK(encoded[encoded_size - 1U] == 0U);
        if (i == 0U)
        {
            output = fopen(argv[1], "wb");
            CHECK(output != NULL);
            CHECK(fwrite(encoded, 1U, encoded_size, output) == encoded_size);
            CHECK(fclose(output) == 0);
        }
    }

    ApanCaptureReleaseEvent(&capture);
    CHECK(ApanCaptureForceBlock(&capture, first_block, 512U));
    event = ApanCaptureGetEvent(&capture);
    CHECK(event != NULL);
    CHECK(event->trigger_index == 0U);
    CHECK(event->sample_count == APAN_INFERENCE_SAMPLES);
    CHECK(event->peak_abs == 10U);

    /* A stop/re-arm gap must not retain either a ready event or stale
       pre-trigger history from the previous arming period. */
    ApanCaptureReset(&capture);
    CHECK(!ApanCaptureEventReady(&capture));
    CHECK(capture.history_count == 0U);
    CHECK(!capture.has_previous_sample);
    CHECK(!capture.collecting);
    ApanCaptureFeed(&capture, second_block, 1U);
    CHECK(!ApanCaptureEventReady(&capture));
    CHECK(!capture.collecting);

    command_size = ApanProtocolEncodeFrame(APAN_MESSAGE_CAPTURE, 0U, 77U, 0U,
                                           NULL, 0U, command_packet,
                                           sizeof(command_packet));
    CHECK(command_size > 0U);
    ApanProtocolDecoderInit(&decoder);
    for (i = 0U; i < command_size; i++)
    {
        bool complete = ApanProtocolDecoderFeed(&decoder, command_packet[i], &command);
        CHECK(complete == (i == (command_size - 1U)));
    }
    CHECK(command.message_type == APAN_MESSAGE_CAPTURE);
    CHECK(command.sequence == 77U);
    CHECK(command.payload_size == 0U);

    /* At 32 g, static Z gravity is about 1024 LSB and satisfies the raw level
       gate. A 700-LSB crossing is only a candidate: it must be followed by
       3000 LSB of baseline-relative displacement within 16 samples. */
    for (i = 0U; i < APAN_PRETRIGGER_SAMPLES; i++)
    {
        gravity_history[i] = 1000;
    }
    ApanCaptureInit(&threshold_capture, &production_threshold);
    ApanCaptureFeed(&threshold_capture, gravity_history, APAN_PRETRIGGER_SAMPLES);
    gravity_history[0] = 1699;
    ApanCaptureFeed(&threshold_capture, gravity_history, 1U);
    CHECK(!threshold_capture.collecting);
    gravity_history[0] = 1000;
    ApanCaptureFeed(&threshold_capture, gravity_history, 1U);
    CHECK(!threshold_capture.collecting);
    gravity_history[0] = 1700;
    ApanCaptureFeed(&threshold_capture, gravity_history, 1U);
    CHECK(threshold_capture.collecting);
    CHECK(threshold_capture.event.trigger_index == APAN_PRETRIGGER_SAMPLES);
    for (i = 0U; i < 15U; i++)
    {
        gravity_history[0] = 1000;
        ApanCaptureFeed(&threshold_capture, gravity_history, 1U);
    }
    CHECK(!threshold_capture.collecting);
    CHECK(!ApanCaptureEventReady(&threshold_capture));

    ApanCaptureReset(&threshold_capture);
    for (i = 0U; i < APAN_PRETRIGGER_SAMPLES; i++)
    {
        gravity_history[i] = 1000;
    }
    ApanCaptureFeed(&threshold_capture, gravity_history, APAN_PRETRIGGER_SAMPLES);
    gravity_history[0] = 1700;
    ApanCaptureFeed(&threshold_capture, gravity_history, 1U);
    gravity_history[0] = 4000;
    ApanCaptureFeed(&threshold_capture, gravity_history, 1U);
    CHECK(threshold_capture.collecting);
    CHECK(threshold_capture.candidate_confirmed);
    CHECK(threshold_capture.event.trigger_index == APAN_PRETRIGGER_SAMPLES);
    return 0;
}
