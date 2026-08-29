#include "apan_position_inference.h"

#include <stddef.h>
#include <stdint.h>

#include "solistAi.h"
#include "apan_position_probability_model.h"
#include "smpl_common.h"
#include "wdt.h"

#define AI_INSTANCE (1U)
#define AI_BUSY_LIMIT (65535UL)
#define BFLOAT_ONE ((bfloat16)0x3F80)

static bfloat16 model_input[APAN_POSITION_INPUT_SIZE];
static bool initialized;

static void set_beta_head(uint8_t head)
{
    uint16_t row;
    uint16_t column;
    bfloat16 row_buffer[APAN_POSITION_ENGINE_OUTPUT_SIZE];
    uint32_t head_offset = (uint32_t)head * APAN_POSITION_HIDDEN_SIZE *
                           APAN_POSITION_ENGINE_OUTPUT_SIZE;
    for (row = 0U; row < APAN_POSITION_HIDDEN_SIZE; row++)
    {
        uint32_t source_offset = head_offset +
                                 (uint32_t)row * APAN_POSITION_ENGINE_OUTPUT_SIZE;
        for (column = 0U; column < APAN_POSITION_ENGINE_OUTPUT_SIZE; column++)
        {
            row_buffer[column] = (bfloat16)apan_position_beta[source_offset + column];
        }
        ODL_SetWeightBeta(row_buffer, AI_INSTANCE,
                          (uint32_t)row * APAN_POSITION_ENGINE_OUTPUT_SIZE * 2U,
                          APAN_POSITION_ENGINE_OUTPUT_SIZE * 2U);
    }
}

static void initialize_head(uint8_t head)
{
    const ODL_Parameters parameters = {
        .inputSize = APAN_POSITION_INPUT_SIZE,
        .hiddenSize = APAN_POSITION_HIDDEN_SIZE,
        .outputSize = APAN_POSITION_ENGINE_OUTPUT_SIZE,
        .forgettingFactor = (bfloat16)0x3F80,
        .activationFunction = APAN_POSITION_ACTIVATION,
        .lossFunction = APAN_POSITION_LOSS,
        .seed = APAN_POSITION_SEED,
        .scaleAlpha = (bfloat16)APAN_POSITION_SCALE_ALPHA_BF16,
        .scaleGamma = 0,
        .leakRate = 0
    };
    smpl_enablePeripheral(AI_PERI);
    ODL_Initialize(AI_INSTANCE, &parameters);
    ODL_Reset(AI_INSTANCE);
    set_beta_head(head);
}

static bfloat16 float_to_bfloat16_rne(float value)
{
    union { float value; uint32_t bits; } converted;
    uint32_t rounding;
    converted.value = value;
    rounding = 0x7FFFUL + ((converted.bits >> 16) & 1UL);
    return (bfloat16)((converted.bits + rounding) >> 16);
}

static float bfloat16_to_float(bfloat16 value)
{
    union { uint32_t bits; float value; } converted;
    converted.bits = ((uint32_t)(uint16_t)value) << 16;
    return converted.value;
}

static bool predict_prepared(float logits[APAN_POSITION_OUTPUT_COUNT],
                             uint8_t *position_id)
{
    bfloat16 raw_output[APAN_POSITION_ENGINE_OUTPUT_SIZE];
    uint16_t index;
    uint8_t head;
    uint8_t best = 0U;

    for (head = 0U; head < APAN_POSITION_HEAD_COUNT; head++)
    {
        uint32_t busy_count = 0UL;
        initialize_head(head);
        ODL_StartPredict(AI_INSTANCE, model_input, NULL);
        while (ODL_IsBusy() != 0UL)
        {
            if (++busy_count >= AI_BUSY_LIMIT) { return false; }
            wdt_clear();
        }
        ODL_GetResult(AI_INSTANCE, raw_output);
        for (index = 0U; index < APAN_POSITION_ENGINE_OUTPUT_SIZE; index++)
        {
            uint16_t output_index = (uint16_t)(
                (uint16_t)head * APAN_POSITION_ENGINE_OUTPUT_SIZE + index);
            logits[output_index] = bfloat16_to_float(raw_output[index]);
            if ((output_index > 0U) && (logits[output_index] > logits[best]))
            {
                best = (uint8_t)output_index;
            }
        }
    }
    *position_id = best;
    return true;
}

void ApanPositionInferenceInitialize(void)
{
    initialize_head(0U);
    initialized = true;
}

bool ApanPositionInferencePredict(const ApanEvent *event,
                                  float logits[APAN_POSITION_OUTPUT_COUNT],
                                  uint8_t *position_id)
{
    int32_t baseline_sum = 0L;
    float baseline;
    float peak = 1.0F;
    uint16_t index;

    if ((event == NULL) || (logits == NULL) || (position_id == NULL) ||
        (event->sample_count != APAN_INFERENCE_SAMPLES) ||
        (event->trigger_index != APAN_PRETRIGGER_SAMPLES))
    {
        return false;
    }
    if (!initialized) { ApanPositionInferenceInitialize(); }
    for (index = 0U; index < APAN_PRETRIGGER_SAMPLES; index++)
    {
        baseline_sum += event->samples[index];
    }
    baseline = (float)baseline_sum / (float)APAN_PRETRIGGER_SAMPLES;
    for (index = APAN_PRETRIGGER_SAMPLES; index < APAN_INFERENCE_SAMPLES; index++)
    {
        float value = (float)event->samples[index] - baseline;
        if (value < 0.0F) { value = -value; }
        if (value > peak) { peak = value; }
    }
    for (index = 0U; index < APAN_POSITION_FEATURE_COUNT; index++)
    {
        uint16_t sample_index = (uint16_t)(APAN_PRETRIGGER_SAMPLES +
                                           apan_position_time_indices[index]);
        float normalized = ((float)event->samples[sample_index] - baseline) / peak;
        float standardized = (normalized - apan_position_feature_mean[index]) /
                             apan_position_feature_scale[index];
        model_input[index] = float_to_bfloat16_rne(standardized);
    }
    model_input[APAN_POSITION_FEATURE_COUNT] = BFLOAT_ONE;
    for (index = APAN_POSITION_FEATURE_COUNT + 1U;
         index < APAN_POSITION_INPUT_SIZE; index++)
    {
        model_input[index] = 0;
    }
    return predict_prepared(logits, position_id);
}

bool ApanPositionInferenceSelfTest(uint8_t case_id,
                                   float logits[APAN_POSITION_OUTPUT_COUNT],
                                   uint8_t *position_id)
{
    uint16_t index;
    uint32_t offset;
    if ((case_id >= APAN_POSITION_GOLDEN_COUNT) || (logits == NULL) ||
        (position_id == NULL))
    {
        return false;
    }
    ApanPositionInferenceInitialize();
    offset = (uint32_t)case_id * APAN_POSITION_INPUT_SIZE;
    for (index = 0U; index < APAN_POSITION_INPUT_SIZE; index++)
    {
        model_input[index] = (bfloat16)apan_position_golden_inputs[offset + index];
    }
    return predict_prepared(logits, position_id);
}

void ApanPositionInferenceSupport(uint8_t position_id, uint16_t *x_mm,
                                  uint16_t *y_mm)
{
    uint8_t safe_id = position_id < APAN_POSITION_OUTPUT_SIZE ? position_id : 0U;
    if (x_mm != NULL) { *x_mm = apan_position_support_xy_mm[safe_id][0]; }
    if (y_mm != NULL) { *y_mm = apan_position_support_xy_mm[safe_id][1]; }
}
