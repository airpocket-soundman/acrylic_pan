#ifndef APAN_POSITION_SOFTMAX_H
#define APAN_POSITION_SOFTMAX_H

/* Integer softmax of the 60 position logits, softmax(20 x logit).
 *
 * The Cortex-M0+ has no FPU, so every float operation is a library call of roughly 300 cycles
 * and a float expf made the softmax take about 13 ms.  Here the logits are decoded straight from
 * their IEEE bits into Q27 integers, exp() comes from three small tables (terms below e^-24 are dropped) and 64-bit integer
 * products, and only the final normalisation uses float.  The result matches the float64
 * softmax to about 1e-7 (see tests/test_position_softmax.py and tests/test_softmax.c).
 */

#include <stdbool.h>
#include <stdint.h>

#include "apan_position_softmax_table.h"

#define APAN_SOFTMAX_SCALE 20UL                /* 1 / temperature (0.05) */
#define APAN_SOFTMAX_Q 27                      /* fractional bits of the logits */
#define APAN_SOFTMAX_LIMIT (1L << 30)          /* |logit| is clamped to 8.0 */
#define APAN_SOFTMAX_PRUNE ((uint32_t)((24UL << APAN_SOFTMAX_Q) / APAN_SOFTMAX_SCALE))

/* value * 2^27 from the IEEE-754 bits of a float; false for NaN and infinity. */
static bool apan_softmax_decode(float value, int32_t *fixed)
{
    union { float value; uint32_t bits; } converted;
    uint32_t exponent;
    uint32_t mantissa;
    int32_t shift;
    int32_t magnitude;

    converted.value = value;
    exponent = (converted.bits >> 23) & 0xFFU;
    if (exponent == 0xFFU) { return false; }
    if (exponent == 0U) { *fixed = 0; return true; }
    mantissa = (converted.bits & 0x7FFFFFUL) | 0x800000UL;
    shift = (int32_t)exponent - 127 - 23 + APAN_SOFTMAX_Q;   /* value = mantissa * 2^(exponent-150) */
    if (shift >= 7) { magnitude = APAN_SOFTMAX_LIMIT; }
    else if (shift >= 0)
    {
        uint32_t scaled = mantissa << shift;
        magnitude = (scaled >= (uint32_t)APAN_SOFTMAX_LIMIT) ? APAN_SOFTMAX_LIMIT : (int32_t)scaled;
    }
    else if (shift <= -24) { magnitude = 0; }
    else { magnitude = (int32_t)(mantissa >> (uint32_t)(-shift)); }
    *fixed = ((converted.bits >> 31) != 0U) ? -magnitude : magnitude;
    return true;
}

/* exp(-distance / 2^27) in Q31 for 0 <= distance < 24 * 2^27. */
static uint32_t apan_softmax_exp(uint32_t distance)
{
    uint32_t integer = distance >> APAN_SOFTMAX_Q;
    uint32_t fraction = distance & ((1UL << APAN_SOFTMAX_Q) - 1UL);
    uint32_t hi = fraction >> 19;                /* 8 bits */
    uint32_t mid = (fraction >> 11) & 0xFFU;     /* 8 bits */
    uint32_t lo = fraction & 0x7FFU;             /* 11 bits: exp(-t) = 1 - t, t < 2^-16 */
    uint64_t product = ((uint64_t)apan_softmax_exp_int[integer] * apan_softmax_exp_hi[hi]) >> 31;
    product = (product * apan_softmax_exp_mid[mid]) >> 31;
    product = (product * ((1UL << 31) - (lo << 4))) >> 31;
    return (uint32_t)product;
}

/* probability[i] = exp(20 (logit[i] - logit[best])) / sum; `best` is the index of the maximum.
   Returns false when a logit is NaN or infinite. */
static bool apan_position_softmax(const float *logits, uint16_t count, uint16_t best,
                                  float *probability)
{
    int32_t fixed;
    int32_t maximum;
    uint32_t terms[64];
    uint64_t total = 0U;
    uint16_t index;
    float inverse;

    if ((count == 0U) || (count > 64U) || (best >= count)) { return false; }
    if (!apan_softmax_decode(logits[best], &maximum)) { return false; }
    for (index = 0U; index < count; index++)
    {
        uint32_t gap;
        if (!apan_softmax_decode(logits[index], &fixed)) { return false; }
        gap = (maximum > fixed) ? (uint32_t)(maximum - fixed) : 0U;
        terms[index] = (gap >= APAN_SOFTMAX_PRUNE) ? 0U : apan_softmax_exp(gap * (uint32_t)APAN_SOFTMAX_SCALE);
        total += terms[index];
    }
    inverse = 1.0F / (float)total;
    for (index = 0U; index < count; index++)
    {
        probability[index] = (float)terms[index] * inverse;
    }
    return true;
}

#endif
