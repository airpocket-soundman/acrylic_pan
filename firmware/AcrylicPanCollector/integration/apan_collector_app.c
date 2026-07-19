#include "apan_collector_app.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "AIContext.h"
#include "ConfigData.h"
#include "Lcd.h"
#include "PeriodicHandler10ms.h"
#include "Sensor.h"
#include "SoftwareInterrupt.h"
#include "Uart1.h"
#include "apan_ai_selftest.h"
#include "apan_inference.h"
#include "apan_capture.h"
#include "apan_protocol.h"
#include "mcu.h"
#include "smpl_common_led.h"

/* Ten no-impact captures on the target board observed adjacent Z differences
   up to 1351 LSB (99.99 percentile about 1298 LSB).  Keep margin above that
   static-noise envelope while retaining sensitivity to a real acrylic hit. */
#define DEFAULT_JERK_THRESHOLD  (1000U)
#define DEFAULT_LEVEL_THRESHOLD (200U)
#define DEFAULT_CONFIRM_THRESHOLD (2000U)
#define DEFAULT_CONFIRM_SAMPLES   (16U)
#define APAN_CPU_CLOCK_HZ        (48000000UL)
#define APAN_SYSTICK_RELOAD      (0x00FFFFFFUL)

static AI_CONTEXT sensor_context[2];
static ApanCapture capture;
static uint8_t transmit_buffer[APAN_ENCODED_FRAME_CAPACITY];
static uint32_t sequence;
static uint32_t collection_event_id;
static uint16_t collection_chunk_index;
static volatile uint8_t pending_command;
static volatile bool pending_binary;
static volatile uint32_t pending_sequence;
static volatile uint8_t pending_request_type;
static volatile uint8_t pending_case_id;
static volatile uint8_t pending_mode;
static volatile uint16_t pending_retrigger_guard_ms;
static uint8_t command_buffer[16];
static uint8_t command_length;
static uint8_t receive_mode;
static bool force_capture;
static bool transmit_busy;
static bool collector_stopped;
static uint8_t warmup_blocks;
static uint8_t ui_status;
static uint8_t operating_mode;
static bool inference_telemetry_pending;
static uint8_t inference_class_id;
static float inference_outputs[APAN_INFERENCE_OUTPUT_COUNT];
static uint32_t inference_sequence;
static volatile bool instrument_transmit_done;
static bool lcd_result_pending;
static uint8_t lcd_class_id;
static uint32_t lcd_inference_us;
static volatile uint32_t app_tick_10ms;
static uint32_t last_accepted_inference_tick;
static uint16_t retrigger_guard_ms = 80U;
static bool accepted_inference_exists;
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
    COMMAND_SET_MODE,
    COMMAND_SET_CONFIG,
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

static void app_periodic_10ms(void)
{
    app_tick_10ms++;
}

static bool accept_instrument_inference(void)
{
    uint32_t now = app_tick_10ms;
    uint32_t guard_ticks = ((uint32_t)retrigger_guard_ms + 9UL) / 10UL;
    if (!accepted_inference_exists || ((now - last_accepted_inference_tick) >= guard_ticks))
    {
        accepted_inference_exists = true;
        last_accepted_inference_tick = now;
        return true;
    }
    return false;
}

static uint32_t elapsed_systick_us(uint32_t start, uint32_t end)
{
    uint32_t cycles = (start >= end) ? (start - end) :
        (start + APAN_SYSTICK_RELOAD + 1UL - end);
    return (cycles + (APAN_CPU_CLOCK_HZ / 2000000UL)) /
           (APAN_CPU_CLOCK_HZ / 1000000UL);
}

static void put_three_digits(char *destination, uint16_t value)
{
    destination[0] = (char)('0' + ((value / 100U) % 10U));
    destination[1] = (char)('0' + ((value / 10U) % 10U));
    destination[2] = (char)('0' + (value % 10U));
}

/* The model class is zero based, so three LEDs can represent all eight
   areas without overflow: area 1 = 000 through area 8 = 111.
   On the board LED1 is the left/MSB and LED3 is the right/LSB. */
static void display_area_on_leds(uint8_t class_id)
{
    if ((class_id & 0x04U) != 0U) { smpl_onLED1(); }
    else { smpl_offLED1(); }
    if ((class_id & 0x02U) != 0U) { smpl_onLED2(); }
    else { smpl_offLED2(); }
    if ((class_id & 0x01U) != 0U) { smpl_onLED3(); }
    else { smpl_offLED3(); }
}

