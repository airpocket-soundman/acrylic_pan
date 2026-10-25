#include "apan_position_inference.h"

#include <stddef.h>
#include <stdint.h>

#include "solistAi.h"
#include "apan_calibration.h"
#include "apan_position_probability_model.h"
#include "smpl_common.h"
#include "wdt.h"

#define AI_INSTANCE (1U)
#define AI_BUSY_LIMIT (65535UL)
#define BFLOAT_ONE ((bfloat16)0x3F80)

static bfloat16 model_input[APAN_POSITION_INPUT_SIZE];
static bool initialized;
static bool calibration_initialized;
static bool model_input_valid;
static bool beginning;
static uint8_t calibration_kind = APAN_POSITION_CALIBRATION_QUICK;

static bfloat16 float_to_bfloat16_rne(float value);

static void load_active_beta(void)
{
    uint16_t row;
    bfloat16 row_buffer[APAN_POSITION_ENGINE_OUTPUT_SIZE];
    for (row = 0U; row < APAN_POSITION_HIDDEN_SIZE; row++)
    {
        const int16_t *beta = ApanCalibrationActiveBetaRow(row, (int16_t *)row_buffer) ?
            (const int16_t *)row_buffer :
            &apan_position_beta[(uint32_t)row * APAN_POSITION_ENGINE_OUTPUT_SIZE];
        ODL_SetWeightBeta(beta, AI_INSTANCE,
                          (uint32_t)row * APAN_POSITION_ENGINE_OUTPUT_SIZE * 2U,
                          APAN_POSITION_ENGINE_OUTPUT_SIZE * 2U);
    }
}

static void load_saved_beta(void)
{
    uint16_t row;
    bfloat16 row_buffer[APAN_POSITION_ENGINE_OUTPUT_SIZE];
    for (row = 0U; row < APAN_POSITION_HIDDEN_SIZE; row++)
    {
        if (!ApanCalibrationSavedBetaRow(row, (int16_t *)row_buffer)) { return; }
        ODL_SetWeightBeta((const int16_t *)row_buffer, AI_INSTANCE,
                          (uint32_t)row * APAN_POSITION_ENGINE_OUTPUT_SIZE * 2U,
                          APAN_POSITION_ENGINE_OUTPUT_SIZE * 2U);
    }
}

static bool wait_engine(void)
{
    uint32_t busy_count = 0UL;
    while (ODL_IsBusy() != 0UL)
    {
        if (++busy_count >= AI_BUSY_LIMIT) { return false; }
        wdt_clear();
    }
    return true;
}

static void load_engine_prior(void)
{
    bfloat16 row_buffer[APAN_POSITION_HIDDEN_SIZE];
    uint16_t row;
    uint16_t column;
    for (row = 0U; row < APAN_POSITION_HIDDEN_SIZE; row++)
    {
        for (column = 0U; column < APAN_POSITION_HIDDEN_SIZE; column++)
        {
            row_buffer[column] = float_to_bfloat16_rne(
                apan_calibration_p0[(uint32_t)row * APAN_POSITION_HIDDEN_SIZE + column]);
        }
        ODL_SetWeightP(row_buffer, AI_INSTANCE,
                       (uint32_t)row * APAN_POSITION_HIDDEN_SIZE * 2U,
                       APAN_POSITION_HIDDEN_SIZE * 2U);
        wdt_clear();
    }
}

static void initialize_model(void)
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
    load_active_beta();
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
    uint8_t best = 0U;
    ODL_StartPredict(AI_INSTANCE, model_input, NULL);
    if (!wait_engine()) { return false; }
    ODL_GetResult(AI_INSTANCE, raw_output);
    for (index = 0U; index < APAN_POSITION_ENGINE_OUTPUT_SIZE; index++)
    {
        logits[index] = bfloat16_to_float(raw_output[index]);
    }
    /* First maximum in output order, as before. */
    for (index = 1U; index < APAN_POSITION_OUTPUT_COUNT; index++)
    {
        if (logits[index] > logits[best]) { best = (uint8_t)index; }
    }
    *position_id = best;
    return true;
}

void ApanPositionInferenceInitialize(void)
{
    if (!calibration_initialized)
    {
        (void)ApanCalibrationInitialize(apan_position_beta);
        calibration_initialized = true;
    }
#if APAN_CALIBRATION_ENGINE
    if (!beginning) { ApanCalibrationDiscard(); }
#endif
    initialize_model();
    model_input_valid = false;
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
    model_input_valid = true;
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
    if (!predict_prepared(logits, position_id)) { return false; }
    return true;
}

