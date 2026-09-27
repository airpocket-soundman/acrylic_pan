#include "apan_inference.h"

#include <stddef.h>
#include <stdint.h>

#define ODL_DISABLE_RAND_GENERATOR_ALPHA
#include "solistAi.h"
#include "apan_12class_model.h"
#include "apan_calibration.h"
#include "smpl_common.h"
#include "wdt.h"

#define AI_INSTANCE (1U)
#define AI_BUSY_LIMIT (65535UL)

static bfloat16 model_input[APAN_MODEL_INPUT_SIZE];
static bool initialized;
static bool calibration_initialized;
static bool model_input_valid;

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

/* Factory beta, or the saved/working calibration held in the FRAM. */
static void load_active_beta(void)
{
    int16_t calibrated[APAN_MODEL_OUTPUT_SIZE];
    uint16_t row;
    for (row = 0U; row < APAN_MODEL_HIDDEN_SIZE; row++)
    {
        const int16_t *beta = ApanCalibrationActiveBetaRow(row, calibrated) ?
            calibrated : &apan_model_beta[row * APAN_MODEL_OUTPUT_SIZE];
        ODL_SetWeightBeta(beta, AI_INSTANCE,
                          (uint32_t)row * APAN_MODEL_OUTPUT_SIZE * 2U,
                          APAN_MODEL_OUTPUT_SIZE * 2U);
    }
}

void ApanInferenceInitialize(void)
{
    ODL_Parameters parameters = {
        .inputSize = APAN_MODEL_INPUT_SIZE,
        .hiddenSize = APAN_MODEL_HIDDEN_SIZE,
        .outputSize = APAN_MODEL_OUTPUT_SIZE,
        .forgettingFactor = (bfloat16)0x3F80,
        .activationFunction = APAN_MODEL_ACTIVATION,
        .lossFunction = APAN_MODEL_LOSS,
        .seed = APAN_MODEL_SEED,
        .scaleAlpha = (bfloat16)APAN_MODEL_SCALE_ALPHA_BF16,
        .scaleGamma = 0,
        .leakRate = 0
    };

    if (!calibration_initialized)
    {
        (void)ApanCalibrationInitialize(apan_model_beta);
        calibration_initialized = true;
    }
    smpl_enablePeripheral(AI_PERI);
    ODL_Initialize(AI_INSTANCE, &parameters);
    ODL_Reset(AI_INSTANCE);
    ODL_SetWeightAlpha(apan_model_alpha, 0U, sizeof(apan_model_alpha));
    load_active_beta();
    model_input_valid = false;
    initialized = true;
}

bool ApanInferencePredict(const ApanEvent *event,
                          float output[APAN_INFERENCE_OUTPUT_COUNT],
                          uint8_t *class_id)
{
    bfloat16 raw_output[APAN_MODEL_OUTPUT_SIZE];
    int32_t baseline_sum = 0L;
    float baseline;
    float peak = 1.0F;
    uint32_t busy_count = 0UL;
    uint16_t index;
    uint8_t best = 0U;

    if ((event == NULL) || (output == NULL) || (class_id == NULL) ||
        (event->sample_count != APAN_MODEL_SAMPLE_COUNT) ||
        (event->trigger_index != APAN_MODEL_TRIGGER_INDEX) ||
        (APAN_MODEL_INPUT_SIZE != APAN_MODEL_TIME_FEATURE_COUNT) ||
        (APAN_MODEL_OUTPUT_SIZE != APAN_INFERENCE_OUTPUT_COUNT))
    {
        return false;
    }
    if (!initialized)
    {
        ApanInferenceInitialize();
    }

    for (index = 0U; index < APAN_MODEL_TRIGGER_INDEX; index++)
    {
        baseline_sum += event->samples[index];
    }
    baseline = (float)baseline_sum / (float)APAN_MODEL_TRIGGER_INDEX;
    for (index = APAN_MODEL_TRIGGER_INDEX; index < APAN_MODEL_SAMPLE_COUNT; index++)
    {
        float value = (float)event->samples[index] - baseline;
        if (value < 0.0F) { value = -value; }
        if (value > peak) { peak = value; }
    }
    for (index = 0U; index < APAN_MODEL_INPUT_SIZE; index++)
    {
        uint16_t sample_index = (uint16_t)(APAN_MODEL_TRIGGER_INDEX +
                                           apan_model_time_indices[index]);
        float normalized = ((float)event->samples[sample_index] - baseline) / peak;
        float standardized = (normalized - apan_model_feature_mean[index]) /
                             apan_model_feature_scale[index];
        model_input[index] = float_to_bfloat16_rne(standardized);
    }

    model_input_valid = true;
    ODL_StartPredict(AI_INSTANCE, model_input, NULL);
    while (ODL_IsBusy() != 0UL)
    {
        if (++busy_count >= AI_BUSY_LIMIT) { return false; }
        wdt_clear();
    }
    ODL_GetResult(AI_INSTANCE, raw_output);
    for (index = 0U; index < APAN_MODEL_OUTPUT_SIZE; index++)
    {
        output[index] = bfloat16_to_float(raw_output[index]);
        if ((index > 0U) && (output[index] > output[best])) { best = (uint8_t)index; }
    }
    *class_id = best;
    return true;
}

bool ApanInferenceCalibrationBegin(void)
{
    if (!ApanCalibrationBegin()) { return false; }
    ApanInferenceInitialize();
    return true;
}

/* Learn the last predicted hit as target, then use the updated beta. */
bool ApanInferenceTrainLast(uint8_t target)
{
    float hidden[APAN_MODEL_HIDDEN_SIZE];
    if (!initialized || !model_input_valid) { return false; }
    ApanCalibrationHidden(model_input, apan_model_alpha, APAN_MODEL_INPUT_SIZE, hidden);
    model_input_valid = false;
    if (!ApanCalibrationUpdate(hidden, target)) { return false; }
    load_active_beta();
    return true;
}
