#ifndef APAN_PROTOCOL_H
#define APAN_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#include "apan_capture.h"

#define APAN_PROTOCOL_VERSION (1U)
#define APAN_MESSAGE_HELLO      (0x01U)
#define APAN_MESSAGE_STATUS     (0x02U)
#define APAN_MESSAGE_START      (0x10U)
#define APAN_MESSAGE_STOP       (0x11U)
#define APAN_MESSAGE_SET_CONFIG (0x12U)
#define APAN_MESSAGE_CAPTURE    (0x13U)
#define APAN_MESSAGE_EVENT_DATA (0x20U)
#define APAN_MESSAGE_ACK        (0x70U)
#define APAN_MESSAGE_NACK       (0x71U)
#define APAN_ENCODED_FRAME_CAPACITY (1070U)
#define APAN_COMMAND_PAYLOAD_CAPACITY (16U)
#define APAN_COMMAND_ENCODED_CAPACITY (64U)

typedef struct
{
    uint8_t message_type;
    uint16_t flags;
    uint32_t sequence;
    uint16_t payload_size;
    uint8_t payload[APAN_COMMAND_PAYLOAD_CAPACITY];
} ApanCommandFrame;

typedef struct
{
    uint8_t encoded[APAN_COMMAND_ENCODED_CAPACITY];
    uint16_t encoded_size;
    uint32_t error_count;
} ApanProtocolDecoder;

void ApanProtocolDecoderInit(ApanProtocolDecoder *decoder);
bool ApanProtocolDecoderFeed(ApanProtocolDecoder *decoder, uint8_t byte,
                             ApanCommandFrame *frame);

size_t ApanProtocolEncodeFrame(uint8_t message_type, uint16_t flags,
                               uint32_t sequence, uint32_t timestamp_us,
                               const uint8_t *payload, uint16_t payload_size,
                               uint8_t *encoded, size_t capacity);

/* Returns encoded bytes including the trailing zero delimiter, or zero on error. */
size_t ApanProtocolEncodeEvent(const ApanEvent *event,
                               uint32_t sequence,
                               uint32_t timestamp_us,
                               uint8_t *encoded,
                               size_t capacity);

#endif
