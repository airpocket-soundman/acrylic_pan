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
    CHECK(ApanCaptureEventReady(&capture));
    event = ApanCaptureGetEvent(&capture);
    CHECK(event != NULL);
    CHECK(event->trigger_index == 128U);
    CHECK(event->peak_abs == 2000U);
    for (i = 0U; i < 128U; i++)
    {
        CHECK(event->samples[i] == 10);
    }
    CHECK(event->samples[128] == 2000);
    CHECK(event->samples[129] == 1);
    CHECK(event->samples[511] == 383);

    encoded_size = ApanProtocolEncodeEvent(event, 42U, 123456U,
                                           encoded, sizeof(encoded));
    CHECK(encoded_size > 0U);
    CHECK(encoded[encoded_size - 1U] == 0U);
    output = fopen(argv[1], "wb");
    CHECK(output != NULL);
    CHECK(fwrite(encoded, 1U, encoded_size, output) == encoded_size);
    CHECK(fclose(output) == 0);

    ApanCaptureReleaseEvent(&capture);
    CHECK(ApanCaptureForceBlock(&capture, first_block, 512U));
    event = ApanCaptureGetEvent(&capture);
    CHECK(event != NULL);
    CHECK(event->trigger_index == 0U);
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
    return 0;
}
