#include "apan_collector_app.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "AIContext.h"
#include "ConfigData.h"
#include "Sensor.h"
#include "SoftwareInterrupt.h"
#include "Uart1.h"
#include "apan_ai_selftest.h"
#include "apan_capture.h"
#include "apan_protocol.h"

#define DEFAULT_JERK_THRESHOLD  (1200U)
#define DEFAULT_LEVEL_THRESHOLD (800U)

static AI_CONTEXT sensor_context[2];
static ApanCapture capture;
static uint8_t transmit_buffer[APAN_ENCODED_FRAME_CAPACITY];
static uint32_t sequence;
static volatile uint8_t pending_command;
static volatile bool pending_binary;
static volatile uint32_t pending_sequence;
static volatile uint8_t pending_request_type;
static volatile uint8_t pending_case_id;
static uint8_t command_buffer[16];
static uint8_t command_length;
static uint8_t receive_mode;
static bool force_capture;
static bool transmit_busy;
static bool collector_stopped;
static uint8_t warmup_blocks;
static uint8_t ui_status;
static ApanProtocolDecoder protocol_decoder;

enum
{
    COMMAND_NONE = 0U,
    COMMAND_PING,
    COMMAND_STATUS,
    COMMAND_CAPTURE,
    COMMAND_START,
    COMMAND_STOP,
    COMMAND_AI_SELFTEST,
    COMMAND_UNKNOWN
};

static const uint8_t RESPONSE_PONG[] = "PONG APAN/1\r\n";
static const uint8_t RESPONSE_ACK_CAPTURE[] = "ACK CAPTURE\r\n";
static const uint8_t RESPONSE_BUSY[] = "NACK BUSY\r\n";
static const uint8_t RESPONSE_UNKNOWN[] = "NACK UNKNOWN\r\n";
static const uint8_t RESPONSE_STATUS_IDLE[] = "STATUS IDLE\r\n";
static const uint8_t RESPONSE_STATUS_ARMED[] = "STATUS ARMED\r\n";
static const uint8_t RESPONSE_STATUS_TX[] = "STATUS TX\r\n";

static void receive_byte(uint32_t value, uint16_t error_status);

static void text_transmit_complete(uint32_t count, uint16_t error_status)
{
    (void)count;
    (void)error_status;
    transmit_busy = false;
    /* Uart1Write replaces the interrupt-enable register with TX-only bits. */
    Uart1StartReadByte(receive_byte);
}

static void write_text(const uint8_t *text, size_t size)
{
    transmit_busy = true;
    Uart1Write((uint8_t *)text, (uint32_t)size, text_transmit_complete);
}

static void write_protocol(uint8_t type, uint32_t response_sequence,
                           const uint8_t *payload, uint16_t payload_size)
{
    size_t size = ApanProtocolEncodeFrame(type, 0U, response_sequence, 0U,
                                          payload, payload_size,
                                          transmit_buffer, sizeof(transmit_buffer));
    if (size > 0U)
    {
        transmit_busy = true;
        Uart1Write(transmit_buffer, (uint32_t)size, text_transmit_complete);
    }
}

static void queue_binary_command(const ApanCommandFrame *frame)
{
    uint8_t command = COMMAND_UNKNOWN;
    if (pending_command != COMMAND_NONE)
    {
        return;
    }
    switch (frame->message_type)
    {
        case APAN_MESSAGE_HELLO: command = COMMAND_PING; break;
        case APAN_MESSAGE_STATUS: command = COMMAND_STATUS; break;
        case APAN_MESSAGE_CAPTURE: command = COMMAND_CAPTURE; break;
        case APAN_MESSAGE_START: command = COMMAND_START; break;
        case APAN_MESSAGE_STOP: command = COMMAND_STOP; break;
        case APAN_MESSAGE_AI_SELFTEST:
            command = COMMAND_AI_SELFTEST;
            pending_case_id = (frame->payload_size > 0U) ? frame->payload[0] : 0U;
            break;
        default: command = COMMAND_UNKNOWN; break;
    }
    pending_binary = true;
    pending_sequence = frame->sequence;
    pending_request_type = frame->message_type;
    pending_command = command;
}

