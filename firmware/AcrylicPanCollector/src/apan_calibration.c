#include "apan_calibration.h"

#include <string.h>

#include "Fram.h"
#include "SoftSpi.h"
#include "wdt.h"

/* On-site calibration of the 12-area beta (docs/odl-calibration-experiment-20260927.md).
   The AxlCORE's own ODL_StartTrain keeps P and beta in bfloat16, and in the
   IchiPing project that rounding made beta diverge on the board.  The OS-ELM
   step therefore runs in float32 on the CPU.  P (4 KB) and beta (1.5 KB) do not
   fit the free RAM, so they live in the FRAM and are streamed row by row. */
#define HIDDEN  APAN_CALIBRATION_HIDDEN_SIZE
#define OUTPUTS APAN_CALIBRATION_OUTPUT_SIZE
#define P_ROW_BYTES    (HIDDEN * 4U)
#define BETA_ROW_BYTES (OUTPUTS * 4U)
#define FRAM_WORK_P     (APAN_CALIBRATION_FRAM_BASE)
#define FRAM_WORK_BETA  (FRAM_WORK_P + HIDDEN * P_ROW_BYTES)
#define FRAM_SAVED_BETA (FRAM_WORK_BETA + HIDDEN * BETA_ROW_BYTES)
#define FRAM_SAVED_HEADER (FRAM_SAVED_BETA + HIDDEN * BETA_ROW_BYTES)
#define HEADER_MAGIC (0x31435041UL) /* "APC1" */

typedef struct
{
    uint32_t magic;
    uint32_t model_crc32;
    uint16_t count;
    uint16_t reserved;
    uint32_t beta_crc32;
} SavedHeader;

static const int16_t *factory_beta;
static bool enabled;
static bool saved_valid;
static uint8_t source;
static uint16_t saved_count;
static uint16_t work_count;
/* Work vectors are static so a calibration hit does not deepen the stack. */
static float p_h[HIDDEN];
static float error[OUTPUTS];
static float row[HIDDEN];

static float bf16_to_float(int16_t bits)
{
    union { uint32_t bits; float value; } converted;
    converted.bits = ((uint32_t)(uint16_t)bits) << 16;
    return converted.value;
}

static int16_t float_to_bf16(float value)
{
    union { float value; uint32_t bits; } converted;
    converted.value = value;
    converted.bits += 0x7FFFUL + ((converted.bits >> 16) & 1UL);
    return (int16_t)(uint16_t)(converted.bits >> 16);
}

static uint32_t fram_row(uint32_t base, uint16_t index, uint32_t row_bytes)
{
    return base + (uint32_t)index * row_bytes;
}

uint32_t ApanCalibrationCrc32(uint32_t crc, const void *data, size_t size)
{
    const uint8_t *bytes = (const uint8_t *)data;
    size_t i;
    uint8_t bit;
    crc = ~crc;
    for (i = 0U; i < size; i++)
    {
        crc ^= bytes[i];
        for (bit = 0U; bit < 8U; bit++)
        {
            crc = (crc >> 1) ^ (((crc & 1UL) != 0UL) ? 0xEDB88320UL : 0UL);
        }
    }
    return ~crc;
}

