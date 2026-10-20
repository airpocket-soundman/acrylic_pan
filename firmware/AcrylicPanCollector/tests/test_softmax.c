/* Host test of the integer softmax against a float64 reference. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "apan_position_softmax.h"

#define COUNT 60U
#define VECTORS 20000U

static uint32_t rng_state = 12345U;

static uint32_t next_random(void)
{
    rng_state = rng_state * 1664525U + 1013904223U;
    return rng_state >> 8;
}

/* A bfloat16-representable value, as the accelerator produces. */
static float bfloat16_value(double value)
{
    union { float f; uint32_t bits; } converted;
    converted.f = (float)value;
    converted.bits = (converted.bits + 0x7FFFU + ((converted.bits >> 16) & 1U)) & 0xFFFF0000UL;
    return converted.f;
}

int main(void)
{
    double worst_absolute = 0.0;
    double worst_relative = 0.0;
    double worst_sum = 0.0;
    uint32_t vector;
    uint16_t index;

    for (vector = 0U; vector < VECTORS; vector++)
    {
        float logits[COUNT];
        float probability[COUNT];
        double reference[COUNT];
        double total = 0.0;
        double maximum;
        double spread = (vector % 4U == 0U) ? 0.05 : ((vector % 4U == 1U) ? 0.3 : ((vector % 4U == 2U) ? 1.0 : 3.0));
        uint16_t best = 0U;
        double sum = 0.0;

        for (index = 0U; index < COUNT; index++)
        {
            double unit = ((double)(next_random() & 0xFFFFU) / 65535.0) * 2.0 - 1.0;
            logits[index] = bfloat16_value(unit * spread);
            if (logits[index] > logits[best]) { best = index; }
        }
        if (!apan_position_softmax(logits, COUNT, best, probability))
        {
            printf("softmax rejected a finite vector %u\n", vector);
            return 1;
        }
        maximum = logits[best];
        for (index = 0U; index < COUNT; index++)
        {
            reference[index] = exp(20.0 * ((double)logits[index] - maximum));
            total += reference[index];
        }
        for (index = 0U; index < COUNT; index++)
        {
            double expected = reference[index] / total;
            double error = fabs((double)probability[index] - expected);
            sum += probability[index];
            if (error > worst_absolute) { worst_absolute = error; }
            if ((expected > 1e-3) && (error / expected > worst_relative)) { worst_relative = error / expected; }
        }
        if (fabs(sum - 1.0) > worst_sum) { worst_sum = fabs(sum - 1.0); }
    }
    printf("vectors %u  max abs error %.3e  max rel error (p > 1e-3) %.3e  max |sum-1| %.3e\n",
           VECTORS, worst_absolute, worst_relative, worst_sum);
    if ((worst_absolute > 5e-7) || (worst_relative > 2e-6) || (worst_sum > 5e-7))
    {
        printf("integer softmax is not accurate enough\n");
        return 1;
    }

    {   /* non-finite input is rejected */
        float logits[COUNT];
        float probability[COUNT];
        union { uint32_t bits; float value; } nan_value;
        memset(logits, 0, sizeof(logits));
        nan_value.bits = 0x7FC00000UL;
        logits[5] = nan_value.value;
        if (apan_position_softmax(logits, COUNT, 0U, probability))
        {
            printf("NaN was accepted\n");
            return 1;
        }
    }
    printf("softmax test passed\n");
    return 0;
}
