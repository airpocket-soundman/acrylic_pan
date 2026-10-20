#include "apan_inference.h"

#include <stddef.h>
#include <stdint.h>

#define ODL_DISABLE_RAND_GENERATOR_ALPHA
#include "solistAi.h"
#include "apan_12class_model.h"
#include "apan_calibration.h"
#include "apan_odl_experiment.h"
#include "smpl_common.h"
#include "wdt.h"

#define AI_INSTANCE (1U)
#define AI_BUSY_LIMIT (65535UL)

static bfloat16 model_input[APAN_MODEL_INPUT_SIZE];
static bool initialized;
static bool calibration_initialized;
static bool model_input_valid;
static bool beginning;   /* ApanInferenceCalibrationBegin is initialising the AxlCORE itself */
#if APAN_ODL_EXPERIMENT
static bool experiment_live;

void ApanInferenceSetExperimentLive(bool live) { experiment_live = live; }
const uint16_t *ApanInferenceLastInput(void) { return (const uint16_t *)model_input; }
#endif

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

void ApanInferenceLoadEnginePrior(void)
{
    bfloat16 prior_row[APAN_MODEL_HIDDEN_SIZE];
    uint16_t row;
    uint16_t column;
    for (row = 0U; row < APAN_MODEL_HIDDEN_SIZE; row++)
    {
        for (column = 0U; column < APAN_MODEL_HIDDEN_SIZE; column++)
        {
            prior_row[column] = float_to_bfloat16_rne(
                apan_calibration_p0[(uint32_t)row * APAN_MODEL_HIDDEN_SIZE + column]);
        }
        ODL_SetWeightP(prior_row, AI_INSTANCE, (uint32_t)row * APAN_MODEL_HIDDEN_SIZE * 2U,
                       APAN_MODEL_HIDDEN_SIZE * 2U);
        wdt_clear();
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
#if APAN_CALIBRATION_ENGINE
    /* The working beta lives in the AxlCORE and is lost by this initialisation. */
    if (!beginning) { ApanCalibrationDiscard(); }
#endif
    smpl_enablePeripheral(AI_PERI);
    ODL_Initialize(AI_INSTANCE, &parameters);
    ODL_Reset(AI_INSTANCE);
#if !APAN_CALIBRATION_ENGINE
    /* Only the CPU OS-ELM needs the alpha copy; the AxlCORE generates the same alpha from the seed and scale above
       (like the position model, whose header carries beta only). */
    ODL_SetWeightAlpha(apan_model_alpha, 0U, sizeof(apan_model_alpha));
#endif
    load_active_beta();
    model_input_valid = false;
#if APAN_ODL_EXPERIMENT
    experiment_live = false;
#endif
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

bool ApanInferenceSelfTest(uint8_t case_index, float output[APAN_INFERENCE_OUTPUT_COUNT], uint8_t *class_id)
{
    bfloat16 raw_output[APAN_MODEL_OUTPUT_SIZE];
    uint16_t index;
    uint8_t best = 0U;
    if ((output == NULL) || (class_id == NULL) || (case_index >= 12U)) { return false; }
    if (!initialized) { ApanInferenceInitialize(); }
    ODL_StartPredict(AI_INSTANCE, (const bfloat16 *)apan_model_golden_inputs[case_index], NULL);
    if (!wait_engine()) { return false; }
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
#if APAN_CALIBRATION_ENGINE
    /* Factory beta (the working source loads none from the FRAM) and P0 go into the AxlCORE. */
    if (!ApanCalibrationBeginEngine()) { return false; }
    beginning = true;
    ApanInferenceInitialize();
    beginning = false;
    ApanInferenceLoadEnginePrior();
    return true;
#else
    if (!ApanCalibrationBegin()) { return false; }
    ApanInferenceInitialize();
    return true;
#endif
}

#if APAN_CALIBRATION_ENGINE
static bool read_engine_row(uint16_t row, int16_t beta_bf16[APAN_MODEL_OUTPUT_SIZE])
{
    ODL_GetWeightBeta(beta_bf16, AI_INSTANCE, (uint32_t)row * APAN_MODEL_OUTPUT_SIZE * 2U,
                      APAN_MODEL_OUTPUT_SIZE * 2U);
    return true;
}
#endif

bool ApanInferenceCalibrationCommit(void)
{
#if APAN_CALIBRATION_ENGINE
    return ApanCalibrationCommitEngine(read_engine_row);
#else
    return ApanCalibrationCommit();
#endif
}

/* Learn the last predicted hit as target, then use the updated beta. */
bool ApanInferenceTrainLast(uint8_t target)
{
    if (!initialized || !model_input_valid) { return false; }
#if APAN_ODL_EXPERIMENT
    if (experiment_live)
    {
        model_input_valid = false;
        return ApanOdlExperimentTrainLive((const uint16_t *)model_input, target);
    }
#endif
#if APAN_CALIBRATION_ENGINE
    {
        bfloat16 target_vector[APAN_MODEL_OUTPUT_SIZE];
        uint16_t index;
        if (target >= APAN_MODEL_OUTPUT_SIZE) { return false; }
        for (index = 0U; index < APAN_MODEL_OUTPUT_SIZE; index++)
        {
            target_vector[index] = (bfloat16)((index == target) ? 0x3F80 : 0x0000);
        }
        model_input_valid = false;
        ODL_StartTrain(AI_INSTANCE, model_input, target_vector);
        return wait_engine() && ApanCalibrationNoteTrained();
    }
#else
    {
        float hidden[APAN_MODEL_HIDDEN_SIZE];
        ApanCalibrationHidden(model_input, apan_model_alpha, APAN_MODEL_INPUT_SIZE, hidden);
        model_input_valid = false;
        if (!ApanCalibrationUpdate(hidden, target)) { return false; }
        load_active_beta();
        return true;
    }
#endif
}