static uint32_t beta_crc32(uint32_t base)
{
    uint32_t crc = 0UL;
    uint16_t r;
    for (r = 0U; r < HIDDEN; r++)
    {
        FramReadBlock(fram_row(base, r, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
        crc = ApanCalibrationCrc32(crc, row, BETA_ROW_BYTES);
    }
    return crc;
}

static void load_saved_header(void)
{
    SavedHeader header;
    FramReadBlock(FRAM_SAVED_HEADER, &header, (int)sizeof(header));
    saved_valid = (header.magic == HEADER_MAGIC) &&
                  (header.model_crc32 == APAN_CALIBRATION_MODEL_CRC32) &&
                  (header.beta_crc32 == beta_crc32(FRAM_SAVED_BETA));
    saved_count = saved_valid ? header.count : 0U;
}

bool ApanCalibrationInitialize(const int16_t *factory_beta_bf16)
{
    factory_beta = factory_beta_bf16;
    enabled = (factory_beta_bf16 != NULL) &&
              (ApanCalibrationCrc32(0UL, factory_beta_bf16,
                                    HIDDEN * OUTPUTS * sizeof(int16_t)) ==
               APAN_CALIBRATION_MODEL_CRC32);
    saved_valid = false;
    saved_count = 0U;
    work_count = 0U;
    source = APAN_CALIBRATION_SOURCE_FACTORY;
    if (!enabled)
    {
        return false;
    }
    SoftSpiPeripheralInit();
    FramInit();
    load_saved_header();
    if (saved_valid) { source = APAN_CALIBRATION_SOURCE_SAVED; }
    return true;
}

void ApanCalibrationGetStatus(ApanCalibrationStatus *status)
{
    if (status == NULL) { return; }
    status->source = source;
    status->saved_valid = saved_valid;
    status->saved_count = saved_count;
    status->work_count = work_count;
}

bool ApanCalibrationActiveBetaRow(uint16_t row_index, int16_t beta_bf16[OUTPUTS])
{
    uint32_t base;
    uint16_t k;
    if ((beta_bf16 == NULL) || (row_index >= HIDDEN) ||
        (source == APAN_CALIBRATION_SOURCE_FACTORY))
    {
        return false;
    }
    base = (source == APAN_CALIBRATION_SOURCE_SAVED) ? FRAM_SAVED_BETA : FRAM_WORK_BETA;
    FramReadBlock(fram_row(base, row_index, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
    for (k = 0U; k < OUTPUTS; k++) { beta_bf16[k] = float_to_bf16(row[k]); }
    return true;
}

void ApanCalibrationHidden(const int16_t *input_bf16, const int16_t *alpha_bf16,
                           uint16_t input_size, float hidden[HIDDEN])
{
    uint16_t i;
    uint16_t j;
    for (j = 0U; j < HIDDEN; j++)
    {
        float z = 0.0F;
        for (i = 0U; i < input_size; i++)
        {
            z += bf16_to_float(input_bf16[i]) * bf16_to_float(alpha_bf16[(uint32_t)i * HIDDEN + j]);
        }
        z = 0.2F * z + 0.5F;
        if (z < 0.0F) { z = 0.0F; }
        if (z > 1.0F) { z = 1.0F; }
        hidden[j] = bf16_to_float(float_to_bf16(z));
    }
}

bool ApanCalibrationBegin(void)
{
    uint16_t r;
    uint16_t k;
    if (!enabled) { return false; }
    for (r = 0U; r < HIDDEN; r++)
    {
        FramWriteBlock(fram_row(FRAM_WORK_P, r, P_ROW_BYTES),
                       &apan_calibration_p0[(uint32_t)r * HIDDEN], (int)P_ROW_BYTES);
        for (k = 0U; k < OUTPUTS; k++)
        {
            row[k] = bf16_to_float(factory_beta[(uint32_t)r * OUTPUTS + k]);
        }
        FramWriteBlock(fram_row(FRAM_WORK_BETA, r, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
        wdt_clear();
    }
    work_count = 0U;
    source = APAN_CALIBRATION_SOURCE_WORKING;
    return true;
}

/* One OS-ELM step with forgetting factor 1:
     Ph = P h,  d = 1 + h.Ph,  P -= Ph Ph^T / d,  e = t - h.beta,  beta += (Ph / d) e^T */
bool ApanCalibrationUpdate(const float hidden[HIDDEN], uint8_t target)
{
    float denominator = 1.0F;
    uint16_t i;
    uint16_t j;
    if ((hidden == NULL) || (target >= OUTPUTS) ||
        (source != APAN_CALIBRATION_SOURCE_WORKING) || (work_count == 0xFFFFU))
    {
        return false;
    }
    for (j = 0U; j < HIDDEN; j++)
    {
        float sum = 0.0F;
        FramReadBlock(fram_row(FRAM_WORK_P, j, P_ROW_BYTES), row, (int)P_ROW_BYTES);
        for (i = 0U; i < HIDDEN; i++) { sum += row[i] * hidden[i]; }
        p_h[j] = sum;
        denominator += hidden[j] * sum;
    }
    /* P stays positive definite, so d >= 1; anything else means corrupted state. */
    if (!(denominator >= 1.0F) || !(denominator < 1.0e30F)) { return false; }
    for (j = 0U; j < HIDDEN; j++)
    {
        FramReadBlock(fram_row(FRAM_WORK_P, j, P_ROW_BYTES), row, (int)P_ROW_BYTES);
        for (i = 0U; i < HIDDEN; i++) { row[i] -= p_h[j] * p_h[i] / denominator; }
        FramWriteBlock(fram_row(FRAM_WORK_P, j, P_ROW_BYTES), row, (int)P_ROW_BYTES);
        wdt_clear();
    }
    for (i = 0U; i < OUTPUTS; i++) { error[i] = (i == target) ? 1.0F : 0.0F; }
    for (j = 0U; j < HIDDEN; j++)
    {
        FramReadBlock(fram_row(FRAM_WORK_BETA, j, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
        for (i = 0U; i < OUTPUTS; i++) { error[i] -= hidden[j] * row[i]; }
    }
    for (j = 0U; j < HIDDEN; j++)
    {
        float gain = p_h[j] / denominator;
        FramReadBlock(fram_row(FRAM_WORK_BETA, j, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
        for (i = 0U; i < OUTPUTS; i++) { row[i] += gain * error[i]; }
        FramWriteBlock(fram_row(FRAM_WORK_BETA, j, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
    }
    work_count++;
    return true;
}

bool ApanCalibrationCommit(void)
{
    SavedHeader header;
    uint16_t r;
    if ((source != APAN_CALIBRATION_SOURCE_WORKING) || (work_count == 0U)) { return false; }
    /* Invalidate first so a power loss mid-copy never leaves a mixed beta valid. */
    memset(&header, 0, sizeof(header));
    FramWriteBlock(FRAM_SAVED_HEADER, &header, (int)sizeof(header));
    for (r = 0U; r < HIDDEN; r++)
    {
        FramReadBlock(fram_row(FRAM_WORK_BETA, r, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
        FramWriteBlock(fram_row(FRAM_SAVED_BETA, r, BETA_ROW_BYTES), row, (int)BETA_ROW_BYTES);
        wdt_clear();
    }
    header.magic = HEADER_MAGIC;
    header.model_crc32 = APAN_CALIBRATION_MODEL_CRC32;
    header.count = work_count;
    header.beta_crc32 = beta_crc32(FRAM_SAVED_BETA);
    FramWriteBlock(FRAM_SAVED_HEADER, &header, (int)sizeof(header));
    load_saved_header();
    if (!saved_valid) { return false; }
    source = APAN_CALIBRATION_SOURCE_SAVED;
    work_count = 0U;
    return true;
}

void ApanCalibrationDiscard(void)
{
    work_count = 0U;
    source = saved_valid ? APAN_CALIBRATION_SOURCE_SAVED : APAN_CALIBRATION_SOURCE_FACTORY;
}

void ApanCalibrationFactoryReset(void)
{
    SavedHeader header;
    if (enabled)
    {
        memset(&header, 0, sizeof(header));
        FramWriteBlock(FRAM_SAVED_HEADER, &header, (int)sizeof(header));
    }
    saved_valid = false;
    saved_count = 0U;
    work_count = 0U;
    source = APAN_CALIBRATION_SOURCE_FACTORY;
}
