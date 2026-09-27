#include <math.h>
#include <stdio.h>
#include <string.h>

#include "Fram.h"
#include "SoftSpi.h"
#include "wdt.h"
#include "apan_12class_model.h"
#include "apan_calibration.h"
#include "apan_calibration_golden.h"

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "check failed at line %d: %s\n", __LINE__, #condition); return 1; \
} } while (0)

#define FRAM_SIZE (256UL * 1024UL)
#define WORK_BETA (APAN_CALIBRATION_FRAM_BASE + 32UL * 32UL * 4UL)
#define SAVED_BETA (WORK_BETA + 32UL * 12UL * 4UL)

static unsigned char fram[FRAM_SIZE];
static int fram_initialized;

void SoftSpiPeripheralInit(void) {}
void wdt_clear(void) {}
void FramInit(void) { fram_initialized = 1; }

void FramReadBlock(uint32_t address, void *dst, int size)
{
    memcpy(dst, &fram[address], (size_t)size);
}

void FramWriteBlock(uint32_t address, const void *src, int size)
{
    memcpy(&fram[address], src, (size_t)size);
}

static float bf16_to_float(uint16_t bits)
{
    union { uint32_t bits; float value; } converted;
    converted.bits = (uint32_t)bits << 16;
    return converted.value;
}

static float fram_float(uint32_t address)
{
    float value;
    memcpy(&value, &fram[address], sizeof(value));
    return value;
}

int main(void)
{
    ApanCalibrationStatus status;
    float hidden[APAN_CALIBRATION_HIDDEN_SIZE];
    int16_t row[APAN_CALIBRATION_OUTPUT_SIZE];
    int16_t wrong_beta[APAN_MODEL_HIDDEN_SIZE * APAN_MODEL_OUTPUT_SIZE];
    double worst_beta = 0.0;
    double worst_p = 0.0;
    unsigned hit;
    unsigned i;
    unsigned j;

    /* The generated prior belongs to the deployed factory beta. */
    CHECK(ApanCalibrationCrc32(0UL, apan_model_beta, sizeof(apan_model_beta)) ==
          APAN_CALIBRATION_MODEL_CRC32);
    CHECK(ApanCalibrationCrc32(0UL, "123456789", 9U) == 0xCBF43926UL);

    /* A different factory model disables calibration without touching FRAM. */
    memcpy(wrong_beta, apan_model_beta, sizeof(wrong_beta));
    wrong_beta[0] ^= 1;
    CHECK(!ApanCalibrationInitialize(wrong_beta));
    CHECK(!fram_initialized);
    CHECK(!ApanCalibrationBegin());

    /* Blank FRAM: factory beta, nothing saved. */
    CHECK(ApanCalibrationInitialize(apan_model_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_FACTORY);
    CHECK(!status.saved_valid);
    CHECK(!ApanCalibrationActiveBetaRow(0U, row));
    CHECK(!ApanCalibrationUpdate(apan_calibration_golden_hidden, 0U));
    CHECK(!ApanCalibrationCommit());

    /* CPU hidden layer equals the PC reference to one bfloat16 step. */
    for (hit = 0U; hit < APAN_CALIBRATION_GOLDEN_COUNT; hit++)
    {
        ApanCalibrationHidden((const int16_t *)apan_calibration_golden_inputs[hit],
                              apan_model_alpha, APAN_MODEL_INPUT_SIZE, hidden);
        for (j = 0U; j < APAN_CALIBRATION_HIDDEN_SIZE; j++)
        {
            CHECK(fabsf(hidden[j] -
                        apan_calibration_golden_hidden[hit * APAN_CALIBRATION_HIDDEN_SIZE + j]) <=
                  0.0040F);
        }
    }

    /* Working calibration starts from the factory beta. */
    CHECK(ApanCalibrationBegin());
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_WORKING);
    CHECK(status.work_count == 0U);
    CHECK(ApanCalibrationActiveBetaRow(3U, row));
    CHECK(memcmp(row, &apan_model_beta[3U * APAN_MODEL_OUTPUT_SIZE], sizeof(row)) == 0);
    CHECK(!ApanCalibrationUpdate(apan_calibration_golden_hidden, APAN_CALIBRATION_OUTPUT_SIZE));

    /* float32 OS-ELM on FRAM rows reproduces the float64 PC sequence. */
    for (hit = 0U; hit < APAN_CALIBRATION_GOLDEN_COUNT; hit++)
    {
        CHECK(ApanCalibrationUpdate(
            &apan_calibration_golden_hidden[hit * APAN_CALIBRATION_HIDDEN_SIZE],
            apan_calibration_golden_targets[hit]));
    }
    ApanCalibrationGetStatus(&status);
    CHECK(status.work_count == APAN_CALIBRATION_GOLDEN_COUNT);
    for (i = 0U; i < 32U * 12U; i++)
    {
        double error = fabs((double)fram_float(WORK_BETA + i * 4U) -
                            (double)apan_calibration_golden_beta[i]);
        if (error > worst_beta) { worst_beta = error; }
    }
    for (i = 0U; i < 32U * 32U; i++)
    {
        double error = fabs((double)fram_float(APAN_CALIBRATION_FRAM_BASE + i * 4U) -
                            (double)apan_calibration_golden_p[i]);
        if (error > worst_p) { worst_p = error; }
    }
    printf("calibration max |beta error| %.3g, max |P error| %.3g\n", worst_beta, worst_p);
    CHECK(worst_beta < 1.0e-4);
    CHECK(worst_p < 1.0e-5);

    /* The accelerator receives the working beta rounded to bfloat16. */
    CHECK(ApanCalibrationActiveBetaRow(5U, row));
    for (j = 0U; j < APAN_CALIBRATION_OUTPUT_SIZE; j++)
    {
        float expected = fram_float(WORK_BETA + (5U * 12U + j) * 4U);
        CHECK(fabsf(bf16_to_float((uint16_t)row[j]) - expected) <= fabsf(expected) / 256.0F);
    }

    /* Commit persists the working beta and survives a restart. */
    CHECK(ApanCalibrationCommit());
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_SAVED);
    CHECK(status.saved_valid);
    CHECK(status.saved_count == APAN_CALIBRATION_GOLDEN_COUNT);
    CHECK(memcmp(&fram[SAVED_BETA], &fram[WORK_BETA], 32U * 12U * 4U) == 0);
    CHECK(ApanCalibrationInitialize(apan_model_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_SAVED);
    CHECK(status.saved_count == APAN_CALIBRATION_GOLDEN_COUNT);

    /* Discarding a new working calibration returns to the saved one. */
    CHECK(ApanCalibrationBegin());
    CHECK(ApanCalibrationUpdate(apan_calibration_golden_hidden, 0U));
    ApanCalibrationDiscard();
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_SAVED);
    CHECK(status.work_count == 0U);

    /* A corrupted saved beta is rejected at start-up. */
    fram[SAVED_BETA + 17U] ^= 0x40U;
    CHECK(ApanCalibrationInitialize(apan_model_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_FACTORY);
    CHECK(!status.saved_valid);
    fram[SAVED_BETA + 17U] ^= 0x40U;
    CHECK(ApanCalibrationInitialize(apan_model_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_SAVED);

    /* Factory reset forgets the saved calibration permanently. */
    ApanCalibrationFactoryReset();
    CHECK(ApanCalibrationInitialize(apan_model_beta));
    ApanCalibrationGetStatus(&status);
    CHECK(status.source == APAN_CALIBRATION_SOURCE_FACTORY);
    CHECK(!status.saved_valid);

    printf("calibration host test passed\n");
    return 0;
}
