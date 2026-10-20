#include "apan_odl_experiment.h"

#if APAN_ODL_EXPERIMENT

#include <stddef.h>
#include <string.h>

#define ODL_DISABLE_RAND_GENERATOR_ALPHA
#include "solistAi.h"
#include "apan_12class_model.h"
#include "apan_calibration.h"
#include "apan_inference.h"
#include "apan_odl_experiment_data.h"
#include "mcu.h"
#include "smpl_common.h"
#include "wdt.h"

#define AI_INSTANCE (1U)
#define AI_BUSY_LIMIT (65535UL)
#define HIDDEN (APAN_MODEL_HIDDEN_SIZE)
#define OUTPUTS (APAN_MODEL_OUTPUT_SIZE)

static uint16_t cursor;
static uint16_t total_updates;


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

bool ApanOdlExperimentBegin(void)
{
    /* CPU working beta = factory beta, P = P0; the AxlCORE gets the same beta and P0. */
    if (!ApanInferenceCalibrationBegin()) { return false; }
    ApanInferenceLoadEnginePrior();
    cursor = 0U;
    total_updates = 0U;
    ApanInferenceSetExperimentLive(true);
    return true;
}

static bool train_pair(const uint16_t *input, uint8_t target, uint32_t *cycles)
{
    bfloat16 target_vector[OUTPUTS];
    float hidden[HIDDEN];
    uint32_t start;
    uint32_t end;
    uint16_t i;
    for (i = 0U; i < OUTPUTS; i++) { target_vector[i] = (bfloat16)((i == target) ? 0x3F80 : 0x0000); }
    start = SysTick->VAL;
    ODL_StartTrain(AI_INSTANCE, (const bfloat16 *)input, target_vector);
    if (!wait_engine()) { return false; }
    end = SysTick->VAL;
    if (cycles != NULL)
    {
        *cycles += (start >= end) ? (start - end) : (start + SysTick->LOAD + 1UL - end);
    }
    ApanCalibrationHidden((const int16_t *)input, apan_model_alpha, APAN_MODEL_INPUT_SIZE, hidden);
    return ApanCalibrationUpdate(hidden, target);
}

bool ApanOdlExperimentTrainLive(const uint16_t *input, uint8_t target)
{
    return train_pair(input, target, NULL);
}

bool ApanOdlExperimentRun(uint8_t count, uint16_t *updates, uint32_t *engine_cycles)
{
    uint32_t cycles = 0UL;
    uint8_t n;
    for (n = 0U; n < count; n++)
    {
        if (!train_pair(apan_odl_experiment_inputs[cursor], apan_odl_experiment_targets[cursor], &cycles))
        {
            return false;
        }
        cursor = (uint16_t)((cursor + 1U) % APAN_ODL_EXPERIMENT_COUNT);
        total_updates++;
    }
    *updates = total_updates;
    *engine_cycles = cycles;
    return true;
}

uint8_t ApanOdlExperimentDumpEngine(uint8_t part, uint8_t *out)
{
    if (part >= APAN_ODL_EXPERIMENT_DUMP_ENGINE_PARTS) { return 0U; }
    ODL_GetWeightBeta(out, AI_INSTANCE, (uint32_t)part * 8U * OUTPUTS * 2U, 8U * OUTPUTS * 2U);
    return (uint8_t)(8U * OUTPUTS * 2U);
}

uint8_t ApanOdlExperimentDumpCpu(uint8_t part, uint8_t *out)
{
    float row[OUTPUTS];
    uint8_t r;
    if (part >= APAN_ODL_EXPERIMENT_DUMP_CPU_PARTS) { return 0U; }
    for (r = 0U; r < 4U; r++)
    {
        if (!ApanCalibrationWorkingBeta((uint16_t)(part * 4U + r), row)) { return 0U; }
        memcpy(&out[(uint32_t)r * OUTPUTS * 4U], row, sizeof(row));
        wdt_clear();
    }
    return (uint8_t)(4U * OUTPUTS * 4U);
}

#endif