static void receive_byte(uint32_t value, uint16_t error_status)
{
    uint8_t byte = (uint8_t)value;
    ApanCommandFrame frame;
    (void)error_status;

    if (receive_mode == 0U)
    {
        receive_mode = ((byte >= 'A') && (byte <= 'z')) ? 1U : 2U;
    }
    if (receive_mode == 2U)
    {
        if (ApanProtocolDecoderFeed(&protocol_decoder, byte, &frame))
        {
            queue_binary_command(&frame);
        }
        if (byte == 0U)
        {
            receive_mode = 0U;
        }
        return;
    }
    if ((byte == '\r') || (byte == '\n'))
    {
        receive_mode = 0U;
        if ((command_length == 0U) || (pending_command != COMMAND_NONE))
        {
            return;
        }
        command_buffer[command_length] = 0U;
        if (strcmp((const char *)command_buffer, "PING") == 0)
        {
            pending_binary = false;
            pending_command = COMMAND_PING;
        }
        else if (strcmp((const char *)command_buffer, "STATUS") == 0)
        {
            pending_binary = false;
            pending_command = COMMAND_STATUS;
        }
        else if ((strcmp((const char *)command_buffer, "CAPTURE") == 0) ||
                 (strcmp((const char *)command_buffer, "GET_STATIC") == 0))
        {
            pending_binary = false;
            pending_command = COMMAND_CAPTURE;
        }
        else if (strncmp((const char *)command_buffer, "AI_SELFTEST", 11U) == 0)
        {
            uint8_t case_id = 0U;
            if ((command_length > 12U) && (command_buffer[11] == ' ') &&
                (command_buffer[12] >= '0') && (command_buffer[12] <= '9'))
            {
                case_id = (uint8_t)(command_buffer[12] - '0');
            }
            pending_case_id = case_id;
            pending_binary = false;
            pending_command = COMMAND_AI_SELFTEST;
        }
        else
        {
            pending_binary = false;
            pending_command = COMMAND_UNKNOWN;
        }
        command_length = 0U;
        return;
    }
    if ((byte >= 'a') && (byte <= 'z'))
    {
        byte = (uint8_t)(byte - ('a' - 'A'));
    }
    if (command_length < (sizeof(command_buffer) - 1U))
    {
        command_buffer[command_length++] = byte;
    }
    else
    {
        command_length = 0U;
        pending_command = COMMAND_UNKNOWN;
    }
}

static void send_ready_event(void)
{
    const ApanEvent *event = ApanCaptureGetEvent(&capture);
    size_t encoded_size;

    if ((event == NULL) || transmit_busy)
    {
        return;
    }
    encoded_size = ApanProtocolEncodeEvent(event, sequence++, 0U,
                                           transmit_buffer, sizeof(transmit_buffer));
    if (encoded_size == 0U)
    {
        ApanCaptureReleaseEvent(&capture);
        SensorStart();
        return;
    }
    transmit_busy = true;
    Uart1Write(transmit_buffer, (uint32_t)encoded_size, NULL);
    /* EVENT_DATA is a one-shot transaction. The PC cannot issue its next
       request until it receives the trailing delimiter, so no TX-complete
       interrupt is required to release the application state. */
    ApanCaptureReleaseEvent(&capture);
    collector_stopped = true;
    transmit_busy = false;
    Uart1StartReadByte(receive_byte);
}