static void display_inference_result(void)
{
    char first_line[17] = "X000 Y000 AREA0 ";
    char second_line[17] = "INFER 000.00ms  ";
    uint16_t x_mm = (uint16_t)((lcd_class_id % 4U) * 100U + 50U);
    uint16_t y_mm = (uint16_t)((lcd_class_id / 4U) * 100U + 50U);
    uint32_t hundredths_ms = (lcd_inference_us + 5UL) / 10UL;

    if (hundredths_ms > 99999UL) { hundredths_ms = 99999UL; }
    put_three_digits(&first_line[1], x_mm);
    put_three_digits(&first_line[6], y_mm);
    first_line[14] = (char)('1' + lcd_class_id);
    put_three_digits(&second_line[6], (uint16_t)(hundredths_ms / 100UL));
    second_line[10] = (char)('0' + ((hundredths_ms / 10UL) % 10UL));
    second_line[11] = (char)('0' + (hundredths_ms % 10UL));
    (void)LcdDraw(LCD_START_OF_FIRST_LINE, first_line);
    (void)LcdDraw(LCD_START_OF_SECOND_LINE, second_line);
}

static void text_transmit_complete(uint32_t count, uint16_t error_status)
{
    (void)count;
    (void)error_status;
    transmit_busy = false;
    /* Uart1Write replaces the interrupt-enable register with TX-only bits. */
    Uart1StartReadByte(receive_byte);
}

static void chunk_transmit_complete(uint32_t count, uint16_t error_status)
{
    (void)count;
    (void)error_status;
    transmit_busy = false;
    collection_chunk_index++;
}

static void inference_transmit_complete(uint32_t count, uint16_t error_status)
{
    (void)count;
    (void)error_status;
    transmit_busy = false;
    inference_telemetry_pending = false;
    ApanCaptureReleaseEvent(&capture);
    collector_stopped = true;
    Uart1StartReadByte(receive_byte);
}

static void instrument_transmit_complete(uint32_t count, uint16_t error_status)
{
    (void)count;
    (void)error_status;
    transmit_busy = false;
    instrument_transmit_done = true;
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
        case APAN_MESSAGE_SET_MODE:
            command = COMMAND_SET_MODE;
            pending_mode = (frame->payload_size == 1U) ? frame->payload[0] : 0xFFU;
            break;
        case APAN_MESSAGE_SET_CONFIG:
            command = COMMAND_SET_CONFIG;
            pending_retrigger_guard_ms = (frame->payload_size == 2U) ?
                (uint16_t)(frame->payload[0] | ((uint16_t)frame->payload[1] << 8)) : 0xFFFFU;
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
    uint16_t chunk_count;

    if ((event == NULL) || transmit_busy)
    {
        return;
    }
    chunk_count = (uint16_t)((event->sample_count + APAN_EVENT_SAMPLES - 1U) /
                             APAN_EVENT_SAMPLES);
    if (collection_chunk_index >= chunk_count)
    {
        ApanCaptureReleaseEvent(&capture);
        collection_chunk_index = 0U;
        collection_event_id++;
        collector_stopped = true;
        Uart1StartReadByte(receive_byte);
        return;
    }
    encoded_size = ApanProtocolEncodeEventChunk(
        event, collection_event_id, collection_chunk_index, sequence++, 0U,
        transmit_buffer, sizeof(transmit_buffer));
    if (encoded_size == 0U)
    {
        ApanCaptureReleaseEvent(&capture);
        collection_chunk_index = 0U;
        collection_event_id++;
        collector_stopped = true;
        Uart1StartReadByte(receive_byte);
        return;
    }
    transmit_busy = true;
    Uart1Write(transmit_buffer, (uint32_t)encoded_size, chunk_transmit_complete);
}

