#include "apan_xy_inference.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "solistAi.h"
#include "apan_xy_staged_model.h"
#include "wdt.h"

#define XY_CONSTANT_ONE ((bfloat16)0x3F80)
#define XY_ENGINE_INPUT (129U)
#define XY_ENGINE_SIZE (64U)

static bfloat16 stage_input[XY_ENGINE_INPUT];
static bfloat16 stage_output[XY_ENGINE_SIZE];
static bool initialized;

static float bfloat16_to_float(bfloat16 value)
{
    union { uint32_t bits; float value; } converted;
    converted.bits = ((uint32_t)(uint16_t)value) << 16;
    return converted.value;
}

static bfloat16 float_to_bfloat16_rne(float value)
{
    union { float value; uint32_t bits; } converted;
    uint32_t rounding;
    converted.value = value;
    rounding = 0x7FFFUL + ((converted.bits >> 16) & 1UL);
    return (bfloat16)((converted.bits + rounding) >> 16);
}

static void prepare_stage(const bfloat16 *values, uint16_t count)
{
    memset(stage_input, 0, sizeof(stage_input));
    memcpy(stage_input, values, (uint32_t)count * sizeof(bfloat16));
    stage_input[XY_ENGINE_INPUT - 1U] = XY_CONSTANT_ONE;
}

static bool run_stage(const int16_t *alpha)
{
    bfloat16 result[XY_ENGINE_SIZE];
    uint16_t input_index;
    uint16_t output_index;
    for (output_index = 0U; output_index < XY_ENGINE_SIZE; output_index++)
    {
        float sum = 0.0F;
        for (input_index = 0U; input_index < XY_ENGINE_INPUT; input_index++)
        {
            sum += bfloat16_to_float(stage_input[input_index]) *
                   bfloat16_to_float(alpha[(uint32_t)input_index * XY_ENGINE_SIZE +
                                            output_index]);
        }
        if (sum < 0.0F) { sum = 0.0F; }
        result[output_index] = float_to_bfloat16_rne(sum);
        wdt_clear();
    }
    memcpy(stage_output, result, sizeof(stage_output));
    return true;
}

void ApanXyInferenceInitialize(void)
{
    initialized = true;
}

bool ApanXySelfTestRun(uint8_t case_id, float output_xy[2])
{
    const bfloat16 *input;
    if ((output_xy == NULL) || (case_id >= APAN_XY_GOLDEN_CASE_COUNT)) { return false; }
    if (!initialized) { ApanXyInferenceInitialize(); }
    input = &apan_xy_golden_inputs[(uint32_t)case_id * APAN_XY_INPUT_SIZE];

    prepare_stage(input, APAN_XY_INPUT_SIZE);
    if (!run_stage(apan_xy_layer_0_bank_0_alpha)) { return false; }
    prepare_stage(stage_output, XY_ENGINE_SIZE);
    if (!run_stage(apan_xy_layer_1_bank_0_alpha)) { return false; }
    prepare_stage(stage_output, XY_ENGINE_SIZE);
    if (!run_stage(apan_xy_layer_2_bank_0_alpha)) { return false; }
    prepare_stage(stage_output, XY_ENGINE_SIZE);
    if (!run_stage(apan_xy_layer_3_bank_0_alpha)) { return false; }
    prepare_stage(stage_output, XY_ENGINE_SIZE);
    if (!run_stage(apan_xy_layer_4_bank_0_alpha)) { return false; }
    prepare_stage(stage_output, XY_ENGINE_SIZE);
    if (!run_stage(apan_xy_layer_5_bank_0_alpha)) { return false; }
    prepare_stage(stage_output, XY_ENGINE_SIZE);
    if (!run_stage(apan_xy_layer_6_bank_0_alpha)) { return false; }
    prepare_stage(stage_output, XY_ENGINE_SIZE);
    if (!run_stage(apan_xy_layer_7_bank_0_alpha)) { return false; }

    output_xy[0] = bfloat16_to_float(stage_output[0]);
    output_xy[1] = bfloat16_to_float(stage_output[1]);
    if (output_xy[0] > 1.0F) { output_xy[0] = 1.0F; }
    if (output_xy[1] > 1.0F) { output_xy[1] = 1.0F; }
    return true;
}