void ApanPositionInferenceSupport(uint8_t position_id, uint16_t *x_mm,
                                  uint16_t *y_mm)
{
    uint8_t safe_id = position_id < APAN_POSITION_OUTPUT_SIZE ? position_id : 0U;
    if (x_mm != NULL) { *x_mm = apan_position_support_xy_mm[safe_id][0]; }
    if (y_mm != NULL) { *y_mm = apan_position_support_xy_mm[safe_id][1]; }
}

bool ApanPositionCalibrationBegin(uint8_t argument)
{
    const uint8_t kind = argument & (uint8_t)~APAN_POSITION_CALIBRATION_CONTINUE;
    const bool keep_saved = (argument & APAN_POSITION_CALIBRATION_CONTINUE) != 0U;
    if ((kind != APAN_POSITION_CALIBRATION_QUICK) &&
        (kind != APAN_POSITION_CALIBRATION_PRECISE)) { return false; }
    calibration_kind = kind;
#if APAN_CALIBRATION_ENGINE
    if (keep_saved ? !ApanCalibrationBeginEngineContinue(kind) : !ApanCalibrationBeginEngine(kind)) { return false; }
    beginning = true;
    ApanPositionInferenceInitialize();
    beginning = false;
    load_engine_prior();
    if (keep_saved) { load_saved_beta(); }
    return true;
#else
    if (keep_saved || !ApanCalibrationBegin(kind)) { return false; }
    ApanPositionInferenceInitialize();
    return true;
#endif
}

#if APAN_CALIBRATION_ENGINE
static bool read_engine_row(uint16_t row, int16_t beta_bf16[APAN_POSITION_OUTPUT_COUNT])
{
    ODL_GetWeightBeta(beta_bf16, AI_INSTANCE,
                      (uint32_t)row * APAN_POSITION_OUTPUT_COUNT * 2U,
                      APAN_POSITION_OUTPUT_COUNT * 2U);
    return true;
}
#endif

bool ApanPositionCalibrationCommit(void)
{
#if APAN_CALIBRATION_ENGINE
    return ApanCalibrationCommitEngine(read_engine_row);
#else
    return ApanCalibrationCommit();
#endif
}

bool ApanPositionTrainLast(uint8_t target)
{
#if APAN_CALIBRATION_ENGINE
    bfloat16 target_vector[APAN_POSITION_OUTPUT_COUNT];
#else
    float cpu_target[APAN_POSITION_OUTPUT_COUNT];
#endif
    uint16_t index;
    if (!initialized || !model_input_valid) { return false; }
    if (((calibration_kind == APAN_POSITION_CALIBRATION_QUICK) && (target >= 12U)) ||
        ((calibration_kind == APAN_POSITION_CALIBRATION_PRECISE) &&
         (target >= APAN_POSITION_OUTPUT_COUNT))) { return false; }
    for (index = 0U; index < APAN_POSITION_OUTPUT_COUNT; index++)
    {
        float value = 0.0F;
        if (calibration_kind == APAN_POSITION_CALIBRATION_PRECISE)
        {
            value = (index == target) ? 1.0F : 0.0F;
        }
        else
        {
            uint16_t x = apan_position_support_xy_mm[index][0];
            uint16_t y = apan_position_support_xy_mm[index][1];
            uint8_t area = (uint8_t)((y / 100U) * 4U + (x / 100U));
            if (area == target)
            {
                value = (((x % 100U) == 50U) && ((y % 100U) == 50U)) ? 0.6F : 0.1F;
            }
        }
#if APAN_CALIBRATION_ENGINE
        target_vector[index] = float_to_bfloat16_rne(value);
#else
        cpu_target[index] = value;
#endif
    }
    model_input_valid = false;
#if APAN_CALIBRATION_ENGINE
    ODL_StartTrain(AI_INSTANCE, model_input, target_vector);
    return wait_engine() && ApanCalibrationNoteTrained();
#else
    {
        float hidden[APAN_POSITION_HIDDEN_SIZE];
        ApanCalibrationHidden(model_input, apan_position_alpha, APAN_POSITION_INPUT_SIZE, hidden);
        if (!ApanCalibrationUpdateTarget(hidden, cpu_target)) { return false; }
        load_active_beta();
        return true;
    }
#endif
}