static void send_inference_result(void)
{
    const ApanEvent *event = ApanCaptureGetEvent(&capture);
    float output[APAN_INFERENCE_OUTPUT_COUNT];
    uint8_t payload[36];
    uint8_t class_id;
    uint8_t index;
    size_t encoded_size;
    uint32_t inference_start;
    uint32_t inference_end;

    if ((event == NULL) || transmit_busy) { return; }
    if (inference_telemetry_pending)
    {
        encoded_size = ApanProtocolEncodeInferenceEvent(
            event, inference_class_id, inference_outputs, inference_sequence, 0U,
            transmit_buffer, sizeof(transmit_buffer));
        if (encoded_size > 0U)
        {
            transmit_busy = true;
            Uart1Write(transmit_buffer, (uint32_t)encoded_size,
                       inference_transmit_complete);
            return;
        }
        inference_telemetry_pending = false;
        ApanCaptureReleaseEvent(&capture);
        collector_stopped = true;
        Uart1StartReadByte(receive_byte);
        return;
    }
    inference_start = SysTick->VAL;
    if (!ApanInferencePredict(event, output, &class_id))
    {
        payload[0] = APAN_MESSAGE_AI_RESULT;
        payload[1] = 4U;
        write_protocol(APAN_MESSAGE_NACK, sequence++, payload, 2U);
    }
    else
    {
        inference_end = SysTick->VAL;
        if ((operating_mode == APAN_MODE_INSTRUMENT) && !accept_instrument_inference())
        {
            ApanCaptureReleaseEvent(&capture);
            if (!collector_stopped) { SensorStart(); }
            return;
        }
        lcd_class_id = class_id;
        lcd_inference_us = elapsed_systick_us(inference_start, inference_end);
        lcd_result_pending = true;
        display_area_on_leds(class_id);
        /* Send the 36-byte class result before the 1 kB waveform.  At
           115200 bit/s this makes the instrument react after roughly one
           5 ms result frame instead of waiting about 95 ms for telemetry. */
        inference_class_id = class_id;
        memcpy(inference_outputs, output, sizeof(inference_outputs));
        inference_sequence = sequence++;
        inference_telemetry_pending = (operating_mode != APAN_MODE_INSTRUMENT);
        payload[0] = 0xFFU;
        payload[1] = class_id;
        payload[2] = 0U;
        payload[3] = 0U;
        for (index = 0U; index < APAN_INFERENCE_OUTPUT_COUNT; index++)
        {
            union { float value; uint32_t bits; } packed;
            uint8_t offset = (uint8_t)(4U + index * 4U);
            packed.value = output[index];
            payload[offset] = (uint8_t)packed.bits;
            payload[offset + 1U] = (uint8_t)(packed.bits >> 8);
            payload[offset + 2U] = (uint8_t)(packed.bits >> 16);
            payload[offset + 3U] = (uint8_t)(packed.bits >> 24);
        }
        if (operating_mode == APAN_MODE_INSTRUMENT)
        {
            encoded_size = ApanProtocolEncodeFrame(
                APAN_MESSAGE_AI_RESULT, 0U, inference_sequence, 0U,
                payload, 36U, transmit_buffer, sizeof(transmit_buffer));
            if (encoded_size > 0U)
            {
                transmit_busy = true;
                Uart1Write(transmit_buffer, (uint32_t)encoded_size,
                           instrument_transmit_complete);
                return;
            }
            ApanCaptureReleaseEvent(&capture);
            collector_stopped = true;
            return;
        }
        write_protocol(APAN_MESSAGE_AI_RESULT, inference_sequence, payload, 36U);
        return;
    }
    ApanCaptureReleaseEvent(&capture);
    collector_stopped = true;
}

