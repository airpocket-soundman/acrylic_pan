#include "apan_ai_selftest.h"

#include <stddef.h>
#include <stdint.h>

#define ODL_DISABLE_RAND_GENERATOR_ALPHA
#include "solistAi.h"
#include "apan_dummy_model.h"
#include "smpl_common.h"
#include "wdt.h"

#define AI_INSTANCE (0U)
#define AI_BUSY_LIMIT (65535UL)

static bool initialized;

static float bfloat16_to_float(bfloat16 value)
{
    union
    {
        uint32_t bits;
        float value;
    } converted;
    converted.bits = ((uint32_t)(uint16_t)value) << 16;
    return converted.value;
}

void ApanAiSelfTestInitialize(void)
{
    ODL_Parameters parameters = {
        .inputSize = APAN_DUMMY_INPUT_SIZE,
        .hiddenSize = APAN_DUMMY_HIDDEN_SIZE,
        .outputSize = APAN_DUMMY_OUTPUT_SIZE,
        .forgettingFactor = (bfloat16)0x3F80,
        .activationFunction = APAN_DUMMY_ACTIVATION,
        .lossFunction = APAN_DUMMY_LOSS,
        .seed = APAN_DUMMY_SEED,
        .scaleAlpha = (bfloat16)APAN_DUMMY_SCALE_ALPHA_BF16,
        .scaleGamma = 0,
        .leakRate = 0
    };
    uint16_t row;

    smpl_enablePeripheral(AI_PERI);
    ODL_Initialize(AI_INSTANCE, &parameters);
    ODL_Reset(AI_INSTANCE);
    ODL_SetWeightAlpha(apan_dummy_alpha, 0U, sizeof(apan_dummy_alpha));
    for (row = 0U; row < APAN_DUMMY_HIDDEN_SIZE; row++)
    {
        ODL_SetWeightBeta(&apan_dummy_beta[row * APAN_DUMMY_OUTPUT_SIZE],
                          AI_INSTANCE,
                          (uint32_t)row * APAN_DUMMY_OUTPUT_SIZE * 2U,
                          APAN_DUMMY_OUTPUT_SIZE * 2U);
    }
    initialized = true;
}

uint8_t ApanAiSelfTestCaseCount(void)
{
    return (uint8_t)APAN_DUMMY_CASE_COUNT;
}

bool ApanAiSelfTestRun(uint8_t case_id, float output[APAN_AI_OUTPUT_COUNT],
                       uint8_t *class_id)
{
    bfloat16 raw_output[APAN_DUMMY_OUTPUT_SIZE];
    uint32_t busy_count = 0UL;
    uint8_t index;
    uint8_t best = 0U;

    if ((output == NULL) || (class_id == NULL) ||
        (case_id >= APAN_DUMMY_CASE_COUNT) ||
        (APAN_DUMMY_OUTPUT_SIZE != APAN_AI_OUTPUT_COUNT))
    {
        return false;
    }
    if (!initialized)
    {
        ApanAiSelfTestInitialize();
    }

    ODL_StartPredict(AI_INSTANCE, apan_dummy_inputs[case_id], NULL);
    while (ODL_IsBusy() != 0UL)
    {
        if (++busy_count >= AI_BUSY_LIMIT)
        {
            return false;
        }
        wdt_clear();
    }
    ODL_GetResult(AI_INSTANCE, raw_output);
    for (index = 0U; index < APAN_DUMMY_OUTPUT_SIZE; index++)
    {
        output[index] = bfloat16_to_float(raw_output[index]);
        if ((index > 0U) && (output[index] > output[best]))
        {
            best = index;
        }
    }
    *class_id = best;
    return true;
}