static void sensor_block_ready(void)
{
    AI_CONTEXT *context = SensorGetAiContextDataStored();
    if (force_capture)
    {
        (void)ApanCaptureForceBlock(&capture, context->LogInfo.InputData,
                                    AI_CONTEXT_INPUT_SOURCE_SIZE);
        force_capture = false;
    }
    else if (warmup_blocks > 0U)
    {
        warmup_blocks--;
    }
    else
    {
        ApanCaptureFeed(&capture, context->LogInfo.InputData, AI_CONTEXT_INPUT_SOURCE_SIZE);
    }
    SensorCompletedUsingAiContext();

    if (!ApanCaptureEventReady(&capture))
    {
        return;
    }

    /* Freeze acquisition while the 115200-bps UART owns the frame buffer. */
    SensorStop();
    send_ready_event();
}

void ApanCollectorAppInitialize(void)
{
    const ApanCaptureConfig capture_config = {
        DEFAULT_JERK_THRESHOLD,
        DEFAULT_LEVEL_THRESHOLD
    };

    /* KX134: use MEMS, Z axis, ODR code 15 = 25.6 kHz, 512-sample blocks. */
    (void)ConfigDataSetUint8Value(EN_CONFIG_USE_SENSOR, 1U);
    (void)ConfigDataSetUint8Value(EN_CONFIG_MEMS_DATA_KIND, 2U);
    (void)ConfigDataSetUint8Value(EN_CONFIG_MEMS_SAMPLING_FREQUENCY, 15U);
    (void)ConfigDataSetUint16Value(EN_CONFIG_MEMS_SAMPLING_NUM, AI_CONTEXT_INPUT_SOURCE_SIZE);
    (void)ConfigDataSetUint8Value(EN_CONFIG_MEMS_LPF, 1U);
    (void)ConfigDataSetUint8Value(EN_CONFIG_REALTIME_COM, 0U);

    ApanCaptureInit(&capture, &capture_config);
    warmup_blocks = 4U;
    ApanProtocolDecoderInit(&protocol_decoder);
    ApanAiSelfTestInitialize();
    SensorInitialize();
    SensorSetAiContextForStoring(&sensor_context[0], &sensor_context[1]);
    SoftwareInterruptSetCallback(sensor_block_ready);
    Uart1StartReadByte(receive_byte);
    collector_stopped = true;
}

void ApanCollectorAppSetUiStatus(bool lcd_ready)
{
    ui_status = lcd_ready ? 0x01U : 0U;
}