static void sensor_block_ready(void)
{
    AI_CONTEXT *context = SensorGetAiContextDataStored();
    if (force_capture)
    {
        (void)ApanCaptureForceFeed(&capture, context->LogInfo.InputData,
                                   AI_CONTEXT_INPUT_SOURCE_SIZE);
        if (ApanCaptureEventReady(&capture)) { force_capture = false; }
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
    /* Inference and UART transmission run in the main loop, never in the
       sensor callback.  This keeps the software interrupt bounded. */
}

void ApanCollectorAppInitialize(void)
{
    const ApanCaptureConfig capture_config = {
        DEFAULT_JERK_THRESHOLD,
        DEFAULT_LEVEL_THRESHOLD,
        DEFAULT_CONFIRM_THRESHOLD,
        DEFAULT_CONFIRM_SAMPLES
    };

    /* KX134: use MEMS, Z axis, ODR code 15 = 25.6 kHz, 512-sample blocks.
       The vendor Kx134Acc driver is installed with GSEL=0x10 (32 g). */
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
    ApanInferenceInitialize();
    PeriodicHandler10msSetCallBack(app_periodic_10ms);
    SysTick->LOAD = APAN_SYSTICK_RELOAD;
    SysTick->VAL = 0UL;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
    SensorInitialize();
    SensorSetAiContextForStoring(&sensor_context[0], &sensor_context[1]);
    SoftwareInterruptSetCallback(sensor_block_ready);
    Uart1StartReadByte(receive_byte);
    collector_stopped = true;
    operating_mode = APAN_MODE_COLLECT;
    inference_telemetry_pending = false;
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

    if (instrument_transmit_done)
    {
        instrument_transmit_done = false;
        ApanCaptureReleaseEvent(&capture);
        /* Instrument mode omits waveform telemetry and immediately rearms. */
        if ((operating_mode == APAN_MODE_INSTRUMENT) && !collector_stopped)
        {
            SensorStart();
        }
    }
    /* Draw only after the priority AI_RESULT frame has left the UART.  In
       instrument mode the sensor is already rearmed, so LCD I2C traffic does
       not delay the first sound or create an extra dead interval. */
    if (lcd_result_pending && !transmit_busy)
    {
        lcd_result_pending = false;
        display_inference_result();
    }
    if (ApanCaptureEventReady(&capture) && !transmit_busy)
    {
        if (operating_mode != APAN_MODE_COLLECT) { send_inference_result(); }
        else { send_ready_event(); }
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
                uint16_t configured_samples =
                    (operating_mode != APAN_MODE_COLLECT) ?
                    APAN_INFERENCE_SAMPLES : APAN_COLLECTION_SAMPLES;
                payload[0] = state;
                payload[1] = ui_status;
                if ((PORT5->P5DO & (1UL << 4U)) != 0U) { payload[1] |= 0x02U; }
                if ((PORT5->P5DO & (1UL << 5U)) != 0U) { payload[1] |= 0x04U; }
                if ((PORT5->P5DO & (1UL << 6U)) != 0U) { payload[1] |= 0x08U; }
                payload[2] = (uint8_t)configured_samples;
                payload[3] = (uint8_t)(configured_samples >> 8);
                payload[4] = force_capture ? 0U : (uint8_t)APAN_PRETRIGGER_SAMPLES;
                payload[5] = force_capture ? 0U : (uint8_t)(APAN_PRETRIGGER_SAMPLES >> 8);
                payload[6] = 0x00U; payload[7] = 0x64U;
                payload[8] = 0x00U; payload[9] = 0x00U; /* 25600 */
                payload[10] = operating_mode;
                write_protocol(APAN_MESSAGE_STATUS, request_sequence, payload, 11U);
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
            if ((operating_mode != APAN_MODE_COLLECT) || force_capture ||
                ApanCaptureEventReady(&capture))
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
                /* A stopped interval is not contiguous sensor time.  Discard
                   the previous event tail/partial event so the next trigger's
                   64 pre-trigger samples all belong to this arming period. */
                ApanCaptureReset(&capture);
                collector_stopped = false;
                SensorStart();
            }
            payload[0] = request_type;
            write_protocol(APAN_MESSAGE_ACK, request_sequence, payload, 1U);
            break;
        case COMMAND_STOP:
            SensorStop();
            ApanCaptureReset(&capture);
            collection_chunk_index = 0U;
            collector_stopped = true;
            payload[0] = request_type;
            write_protocol(APAN_MESSAGE_ACK, request_sequence, payload, 1U);
            break;
        case COMMAND_AI_SELFTEST:
        {
            float output[APAN_AI_OUTPUT_COUNT];
            uint8_t class_id;
            uint8_t index;
            bool result_ok = false;
            if (collector_stopped)
            {
                result_ok = ApanAiSelfTestRun(pending_case_id, output, &class_id);
            }
            /* The self-test replaces the accelerator's global alpha. */
            ApanInferenceInitialize();
            if (!result_ok)
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
        case COMMAND_SET_MODE:
            if (!collector_stopped || force_capture || ApanCaptureEventReady(&capture) ||
                (pending_mode > APAN_MODE_INSTRUMENT))
            {
                payload[0] = request_type;
                payload[1] = (pending_mode > APAN_MODE_INSTRUMENT) ? 2U : 1U;
                write_protocol(APAN_MESSAGE_NACK, request_sequence, payload, 2U);
            }
            else
            {
                operating_mode = pending_mode;
                (void)ApanCaptureSetTargetSamples(
                    &capture,
                    (operating_mode != APAN_MODE_COLLECT) ?
                    APAN_INFERENCE_SAMPLES : APAN_COLLECTION_SAMPLES);
                payload[0] = request_type;
                payload[1] = operating_mode;
                write_protocol(APAN_MESSAGE_ACK, request_sequence, payload, 2U);
            }
            break;
        case COMMAND_SET_CONFIG:
            if (pending_retrigger_guard_ms > 500U)
            {
                payload[0] = request_type;
                payload[1] = 2U;
                write_protocol(APAN_MESSAGE_NACK, request_sequence, payload, 2U);
            }
            else
            {
                retrigger_guard_ms = pending_retrigger_guard_ms;
                accepted_inference_exists = false;
                payload[0] = request_type;
                payload[1] = (uint8_t)retrigger_guard_ms;
                payload[2] = (uint8_t)(retrigger_guard_ms >> 8);
                write_protocol(APAN_MESSAGE_ACK, request_sequence, payload, 3U);
            }
            break;
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