void ApanCollectorAppProcess(void)
{
    uint8_t command;
    bool binary;
    uint32_t request_sequence;
    uint8_t request_type;
    uint8_t payload[36];

    if (ApanCaptureEventReady(&capture))
    {
        send_ready_event();
    }
    if (transmit_busy || (pending_command == COMMAND_NONE))
    {
        return;
    }
    command = pending_command;
    binary = pending_binary;
    request_sequence = pending_sequence;
    request_type = pending_request_type;
    pending_command = COMMAND_NONE;
    switch (command)
    {
        case COMMAND_PING:
            if (binary)
            {
                static const uint8_t identity[] = "AcrylicPanCollector";
                write_protocol(APAN_MESSAGE_HELLO, request_sequence,
                               identity, sizeof(identity) - 1U);
            }
            else
            {
                write_text(RESPONSE_PONG, sizeof(RESPONSE_PONG) - 1U);
            }
            break;
        case COMMAND_STATUS:
            if (binary)
            {
                uint8_t state = collector_stopped ? 3U :
                    (ApanCaptureEventReady(&capture) ? 2U : (force_capture ? 1U : 0U));
                payload[0] = state;
                payload[1] = ui_status;
                if ((PORT5->P5DO & (1UL << 4U)) != 0U) { payload[1] |= 0x02U; }
                if ((PORT5->P5DO & (1UL << 5U)) != 0U) { payload[1] |= 0x04U; }
                if ((PORT5->P5DO & (1UL << 6U)) != 0U) { payload[1] |= 0x08U; }
                payload[2] = 0x00U; payload[3] = 0x02U; /* 512 */
                payload[4] = force_capture ? 0U : 0x80U; payload[5] = 0U;
                payload[6] = 0x00U; payload[7] = 0x64U;
                payload[8] = 0x00U; payload[9] = 0x00U; /* 25600 */
                write_protocol(APAN_MESSAGE_STATUS, request_sequence, payload, 10U);
            }
            else if (ApanCaptureEventReady(&capture))
            {
                write_text(RESPONSE_STATUS_TX, sizeof(RESPONSE_STATUS_TX) - 1U);
            }
            else if (force_capture)
            {
                write_text(RESPONSE_STATUS_ARMED, sizeof(RESPONSE_STATUS_ARMED) - 1U);
            }
            else { write_text(RESPONSE_STATUS_IDLE, sizeof(RESPONSE_STATUS_IDLE) - 1U); }
            break;
        case COMMAND_CAPTURE:
            if (force_capture || ApanCaptureEventReady(&capture))
            {
                if (binary)
                {
                    payload[0] = request_type; payload[1] = 1U;
                    write_protocol(APAN_MESSAGE_NACK, request_sequence, payload, 2U);
                }
                else { write_text(RESPONSE_BUSY, sizeof(RESPONSE_BUSY) - 1U); }
            }
            else
            {
                collector_stopped = false;
                force_capture = true;
                SensorStart();
                if (binary)
                {
                    payload[0] = request_type;
                    write_protocol(APAN_MESSAGE_ACK, request_sequence, payload, 1U);
                }
                else { write_text(RESPONSE_ACK_CAPTURE, sizeof(RESPONSE_ACK_CAPTURE) - 1U); }
            }
            break;
        case COMMAND_START:
            if (collector_stopped)
            {
                collector_stopped = false;
                SensorStart();
            }
            payload[0] = request_type;
            write_protocol(APAN_MESSAGE_ACK, request_sequence, payload, 1U);
            break;
        case COMMAND_STOP:
            SensorStop();
            collector_stopped = true;
            payload[0] = request_type;
            write_protocol(APAN_MESSAGE_ACK, request_sequence, payload, 1U);
            break;
        case COMMAND_AI_SELFTEST:
        {
            float output[APAN_AI_OUTPUT_COUNT];
            uint8_t class_id;
            uint8_t index;
            if (!collector_stopped ||
                !ApanAiSelfTestRun(pending_case_id, output, &class_id))
            {
                if (binary)
                {
                    payload[0] = request_type;
                    payload[1] = 3U;
                    write_protocol(APAN_MESSAGE_NACK, request_sequence, payload, 2U);
                }
                else { write_text(RESPONSE_BUSY, sizeof(RESPONSE_BUSY) - 1U); }
                break;
            }
            payload[0] = pending_case_id;
            payload[1] = class_id;
            payload[2] = 0U;
            payload[3] = 0U;
            for (index = 0U; index < APAN_AI_OUTPUT_COUNT; index++)
            {
                union { float value; uint32_t bits; } packed;
                uint8_t offset = (uint8_t)(4U + (index * 4U));
                packed.value = output[index];
                payload[offset] = (uint8_t)packed.bits;
                payload[offset + 1U] = (uint8_t)(packed.bits >> 8);
                payload[offset + 2U] = (uint8_t)(packed.bits >> 16);
                payload[offset + 3U] = (uint8_t)(packed.bits >> 24);
            }
            /* AI_RESULT is binary even for the optional text command so all
               eight scores retain a stable, machine-readable format. */
            write_protocol(APAN_MESSAGE_AI_RESULT, request_sequence, payload, 36U);
            break;
        }
        default:
            if (binary)
            {
                payload[0] = request_type; payload[1] = 2U;
                write_protocol(APAN_MESSAGE_NACK, request_sequence, payload, 2U);
            }
            else { write_text(RESPONSE_UNKNOWN, sizeof(RESPONSE_UNKNOWN) - 1U); }
            break;
    }
}
